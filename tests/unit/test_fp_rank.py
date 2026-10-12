"""PR 4: the FantasyPros ECR and D/ST adapters, the crosswalk, and FP in a sync (SPEC §5.2, §6.1, §6.4)."""

import json
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import pytest
from jinja2 import Environment, PackageLoader
from test_multisource import Client, _season, cards, multi_routes, parts
from test_sync_site import CFG, NOW, SCHED

from sadlidp import crosswalk, storage
from sadlidp.matching import Matcher
from sadlidp.net import Response
from sadlidp.positions import slot_of
from sadlidp.site.build import build_site
from sadlidp.sleeper import API, V1
from sadlidp.sources import fp_dst, fp_ecr
from sadlidp.sources.base import OK, FetchContext
from sadlidp.stats import evaluate
from sadlidp.sync import run_sync

ROOT_CFG = {"_root": CFG["_root"]}
TS = 1791737981  # 2026-10-11 16:59:41 UTC


def page(week: int, pos: str, players: list[dict], year: int = 2026) -> str:
    data = {"sport": "NFL", "year": str(year), "week": str(week), "position_id": pos, "total_experts": 25,
            "last_updated_ts": TS, "count": len(players), "players": players}  # fmt: skip
    return f"<html><script>\nvar ecrData = {json.dumps(data)};\nvar other = {{}};\n</script></html>"


def entry(fp_id, name, team, pos, rank, opp="vs. NYG"):
    return {"player_id": fp_id, "player_name": name, "player_team_id": team, "player_position_id": pos,
            "rank_ecr": rank, "rank_ave": f"{rank + 0.4:.2f}", "rank_std": "1.10", "player_opponent": opp,
            "player_opponent_id": opp.split()[-1]}  # fmt: skip


class FPClient:
    """Serves {page: {week: html | status}} for the FantasyPros ranking pages."""

    def __init__(self, pages: dict):
        self.pages, self.calls, self.request_count = pages, [], 0

    def get(self, url, **kw):
        self.calls.append(url)
        self.request_count += 1
        parts_ = urlsplit(url)
        name = parts_.path.rsplit("/", 1)[-1].removesuffix(".php")
        week = int(parse_qs(parts_.query)["week"][0])
        body = self.pages.get(name, {}).get(week, 404)
        if isinstance(body, int):
            return Response(url=url, final_url=url, status=body)
        return Response(url=url, final_url=url, status=200, body=body.encode())


PLAYERS = {
    "100": {"full_name": "Fred Warner", "position": "LB", "fantasy_positions": ["LB"], "team": "SF", "active": True},
    "101": {"full_name": "Travon Walker", "position": "DE", "fantasy_positions": ["DL"], "team": "JAX", "active": True},
    "102": {"full_name": "Kyle Hamilton", "position": "S", "fantasy_positions": ["DB"], "team": "BAL", "active": True},
    "103": {"full_name": "Hy Brid", "position": "LB", "fantasy_positions": ["DL", "LB"], "team": "NYJ", "active": True},
    "900": {"full_name": "Not Defense", "position": "WR", "fantasy_positions": ["WR"], "team": "SF", "active": True},
}
XWALK = {"fantasypros": {"17526": "100", "555": "900"}}  # 555 lands on a non-IDP: ignored (§6.1 guard)


def ctx(client, week=5, players=PLAYERS, xwalk=XWALK, archive=False):
    matcher = Matcher(players, crosswalk=xwalk)
    now = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
    return FetchContext(client, None, 2026, week, now, players, dict(ROOT_CFG), matcher, None, archive)


def lists(week=5):
    dl = [entry(1, "Travon Walker", "JAC", "DE", 1), entry(2, "Hy Brid", "NYJ", "LB", 7)]
    lb = [entry(17526, "F. Warner (FP spelling)", "SF", "LB", 1), entry(2, "Hy Brid", "NYJ", "LB", 4),
          entry(3, "Nobody Here", "DAL", "LB", 5)]  # fmt: skip
    db = [entry(4, "Kyle Hamilton", "BAL", "S", 2), entry(555, "Free Agent", "FA", "S", 3)]
    return {name: {week: page(week, name.upper(), rows)} for name, rows in (("dl", dl), ("lb", lb), ("db", db))}


def test_parse_ecr_reads_the_embedded_object():
    data = fp_ecr.parse_ecr(page(5, "LB", [entry(1, "A", "SF", "LB", 1)]))
    assert data["week"] == "5" and data["players"][0]["rank_ecr"] == 1
    assert fp_ecr.parse_ecr("<html>no rankings here</html>") is None
    assert fp_ecr.parse_ecr("var ecrData = {broken") is None


