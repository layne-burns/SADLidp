import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sadlidp.matching import Match, Matcher, normalize_name
from sadlidp.positions import slot_of
from sadlidp.sources import fantasypros, fp_dst
from sadlidp.sources.base import OK, FetchContext

ROOT = Path(__file__).parent.parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
CFG = {"_root": str(ROOT / "config")}
ECR = {
    name: json.loads((FIXTURES / f"fp_{name}_week05_trimmed.json").read_text()) for name in ("dl", "lb", "db", "dst")
}
ARCHIVE_DL = json.loads((FIXTURES / "fp_dl_week04_archive_trimmed.json").read_text())
SLEEPER = json.loads((FIXTURES / "fp_sleeper_players.json").read_text())["players"]
IDP = {pid: p for pid, p in SLEEPER.items() if slot_of(p)}
NICKNAMES = json.loads((ROOT / "config" / "aliases" / "nicknames.json").read_text())
WEEK5_TS = "2026-10-11T16:59:42+00:00"
WATT, ROUSSEAU = "4070", "7627"  # LB and DE by Sleeper position; both in the DL and LB lists


def page(ecr, script_ws=" ") -> bytes:
    """An HTML page embedding the fixture the way FantasyPros does, followed by another script variable."""
    return (
        f"<html><script>\n    var ecrData{script_ws}={script_ws}{json.dumps(ecr)};\n"
        f'    var sosData = {{"ARI": {{"qb_stars": 4.98}}}};\n</script></html>'
    ).encode()


class FakeResponse:
    def __init__(self, status=200, body=b"", error=None, challenged=False):
        self.status, self.body, self.error, self.challenged = status, body, error, challenged

    @property
    def ok(self):
        return self.status is not None and 200 <= self.status < 300


class FakeClient:
    """Serves ranking pages by file name ('dl', 'dst', ...); `pages` overrides with an ecrData dict or a response."""

    def __init__(self, pages=None):
        self.pages = {name: ecr for name, ecr in ECR.items()} | (pages or {})
        self.urls = []

    def get(self, url, headers=None):
        self.urls.append(url)
        name = url.rsplit("/", 1)[1].split(".php")[0]
        got = self.pages.get(name)
        if got is None:
            return FakeResponse(404, error="HTTP 404")
        return got if isinstance(got, FakeResponse) else FakeResponse(body=page(got))


class FixedMatcher:
    """The real matcher, except for the FantasyPros ids pinned in `fixed`."""

    def __init__(self, fixed):
        self.real, self.fixed = Matcher(SLEEPER, {}, NICKNAMES), fixed
        self.players = self.real.players

    def match(self, source, **kw):
        return self.fixed.get(kw["source_id"]) or self.real.match(source, **kw)


def ctx(client=None, matcher=None, week=5, archive=False, cfg=None):
    matcher = matcher or Matcher(SLEEPER, {}, NICKNAMES)
    return FetchContext(client or FakeClient(), None, 2026, week, NOW, IDP, cfg or CFG, matcher, None, archive)


def pid_of(name):
    """The Sleeper id of a fixture IDP by name ('Will Anderson Jr.' is 'Will Anderson' in Sleeper)."""
    return next(pid for pid, p in IDP.items() if normalize_name(p["full_name"]) == normalize_name(name))


def with_players(ecr, players):
    return {**ecr, "players": players}


# --- parsing -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("ws", [" ", "", "\n\t  "])
def test_parse_ecr_is_robust_to_whitespace_and_stops_at_the_object(ws):
    ecr = fantasypros.parse_ecr(page(ECR["dl"], ws).decode())
    assert ecr["position_id"] == "DL" and len(ecr["players"]) == len(ECR["dl"]["players"])


def test_parse_ecr_ignores_braces_inside_strings():
    ecr = with_players(ECR["dl"], [{**ECR["dl"]["players"][0], "note": "a }; tricky {note"}])
    assert fantasypros.parse_ecr(page(ecr).decode())["players"][0]["note"] == "a }; tricky {note"


