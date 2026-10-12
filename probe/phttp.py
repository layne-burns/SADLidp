"""Polite stdlib HTTP client for the probe.

Per-host request spacing, retries with backoff, an identifying User-Agent,
manual redirect handling that never forwards credentials to another domain,
and no logging of request headers (SPEC.md D5, §7).
"""

from __future__ import annotations

import gzip
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass, field

USER_AGENT = "SADLidp-probe/0.1 (+https://github.com/layne-burns/SADLidp)"
MIN_INTERVAL_S = 2.0
SENSITIVE_HEADERS = {"cookie", "authorization"}
SAFE_RESPONSE_HEADERS = {
    "content-type",
    "content-length",
    "etag",
    "last-modified",
    "cache-control",
    "age",
    "retry-after",
    "server",
    "cf-cache-status",
    "x-requests-remaining",
    "x-requests-used",
    "x-requests-last",
}
RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_REDIRECTS = 5


@dataclass
class Response:
    url: str  # requested URL, with redacted query params
    final_url: str  # URL after redirects, with redacted query params
    status: int | None
    headers: dict[str, str] = field(default_factory=dict)  # SAFE_RESPONSE_HEADERS only
    body: bytes = b""
    error: str | None = None
    elapsed_s: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status is not None and 200 <= self.status < 300

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.body)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # surface 3xx as HTTPError so redirects are followed manually


def redact_url(url: str, redact: tuple[str, ...] = ()) -> str:
    if not redact:
        return url
    parts = urllib.parse.urlsplit(url)
    query = [
        (k, "REDACTED" if k in redact else v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    ]
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query, safe="[]")))


def base_domain(host: str) -> str:
    labels = host.lower().split(":")[0].split(".")
    return ".".join(labels[-2:])


def _decode(body: bytes, encoding: str | None) -> bytes:
    if encoding == "gzip":
        return gzip.decompress(body)
    if encoding == "deflate":
        return zlib.decompress(body)
    return body


class Client:
    def __init__(
        self,
        min_interval_s: float = MIN_INTERVAL_S,
        timeout_s: float = 45.0,
        retries: int = 3,
        sleep=time.sleep,
        clock=time.monotonic,
    ):
        self.min_interval_s = min_interval_s
        self.timeout_s = timeout_s
        self.retries = retries
        self._sleep = sleep
        self._clock = clock
        self._last_request: dict[str, float] = {}
        self._opener = urllib.request.build_opener(_NoRedirect)
        self.request_count = 0

    def _space(self, host: str) -> None:
        last = self._last_request.get(host)
        if last is not None:
            wait = self.min_interval_s - (self._clock() - last)
            if wait > 0:
                self._sleep(wait)
        self._last_request[host] = self._clock()

    def _once(self, url: str, headers: dict[str, str]) -> tuple[int | None, dict, bytes, str | None]:
        req = urllib.request.Request(url, method="GET")
        req.add_header("User-Agent", USER_AGENT)
        req.add_header("Accept-Encoding", "gzip, deflate")
        for k, v in headers.items():
            req.add_header(k, v)
        self.request_count += 1
        try:
            with self._opener.open(req, timeout=self.timeout_s) as resp:
                raw = resp.read()
                hdrs = {k.lower(): v for k, v in resp.headers.items()}
                return resp.status, hdrs, _decode(raw, hdrs.get("content-encoding")), None
        except urllib.error.HTTPError as e:
            hdrs = {k.lower(): v for k, v in (e.headers or {}).items()}
            try:
                raw = e.read()
                body = _decode(raw, hdrs.get("content-encoding"))
            except Exception:  # noqa: BLE001 - body of an error response is best-effort
                body = b""
            return e.code, hdrs, body, None
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            return None, {}, b"", f"{type(e).__name__}: {getattr(e, 'reason', e)}"

    def get(self, url: str, *, headers: dict[str, str] | None = None, redact: tuple[str, ...] = ()) -> Response:
        """GET with spacing, retries and manual redirects.

        Sensitive headers (Cookie, Authorization) are only sent to hosts sharing the
        original request's base domain; they are dropped on cross-domain redirects.
        """
        headers = dict(headers or {})
        origin_domain = base_domain(urllib.parse.urlsplit(url).netloc)
        start = self._clock()
        current = url
        for _hop in range(MAX_REDIRECTS + 1):
            host = urllib.parse.urlsplit(current).netloc
            hop_headers = {
                k: v
                for k, v in headers.items()
                if k.lower() not in SENSITIVE_HEADERS or base_domain(host) == origin_domain
            }
            status, hdrs, body, error = None, {}, b"", None
            for attempt in range(self.retries + 1):
                self._space(host)
                status, hdrs, body, error = self._once(current, hop_headers)
                retryable = error is not None or status in RETRY_STATUSES
                if not retryable or attempt == self.retries:
                    break
                backoff = 2.0 * (2**attempt)
                retry_after = hdrs.get("retry-after", "")
                if retry_after.isdigit():
                    backoff = max(backoff, min(float(retry_after), 30.0))
                self._sleep(backoff)
            if status in (301, 302, 303, 307, 308) and hdrs.get("location"):
                current = urllib.parse.urljoin(current, hdrs["location"])
                continue
            return Response(
                url=redact_url(url, redact),
                final_url=redact_url(current, redact),
                status=status,
                headers={k: v for k, v in hdrs.items() if k in SAFE_RESPONSE_HEADERS},
                body=body,
                error=error,
                elapsed_s=round(self._clock() - start, 2),
            )
        return Response(
            url=redact_url(url, redact),
            final_url=redact_url(current, redact),
            status=None,
            error=f"too many redirects (> {MAX_REDIRECTS})",
            elapsed_s=round(self._clock() - start, 2),
        )
