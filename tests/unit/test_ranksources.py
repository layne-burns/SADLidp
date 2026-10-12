"""PR 4 end to end, offline: FantasyPros ECR next to Sleeper and ESPN (SPEC §5.2, §6.4, §6.5, §6.8): rank -> value
through the projections, the 60/40 group split, censoring of observed players, list-scoped merges, the week check,
the D/ST signal, the archive rule and per-source stats."""

import copy
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from test_multisource import Client, cards, multi_routes, parts
from test_sync_site import FIXTURES, FULL_CFG, NOW, SCHED, TNF

from sadlidp import backfill, storage
from sadlidp import sync as syncmod
from sadlidp.net import Response
from sadlidp.positions import slot_of
from sadlidp.site.build import build_site
from sadlidp.sleeper import API, V1
from sadlidp.sources import fantasypros
from sadlidp.stats import evaluate, week_values
from sadlidp.sync import SOURCES, build_matcher, run_sync

CFG = FULL_CFG
ECR = {n: json.loads((FIXTURES / f"fp_{n}_week05_trimmed.json").read_text()) for n in ("dl", "lb", "db", "dst")}
ARCHIVE_DL = json.loads((FIXTURES / "fp_dl_week04_archive_trimmed.json").read_text())
FP_PLAYERS = json.loads((FIXTURES / "fp_sleeper_players.json").read_text())["players"]
POSTED = int((NOW - timedelta(hours=10)).timestamp())  # Tuesday morning: before the pre-waiver sync
STAR_NYJ, STAR_TB = "c_nyj", "c_tb"  # unlisted LBs projected near the top: NYJ plays Sunday, TB on Thursday


def page(ecr: dict) -> str:
    return f"<html><script>\n    var ecrData = {json.dumps(ecr)};\n    var sosData = {{}};\n</script></html>"


def ecr(name: str, week: int = 5, ts: int = POSTED, keep: int | None = None) -> dict:
    out = copy.deepcopy(ECR[name])
    out.update(week=str(week), last_updated_ts=ts)
    if keep is not None:
        out["players"] = out["players"][:keep]
    return out


class FPClient(Client):
    """test_multisource's client plus the FantasyPros pages: `fp[(page, week)]` is an ecrData dict, an HTTP status
    or a raw body; a live page (no ?week) is `fp[(page, None)]`."""

    def __init__(self, routes: dict, fp: dict, **kw):
        super().__init__(routes, **kw)
        self.fp = fp

    def get(self, url, headers=None, **kw):
        if "fantasypros.com" not in url:
            return super().get(url, headers=headers, **kw)
        self.calls.append(url)
        self.request_count += 1
        name = url.rsplit("/", 1)[1].split(".php")[0]
        week = parse_qs(urlsplit(url).query).get("week")
        got = self.fp.get((name, int(week[0]) if week else None), 404)
        if isinstance(got, int):
            return Response(url=url, final_url=url, status=got)
        body = page(got) if isinstance(got, dict) else got
        return Response(url=url, final_url=url, status=200, body=body.encode())


def live_pages(**over) -> dict:
    return {(n, None): ecr(n) for n in ECR} | {(n, None): v for n, v in over.items()}


def fp_routes() -> dict:
    """test_multisource's league plus every FantasyPros fixture IDP with a Sleeper week-5 row (tackles vary by
    player, so P has a shape) and two unlisted LBs projected near the top."""
    r = multi_routes()
    players, proj = r[f"{V1}/players/nfl"], r[f"{API}/projections/nfl/2026/5"]
    for pid, p in FP_PLAYERS.items():
        players[pid] = {"injury_status": None, **p, **players.get(pid, {})}
    for pid, team in ((STAR_NYJ, "NYJ"), (STAR_TB, "TB")):
        players[pid] = {"full_name": f"Star {team}", "position": "LB", "fantasy_positions": ["LB"], "team": team,
                        "status": "Active", "active": True, "injury_status": None}  # fmt: skip
    have = {x["player_id"] for x in proj}
    template = next(x for x in proj if x["player_id"] == "11017")
    for i, pid in enumerate(sorted({*FP_PLAYERS, STAR_NYJ, STAR_TB})):
        p = players[pid]
        opp = SCHED.opponent(p.get("team"), 5)
        if pid in have or not opp or not slot_of(p):
            continue
        row = copy.deepcopy(template)
        row.update(player_id=pid, team=p["team"], opponent=opp.lstrip("@"))
        solo = 12.0 if pid.startswith("c_") else 1.0 + i % 6
        row["stats"] = {
            "gp": 1.0,
            "idp_tkl_solo": solo,
            "idp_tkl_ast": 1.0,
            "idp_tkl": solo + 1,
            "idp_sack": 0.1 * (i % 4),
        }
        proj.append(row)
    return r