def test_fetch_matches_every_list_and_keeps_both_ranks_of_a_two_list_player():
    client = FPClient(lists())
    res = fp_ecr.fetch(ctx(client))
    assert res.status == OK and res.kind == "rank" and res.week == 5
    assert [c.split("rankings/")[1] for c in client.calls] == ["dl.php?week=5", "lb.php?week=5", "db.php?week=5"]
    assert res.rows["100"]["ranks"] == {"LB": 1}  # by the crosswalk id, whatever the name says
    assert res.rows["101"]["ranks"] == {"DL": 1} and res.rows["101"]["team"] == "JAX"  # JAC -> JAX
    assert res.rows["103"]["ranks"] == {"DL": 7, "LB": 4}
    assert res.rows["102"]["ranks"] == {"DB": 2} and res.rows["102"]["ave"] == {"DB": 2.4}
    assert res.lists == {"DL": {"101": 1, "103": 7}, "LB": {"100": 1, "103": 4}, "DB": {"102": 2}}
    assert res.universe == {s: {"complete": False, "depth": d} for s, d in (("DL", 2), ("LB", 3), ("DB", 2))}
    assert res.published_at == datetime.fromtimestamp(TS, UTC).isoformat()
    match = res.report["match"]
    assert match["by_method"] == {"source_id": 1, "name_team_slot": 4, "unmatched": 1, "no_team": 1}
    assert match["unmatched"] == [{"name": "Nobody Here", "team": "DAL", "slot": "LB", "how": "unmatched"}]


def test_a_list_not_posted_yet_is_reported_and_the_others_still_count():
    pages = lists()
    pages["db"] = {5: page(6, "DB", [])}  # FP shows another week (or an empty list): not this week's
    res = fp_ecr.fetch(ctx(FPClient(pages)))
    assert res.status == OK and set(res.universe) == {"DL", "LB"} and "102" not in res.rows
    assert res.report["lists"]["DB"]["status"] == "not_yet_published"
    empty = {name: {5: page(5, name.upper(), [])} for name in ("dl", "lb", "db")}
    res = fp_ecr.fetch(ctx(FPClient(empty)))
    assert res.status == "not_yet_published" and not res.rows and res.week == 5


def test_a_challenge_blocks_the_source_and_stops_reading():
    client = FPClient({"dl": {5: 403}})
    res = fp_ecr.fetch(ctx(client))
    assert res.status == "blocked" and len(client.calls) == 1 and "HTTP 403" in res.error
    res = fp_ecr.fetch(ctx(FPClient({"dl": {5: "<html>maintenance</html>"}})))
    assert res.status == "parse_error"


def test_dst_ranks_are_a_signal_keyed_by_team():
    dst = [entry(8120, "Jacksonville Jaguars", "JAC", "DST", 1, "at HOU"), entry(8170, "Vikings", "MIN", "DST", 2)]
    res = fp_dst.fetch(ctx(FPClient({"dst": {5: page(5, "DST", dst)}})))
    assert res.status == OK and res.kind == "signal"
    assert res.rows == {"JAX": {"rank": 1, "opponent": "HOU", "home": False},
                        "MIN": {"rank": 2, "opponent": "NYG", "home": True}}  # fmt: skip
    assert fp_dst.fetch(ctx(FPClient({}))).status == "failed"


def test_crosswalk_parse_drops_missing_and_clashing_ids(tmp_path):
    text = "fantasypros_id,sleeper_id,name\n17526,5041,Fred Warner\nNA,1,X\n2,NA,Y\n9,10,A\n9,11,B\n,12,C\n"
    assert crosswalk.parse(text) == {"fantasypros": {"17526": "5041"}}

    class Once:
        def __init__(self, status, body=b""):
            self.status, self.body, self.calls = status, body, 0

        def get(self, url, **kw):
            self.calls += 1
            return Response(url=url, final_url=url, status=self.status, body=self.body)

    ok = Once(200, text.encode())
    assert crosswalk.load(ok, tmp_path, "2026-10-12", log=lambda m: None) == {"fantasypros": {"17526": "5041"}}
    assert crosswalk.load(ok, tmp_path, "2026-10-12", log=lambda m: None)["fantasypros"] and ok.calls == 1  # cached
    assert crosswalk.load(Once(500), tmp_path / "other", "2026-10-12", log=lambda m: None) == {}


# ---- FantasyPros in a sync -------------------------------------------------------------------------------


