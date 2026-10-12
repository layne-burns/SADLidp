"""SPEC §7 network: per-host spacing, the challenge breaker, and the existing retry/credential rules."""

import email.message
import gzip
import io
import urllib.error

import pytest

from sadlidp.net import Client, Response, _Raw, is_challenge

CHALLENGE_HTML = b"<!DOCTYPE html><html><head><title>Just a moment...</title></head><body>cf-chl</body></html>"


def _fake_once(monkeypatch, seq):
    """Patch Client._once to return items of `seq` in turn (the last one repeats); returns the call log."""
    calls = []
    items = list(seq)

    def fake_once(self, method, url, headers, data):
        calls.append((method, url, dict(headers)))
        return items.pop(0) if len(items) > 1 else items[0]

    monkeypatch.setattr(Client, "_once", fake_once)
    return calls


def _clocked(**kw):
    """A Client whose sleep advances an injected clock; returns (client, slept)."""
    t = [0.0]
    slept = []

    def sleep(s):
        slept.append(s)
        t[0] += s

    return Client(sleep=sleep, clock=lambda: t[0], **kw), slept


def test_spacing_uses_the_hosts_interval(monkeypatch):
    _fake_once(monkeypatch, [_Raw(200)])
    c, slept = _clocked(min_interval_s=2.0, host_intervals={"www.FantasyPros.com": 5.0})
    for url in ("https://www.fantasypros.com/a", "https://www.fantasypros.com/b", "https://api.sleeper.app/1"):
        c.get(url)
    c.get("https://api.sleeper.app/2")
    assert slept == [5.0, 2.0]


def test_host_interval_can_be_shorter_than_the_default(monkeypatch):
    _fake_once(monkeypatch, [_Raw(200)])
    c, slept = _clocked(min_interval_s=2.0, host_intervals={"lm-api-reads.fantasy.espn.com": 0.5})
    c.get("https://lm-api-reads.fantasy.espn.com/a")
    c.get("https://lm-api-reads.fantasy.espn.com/b")
    assert slept == [0.5]


def test_503_with_cf_mitigated_is_not_retried_and_blocks_the_host(monkeypatch):
    calls = _fake_once(monkeypatch, [_Raw(503, {"cf-mitigated": "challenge", "set-cookie": "x"}, [], b"")])
    c, slept = _clocked(min_interval_s=0)
    r = c.get("https://www.fantasysharks.com/idp")
    assert r.status == 503 and r.challenged and r.error is None and not r.ok
    assert r.headers == {"cf-mitigated": "challenge"}
    assert len(calls) == 1 and slept == []
    assert c.blocked_hosts == {"www.fantasysharks.com"}
    again = c.get("https://www.fantasysharks.com/other")
    assert again.status is None and again.error == "blocked: challenge earlier this run" and not again.challenged
    assert len(calls) == 1
    assert c.get("https://fantasysharks.com/idp").status == 503  # a different host is not blocked
    assert len(calls) == 2


@pytest.mark.parametrize("status", [403, 503])
@pytest.mark.parametrize(
    "body", [CHALLENGE_HTML, b"<html>JUST A MOMENT please</html>", b'<script src="/cdn-cgi/cf-chl"></script>']
)
def test_challenge_page_body_is_detected(monkeypatch, status, body):
    calls = _fake_once(monkeypatch, [_Raw(status, {"content-type": "text/html"}, [], body)])
    c, _ = _clocked(min_interval_s=0)
    r = c.get("https://example.com/a")
    assert r.challenged and r.status == status and len(calls) == 1
    assert c.get("https://example.com/b").error == "blocked: challenge earlier this run"


def test_challenge_marker_only_counts_in_the_first_4kb():
    assert is_challenge(503, {}, b"x" * 4000 + b"cf-chl")
    assert not is_challenge(503, {}, b"x" * 4096 + b"cf-chl")


@pytest.mark.parametrize(
    "status,headers,body",
    [
        (200, {"cf-mitigated": "challenge"}, CHALLENGE_HTML),  # not a 403/503
        (429, {}, CHALLENGE_HTML),
        (403, {}, b'{"error": "forbidden"}'),
        (503, {"server": "cloudflare"}, b"Service Unavailable"),
    ],
)
def test_not_a_challenge(status, headers, body):
    assert not is_challenge(status, headers, body)


def test_plain_503_is_still_retried(monkeypatch):
    calls = _fake_once(monkeypatch, [_Raw(503, {}, [], b"Service Unavailable"), _Raw(200, {}, [], b"{}")])
    c, slept = _clocked(min_interval_s=0)
    r = c.get("https://api.sleeper.app/v1/state/nfl")
    assert r.ok and not r.challenged and len(calls) == 2 and slept == [2.0]
    assert c.blocked_hosts == set()