def sync(data, now=NOW, fp=None, routes=None, force=False, espn=None):
    client = FPClient(routes or fp_routes(), live_pages() if fp is None else fp, espn=espn)
    return run_sync(CFG, data, data.parent / "cache", now, force=force, client=client, log=lambda m: None)


def week5(data) -> tuple[dict, dict, dict]:
    wd = data / "2026" / "week05"
    return (json.loads((wd / "consensus.json").read_text()), json.loads((wd / "run.json").read_text()),
            storage.load_source(wd / "sources" / "fantasypros.json"))  # fmt: skip


def three_source_card(d: dict) -> dict:
    return next(c for c in cards(d) if {"Sleeper", "ESPN", "FantasyPros"} <= {p["source"] for p in
                c["breakdown"]["parts"] if "pts" in p})  # fmt: skip


def test_fantasypros_joins_as_a_rank_source_with_a_60_40_split(tmp_path):
    data = tmp_path / "d"
    out = sync(data)
    assert out.synced and not [w for w in out.warnings if w.startswith(("fantasypros", "FantasyPros", "fp_dst"))]
    d, run, src = week5(data)
    fp = run["sources"]["fantasypros"]
    assert (fp["status"], fp["kind"], fp["label"], fp["converted"]) == ("ok", "rank", "FantasyPros", True)
    assert {n: x["status"] for n, x in fp["report"]["lists"].items()} == {"DL": "ok", "LB": "ok", "DB": "ok"}
    assert all(fp["lists"][n]["ok"] and fp["lists"][n]["valued"] >= 10 for n in ("DL", "LB", "DB"))
    assert fp["published_at"] == datetime.fromtimestamp(POSTED, UTC).isoformat()
    assert {r["list"] for r in src["rows"].values()} == {"DL", "LB", "DB"} and src["kind"] == "rank"
    assert d["sources"]["fantasypros"]["lists"]["LB"] == {"status": "ok", "players": 42, "experts": 26, "week": 5}

    card = three_source_card(d)
    by = parts(card)
    fp_part = by["FantasyPros"]
    assert fp_part["kind"] == "rank" and fp_part["list"] in ("DL", "LB", "DB") and fp_part["of"] >= fp_part["rank"]
    assert (by["Sleeper"]["share"], by["ESPN"]["share"], fp_part["share"]) == (0.3, 0.3, 0.4)
    assert card["breakdown"]["split"] == {"projection": 0.6, "rank": 0.4}
    p_mean = (by["Sleeper"]["pts"] + by["ESPN"]["pts"]) / 2
    assert card["mu"] == pytest.approx(0.6 * p_mean + 0.4 * fp_part["pts"], abs=0.02)
    frozen = json.loads((data / "2026" / "week05" / "frozen.json").read_text())["players"][card["id"]]
    assert frozen["eff"] == {"sleeper_proj": 0.3, "espn_proj": 0.3, "fantasypros": 0.4}
    assert frozen["ranks"]["fantasypros"]["rank"] == fp_part["rank"] and "fp_dst" not in frozen["parts"]

    html = build_site(data, tmp_path / "site", CFG).read_text()
    rank = f"{fp_part['list']} #{fp_part['rank']:g} of {fp_part['of']}"
    text = f"<b>FantasyPros</b> · {rank} → {fp_part['pts']:.1f} pts · 40% of his number"
    assert text in html and "(projections 60% / rankings 40%)" in html
    assert "DL ok (26 experts) · LB ok (26 experts) · DB ok (26 experts)" in html


