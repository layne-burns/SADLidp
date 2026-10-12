"""Polite stdlib HTTP client (ported from the Phase 1 probe, probe/phttp.py).

Per-host request spacing, retries with backoff, an identifying User-Agent, a global
deadline with per-host circuit breakers (timeouts, 429, bot challenges), and manual
redirect handling that only sends credentials (Cookie/Authorization, cookie jar) over
https to the original site. Error strings never contain request headers or exception
messages that could echo them (SPEC.md D5, §7).
"""

from __future__ import annotations

import gzip
import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from collections import Counter
from dataclasses import dataclass, field
from http.cookies import CookieError, SimpleCookie

USER_AGENT = "SADLidp/0.1 (+https://github.com/layne-burns/SADLidp)"
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
    "cf-mitigated",
    "x-requests-remaining",
    "x-requests-used",
    "x-requests-last",
}
RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_REDIRECTS = 5
DEAD_AFTER_TIMEOUTS = 2
CHALLENGE_STATUSES = {403, 503}
CHALLENGE_MARKERS = (b"just a moment", b"cf-chl")
CHALLENGE_SNIFF_BYTES = 4096


@dataclass
class Response:
    url: str  # requested URL, with redacted query params
    final_url: str  # URL after redirects, with redacted query params
    status: int | None
    headers: dict[str, str] = field(default_factory=dict)  # SAFE_RESPONSE_HEADERS only
    body: bytes = b""
    error: str | None = None
    elapsed_s: float = 0.0
    challenged: bool = False  # a bot challenge (SPEC §7); the host is blocked for the rest of the run

    @property
    def ok(self) -> bool:
        return self.status is not None and 200 <= self.status < 300

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.body)


@dataclass
class _Raw:
    status: int | None = None
    headers: dict[str, str] = field(default_factory=dict)
    set_cookies: list[str] = field(default_factory=list)
    body: bytes = b""
    error: str | None = None
    retryable: bool = False


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


def base_domain(hostname: str) -> str:
    """Last two labels of a hostname (adequate for the .com hosts this probe talks to)."""
    labels = (hostname or "").lower().rstrip(".").split(".")
    return ".".join(labels[-2:])


