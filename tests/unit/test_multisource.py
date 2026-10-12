"""PR 3 end to end, offline: Sleeper and ESPN in one sync, the observation rule, the backfill runner, replays
from saved sources and per-source stats (SPEC §5.1–§5.3, §6.4, §6.8)."""

import copy
import json
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import pytest
from jinja2 import Environment, PackageLoader
from test_sync_site import CFG, FIXTURES, NOW, ROOT, SCHED, TNF, routes

from sadlidp import backfill, storage
from sadlidp.net import Response
from sadlidp.positions import slot_of
from sadlidp.replay import replay_week
from sadlidp.site.build import build_site
from sadlidp.sleeper import API, V1
from sadlidp.sources import espn_proj
from sadlidp.stats import evaluate
from sadlidp.sync import SOURCES, build_matcher, run_sync, zero_fill

ESPN = json.loads((FIXTURES / "espn_week06_trimmed.json").read_text())["players"]
ESPN_PLAYERS = json.loads((FIXTURES / "espn_sleeper_players.json").read_text())["players"]
LEAGUE_URL = espn_proj.URL.format(season=2026, league=CFG["espn"]["league_id"])
ESPN_HOST = "lm-api-reads.fantasy.espn.com"
TYKEE = "11705"  # my DB (TB): projected by both sources; TB @ DAL is the Thursday game


def espn_week(week: int) -> list[dict]:
    """The ESPN fixture with its week-6 projection lines relabelled as `week` (actual lines as the week before)."""
    players = copy.deepcopy(ESPN)
    for e in players:
        for line in e["player"]["stats"]:
            line["scoringPeriodId"] = week if line["statSourceId"] == 1 else week - 1
    return players


class Client:
    """test_sync_site's exact-URL routes, plus the ESPN league paged by the X-Fantasy-Filter offset for the
    requested scoringPeriodId. `espn` = an HTTP status serves that instead. Counts requests like net.Client."""

    def __init__(self, routes: dict, espn: int | None = None):
        self.routes, self.espn, self.calls, self.request_count = routes, espn, [], 0

    def get(self, url, headers=None, **kw):
        self.calls.append(url)
        self.request_count += 1
        base = url.split("?")[0]
        if base == LEAGUE_URL:
            if self.espn:
                return Response(url=url, final_url=url, status=self.espn)
            flt = json.loads(headers["X-Fantasy-Filter"])["players"]
            week = int(parse_qs(urlsplit(url).query)["scoringPeriodId"][0])
            page = espn_week(week)[flt["offset"] : flt["offset"] + flt["limit"]]
            return Response(url=url, final_url=url, status=200, body=json.dumps({"players": page}).encode())
        body = self.routes.get(base, 404)
        if isinstance(body, int):
            return Response(url=url, final_url=url, status=body)
        raw = body if isinstance(body, str) else json.dumps(body)
        return Response(url=url, final_url=url, status=200, body=raw.encode())


def multi_routes() -> dict:
    """test_sync_site's league plus the ESPN fixture's players, each with a Sleeper week-5 row if his team plays
    (week 4's Sleeper projections are the same rows, for the backfill)."""
    r = routes()
    players, proj = r[f"{V1}/players/nfl"], r[f"{API}/projections/nfl/2026/5"]
    for pid, p in ESPN_PLAYERS.items():
        players[pid] = {"injury_status": None, **p, **players.get(pid, {})}
    # the trimmed ESPN list is this test's ESPN world: one Sleeper player per slot outside it keeps his ESPN
    # id (so ESPN zero-fills him); the rest carry none, as for players ESPN has never listed
    in_espn = {str(e["player"]["id"]) for e in ESPN}
    kept: set[str] = set()
    for pid in sorted(players):
        p, slot = players[pid], slot_of(players[pid])
        if not p.get("espn_id") or str(p["espn_id"]) in in_espn:
            continue
        if slot and slot not in kept and p.get("active") and p.get("team"):
            kept.add(slot)
            continue
        players[pid] = {**p, "espn_id": None}
    have = {x["player_id"] for x in proj}
    template = next(x for x in proj if x["player_id"] == "11017")
    for pid, p in ESPN_PLAYERS.items():
        opp = SCHED.opponent(p.get("team"), 5)
        if pid in have or not opp or not p.get("active"):
            continue
        row = copy.deepcopy(template)
        row.update(player_id=pid, team=p["team"], opponent=opp.lstrip("@"))
        row["stats"] = {"gp": 1.0, "idp_tkl_solo": 3.0, "idp_tkl_ast": 2.0, "idp_tkl": 5.0, "idp_sack": 0.25}
        proj.append(row)
    r[f"{API}/projections/nfl/2026/4"] = [{**x, "week": 4} for x in proj]
    return r