@pytest.mark.parametrize(
    "text", ["<html>no rankings</html>", "var ecrData = {broken", "var ecrData = [];", 'var ecrData = {"a": 1};']
)
def test_parse_ecr_rejects_missing_or_malformed(text):
    assert fantasypros.parse_ecr(text) is None


# --- the IDP lists -------------------------------------------------------------------------------------------------


def test_fetch_reads_three_lists_and_matches_every_teamed_player():
    client = FakeClient()
    r = fantasypros.fetch(ctx(client))
    assert r.status == OK and r.kind == "rank" and r.week == 5 and r.error is None
    assert client.urls == [f"https://www.fantasypros.com/nfl/rankings/{p}.php" for p in ("dl", "lb", "db")]
    assert r.published_at == WEEK5_TS
    match = r.report["match"]
    assert match["teamed_match_rate"] == 1.0 and match["unmatched"] == [] and match["conflicts"] == []
    assert match["by_method"]["no_team"] == 1  # Chris Johnson (FA) is counted, never a row
    assert r.report["unresolved_teams"] == {}
    for name in ("DL", "LB", "DB"):
        listed = ECR[name.lower()]["players"]
        rep = r.report["lists"][name]
        assert rep == {"status": OK, "players": len(listed), "experts": 26, "week": 5,
                       "last_updated": WEEK5_TS, "error": None}  # fmt: skip
        members = [pid for pid, row in r.rows.items() if row["list"] == name or name in row.get("other_lists", {})]
        teamed = sum(p["player_team_id"] != "FA" for p in listed)
        assert r.universe[name] == {"complete": False, "depth": teamed, "ok": True} and len(members) == teamed


def test_rows_carry_rank_fields_and_sleeper_team_codes():
    r = fantasypros.fetch(ctx())
    anderson = r.rows[pid_of("Will Anderson Jr.")]
    assert anderson == {
        "rank": 1.0, "list": "DL", "fp_id": "24717", "name": "Will Anderson Jr.", "team": "HOU", "pos": "DE",
        "rank_ave": 1.33, "rank_std": 0.55, "rank_min": 1.0, "rank_max": 3.0, "experts": 26,
    }  # fmt: skip
    jac = [p for p in ECR["dl"]["players"] if p["player_team_id"] == "JAC"]
    assert jac and all(r.rows[pid_of(p["player_name"])]["team"] == "JAX" for p in jac)
    assert all("list_published_at" not in row for row in r.rows.values())  # archive only


def test_multi_list_player_keeps_his_d14_slot_row_and_the_other_ranks():
    r = fantasypros.fetch(ctx())
    watt, rousseau = r.rows[WATT], r.rows[ROUSSEAU]
    assert (watt["list"], watt["rank"], watt["other_lists"]) == ("LB", 39.0, {"DL": 5.0})
    assert (rousseau["list"], rousseau["rank"], rousseau["other_lists"]) == ("DL", 9.0, {"LB": 59.0})
    assert r.report["multi_list"] == sum("other_lists" in row for row in r.rows.values()) == 12
    assert "other_lists" not in r.rows[pid_of("Will Anderson Jr.")]


def test_multi_list_player_outside_his_slot_list_falls_back_to_dl_lb_db_order():
    """Watt (LB) listed only in DL and DB: his row comes from DL, the first list in DL, LB, DB order."""
    dl = next(p for p in ECR["dl"]["players"] if p["player_name"] == "T.J. Watt")
    db = with_players(ECR["db"], [{**dl, "rank_ecr": 3}])
    lb = with_players(ECR["lb"], [p for p in ECR["lb"]["players"] if p["player_name"] != "T.J. Watt"])
    r = fantasypros.fetch(ctx(FakeClient({"lb": lb, "db": db})))
    assert (r.rows[WATT]["list"], r.rows[WATT]["rank"], r.rows[WATT]["other_lists"]) == ("DL", 5.0, {"DB": 3.0})


