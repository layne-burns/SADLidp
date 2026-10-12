import json

from probe import leakcheck as lc
from probe import sources_probe as sp
from probe import structure as st
from probe.phttp import Client, _Raw, base_domain, redact_url

HTML = """
<html><body>
<p>Week 6 IDP Rankings</p>
<table id="data"><thead><tr><th>Rank</th><th>Player</th><th>Opp</th></tr></thead>
<tbody><tr><td>1</td><td>A. Player</td><td>vs MIA</td></tr><tr><td>2</td><td>B. Player</td><td>@ NE</td></tr></tbody></table>
<table><tr><th>X</th></tr><tr><td>1</td></tr></table>
<a href="/idp/week-6-idp-rankings">Week 6 IDP rankings</a><a href="/about">About</a>
<script>var ecrData = {"players":[{"player_name":"A \\"Q\\" B","player_position_id":"LB"}],"week":6,"note":"a } b"};</script>
</body></html>
"""


def test_html_tables_structure_only():
    tables = st.html_tables(HTML)
    assert tables[0] == {
        "id": "data",
        "class": None,
        "headers": ["Rank", "Player", "Opp"],
        "headers_all_th": True,
        "row_count": 2,
    }
    assert tables[1]["headers"] == ["X"] and tables[1]["row_count"] == 1
    assert "A. Player" not in str(tables)


def test_unclosed_thead_does_not_leak_into_later_tables():
    html = (
        "<table><thead><tr><th>H</th></tr></table>"  # thead never closed
        "<table><tbody><tr><td>Secret Name</td><td>9.5</td></tr></tbody></table>"
    )
    second = st.html_tables(html)[1]
    assert second["headers"] == [] and second["row_count"] == 1


def test_td_header_rows_are_flagged_not_trusted():
    html = "<table><thead><tr><td>Secret Name</td><td>9.5</td></tr></thead></table>"
    t = st.html_tables(html)[0]
    assert t["headers_all_th"] is False
    assert sp.fp_structure(html)["tables"][0] == {"headers": None, "header_cells": 2, "row_count": 0}


def test_table_excerpts_include_rows():
    ex = st.table_excerpts(HTML, max_rows=1)
    assert ex[0]["rows"] == [["1", "A. Player", "vs MIA"]]


def test_find_js_json_handles_strings_with_braces():
    obj = st.find_js_json(HTML, "ecrData")
    assert obj["week"] == 6 and obj["note"] == "a } b"
    assert obj["players"][0]["player_position_id"] == "LB"
    assert st.find_js_json(HTML, "missingVar") is None


def test_json_key_paths_have_no_values():
    paths = st.json_key_paths({"players": [{"name": "Secret Name", "pts": 1.5}], "week": 6})
    assert "players[].name: str" in paths and "week: int" in paths
    assert not any("Secret Name" in p for p in paths)


def test_links_and_markers_and_weeks():
    assert st.matching_links(HTML, r"week.*idp") == [
        {"href": "/idp/week-6-idp-rankings", "text": "Week 6 IDP rankings"}
    ]
    assert st.markers("<a>Log Out</a>", {"logout": r"log\s?out"}) == {"logout": True}
    assert st.week_labels(HTML) == [6]


def test_login_form_separates_reportable_fields_from_hidden_values():
    html = (
        '<form method="post" action="/accounts/login/"><input type="hidden" name="csrfmiddlewaretoken" value="tok123">'
        '<input type="email" name="username"><input type="password" name="password"><input type="submit"></form>'
    )
    f = st.login_form(html)
    assert f["user_field"] == "username" and f["password_field"] == "password"
    assert f["hidden"] == {"csrfmiddlewaretoken": "tok123"}
    assert "tok123" not in json.dumps(f["fields"])
    assert st.login_form("<form><input name='q'></form>") is None


def test_redact_and_base_domain():
    assert "REDACTED" in redact_url("https://x.com/a?apiKey=abc&markets=m", ("apiKey",))
    assert "abc" not in redact_url("https://x.com/a?apiKey=abc", ("apiKey",))
    assert base_domain("secure.fantasypros.com") == "fantasypros.com"


def _redirect_client(monkeypatch, location: str):
    calls = []

    def fake_once(self, method, url, headers, data):
        calls.append((url, dict(headers)))
        if url.startswith("https://www.fantasypros.com/"):
            return _Raw(302, {"location": location}, ["sessionid=newsecret; Path=/"])
        return _Raw(200, {"content-type": "text/html"}, [], b"<html></html>")

    monkeypatch.setattr(Client, "_once", fake_once)
    return Client(min_interval_s=0, sleep=lambda s: None), calls


def test_client_drops_cookie_on_cross_domain_redirect(monkeypatch):
    c, calls = _redirect_client(monkeypatch, "https://auth.example.com/login")
    r = c.get("https://www.fantasypros.com/nfl/rankings/idp.php", headers={"Cookie": "sid=supersecretvalue"})
    assert r.status == 200 and r.final_url == "https://auth.example.com/login"
    assert calls[0][1].get("Cookie") == "sid=supersecretvalue"
    assert "Cookie" not in calls[1][1]


def test_client_drops_credentials_on_https_to_http_and_userinfo(monkeypatch):
    for location in ("http://www.fantasypros.com/other", "https://fantasypros.com:x@evil.com/"):
        c, calls = _redirect_client(monkeypatch, location)
        jar = {"csrftoken": "abc"}
        c.get("https://www.fantasypros.com/a", headers={"Cookie": "sid=supersecretvalue"}, jar=jar)
        assert "Cookie" not in calls[1][1], location