def cards(obj):
    """Every player card (with a breakdown) in the site data."""
    if isinstance(obj, dict):
        if "id" in obj and "breakdown" in obj:
            yield obj
        for v in obj.values():
            yield from cards(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from cards(v)


def parts(card) -> dict[str, dict]:
    return {p["source"]: p for p in card["breakdown"]["parts"]}


def test_two_sources_blend_equally_and_espn_estimates_are_marked(tmp_path):
    data = tmp_path / "d"
    out = run_sync(CFG, data, tmp_path / "c", NOW, client=Client(multi_routes()), log=lambda m: None)
    assert out.synced and not [w for w in out.warnings if w.startswith(("espn_proj", "sleeper_proj"))]
    week = data / "2026" / "week05"
    run = json.loads((week / "run.json").read_text())
    espn = run["sources"]["espn_proj"]
    assert (espn["status"], espn["label"], espn["rows"], espn["published_at"]) == ("ok", "ESPN", 44, None)
    assert espn["report"]["complete"] and espn["report"]["match"]["teamed_match_rate"] > 0.98
    assert espn["zero_filled"] > 0 and espn["observed_after"] == NOW.isoformat()
    assert run["sources"]["sleeper_proj"]["status"] == "ok"
    assert run["backfilled"] == {"sleeper_proj": [4], "espn_proj": [1, 2, 3, 4]}  # after the live fetches

    d = json.loads((week / "consensus.json").read_text())
    tykee = next(p for p in d["my_idps"]["DB"]["players"] if p["id"] == TYKEE)
    assert tykee["n"] == 2 and tykee["sigma"] is not None
    frozen = json.loads((week / "frozen.json").read_text())["players"][TYKEE]
    assert frozen["eff"] == {"sleeper_proj": 0.5, "espn_proj": 0.5} and frozen["n_sources"] == 2
    by = parts(tykee)
    assert [p["source"] for p in tykee["breakdown"]["parts"]] == ["Sleeper", "ESPN", "FantasyPros"]
    assert by["FantasyPros"]["missing"] == "couldn't be read this sync"  # not served offline
    assert by["Sleeper"]["share"] == by["ESPN"]["share"] == 0.5
    assert tykee["mu"] == round((by["Sleeper"]["pts"] + by["ESPN"]["pts"]) / 2, 2)
    espn_stats = {label: est for label, _, est in by["ESPN"]["stats"]}
    assert espn_stats["TFL"] is True and espn_stats["solo"] is False and tykee["breakdown"]["est"]
    assert not any(est for _, _, est in by["Sleeper"]["stats"])  # Sleeper projects every key itself
    zeros = [c for c in cards(d) if parts(c).get("ESPN", {}).get("zero")]
    assert zeros and all(c["n"] == 2 for c in zeros)  # observed, listed for zero-fill, absent: ESPN says 0

    footer = d["sources"]["espn_proj"]
    assert "report" not in footer and footer["match"]["unmatched"] == 1
    assert footer["match"]["names"] == ["Jeremiah Moon CAR DL"]
    src = storage.load_source(week / "sources" / "espn_proj.json")
    assert src["rows"][TYKEE]["imputed"] == ["idp_def_td", "idp_qb_hit", "idp_safe", "idp_tkl_loss"]
    old = storage.load_source(data / "2026" / "week02" / "sources" / "espn_proj.json")
    assert old["week"] == 2 and old["rows"] and all(r["backfill"] for r in old["rows"].values())

    html = build_site(data, tmp_path / "site", CFG).read_text()
    assert "* = our estimate" in html and "TFL*" in html and "<td>ESPN</td>" in html
    assert "98.2%" in html and "1 unmatched" in html and "Jeremiah Moon CAR DL" in html


def test_first_espn_fetch_after_thursday_kickoff_observes_only_later_games(tmp_path):
    data = tmp_path / "d"
    run_sync(CFG, data, tmp_path / "c", TNF, force=True, client=Client(multi_routes()), log=lambda m: None)
    week = data / "2026" / "week05"
    src = storage.load_source(week / "sources" / "espn_proj.json")
    players = multi_routes()[f"{V1}/players/nfl"]
    assert TYKEE not in src["rows"] and not {p for p in src["rows"] if players[p].get("team") in ("TB", "DAL")}
    run = json.loads((week / "run.json").read_text())
    assert run["sources"]["espn_proj"]["merge"]["skipped_after_kickoff"] >= 1  # ESPN rows aren't late rows
    d = json.loads((week / "consensus.json").read_text())
    tykee = next(p for p in d["my_idps"]["DB"]["players"] if p["id"] == TYKEE)
    assert tykee["n"] == 1 and parts(tykee)["Sleeper"]["late"]  # Sleeper keeps late rows; ESPN doesn't
    assert parts(tykee)["ESPN"]["missing"] == "first read this week after his game started"  # not zero-filled


def test_zero_fill_only_for_observed_players_in_the_listed_universe():
    kick = {"tb": TNF.replace(hour=0), "sun": datetime(2026, 10, 11, 17, 0, tzinfo=UTC)}
    kick["other"] = kick["sun"]

    class Status:
        def __init__(self, pid, now):
            self.kickoff, self.locked = kick[pid], kick[pid] <= now

    idp = {pid: {"position": "LB", "active": True} for pid in kick}
    universe = {"LB": {"complete": True, "depth": None, "zero_fill_ids": ["tb", "sun"]}}
    after_tnf = {"fetches": [TNF.isoformat()], "universe": universe}
    filled = zero_fill(after_tnf, {}, idp, lambda pid: Status(pid, TNF))
    assert filled == {"sun": 0.0}  # tb kicked off before the first fetch; "other" isn't in zero_fill_ids
    tuesday = {**after_tnf, "fetches": [NOW.isoformat(), TNF.isoformat()]}
    assert zero_fill(tuesday, {}, idp, lambda pid: Status(pid, TNF)) == {"tb": 0.0, "sun": 0.0}  # observed Tuesday
    late_post = zero_fill(tuesday, {}, idp, lambda pid: Status(pid, TNF), published_at=TNF.isoformat())
    assert late_post == {"sun": 0.0}  # observed only after the source says it posted the week
    no_list = {"fetches": [NOW.isoformat()], "universe": {"LB": {"complete": True, "depth": None}}}
    assert zero_fill(no_list, {}, idp, lambda pid: Status(pid, NOW)) == dict.fromkeys(kick, 0.0)


def test_backfill_runner_writes_espn_weeks_within_its_budgets(tmp_path):
    db = multi_routes()[f"{V1}/players/nfl"]
    players, matcher = {pid: p for pid, p in db.items() if slot_of(p)}, build_matcher(CFG, db)
    for w in (1, 2):  # imputation history: weeks before the backfilled week only
        stats = {TYKEE: {"gp": 1, "idp_tkl_solo": 4 + w, "idp_tkl_loss": w}, "x": {"gp": 0}}
        storage.write_json(storage.week_dir(tmp_path / "d", 2026, w) / "actuals.json", {"week": w, "stats": stats})
    assert [line["idp_tkl_loss"] for line in backfill.history(tmp_path / "d", 2026, 3)[TYKEE]] == [1, 2]
    assert list(backfill.history(tmp_path / "d", 2026, 2)) == [TYKEE]  # week 1 only; "x" didn't play

    def run(data, cfg=CFG, client=None, **kw):
        logs = []
        kw = {
            "client": client or Client(multi_routes()),
            "sleeper": None,
            "players": players,
            "matcher": matcher,
            "log": logs.append,
            **kw,
        }
        return backfill.run(SOURCES, cfg, data, 2026, range(1, 5), NOW, only="espn_proj", **kw), logs

    client = Client(multi_routes())
    done, _ = run(tmp_path / "d", client=client)
    assert done == {"espn_proj": [1, 2, 3, 4]} and client.request_count == 4  # one short page per week
    for w in (1, 2, 3, 4):
        src = storage.load_source(storage.week_dir(tmp_path / "d", 2026, w) / "sources" / "espn_proj.json")
        assert src["week"] == w and src["fetches"] == [NOW.isoformat()] and len(src["rows"]) == 44
        assert all(r["backfill"] and r["imputed"] for r in src["rows"].values())
    tfl = [
        storage.load_source(storage.week_dir(tmp_path / "d", 2026, w) / "sources" / "espn_proj.json")["rows"][TYKEE]
        ["stats"]["idp_tkl_loss"] for w in (1, 2, 3, 4)
    ]  # fmt: skip
    assert len({tfl[0], tfl[1], tfl[2]}) == 3 and tfl[2] == tfl[3]  # same ESPN line; history grows until week 3
    assert run(tmp_path / "d", client=client)[0] == {} and client.request_count == 4  # fetched once

    tight = {**CFG, "backfill": {"budget_s": 300, "max_requests": 2}}
    done, logs = run(tmp_path / "d2", tight)
    assert done == {"espn_proj": [1, 2]}
    assert f"backfill: 2 requests to {ESPN_HOST} reached; espn_proj weeks 3, 4 wait for the next sync" in logs
    done, logs = run(tmp_path / "d3", clock=iter([0.0, 0.0, 400.0]).__next__)
    assert done == {"espn_proj": [1]} and "time budget (300 s) reached; espn_proj weeks 2, 3, 4" in logs[-1]

    live = storage.week_dir(tmp_path / "d2", 2026, 2) / "sources" / "espn_proj.json"
    doc = storage.load_source(live)
    storage.write_json(live, {**doc, "rows": {k: {**r, "backfill": False} for k, r in doc["rows"].items()}})
    done, logs = run(tmp_path / "d2", redo=True)
    assert done == {"espn_proj": [1, 3, 4]} and "backfill: espn_proj week 2 has live rows; left alone" in logs
    # a --redo never shrinks a stored week: a short response (e.g. caught mid-update) keeps the rows
    week1 = storage.week_dir(tmp_path / "d", 2026, 1) / "sources" / "espn_proj.json"
    rows = storage.load_source(week1)["rows"]

    class Short(Client):
        def get(self, url, headers=None, **kw):
            resp = super().get(url, headers, **kw)
            if url.split("?")[0] == LEAGUE_URL and resp.ok:
                resp.body = json.dumps({"players": json.loads(resp.body)["players"][:10]}).encode()
            return resp

    done, logs = run(tmp_path / "d", client=Short(multi_routes()), redo=True)
    assert 1 not in done.get("espn_proj", []) and storage.load_source(week1)["rows"] == rows
    assert any("week 1:" in m and "kept the stored rows" in m for m in logs)
    _, logs = run(tmp_path / "d4", {**CFG, "sources": {**CFG["sources"], "espn_proj": {"enabled": True}}})
    assert logs == ["backfill: espn_proj is disabled or has no archive (sources.espn_proj.archive); nothing to do"]


def test_replay_reads_every_saved_source_of_the_week(tmp_path):
    data = tmp_path / "d"
    r = multi_routes()
    run_sync(CFG, data, tmp_path / "c", NOW, client=Client(r), log=lambda m: None)  # backfills week 4 of both
    truth = json.loads((FIXTURES / "sleeper_week04_ground_truth.json").read_text())
    out = replay_week(CFG, data, 2026, 4, truth["matchups"], r[f"{V1}/players/nfl"], log=lambda m: None)
    d = json.loads(out.read_text())
    both = [c for c in cards(d) if c["n"] == 2]
    assert both and all({"Sleeper", "ESPN"} <= {p["source"] for p in c["breakdown"]["parts"] if "pts" in p}
                        for c in both)  # fmt: skip
    assert not [w for w in d["warnings"] if w.startswith(("espn_proj", "sleeper_proj"))]
    assert {s["status"] for s in d["sources"].values()} == {"saved"}  # read from saved files, not "failed"
    # a source with no file that week is left out of the rebuilt page, not shown as missing
    (data / "2026" / "week04" / "sources" / "sleeper_proj.json").unlink()
    d = json.loads(replay_week(CFG, data, 2026, 4, truth["matchups"], r[f"{V1}/players/nfl"], log=lambda m: None)
                   .read_text())  # fmt: skip
    assert {p["source"] for c in cards(d) for p in c["breakdown"]["parts"]} == {"ESPN"}


def test_an_espn_failure_leaves_a_sleeper_only_consensus_with_a_banner(tmp_path):
    data = tmp_path / "d"
    out = run_sync(CFG, data, tmp_path / "c", NOW, client=Client(multi_routes(), espn=503), log=lambda m: None)
    assert "espn_proj: failed (page 1 (offset 0): HTTP 503)" in out.warnings
    d = json.loads((data / "2026" / "week05" / "consensus.json").read_text())
    assert d["sources"]["espn_proj"]["status"] == "failed" and "match" not in d["sources"]["espn_proj"]
    assert all(c["n"] <= 1 for c in cards(d))
    tykee = next(p for p in d["my_idps"]["DB"]["players"] if p["id"] == TYKEE)
    assert parts(tykee)["ESPN"]["missing"] == "couldn't be read this sync" and parts(tykee)["Sleeper"]["pts"]
    html = build_site(data, tmp_path / "site", CFG).read_text()
    assert "espn_proj: failed (page 1 (offset 0): HTTP 503)" in html and "* = our estimate" not in html
    # after a good fetch, a failed one keeps ESPN's earlier rows
    run_sync(CFG, data, tmp_path / "c", NOW, force=True, client=Client(multi_routes()), log=lambda m: None)
    out = run_sync(CFG, data, tmp_path / "c", NOW, force=True, client=Client(multi_routes(), 503), log=lambda m: None)
    assert "espn_proj: failed (page 1 (offset 0): HTTP 503); using earlier rows" in out.warnings
    d = json.loads((data / "2026" / "week05" / "consensus.json").read_text())
    assert next(p for p in d["my_idps"]["DB"]["players"] if p["id"] == TYKEE)["n"] == 2


def _season(tmp_path, espn: bool):
    """Three settled weeks of 60 LBs: Sleeper projects everyone, ESPN (optionally) the even ones, higher."""
    data = tmp_path / ("both" if espn else "sleeper")
    storage.write_json(data / "2026" / "league.json", {"scoring_settings": {"idp_tkl_solo": 2.0, "idp_tkl_ast": 1.0}})
    storage.write_json(
        data / "2026" / "schedule.json", json.loads((ROOT / "data" / "2026" / "schedule.json").read_text())
    )
    players = {f"p{i:02d}": {"position": "LB", "team": "NYJ"} for i in range(60)}
    for week in (1, 2, 3):
        wd = storage.week_dir(data, 2026, week)
        sleeper = {pid: {"stats": {"idp_tkl_solo": 3 + i * 0.05}} for i, pid in enumerate(players)}
        storage.write_json(wd / "sources" / "sleeper_proj.json", {"kind": "projection", "rows": sleeper})
        if espn:
            rows = {
                pid: {"stats": {"idp_tkl_solo": 5.0}, "backfill": True} for i, pid in enumerate(players) if i % 2 == 0
            }
            storage.write_json(wd / "sources" / "espn_proj.json", {"kind": "projection", "rows": rows})
        stats = {pid: {"gp": 1, "idp_tkl_solo": 3 + (i % 7)} for i, pid in enumerate(players) if i != 59}
        storage.write_json(wd / "actuals.json", {"captured_at": "2026-12-01T00:00:00+00:00", "stats": stats})
        storage.write_json(wd / "players.json", {"players": players})
    return data, players


def test_stats_score_each_source_on_the_consensus_pool(tmp_path):
    data, players = _season(tmp_path, espn=True)
    ev = evaluate(data, 2026, CFG)
    assert list(ev["season_by_source"]) == ["sleeper_proj", "espn_proj"]
    assert ev["source_labels"] == {"sleeper_proj": "Sleeper", "espn_proj": "ESPN"}
    assert ev["sources"] == [{"name": "Sleeper", "share": 0.5, "kind": "projection"},
                             {"name": "ESPN", "share": 0.5, "kind": "projection"}]  # fmt: skip
    # the pool is chosen by the equal-weight mean: every even LB averages above every odd one
    sleeper = {pid: 2 * (3 + i * 0.05) for i, pid in enumerate(players)}
    mean = {pid: (v + 10.0) / 2 if int(pid[1:]) % 2 == 0 else v for pid, v in sleeper.items()}
    actual = {pid: 2 * (3 + (i % 7)) for i, pid in enumerate(players) if i != 59}
    pool = sorted((p for p in mean if p in actual), key=lambda p: (-mean[p], p))[:50]
    w1 = ev["weekly"][0]
    assert w1["by_slot"]["LB"]["n"] == 50 and w1["backfill"] == 30
    assert w1["by_slot"]["LB"]["mae"] == sum(abs(mean[p] - actual[p]) for p in pool) / 50
    espn_pool = [p for p in pool if int(p[1:]) % 2 == 0]
    e1 = w1["by_source"]["espn_proj"]["LB"]
    assert (e1["covered"], e1["pool"]) == (len(espn_pool), 50) and len(espn_pool) == 30
    assert e1["mae"] == sum(abs(10.0 - actual[p]) for p in espn_pool) / 30
    assert w1["by_source"]["sleeper_proj"]["LB"]["mae"] == sum(abs(sleeper[p] - actual[p]) for p in pool) / 50
    season = ev["season_by_source"]["espn_proj"]["LB"]
    assert (season["covered"], season["pool"], season["n"]) == (90, 150, 90)
    assert season["bias"] == pytest.approx(sum(10.0 - actual[p] for p in espn_pool) / 30)  # ESPN runs low here

    only, _ = _season(tmp_path, espn=False)  # one source: the consensus is that source, numbers unchanged
    ev1 = evaluate(only, 2026, CFG)
    assert list(ev1["season_by_source"]) == ["sleeper_proj"]
    assert ev1["sources"] == [{"name": "Sleeper", "share": 1.0, "kind": "projection"}]
    top = sorted((p for p in sleeper if p in actual), key=lambda p: (-sleeper[p], p))[:50]
    assert ev1["weekly"][0]["by_slot"]["LB"]["mae"] == sum(abs(sleeper[p] - actual[p]) for p in top) / 50
    for key in ("mae", "rmse", "bias", "n", "mae_base"):
        assert ev1["season_by_source"]["sleeper_proj"]["LB"][key] == ev1["season_by_slot"]["LB"][key]

    env = Environment(loader=PackageLoader("sadlidp.site", "templates"), autoescape=True)
    html = env.get_template("stats.html.j2").render(s=ev, slots=("DL", "LB", "DB"), league_name="SADL")
    assert "Each source on the same players" in html and "<td>ESPN</td><td>LB</td>" in html
    assert "Sleeper and ESPN have an equal say (50% each)" in html and "60% <small>(90)</small>" in html
    single = env.get_template("stats.html.j2").render(s=ev1, slots=("DL", "LB", "DB"), league_name="SADL")
    assert "Each source on the same players" not in single and "Only Sleeper so far" in single


def test_zero_fills_alone_never_bring_back_a_player_another_source_lists_as_no_projection(tmp_path):
    r = multi_routes()
    players, proj = r[f"{V1}/players/nfl"], r[f"{API}/projections/nfl/2026/5"]
    in_espn = {str(e["player"]["id"]) for e in ESPN}
    gone = next(pid for pid, p in sorted(players.items()) if p.get("espn_id") and str(p["espn_id"]) not in in_espn
                and slot_of(p) and p.get("active") and p.get("team"))  # fmt: skip
    proj[:] = [x for x in proj if x["player_id"] != gone]
    proj.append({"player_id": gone, "week": 5, "team": players[gone]["team"], "opponent": None, "stats": {}})
    data = tmp_path / "d"
    run_sync(CFG, data, tmp_path / "c", NOW, client=Client(r), log=lambda m: None)
    week = data / "2026" / "week05"
    assert gone not in json.loads((week / "frozen.json").read_text())["players"]  # "no projection", not 0.0
    assert gone not in {c["id"] for c in cards(json.loads((week / "consensus.json").read_text()))}