def test_a_locked_players_rank_part_is_frozen(tmp_path):
    data = tmp_path / "d"
    sync(data)
    d, _, _ = week5(data)
    before = {c["id"]: parts(c)["FantasyPros"] for c in cards(d) if c["team"] in ("TB", "DAL")
              and "pts" in parts(c).get("FantasyPros", {})}  # fmt: skip
    assert before
    sync(data, now=TNF, fp=live_pages(dl=ecr("dl", keep=30)), force=True)  # TB @ DAL has kicked off
    d, _, _ = week5(data)
    after = {c["id"]: parts(c)["FantasyPros"] for c in cards(d) if c["id"] in before}
    assert after and all(after[pid] == before[pid] for pid in after)


def test_the_dst_signal_is_context_only(tmp_path):
    data = tmp_path / "d"
    sync(data)
    d, run, _ = week5(data)
    dst = storage.load_source(data / "2026" / "week05" / "sources" / "fp_dst.json")
    assert dst["kind"] == "signal" and dst["rows"]["HOU"]["rank"] == 1 and len(dst["rows"]) == 30
    assert run["sources"]["fp_dst"]["status"] == "ok"
    assert not any(p["source"].startswith("FantasyPros D") for c in cards(d) for p in c["breakdown"]["parts"])
    frozen = json.loads((data / "2026" / "week05" / "frozen.json").read_text())["players"]
    assert not any("fp_dst" in rec["eff"] for rec in frozen.values())
    houston = [c for c in cards(d) if c["team"] == "HOU"]
    assert houston and all(c["breakdown"]["dst"]["rank"] == 1 for c in houston)
    html = build_site(data, tmp_path / "site", CFG).read_text()
    assert "Team defense: #1 this week (not used)" in html


def test_censoring_reaches_only_observed_unlisted_players(tmp_path):
    data = tmp_path / "d"
    sync(data, now=TNF, force=True)  # first FantasyPros read after the Thursday kickoff (TB @ DAL)
    d, run, src = week5(data)
    assert STAR_NYJ not in src["rows"] and STAR_TB not in src["rows"]
    depth = run["sources"]["fantasypros"]["lists"]["LB"]["valued"]
    assert run["sources"]["fantasypros"]["lists"]["LB"]["censored"] >= 1
    nyj = next(c for c in cards(d) if c["id"] == STAR_NYJ)
    part = parts(nyj)["FantasyPros"]
    assert part["censored"] and part["rank"] == depth + 1 and part["depth"] == depth and part["list"] == "LB"
    tb = next(c for c in cards(d) if c["id"] == STAR_TB)
    assert parts(tb)["FantasyPros"]["missing"] == "first read this week after his game started"
    html = build_site(data, tmp_path / "site", CFG).read_text()
    assert f"<b>FantasyPros</b> · not in its LB list → counted as #{depth + 1} → {part['pts']:.1f} pts" in html