def test_failed_list_fails_alone():
    r = fantasypros.fetch(ctx(FakeClient({"lb": FakeResponse(500, error="HTTP 500")})))
    assert r.status == OK and r.error == "LB: failed (HTTP 500)"
    assert r.report["lists"]["LB"]["status"] == "failed" and r.report["lists"]["DL"]["status"] == OK
    assert r.universe["LB"] == {"complete": False, "depth": None, "ok": False}
    assert {row["list"] for row in r.rows.values()} == {"DL", "DB"}
    assert r.rows[WATT]["list"] == "DL" and "other_lists" not in r.rows[WATT]


@pytest.mark.parametrize(
    "resp",
    [FakeResponse(403, body=b"<title>Just a moment...</title>", challenged=True),
     FakeResponse(None, error="blocked: challenge earlier this run")],
)  # fmt: skip
def test_challenge_is_blocked(resp):
    r = fantasypros.fetch(ctx(FakeClient({"dl": resp, "lb": resp, "db": resp})))
    assert r.status == "blocked" and not r.rows
    assert all(rep["status"] == "blocked" for rep in r.report["lists"].values())
    assert r.published_at is None and not any(u["ok"] for u in r.universe.values())


def test_other_week_or_year_is_not_yet_published():
    r = fantasypros.fetch(ctx(week=6))
    assert r.status == "not_yet_published" and r.week == 5 and not r.rows
    assert r.report["lists"]["DL"]["error"] == "page shows 2026 week 5"
    stale_year = {name: {**ECR[name], "year": "2025"} for name in ("dl", "lb", "db")}
    assert fantasypros.fetch(ctx(FakeClient(stale_year))).status == "not_yet_published"


def test_parse_errors_and_the_most_informative_failure():
    no_ecr = FakeResponse(body=b"<html>redesigned</html>")
    r = fantasypros.fetch(ctx(FakeClient({"dl": no_ecr, "lb": {**ECR["lb"], "week": "6"}, "db": FakeResponse(502)})))
    assert r.status == "parse_error"
    assert [r.report["lists"][n]["status"] for n in ("DL", "LB", "DB")] == [
        "parse_error",
        "not_yet_published",
        "failed",
    ]
    assert r.report["lists"]["DL"]["error"] == "no ecrData" and r.report["lists"]["DB"]["error"] == "HTTP 502"
    wrong = fantasypros.fetch(ctx(FakeClient({"dl": ECR["lb"], "lb": FakeResponse(502), "db": FakeResponse(502)})))
    assert wrong.status == "parse_error" and wrong.report["lists"]["DL"]["error"] == "position_id 'LB', expected DL"
    assert fantasypros.overall({"a": fantasypros.Page("failed"), "b": fantasypros.Page("not_yet_published")}) == (
        "not_yet_published"
    )


def test_published_at_is_the_earliest_ok_list():
    lb = {**ECR["lb"], "last_updated_ts": 1791700000}
    r = fantasypros.fetch(ctx(FakeClient({"lb": lb, "db": {**ECR["db"], "last_updated_ts": None}})))
    assert r.published_at == "2026-10-11T06:26:40+00:00" and r.report["lists"]["DB"]["last_updated"] is None


def test_archive_reads_week_n_and_stamps_list_published_at():
    client = FakeClient({"dl": ARCHIVE_DL, "lb": None, "db": None})
    r = fantasypros.fetch(ctx(client, week=4, archive=True))
    assert client.urls[0] == "https://www.fantasypros.com/nfl/rankings/dl.php?week=4"
    assert r.status == OK and r.week == 4 and r.report["archive"] is True
    assert r.published_at == "2026-10-04T16:59:38+00:00" and r.report["lists"]["DL"]["experts"] == 24
    assert r.rows and all(row["list_published_at"] == r.published_at for row in r.rows.values())
    assert {row["list"] for row in r.rows.values()} == {"DL"}


