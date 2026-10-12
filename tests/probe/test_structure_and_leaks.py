from probe import leakcheck as lc
from probe import structure as st
from probe.phttp import Client, base_domain, redact_url

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
    assert tables[0] == {"id": "data", "class": None, "headers": ["Rank", "Player", "Opp"], "row_count": 2}
    assert tables[1]["headers"] == ["X"] and tables[1]["row_count"] == 1
    assert "A. Player" not in str(tables)


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


def test_redact_and_base_domain():
    assert "REDACTED" in redact_url("https://x.com/a?apiKey=abc&markets=m", ("apiKey",))
    assert "abc" not in redact_url("https://x.com/a?apiKey=abc", ("apiKey",))
    assert base_domain("secure.fantasypros.com") == "fantasypros.com"


def test_client_drops_cookie_on_cross_domain_redirect(monkeypatch):
    calls = []

    def fake_once(self, url, headers):
        calls.append((url, dict(headers)))
        if url.startswith("https://www.fantasypros.com/"):
            return 302, {"location": "https://auth.example.com/login"}, b"", None
        return 200, {"content-type": "text/html"}, b"<html></html>", None

    monkeypatch.setattr(Client, "_once", fake_once)
    c = Client(min_interval_s=0, sleep=lambda s: None)
    r = c.get("https://www.fantasypros.com/nfl/rankings/idp.php", headers={"Cookie": "sid=supersecretvalue"})
    assert r.status == 200 and r.final_url == "https://auth.example.com/login"
    assert calls[0][1].get("Cookie") == "sid=supersecretvalue"
    assert "Cookie" not in calls[1][1]


def test_client_retries_then_succeeds(monkeypatch):
    seq = iter([(503, {}, b"", None), (None, {}, b"", "URLError: x"), (200, {}, b"{}", None)])
    monkeypatch.setattr(Client, "_once", lambda self, url, headers: next(seq))
    slept = []
    r = Client(min_interval_s=0, sleep=slept.append).get("https://api.sleeper.app/v1/state/nfl")
    assert r.ok and slept == [2.0, 4.0]


def test_client_spaces_requests_per_host(monkeypatch):
    monkeypatch.setattr(Client, "_once", lambda self, url, headers: (200, {}, b"", None))
    t = [0.0]
    slept = []
    c = Client(min_interval_s=2.0, sleep=lambda s: (slept.append(s), t.__setitem__(0, t[0] + s)), clock=lambda: t[0])
    c.get("https://a.com/1")
    c.get("https://a.com/2")
    c.get("https://b.com/1")
    assert slept == [2.0]


def test_leakcheck_detects_secrets_headers_and_private_plaintext(tmp_path):
    env = {"FP_COOKIE": "sid=abcdef123456; short=1", "ODDS_API_KEY": "oddskey12345"}
    secrets = lc.secret_values(env)
    assert secrets["fp_cookie_value"] == [b"abcdef123456"]
    (tmp_path / "ok.json").write_text('{"x": 1}')
    (tmp_path / "cookie.txt").write_text("value abcdef123456 here")
    (tmp_path / "hdr.txt").write_text("GET /\nSet-Cookie: x=y\n")
    (tmp_path / "odds.txt").write_text("url?apiKey=oddskey12345")
    (tmp_path / "age.txt").write_text("AGE-SECRET-KEY-1ABC")
    (tmp_path / "fantasypros_rankings.json").write_text("{}")
    (tmp_path / "fantasypros_rankings.html.age").write_bytes(b"AGE-SECRET-KEY- ciphertext is skipped")
    hits = {f.name: lc.scan_file(f, secrets) for f in lc.iter_files([tmp_path])}
    assert hits["ok.json"] == []
    assert hits["cookie.txt"] == ["fp_cookie_value"]
    assert hits["hdr.txt"] == ["http_credential_header"]
    assert hits["odds.txt"] == ["odds_api_key"]
    assert hits["age.txt"] == ["age_secret_key"]
    assert hits["fantasypros_rankings.json"] == ["private_source_plaintext"]
    assert hits["fantasypros_rankings.html.age"] == []


def test_leakcheck_main_exit_codes(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("FP_COOKIE", raising=False)
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    (tmp_path / "a.json").write_text("{}")
    assert lc.main([str(tmp_path)]) == 0
    (tmp_path / "b.txt").write_text("Authorization: Bearer x")
    assert lc.main([str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "b.txt: http_credential_header" in err and "Bearer" not in err