def test_a_failed_list_keeps_its_earlier_rows(tmp_path):
    data = tmp_path / "d"
    sync(data)
    _, _, first = week5(data)
    lb_before = {pid: r for pid, r in first["rows"].items() if r["list"] == "LB"}
    later = NOW + timedelta(hours=1)
    out = sync(data, now=later, fp=live_pages(lb=500), force=True)
    assert "fantasypros: LB: failed (HTTP 500); its earlier rows of that list are used" in out.warnings
    d, run, src = week5(data)
    assert run["sources"]["fantasypros"]["status"] == "ok" and src["universe"]["LB"]["ok"] is False
    assert {pid: r for pid, r in src["rows"].items() if r["list"] == "LB"} == lb_before  # stale, untouched
    assert storage.list_counts(src["rows"]) == storage.list_counts(first["rows"])
    assert run["sources"]["fantasypros"]["lists"]["LB"] == {**run["sources"]["fantasypros"]["lists"]["LB"],
                                                            "ok": False, "censored": 0}  # fmt: skip
    lb_card = next(c for c in cards(d) if c["id"] in lb_before and "pts" in parts(c).get("FantasyPros", {}))
    assert parts(lb_card)["FantasyPros"]["list"] == "LB"
    assert "LB failed" in build_site(data, tmp_path / "site", CFG).read_text()

    # the shrink guard per list: a DB page with a fraction of its players keeps the earlier DB rows
    out = sync(data, now=later + timedelta(hours=1), fp=live_pages(db=ecr("db", keep=5)), force=True)
    assert any(w.startswith("fantasypros: DB: 5 rows against") for w in out.warnings)
    _, _, src = week5(data)
    assert storage.list_counts(src["rows"]) == storage.list_counts(first["rows"]) and not src["universe"]["DB"]["ok"]


def test_list_helpers_move_and_keep_ranks():
    rows = {
        "a": {"list": "DL", "rank": 3.0, "other_lists": {"LB": 9.0}, "rank_ave": 3.2},
        "b": {"list": "LB", "rank": 4.0},
    }
    assert storage.without_list(rows, "DL") == {"a": {"list": "LB", "rank": 9.0}, "b": rows["b"]}
    assert storage.without_list(rows, "LB") == {"a": {"list": "DL", "rank": 3.0, "rank_ave": 3.2}}
    fresh = {"a": {"list": "DL", "rank": 2.0}}  # LB failed: a keeps his LB rank, b his LB row
    kept = storage.list_scoped(rows, fresh, {"DL"})
    assert kept == {"a": {"list": "DL", "rank": 2.0, "other_lists": {"LB": 9.0}}, "b": {"list": "LB", "rank": 4.0}}
    assert storage.list_scoped(rows, {}, {"DL"})["a"] == {"list": "LB", "rank": 9.0}  # dropped from DL only
    own = {"c": {"list": "LB", "rank": 5.0, "other_lists": {"DL": 7.0}, "rank_ave": 5.1, "fetched_at": "t"}}
    assert storage.list_scoped(own, {"c": {"list": "DL", "rank": 6.0}}, {"DL"}) == {
        "c": {"list": "LB", "rank": 5.0, "rank_ave": 5.1, "other_lists": {"DL": 6.0}}
    }  # his own list failed: its row stays, with the fresh DL rank


def test_not_yet_published_leaves_the_projections_with_a_banner(tmp_path):
    data = tmp_path / "d"
    out = sync(data, fp={(n, None): ecr(n, week=4) for n in ECR})
    banner = next(w for w in out.warnings if w.startswith("FantasyPros: week 5 not posted yet"))
    assert "DL: not_yet_published (page shows 2026 week 4)" in banner
    d, run, _ = week5(data)
    assert run["sources"]["fantasypros"]["status"] == "not_yet_published"
    assert not (data / "2026" / "week05" / "sources" / "fantasypros.json").exists()
    both = [c for c in cards(d) if c["n"] == 2]
    assert both and all(c["n"] <= 2 for c in cards(d))
    assert all(parts(c)["FantasyPros"]["missing"] == "nothing posted for this week yet" for c in both)
    assert all(parts(c)["Sleeper"]["share"] == 0.5 for c in both)
    assert banner in build_site(data, tmp_path / "site", CFG).read_text()


def test_ranks_are_not_converted_without_projections(tmp_path):
    data = tmp_path / "d"
    r = fp_routes()
    r[f"{API}/projections/nfl/2026/5"] = 503
    out = sync(data, routes=r, fp=live_pages(), espn=503)
    assert any(w.startswith("FantasyPros: ranks not converted") for w in out.warnings)
    d, run, _ = week5(data)
    assert run["sources"]["fantasypros"]["converted"] is False
    card = next(c for c in cards(d) if "FantasyPros" in parts(c))
    assert "can't be turned into points" in parts(card)["FantasyPros"]["missing"]