def credentials_allowed(url: str, origin_domain: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    return (
        parts.scheme == "https"
        and parts.username is None
        and parts.password is None
        and base_domain(parts.hostname or "") == origin_domain
    )


def is_challenge(status: int | None, headers: dict[str, str], body: bytes) -> bool:
    """A 403/503 with `cf-mitigated`, or a "Just a moment" / cf-chl page (SPEC §7)."""
    if status not in CHALLENGE_STATUSES:
        return False
    head = body[:CHALLENGE_SNIFF_BYTES].lower()
    return "cf-mitigated" in headers or any(m in head for m in CHALLENGE_MARKERS)


def _decode(body: bytes, encoding: str | None) -> bytes:
    enc = (encoding or "").strip().lower()
    if enc in ("gzip", "x-gzip"):
        return gzip.decompress(body)
    if enc == "deflate":
        try:
            return zlib.decompress(body)
        except zlib.error:
            return zlib.decompress(body, -zlib.MAX_WBITS)
    return body


def _headers(msg) -> tuple[dict[str, str], list[str]]:
    if msg is None:
        return {}, []
    return {k.lower(): v for k, v in msg.items()}, list(msg.get_all("Set-Cookie") or [])


def _merge_cookies(jar: dict[str, str], set_cookies: list[str]) -> None:
    for header in set_cookies:
        try:
            parsed = SimpleCookie()
            parsed.load(header)
        except CookieError:
            continue
        for name, morsel in parsed.items():
            jar[name] = morsel.value


class Client:
    def __init__(
        self,
        min_interval_s: float = MIN_INTERVAL_S,
        timeout_s: float = 40.0,
        retries: int = 3,
        deadline_s: float = 20 * 60,
        sleep=time.sleep,
        clock=time.monotonic,
        host_intervals: dict[str, float] | None = None,
    ):
        self.min_interval_s = min_interval_s
        self.host_intervals = {h.lower(): float(s) for h, s in (host_intervals or {}).items()}
        self.timeout_s = timeout_s
        self.retries = retries
        self._sleep = sleep
        self._clock = clock
        self._deadline = clock() + deadline_s
        self._last_request: dict[str, float] = {}
        self._timeouts: Counter = Counter()
        self.dead_hosts: set[str] = set()
        self.rate_limited_hosts: set[str] = set()
        self.blocked_hosts: set[str] = set()
        self._opener = urllib.request.build_opener(_NoRedirect)
        self.request_count = 0

    def _space(self, host: str) -> None:
        """Wait out the host's interval (`host_intervals`, else `min_interval_s`) since its last request."""
        last = self._last_request.get(host)
        if last is not None:
            wait = self.host_intervals.get(host, self.min_interval_s) - (self._clock() - last)
            if wait > 0:
                self._sleep(wait)
        self._last_request[host] = self._clock()

    def _once(self, method: str, url: str, headers: dict[str, str], data: bytes | None) -> _Raw:
        self.request_count += 1
        try:
            req = urllib.request.Request(url, data=data, method=method)
            req.add_header("User-Agent", USER_AGENT)
            req.add_header("Accept-Encoding", "gzip, deflate")
            for k, v in headers.items():
                req.add_header(k, v)
            with self._opener.open(req, timeout=self.timeout_s) as resp:
                raw = resp.read()
                hdrs, cookies = _headers(resp.headers)
                return _Raw(resp.status, hdrs, cookies, _decode(raw, hdrs.get("content-encoding")))
        except urllib.error.HTTPError as e:
            hdrs, cookies = _headers(e.headers)
            try:
                body = _decode(e.read(), hdrs.get("content-encoding"))
            except Exception:  # noqa: BLE001 - body of an error response is best-effort
                body = b""
            return _Raw(e.code, hdrs, cookies, body)
        except (http.client.InvalidURL, ValueError) as e:
            # Bad URL or header value. Never echo str(e): it can contain the header (e.g. a cookie).
            return _Raw(error=f"invalid request: {type(e).__name__}")
        except (urllib.error.URLError, OSError) as e:
            reason = getattr(e, "reason", e)
            if isinstance(e, TimeoutError) or isinstance(reason, TimeoutError):
                return _Raw(error="timeout", retryable=True)
            return _Raw(error=f"{type(e).__name__}: {type(reason).__name__}", retryable=True)
        except (http.client.HTTPException, EOFError, zlib.error) as e:
            return _Raw(error=f"transfer error: {type(e).__name__}", retryable=True)

    def _skip(self, host: str) -> str | None:
        if host in self.blocked_hosts:
            return "blocked: challenge earlier this run"
        if host in self.dead_hosts:
            return "skipped: host unresponsive"
        if host in self.rate_limited_hosts:
            return "skipped: host rate-limited (429)"
        if self._clock() > self._deadline:
            return "skipped: probe deadline"
        return None

    def get(self, url: str, **kw) -> Response:
        return self.request("GET", url, **kw)

    def post_form(self, url: str, fields: dict[str, str], **kw) -> Response:
        headers = dict(kw.pop("headers", None) or {})
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        return self.request("POST", url, data=urllib.parse.urlencode(fields).encode(), headers=headers, **kw)

    def request(
        self,
        method: str,
        url: str,
        *,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        jar: dict[str, str] | None = None,
        redact: tuple[str, ...] = (),
        credential_domain: str | None = None,
        retries: int | None = None,
    ) -> Response:
        """Request with spacing, retries, a deadline and manual redirects.

        Sensitive headers, the cookie `jar` and any request body are only sent (and
        Set-Cookie only accepted) on https hops whose base domain is `credential_domain`,
        or the original request's base domain if not given. Only GET/HEAD are retried
        by default: a POST (e.g. a login) is never re-sent unless `retries` says so.
        A bot challenge is never retried and blocks its host for the rest of the run (§7).
        """
        headers = dict(headers or {})
        origin_domain = credential_domain or base_domain(urllib.parse.urlsplit(url).hostname or "")
        max_retries = retries if retries is not None else (self.retries if method in ("GET", "HEAD") else 0)
        start = self._clock()
        current = url
        raw = _Raw()
        challenged = False
        for _hop in range(MAX_REDIRECTS + 1):
            host = urllib.parse.urlsplit(current).hostname or ""
            allowed = credentials_allowed(current, origin_domain)
            if data is not None and not allowed:
                raw = _Raw(error="refused: request body is only sent over https to the original site")
                break
            hop_headers = {k: v for k, v in headers.items() if k.lower() not in SENSITIVE_HEADERS or allowed}
            if jar and allowed:
                hop_headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in jar.items())
            for attempt in range(max_retries + 1):
                skipped = self._skip(host)
                if skipped:
                    raw = _Raw(error=skipped)
                    break
                self._space(host)
                raw = self._once(method, current, hop_headers, data)
                if raw.error == "timeout":
                    self._timeouts[host] += 1
                    if self._timeouts[host] >= DEAD_AFTER_TIMEOUTS:
                        self.dead_hosts.add(host)
                elif raw.status is not None:
                    self._timeouts[host] = 0
                if is_challenge(raw.status, raw.headers, raw.body):
                    challenged = True
                    self.blocked_hosts.add(host)
                    break
                if raw.status == 429 and attempt == max_retries:
                    self.rate_limited_hosts.add(host)
                if not (raw.retryable or raw.status in RETRY_STATUSES) or attempt == max_retries:
                    break
                backoff = 2.0 * (2**attempt)
                retry_after = raw.headers.get("retry-after", "")
                if retry_after.isdigit():
                    backoff = max(backoff, min(float(retry_after), 30.0))
                self._sleep(backoff)
            if jar is not None and allowed:
                _merge_cookies(jar, raw.set_cookies)
            if raw.status in (301, 302, 303, 307, 308) and raw.headers.get("location"):
                current = urllib.parse.urljoin(current, raw.headers["location"])
                if raw.status in (301, 302, 303):
                    method, data = "GET", None
                continue
            break
        else:
            raw = _Raw(error=f"too many redirects (> {MAX_REDIRECTS})")
        return Response(
            url=redact_url(url, redact),
            final_url=redact_url(current, redact),
            status=raw.status,
            headers={k: v for k, v in raw.headers.items() if k in SAFE_RESPONSE_HEADERS},
            body=raw.body,
            error=raw.error,
            elapsed_s=round(self._clock() - start, 2),
            challenged=challenged,
        )