def fp_world(week=5):
    """FP pages built from the sync fixture: each slot's projected players, ranked in reverse of Sleeper's
    tackles, so FP disagrees with the projections, plus a D/ST page; every week served under its own number."""
    r = multi_routes()
    players, proj = r[f"{V1}/players/nfl"], r[f"{API}/projections/nfl/2026/5"]
    by_slot: dict[str, list] = {}
    for row in proj:
        p = players.get(row["player_id"]) or {}
        if slot_of(p) and row.get("opponent") and (row.get("stats") or {}).get("gp"):
            by_slot.setdefault(slot_of(p), []).append((row["stats"].get("idp_tkl", 0), row["player_id"], p))
    pages = {}
    for slot, rows in by_slot.items():
        rows.sort(key=lambda x: (x[0], x[1]))  # fewest tackles first: FP's #1
        ranked = [entry(1000 + i, p.get("full_name"), p.get("team"), p.get("position"), i)
                  for i, (_t, _pid, p) in enumerate(rows[:12], start=1)]  # fmt: skip
        pages[slot.lower()] = {w: page(w, slot, ranked) for w in range(1, 6)}
    pages["dst"] = {w: page(w, "DST", [entry(8000, "Jets", "NYJ", "DST", 1), entry(8001, "Jags", "JAC", "DST", 2)])
                    for w in range(1, 6)}  # fmt: skip
    return r, pages


class SyncClient(Client):
    """test_multisource's client plus the FantasyPros pages."""

    def __init__(self, routes, pages, espn=None):
        super().__init__(routes, espn)
        self.fp = FPClient(pages)

    def get(self, url, headers=None, **kw):
        if url.startswith("https://www.fantasypros.com/"):
            self.calls.append(url)
            self.request_count += 1
            return self.fp.get(url)
        return super().get(url, headers=headers, **kw)


def test_fp_ranks_join_the_consensus_at_the_rank_groups_share(tmp_path):
    data = tmp_path / "d"
    routes, pages = fp_world()
    out = run_sync(CFG, data, tmp_path / "c", NOW, client=SyncClient(routes, pages), log=lambda m: None)
    assert out.synced and not [w for w in out.warnings if w.startswith("fp_")]
    week = data / "2026" / "week05"
    run = json.loads((week / "run.json").read_text())
    fp = run["sources"]["fp_ecr"]
    assert (fp["status"], fp["label"], fp["kind"]) == ("ok", "FantasyPros", "rank") and fp["mapped"] >= 30
    assert fp["report"]["match"]["teamed_match_rate"] == 1.0
    saved = storage.load_source(week / "sources" / "fp_ecr.json")
    assert saved["kind"] == "rank" and set(saved["lists"]) == {"DL", "LB", "DB"}

    d = json.loads((week / "consensus.json").read_text())
    three = [c for c in cards(d) if c["n"] == 3]
    assert three
    for c in three:
        by = parts(c)
        assert by["FantasyPros"]["share"] == 0.4 and by["Sleeper"]["share"] == by["ESPN"]["share"] == 0.3
        assert by["FantasyPros"]["list"] == c["slot"] and by["FantasyPros"]["rank"] >= 1
        assert abs(c["mu"] - sum(p["pts"] * p["share"] for p in by.values() if "pts" in p)) < 0.02
    # FP reverses Sleeper's order, so its #1 at a position maps to the best projection on that list
    fp_rows = storage.load_source(week / "sources" / "fp_ecr.json")
    frozen = json.loads((week / "frozen.json").read_text())["players"]

    def p_of(pid):
        proj = [v for k, v in frozen[pid]["parts"].items() if k != "fp_ecr"]
        return sum(proj) / len(proj) if proj else None

    for slot in ("DL", "LB", "DB"):
        first = next(pid for pid, r in fp_rows["rows"].items() if r["ranks"].get(slot) == 1)
        top = max(p for pid in fp_rows["lists"][slot] if pid in frozen and (p := p_of(pid)) is not None)
        assert frozen[first]["parts"]["fp_ecr"] == pytest.approx(top, abs=0.002)

    # the D/ST ranks are stored as a signal and never blended
    dst = storage.load_source(week / "sources" / "fp_dst.json")
    assert dst["kind"] == "signal" and dst["rows"]["JAX"]["rank"] == 2
    assert all("FantasyPros D/ST" not in parts(c) for c in cards(d))
    assert run["sources"]["fp_dst"]["signal"] and run["backfilled"]["fp_dst"] == [1, 2, 3, 4]
    assert run["backfilled"]["fp_ecr"] == [1, 2, 3, 4]

    html = build_site(data, tmp_path / "site", CFG).read_text()
    assert "<td>FantasyPros</td>" in html and "rank #" in html