def test_archive_rows_listed_after_kickoff_are_dropped(tmp_path):
    db = fp_routes()[f"{V1}/players/nfl"]
    idp = {pid: p for pid, p in db.items() if slot_of(p)}
    ts = ARCHIVE_DL["last_updated_ts"]
    fp = {
        ("dl", 4): ARCHIVE_DL,
        ("lb", 4): ecr("lb", 4, ts),
        ("db", 4): ecr("db", 4, ts),
        ("dst", 4): ecr("dst", 4, ts),
    }
    client, notes, logs = FPClient(fp_routes(), fp), {}, []
    done = backfill.run(SOURCES, CFG, tmp_path / "d", 2026, [4], NOW, client=client, sleeper=None, players=idp,
                        matcher=build_matcher(CFG, db), only="fantasypros", log=logs.append, schedule=SCHED,
                        notes=notes)  # fmt: skip
    assert done == {"fantasypros": [4]}
    assert any(u.endswith("dl.php?week=4") for u in client.calls)
    posted = datetime.fromtimestamp(ts, UTC)
    early = {t for g in SCHED.week_games(4) if g.kickoff <= posted for t in (g.home, g.away)}
    assert early  # Thursday night and London
    src = storage.load_source(storage.week_dir(tmp_path / "d", 2026, 4) / "sources" / "fantasypros.json")
    assert src["rows"] and not {r["team"] for r in src["rows"].values()} & early
    assert all(r["backfill"] and r["list_published_at"] == posted.isoformat() for r in src["rows"].values())
    dropped = notes["fantasypros"]["4"]["archive_dropped"]
    assert sum(dropped.values()) > 0 and any("rows listed after kickoff" in m for m in logs)


def test_stats_score_fantasypros_after_mapping(tmp_path):
    data = tmp_path / "d"
    sync(data)
    wd = data / "2026" / "week05"
    frozen = json.loads((wd / "frozen.json").read_text())["players"]
    stats = {pid: {"gp": 1, "idp_tkl_solo": 2 + i % 5} for i, pid in enumerate(sorted(frozen))}
    storage.write_json(wd / "actuals.json", {"captured_at": "2026-12-01T00:00:00+00:00", "stats": stats})
    ev = evaluate(data, 2026, CFG)
    w5 = next(w for w in ev["weekly"] if w["week"] == 5)
    assert list(w5["by_source"]) == ["sleeper_proj", "espn_proj", "fantasypros"]
    assert any(x["covered"] for x in w5["by_source"]["fantasypros"].values())
    assert ev["source_labels"].get("fantasypros", "FantasyPros") == "FantasyPros"

    # frozen parts at kickoff and a recompute from the saved files give the same FantasyPros numbers
    sc = json.loads((data / "2026" / "league.json").read_text())["scoring_settings"]
    slots = {pid: slot_of(p) for pid, p in json.loads((wd / "players.json").read_text())["players"].items()}
    sources = {n: {**storage.load_source(wd / "sources" / f"{n}.json")} for n in ("sleeper_proj", "espn_proj",
                                                                                 "fantasypros")}  # fmt: skip
    from_frozen, _ = week_values(wd, 5, sources, SCHED, slots, sc, CFG)
    (wd / "frozen.json").unlink()
    recomputed, groups = week_values(wd, 5, sources, SCHED, slots, sc, CFG)
    assert groups["fantasypros"] == "rank" and recomputed["fantasypros"]
    assert {p: round(v, 3) for p, v in recomputed["fantasypros"].items()} == from_frozen["fantasypros"]


def test_fantasypros_is_registered():
    assert SOURCES["fantasypros"] == (fantasypros, "rank") and SOURCES["fp_dst"][1] == "signal"
    assert syncmod.prior_weights(CFG)["fantasypros"] == 0.5 and syncmod.prior_weights(CFG)["sleeper_proj"] == 1.0
    assert CFG["net"]["host_interval_s"]["www.fantasypros.com"] == 5.0 and CFG["censor"]["M"] == 10