def test_conflict_keeps_the_higher_precedence_match():
    """Two DL rows land on Will Anderson: the override wins over a better-ranked id match; the other is reported."""
    anderson = pid_of("Will Anderson Jr.")
    hutch = next(p for p in ECR["dl"]["players"] if p["player_name"] == "Aidan Hutchinson")
    matcher = FixedMatcher(
        {"24717": Match(anderson, "source_id"), str(hutch["player_id"]): Match(anderson, "override")}
    )
    r = fantasypros.fetch(ctx(matcher=matcher))
    assert r.rows[anderson]["name"] == "Aidan Hutchinson" and r.rows[anderson]["rank"] == float(hutch["rank_ecr"])
    (conflict,) = r.report["match"]["conflicts"]
    assert conflict["pid"] == anderson and "Will Anderson Jr." in conflict["dropped"]
    same = FixedMatcher({"24717": Match(anderson, "name_team"), str(hutch["player_id"]): Match(anderson, "name_team")})
    assert fantasypros.fetch(ctx(matcher=same)).rows[anderson]["name"] == "Will Anderson Jr."  # tie: better rank


def test_unknown_team_code_is_reported_and_missing_alias_table_fails(tmp_path):
    dl = with_players(ECR["dl"], [{**ECR["dl"]["players"][0], "player_team_id": "XYZ"}])
    r = fantasypros.fetch(ctx(FakeClient({"dl": dl})))
    assert r.report["unresolved_teams"] == {"XYZ": 1} and pid_of("Will Anderson Jr.") not in r.rows
    missing = fantasypros.fetch(ctx(cfg={"_root": str(tmp_path)}))
    assert missing.status == "failed" and "fantasypros_teams.json" in missing.error


def test_bad_rank_rows_are_skipped():
    dl = with_players(ECR["dl"], [{**ECR["dl"]["players"][0], "rank_ecr": None}, "junk", *ECR["dl"]["players"][1:3]])
    r = fantasypros.fetch(ctx(FakeClient({"dl": dl})))
    assert r.universe["DL"]["depth"] == 2 and pid_of("Will Anderson Jr.") not in r.rows


# --- team aliases and D/ST -----------------------------------------------------------------------------------------


def test_team_alias_table_covers_the_sleeper_codes():
    teams = json.loads((ROOT / "config" / "aliases" / "fantasypros_teams.json").read_text())
    sleeper_codes = {p["team"] for p in SLEEPER.values() if p.get("team")}
    assert sleeper_codes <= set(teams.values()) and len(set(teams.values()) - {None}) == 32
    assert (teams["JAC"], teams["WSH"], teams["LA"], teams["OAK"], teams["FA"]) == ("JAX", "WAS", "LAR", "LV", None)
    codes = {p["player_team_id"] for ecr in ECR.values() for p in ecr["players"]}
    assert codes <= set(teams)


def test_dst_signal_rows_by_sleeper_team():
    client = FakeClient()
    r = fp_dst.fetch(ctx(client))
    assert client.urls == ["https://www.fantasypros.com/nfl/rankings/dst.php"]
    assert r.status == OK and r.kind == "signal" and r.published_at == "2026-10-11T16:59:29+00:00"
    assert len(r.rows) == 30 and "JAX" in r.rows and "JAC" not in r.rows
    assert r.rows["HOU"] == {"rank": 1.0, "experts": 42, "name": "Houston Texans"}
    assert r.universe == {"DST": {"complete": False, "depth": 30, "ok": True}}
    assert r.report["lists"]["DST"]["players"] == 30 and r.report["unresolved_teams"] == {}


def test_dst_failures():
    assert fp_dst.fetch(ctx(week=6)).status == "not_yet_published"
    blocked = fp_dst.fetch(ctx(FakeClient({"dst": FakeResponse(403, challenged=True)})))
    assert blocked.status == "blocked" and not blocked.rows and blocked.error == "DST: blocked (bot challenge)"
    assert fp_dst.fetch(ctx(FakeClient({"dst": ECR["dl"]}))).status == "parse_error"
    archive = FakeClient()
    fp_dst.fetch(ctx(archive, archive=True))
    assert archive.urls == ["https://www.fantasypros.com/nfl/rankings/dst.php?week=5"]