def test_a_list_that_fails_keeps_its_stored_ranks(tmp_path):
    data = tmp_path / "d"
    routes, pages = fp_world()
    run_sync(CFG, data, tmp_path / "c", NOW, client=SyncClient(routes, pages), log=lambda m: None)
    path = data / "2026" / "week05" / "sources" / "fp_ecr.json"
    before = storage.load_source(path)
    db_rows = {pid for pid, r in before["rows"].items() if "DB" in r["ranks"]}
    assert db_rows

    pages["db"] = {5: 503}
    pages["lb"] = {5: page(5, "LB", [])}  # and LB not posted at the moment
    later = NOW.replace(hour=23)
    out = run_sync(CFG, data, tmp_path / "c", later, force=True, client=SyncClient(routes, pages), log=lambda m: None)
    assert out.synced
    after = storage.load_source(path)
    assert {pid for pid, r in after["rows"].items() if "DB" in r["ranks"]} == db_rows  # DB kept, not deleted
    assert set(after["universe"]) == {"DL", "LB", "DB"} and set(after["lists"]) == {"DL", "LB", "DB"}
    run = json.loads((data / "2026" / "week05" / "run.json").read_text())
    lists_ = run["sources"]["fp_ecr"]["report"]["lists"]
    assert (lists_["DB"]["status"], lists_["LB"]["status"], lists_["DL"]["status"]) == (
        "blocked", "not_yet_published", "ok")  # fmt: skip


def test_stats_score_fp_on_its_mapped_points(tmp_path):
    """FP ranks the 60 LBs in reverse of Sleeper: rank r maps to the r-th best Sleeper value."""
    data, players = _season(tmp_path, espn=False)
    order = list(players)  # Sleeper's values rise with the index: p59 is the best
    for week in (1, 2, 3):
        wd = storage.week_dir(data, 2026, week)
        rows = {pid: {"ranks": {"LB": i}} for i, pid in enumerate(order[:40], start=1)}  # p00 is FP's #1
        storage.write_json(wd / "sources" / "fp_ecr.json", {"kind": "rank", "rows": rows,
                           "universe": {"LB": {"complete": False, "depth": 40}}})  # fmt: skip
    ev = evaluate(data, 2026, CFG)
    assert list(ev["season_by_source"]) == ["sleeper_proj", "fp_ecr"]
    shares = {x["name"]: (x["share"], x["kind"]) for x in ev["sources"]}
    assert shares == {"Sleeper": (0.6, "projection"), "FantasyPros": (0.4, "rank")}
    sleeper = {pid: 2 * (3 + i * 0.05) for i, pid in enumerate(order)}
    listed = sorted((sleeper[pid] for pid in order[:40]), reverse=True)
    fp = {pid: listed[i] for i, pid in enumerate(order[:40])}
    actual = {pid: 2 * (3 + (i % 7)) for i, pid in enumerate(order) if i != 59}
    pool = sorted((p for p in sleeper if p in actual), key=lambda p: (-sleeper[p], p))[:50]  # Sleeper alone picks it
    covered = [p for p in pool if p in fp]
    w1 = ev["weekly"][0]["by_source"]["fp_ecr"]["LB"]
    assert (w1["covered"], w1["pool"]) == (len(covered), 50)
    assert w1["mae"] == pytest.approx(sum(abs(fp[p] - actual[p]) for p in covered) / len(covered))
    env = Environment(loader=PackageLoader("sadlidp.site", "templates"), autoescape=True)
    html = env.get_template("stats.html.j2").render(s=ev, slots=("DL", "LB", "DB"), league_name="SADL")
    assert "rankings (FantasyPros) 40%" in html and "Sleeper 60%" in html


def test_replay_reads_fp_from_its_saved_file_and_skips_the_signal(tmp_path):
    from sadlidp.replay import replay_week

    data = tmp_path / "d"
    routes, pages = fp_world()
    run_sync(CFG, data, tmp_path / "c", NOW, client=SyncClient(routes, pages), log=lambda m: None)
    players = routes[f"{V1}/players/nfl"]
    out = replay_week(CFG, data, 2026, 4, [], players, log=lambda m: None)
    d = json.loads(out.read_text())
    assert d["sources"]["fp_ecr"]["status"] == "saved" and "fp_dst" not in d["sources"]
    assert any("FantasyPros" in parts(c) and "pts" in parts(c)["FantasyPros"] for c in cards(d))


def test_sched_fixture_has_the_dst_teams_playing():
    assert SCHED.game_for("NYJ", 5) is not None