def test_client_cookie_jar_collects_and_sends(monkeypatch):
    c, calls = _redirect_client(monkeypatch, "https://secure.fantasypros.com/next")
    jar: dict = {}
    c.get("https://www.fantasypros.com/a", jar=jar)
    assert jar == {"sessionid": "newsecret"}
    assert calls[1][1]["Cookie"] == "sessionid=newsecret"


def test_client_retries_then_succeeds(monkeypatch):
    seq = iter([_Raw(503), _Raw(error="timeout", retryable=True), _Raw(200, {}, [], b"{}")])
    monkeypatch.setattr(Client, "_once", lambda self, method, url, headers, data: next(seq))
    slept = []
    r = Client(min_interval_s=0, sleep=slept.append).get("https://api.sleeper.app/v1/state/nfl")
    assert r.ok and slept == [2.0, 4.0]


def test_client_invalid_request_is_not_retried_or_echoed(monkeypatch):
    c = Client(min_interval_s=0, sleep=lambda s: None)
    r = c.get("https://www.fantasypros.com/a", headers={"Cookie": "sid=abc\nsecretpart12345"})
    assert r.status is None and r.error == "invalid request: ValueError"
    assert c.request_count == 1


def test_client_circuit_breaker_and_deadline(monkeypatch):
    attempts = []
    monkeypatch.setattr(
        Client,
        "_once",
        lambda self, method, url, headers, data: attempts.append(url) or _Raw(error="timeout", retryable=True),
    )
    c = Client(min_interval_s=0, retries=3, sleep=lambda s: None)
    r = c.get("https://slow.example.com/a")
    assert r.error == "skipped: host unresponsive" and len(attempts) == 2
    assert c.dead_hosts == {"slow.example.com"}
    assert c.get("https://slow.example.com/b").error == "skipped: host unresponsive"
    t = [0.0]
    c2 = Client(min_interval_s=0, deadline_s=10, sleep=lambda s: None, clock=lambda: t[0])
    t[0] = 11
    assert c2.get("https://other.example.com/").error == "skipped: probe deadline"


def test_client_spaces_requests_per_host(monkeypatch):
    monkeypatch.setattr(Client, "_once", lambda self, method, url, headers, data: _Raw(200))
    t = [0.0]
    slept = []
    c = Client(min_interval_s=2.0, sleep=lambda s: (slept.append(s), t.__setitem__(0, t[0] + s)), clock=lambda: t[0])
    c.get("https://a.com/1")
    c.get("https://a.com/2")
    c.get("https://b.com/1")
    assert slept == [2.0]


def test_fp_cookie_normalisation():
    assert sp.fp_cookie({"FP_COOKIE": "cookie: a=1;\nb=2\r\n"}) == ("a=1; b=2", None)
    cookie, err = sp.fp_cookie({"FP_COOKIE": "a=1\tb"})
    assert cookie == "" and "control" in err and "a=1" not in err


def test_leakcheck_detects_secrets_headers_and_private_plaintext(tmp_path):
    env = {
        "FP_COOKIE": "sid=abcdef123456; short=1\ncsrf=line2value99",
        "ODDS_API_KEY": "oddskey12345",
        "FP_EMAIL": "me@example.com",
        "FP_PASSWORD": "hunter22",
    }
    secrets = lc.secret_values(env)
    assert b"abcdef123456" in secrets["fp_cookie_value"] and b"line2value99" in secrets["fp_cookie_value"]
    files = {
        "ok.json": '{"cookie_configured": true, "x": "cookie configured: **True**"}',
        "cookie.txt": "value abcdef123456 here",
        "escaped.json": json.dumps({"e": "sid=abcdef123456; short=1\ncsrf=line2value99"}),
        "hdr.txt": "GET /\nSet-Cookie: x=y\n",
        "hdr_json.json": '{"Cookie": "a=b"}',
        "hdr_dict.txt": "{'Authorization': 'Bearer x'}",
        "odds.txt": "url?apiKey=oddskey12345",
        "age.txt": "AGE-SECRET-KEY-1ABC",
        "email.txt": "logged in as me@example.com",
        "fantasypros_rankings.json": "{}",
    }
    for name, content in files.items():
        (tmp_path / name).write_text(content)
    (tmp_path / "fantasypros_rankings.html.age").write_bytes(b"AGE-SECRET-KEY- ciphertext is skipped")
    hits = {f.name: lc.scan_file(f, secrets) for f in lc.iter_files([tmp_path])}
    assert hits["ok.json"] == []
    assert hits["cookie.txt"] == ["fp_cookie_value"]
    assert hits["escaped.json"] == ["fp_cookie_value"]
    assert hits["hdr.txt"] == ["http_credential_header"]
    assert hits["hdr_json.json"] == ["http_credential_header"]
    assert hits["hdr_dict.txt"] == ["http_credential_header"]
    assert hits["odds.txt"] == ["odds_api_key"]
    assert hits["age.txt"] == ["age_secret_key"]
    assert hits["email.txt"] == ["fp_email"]
    assert hits["fantasypros_rankings.json"] == ["private_source_plaintext"]
    assert hits["fantasypros_rankings.html.age"] == []


def test_leakcheck_main_exit_codes(tmp_path, monkeypatch, capsys):
    for var in ("FP_COOKIE", "ODDS_API_KEY", "FP_EMAIL", "FP_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    (tmp_path / "a.json").write_text("{}")
    assert lc.main([str(tmp_path)]) == 0
    (tmp_path / "b.txt").write_text("Authorization: Bearer x")
    assert lc.main([str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "b.txt: http_credential_header" in err and "Bearer" not in err