def test_plain_403_is_returned_without_retry_or_block(monkeypatch):
    calls = _fake_once(monkeypatch, [_Raw(403, {}, [], b"Forbidden")])
    c, _ = _clocked(min_interval_s=0)
    assert c.get("https://example.com/a").status == 403
    assert c.get("https://example.com/b").status == 403
    assert len(calls) == 2 and c.blocked_hosts == set()


def test_challenge_on_a_redirect_target_blocks_that_host_only(monkeypatch):
    def fake_once(self, method, url, headers, data):
        if url.startswith("https://a.example.com/"):
            return _Raw(302, {"location": "https://b.example.com/x"})
        return _Raw(403, {"cf-mitigated": "challenge"}, [], CHALLENGE_HTML)

    monkeypatch.setattr(Client, "_once", fake_once)
    c, _ = _clocked(min_interval_s=0)
    r = c.get("https://a.example.com/start")
    assert r.challenged and r.final_url == "https://b.example.com/x"
    assert c.blocked_hosts == {"b.example.com"}


class _FakeOpener:
    """Stands in for urllib's opener: raises HTTPError like a real non-2xx response."""

    def __init__(self, status, headers, body):
        self.status, self.headers, self.body = status, headers, body
        self.opened = 0

    def open(self, req, timeout=None):
        self.opened += 1
        msg = email.message.Message()
        for k, v in self.headers.items():
            msg[k] = v
        raise urllib.error.HTTPError(req.full_url, self.status, "x", msg, io.BytesIO(self.body))


def test_challenge_detected_through_the_real_once_path():
    c, slept = _clocked(min_interval_s=0)
    c._opener = _FakeOpener(
        503,
        {"Content-Encoding": "gzip", "Content-Type": "text/html", "Server": "cloudflare"},
        gzip.compress(CHALLENGE_HTML),
    )
    r = c.get("https://www.example.com/rankings")
    assert r.challenged and r.status == 503 and b"Just a moment" in r.body
    assert c._opener.opened == 1 and c.request_count == 1 and slept == []
    assert c.get("https://www.example.com/rankings").error == "blocked: challenge earlier this run"
    assert c._opener.opened == 1


def test_cf_mitigated_header_is_kept_in_responses():
    c, _ = _clocked(min_interval_s=0)
    c._opener = _FakeOpener(403, {"cf-mitigated": "challenge", "Set-Cookie": "__cf_bm=secret"}, b"")
    r = c.get("https://www.example.com/a")
    assert r.challenged and r.headers == {"cf-mitigated": "challenge"}


def test_response_challenged_defaults_false():
    assert Response("u", "u", 200).challenged is False


# Existing behaviour kept (ported from the probe client tests).


def test_retries_then_succeeds(monkeypatch):
    _fake_once(monkeypatch, [_Raw(503), _Raw(error="timeout", retryable=True), _Raw(200, {}, [], b"{}")])
    c, slept = _clocked(min_interval_s=0)
    assert c.get("https://api.sleeper.app/v1/state/nfl").ok and slept == [2.0, 4.0]


def test_rate_limited_host_is_skipped_afterwards(monkeypatch):
    _fake_once(monkeypatch, [_Raw(429)])
    c, _ = _clocked(min_interval_s=0)
    assert c.get("https://www.fantasypros.com/a", retries=0).status == 429
    assert c.get("https://www.fantasypros.com/b").error == "skipped: host rate-limited (429)"
    assert c.blocked_hosts == set()


def test_dead_host_and_deadline(monkeypatch):
    calls = _fake_once(monkeypatch, [_Raw(error="timeout", retryable=True)])
    c, _ = _clocked(min_interval_s=0, retries=3)
    assert c.get("https://slow.example.com/a").error == "skipped: host unresponsive" and len(calls) == 2
    assert c.dead_hosts == {"slow.example.com"}
    t = [0.0]
    c2 = Client(min_interval_s=0, deadline_s=10, sleep=lambda s: None, clock=lambda: t[0])
    t[0] = 11
    assert c2.get("https://other.example.com/").error == "skipped: probe deadline"


def test_cookie_dropped_on_cross_domain_redirect(monkeypatch):
    def fake_once(self, method, url, headers, data):
        calls.append(dict(headers))
        if url.startswith("https://www.fantasypros.com/"):
            return _Raw(302, {"location": "https://auth.example.com/login"}, ["sessionid=new; Path=/"])
        return _Raw(200, {}, [], b"<html></html>")

    calls = []
    monkeypatch.setattr(Client, "_once", fake_once)
    c, _ = _clocked(min_interval_s=0)
    r = c.get("https://www.fantasypros.com/a", headers={"Cookie": "sid=secret"})
    assert r.status == 200 and calls[0].get("Cookie") == "sid=secret" and "Cookie" not in calls[1]
