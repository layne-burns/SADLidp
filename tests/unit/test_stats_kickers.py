"""Kickers on the Stats page (SPEC §6.8, 2026-11-17), and the proof that they change no IDP figure.

One synthetic season is evaluated twice: as it is (DL/LB/DB only, the way every season was before kickers), and
again after kicker players, kicker source files (some rows from an archive), kicker results, kicker frozen
records, kicker rows in the tiers log, kicker badges, a kicker weights record and kicker waiver claims were
added to the very same files. Every key the first evaluation holds must come back identical in the second;
what the second adds must sit under kicker-only keys.
"""

import json
import shutil
from datetime import timedelta
from pathlib import Path

import pytest
import test_kickers_isolation as iso
import test_sync_site as site
from jinja2 import Environment, PackageLoader

from sadlidp import backtest, league_tx, metrics, scoreboard, stats, tune
from sadlidp.config import load_config
from sadlidp.sleeper import API, V1
from sadlidp.sources import registry
from sadlidp.sync import run_sync

ROOT = Path(__file__).parents[2]
CFG = load_config(ROOT / "config" / "sadl.toml")
IDP = ("DL", "LB", "DB")
WEEKS = (1, 2, 3, 4)
N_K = 24  # kickers in the players file; k23 never plays
SC = {"idp_tkl_solo": 2.0, "idp_tkl_ast": 1.0, "idp_sack": 6.0, "fgm_yds": 0.1, "fgmiss": -1.0, "xpm": 1.0,
      "xpmiss": -1.0}  # fmt: skip
# every key a season with kicker data may add; a season without has none of them, anywhere
K_ONLY = {"K", "k", "slots", "pools", "k_weeks", "k_note", "decisions_to_date_k", "sources_k", "weights_now_k",
          "weights_history_k", "k_untrusted", *registry.names_for("k")}  # fmt: skip


def _write(path: Path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc))


def _read(path: Path):
    return json.loads(path.read_text())


def _base(root: Path) -> Path:
    """Four settled weeks of DL/LB/DB: three sources, frozen records, the Tuesday log with tags, badges,
    recorded weights and waiver claims. No kicker anywhere."""
    data = root / "base"
    season = data / "2026"
    _write(season / "league.json", {"scoring_settings": SC, "settings": {"waiver_type": 2, "waiver_budget": 100}})
    (season / "schedule.json").write_text((ROOT / "data" / "2026" / "schedule.json").read_text())
    players = {f"{s.lower()}{i}": {"position": s, "team": "NYJ", "full_name": f"{s} {i}"}
               for s in IDP for i in range(58)}  # fmt: skip
    ids = list(players)
    for week in WEEKS:
        wd = season / f"week{week:02d}"
        sleeper = {pid: {"stats": {"idp_tkl_solo": 2 + ((i * 7 + week) % 23) * 0.2, "idp_sack": ((i + week) % 5) * 0.1}}
                   for i, pid in enumerate(ids)}  # fmt: skip
        espn = {pid: {"stats": {"idp_tkl_solo": 2.5 + ((i * 11 + week) % 19) * 0.2}, **({"backfill": True} if week == 1
                                                                                         else {})}
                for i, pid in enumerate(ids) if i % 3}  # fmt: skip
        ranks = {f"{s.lower()}{i}": {"ranks": {s: 1 + (i * 3 + week) % 40}} for s in IDP for i in range(40)}
        _write(wd / "sources" / "sleeper_proj.json", {"kind": "projection", "rows": sleeper})
        _write(wd / "sources" / "espn_proj.json", {"kind": "projection", "rows": espn})
        universe = {s: {"complete": False, "depth": 40} for s in IDP}
        _write(wd / "sources" / "fp_ecr.json", {"kind": "rank", "rows": ranks, "universe": universe})
        actual = {pid: {"gp": 1, "idp_tkl_solo": 1 + (i * 5 + week * 3) % 9, "idp_tkl_ast": (i + week) % 4}
                  for i, pid in enumerate(ids) if (i + week) % 29}  # fmt: skip
        _write(wd / "actuals.json", {"captured_at": "2026-12-01T00:00:00+00:00", "stats": actual})
        _write(wd / "players.json", {"players": players})
        if week >= 3:  # the pages of weeks 3 and 4 froze a number; week 3 lacks a few (the plain average stands in)
            frozen = {}
            for i, pid in enumerate(ids):
                if week == 3 and i % 17 == 0:
                    continue
                mu = 2 * sleeper[pid]["stats"]["idp_tkl_solo"] + 0.4
                parts = {"sleeper_proj": mu - 1, "espn_proj": mu + 1, "fp_ecr": mu}
                frozen[pid] = {"mu": mu, "base": mu - 0.3, "sigma": 1.2, "swing": 5.0, "floor": mu - 4,
                               "ceiling": mu + 4, "parts": parts}  # fmt: skip
            _write(wd / "frozen.json", {"players": frozen})
        log = {f"{s.lower()}{i}": {"slot": s, "tier": 1 + i // 8, "mu": 15 - i * 0.2, **({"flags": ["Q"]} if i % 9 == 0
                                                                                       else {})}
               for s in IDP for i in range(30)}  # fmt: skip
        _write(wd / "tiers_log.json", {"prewaiver": {"players": log, "tags": True}})
        if week >= 2:
            badges = {f"b{n}": {"w": f"lb{n}", "over": f"lb{n + 20}", "badge": "UPGRADE", "slot": "LB",
                                "p": 0.6 + n / 50, "mu_w": 9.0 + n, "active": True} for n in range(1, 4)}  # fmt: skip
            badges["ref"] = {"w": "db3", "over": "db9", "badge": "START", "slot": "DB", "p": 0.9, "active": True,
                             "reference": True}  # fmt: skip
            _write(wd / "badges.json", {"badges": badges})
        swing = {
            "a": {"DL": 4.3, "LB": 18.0, "DB": 27.0},
            "b": {"DL": 2.2, "LB": 1.35, "DB": 0.65},
            "lo": -0.8,
            "hi": 0.75,
        }
        _write(
            wd / "consensus.json",
            {"sigma_out": {"DL": 5.6, "LB": 5.5, "DB": 5.2}, "swing": swing if week >= 3 else None},
        )
        rec = {"method": "prior", "alpha": 0.6, "from_weeks": [], "sources": {},
               "priors": {"fp_ecr": 2.0}, "k": 4.0}  # fmt: skip
        if week == 4:
            rec = {"method": "accuracy", "alpha": 0.62, "alpha_hat": 0.7, "from_weeks": [1, 2], "k": 4.0,
                   "priors": {"fp_ecr": 2.0},
                   "sources": {"sleeper_proj": {"w": 1.1}, "espn_proj": {"w": 0.9}, "fp_ecr": {"w": 2.0}}}  # fmt: skip
        _write(wd / "weights.json", rec)
    claims = [{"type": "waiver", "status": "complete", "adds": {f"lb{i}": 1}, "bid": b, "leg": 2}
              for i, b in enumerate((0, 1, 1, 4, 15))]  # fmt: skip
    claims.append({"type": "waiver", "status": "failed", "adds": {"lb4": 2}, "bid": 9, "leg": 2})
    claims.append({"type": "free_agent", "status": "complete", "adds": {"dl6": 1}, "created": 1793203200000, "leg": 2})
    _write(season / "week02" / "transactions.json", {"leg": 2, "transactions": claims})
    return data


def k_line(i: int, week: int, shift: float = 0.0) -> dict:
    """A kicker's projected line."""
    return {"fga": 2.2, "fgm": 1.9, "fgm_yds": 50.0 + (i * 7 + week * 3) % 40 + shift, "fgmiss": 0.3,
            "xpm": 2.0 + (i % 3) * 0.5, "xpmiss": 0.1}  # fmt: skip


def k_actual(i: int, week: int) -> dict:
    return {"gp": 1, "fgm_yds": float((i * 13 + week * 17) % 120), "fgmiss": (i + week) % 2,
            "xpm": (i * 3 + week) % 5, "xpmiss": 0}  # fmt: skip


def kpts(line: dict) -> float:
    return sum(SC[k] * line[k] for k in ("fgm_yds", "fgmiss", "xpm", "xpmiss"))


def k_played(week: int) -> list[int]:
    """Who has a kicker line: 15 of them in week 2 (a partial pool of 20), everyone but k23 otherwise."""
    return list(range(15)) if week == 2 else list(range(N_K - 1))


def k_espn(week: int) -> list[int]:
    """ESPN's kicker file: none in weeks 1-2, first read partway through week 3 (8 kickers), everyone in week 4."""
    return {3: list(range(8)), 4: list(range(N_K))}.get(week, [])


def k_mean(i: int, week: int) -> float:
    """The plain average of the kicker projection sources (Sleeper; ESPN projects 1 yard-point more)."""
    vals = [kpts(k_line(i, week))] + ([kpts(k_line(i, week, 10.0))] if i in k_espn(week) else [])
    return sum(vals) / len(vals)


def k_pool(week: int) -> list[str]:
    cand = {f"k{i}": k_mean(i, week) for i in k_played(week)}
    return sorted(cand, key=lambda pid: (-cand[pid], pid))[:20]


def _with_kickers(base: Path, root: Path) -> Path:
    """The same season with kickers added to the very same files (and to their own source files)."""
    data = root / "kick"
    shutil.copytree(base, data)
    season = data / "2026"
    kickers = {f"k{i}": {"position": "K", "fantasy_positions": ["K"], "team": "NYJ", "full_name": f"Kicker {i}"}
               for i in range(N_K)}  # fmt: skip
    punter = {"pu": {"position": "P", "fantasy_positions": ["K", "P"], "team": "NYJ", "full_name": "A Punter"}}
    for week in WEEKS:
        wd = season / f"week{week:02d}"
        doc = _read(wd / "players.json")
        doc["players"].update(kickers | punter)
        _write(wd / "players.json", doc)
        rows = {f"k{i}": {"stats": k_line(i, week), **({"backfill": True} if week <= 2 else {})} for i in range(N_K)}
        rows["pu"] = {"stats": k_line(0, week)}  # a punter is not a kicker: never scored
        _write(wd / "sources" / "sleeper_k.json", {"kind": "projection", "rows": rows,
                                                   "universe": {"K": {"complete": True, "depth": None}}})  # fmt: skip
        if k_espn(week):
            espn = {f"k{i}": {"stats": k_line(i, week, 10.0)} for i in k_espn(week)}
            _write(wd / "sources" / "espn_k.json", {"kind": "projection", "rows": espn})
        if week == 4:
            _write(wd / "sources" / "fp_k.json",
                   {"kind": "rank", "rows": {f"k{i}": {"ranks": {"K": N_K - i}} for i in range(N_K)},
                    "universe": {"K": {"complete": False, "depth": N_K}}})  # fmt: skip
        act = _read(wd / "actuals.json")
        act["stats"].update({f"k{i}": k_actual(i, week) for i in k_played(week)})
        act.update(k_added_at="2026-12-02T00:00:00+00:00", k_added=len(k_played(week)))
        _write(wd / "actuals.json", act)
        if week == 4:  # week 4's page showed kicker numbers; week 3's did not (the plain average stands in)
            frozen = _read(wd / "frozen.json")
            for i in range(N_K):
                parts = {"sleeper_k": kpts(k_line(i, 4)), "espn_k": kpts(k_line(i, 4, 10.0)), "fp_k": 7.0}
                number = k_mean(i, 4) + 0.5
                frozen["players"][f"k{i}"] = {"mu": number, "base": number, "sigma": 0.9, "parts": parts}
            _write(wd / "frozen.json", frozen)
        if week >= 3:
            log = _read(wd / "tiers_log.json")
            for i in range(N_K):
                tag = {"flags": ["Q"]} if i % 4 == 0 else {}
                log["prewaiver"]["players"][f"k{i}"] = {"slot": "K", "tier": 1 + i // 6, "mu": 9.0 - i * 0.1, **tag}
            _write(wd / "tiers_log.json", log)
            badges = _read(wd / "badges.json")
            badges["badges"].update({
                "k1": {"w": "k1", "over": "k2", "badge": "START", "slot": "K", "p": 0.61, "mu_w": 8.0, "active": True},
                "k2": {"w": "k3", "over": "k2", "badge": "START", "slot": "K", "p": 0.66, "mu_w": 8.5, "active": True},
            })  # fmt: skip
            _write(wd / "badges.json", badges)
            page = _read(wd / "consensus.json")
            page["sigma_out"]["K"] = 5.0
            page["swing"] = {
                **page["swing"],
                "a": {**page["swing"]["a"], "K": 0.0},
                "b": {**page["swing"]["b"], "K": 1.0},
            }
            page["kicker"] = {"sigma_out": 5.0, "one_point_edge": 0.556}
            _write(wd / "consensus.json", page)
            rec = _read(wd / "weights.json")
            rec["kickers"] = {"method": "prior", "alpha": 0.6, "from_weeks": [], "sources": {}, "k": 4.0,
                              "priors": {"fp_k": 2.0},
                              "reason": "0 evaluated week(s); data weights start at 3"}  # fmt: skip
            _write(wd / "weights.json", rec)
    tx = _read(season / "week02" / "transactions.json")
    tx["transactions"] += [
        {"type": "waiver", "status": "complete", "adds": {"k5": 1}, "bid": 40, "leg": 2},
        {"type": "waiver", "status": "failed", "adds": {"k5": 2}, "bid": 30, "leg": 2},
        {"type": "free_agent", "status": "complete", "adds": {"k6": 1}, "created": 1793289600000, "leg": 2},
    ]
    _write(season / "week02" / "transactions.json", tx)
    return data


@pytest.fixture(scope="module")
def seasons(tmp_path_factory):
    root = tmp_path_factory.mktemp("stats_kickers")
    base = _base(root)
    kick = _with_kickers(base, root)
    return base, kick, stats.evaluate(base, 2026, CFG), stats.evaluate(kick, 2026, CFG)


def _compare(old, new, path: tuple, extra: list, diff: list) -> None:
    """Every key of `old` must be in `new` with the same value (lists: the same length, item by item). Keys
    only `new` has are collected in `extra`, not followed."""
    if isinstance(old, dict) and isinstance(new, dict):
        extra += [(*path, k) for k in new if k not in old]
        for k, v in old.items():
            if k not in new:
                diff.append((*path, k, "gone"))
            else:
                _compare(v, new[k], (*path, k), extra, diff)
    elif isinstance(old, list | tuple) and isinstance(new, list | tuple) and len(old) == len(new):
        for i, (a, b) in enumerate(zip(old, new, strict=True)):
            _compare(a, b, (*path, i), extra, diff)
    elif old != new or type(old) is not type(new):
        diff.append((*path, old, new))


def _keys(doc, found: set) -> set:
    if isinstance(doc, dict):
        for k, v in doc.items():
            found.add(k)
            _keys(v, found)
    elif isinstance(doc, list | tuple):
        for v in doc:
            _keys(v, found)
    return found


def _view(ev: dict, key: str) -> dict:
    return next(v for v in ev["views"] if v["key"] == key)


def _html(ev: dict, slots=IDP) -> str:
    env = Environment(loader=PackageLoader("sadlidp.site", "templates"), autoescape=True)
    return env.get_template("stats.html.j2").render(s=ev, slots=slots, league_name="SADL")


def test_every_idp_figure_is_identical_with_and_without_kicker_rows(seasons):
    """The proof (design brief, section H): stats.evaluate on the same season with and without kicker data
    gives the same value for every key that existed before, down to the last leaf."""
    base, kick, eb, ek = seasons
    extra, diff = [], []
    _compare(eb, ek, (), extra, diff)
    assert diff == []
    assert extra and all(path[-1] in K_ONLY for path in extra), [p for p in extra if p[-1] not in K_ONLY]
    assert not _keys(eb, set()) & K_ONLY  # the season without kickers holds no kicker key at any depth
    # the base is not a hollow season: it exercises every pooled figure the brief names
    season = _view(eb, "season")
    assert season["scoreboard"]["all"]["weeks"] == [1, 2, 3, 4] and eb["db_note"]["r"] is not None
    assert eb["suggestions"]["live"]["decided"] == 9 and eb["decisions_to_date"][4]["live"]["decided"] == 9
    assert eb["tuesday_q"]["tagged"] == 4 * 3 * 4 and eb["tuesday_q"]["untagged"] == 4 * 3 * 26
    assert [h["shares"] for h in eb["weights_history"]][-1] == pytest.approx(
        {"Sleeper": 0.62 * 1.1 / 2, "ESPN": 0.62 * 0.9 / 2, "FantasyPros": 0.38}
    )
    assert eb["weekly"][0]["backfill"] == 116 and eb["weekly"][2]["fallback"] > 0 and season["live_weeks"] == [4]
    assert list(eb["season_by_source"]) == ["sleeper_proj", "espn_proj", "fp_ecr"]
    assert eb["weights_now"] is not None and set(eb["weights_now"]["sources"]) == {"Sleeper", "ESPN", "FantasyPros"}
    # σ_out from residuals: DL, LB and DB only, and the same numbers (a kicker never sets an IDP's swing)
    sig = stats.residual_sigma_out(base, 2026, CFG, target_week=6)
    assert set(sig) == set(IDP) and stats.residual_sigma_out(kick, 2026, CFG, target_week=6) == sig
    assert ek["_resid"]["LB"] == eb["_resid"]["LB"]
    # the FAAB line: the same counts, though the kicker season holds a contested kicker claim and a kicker add
    settings = {"waiver_type": 2, "waiver_budget": 100}
    faab = [league_tx.faab_summary(d, 2026, stats._positions(d / "2026"), settings) for d in (base, kick)]
    assert faab[0] == faab[1] and (faab[0]["claims"], faab[0]["contested"], faab[0]["fa_adds"]) == (5, 1, 1)
    assert stats._positions(kick / "2026")["k5"] == "K" and "pu" not in stats._positions(kick / "2026")


def test_the_page_of_a_season_without_kickers_never_mentions_them(seasons):
    _, _, eb, _ = seasons
    html = _html(eb)
    assert "kicker" not in html.lower() and "DL, LB and DB" not in html and '<th class="num">K</th>' not in html
    assert "at each position each week" in html and "the three positions count as one week" in html
    assert "(DL), " in html and " (LB) and " in html and "For each position's 60" not in html  # no D/ST file here
    # the page decides its own slots: passing K for a season that has none changes nothing below the stylesheet
    wide = _html(eb, slots=(*IDP, "K"))
    assert wide[wide.index("<body>") :] == html[html.index("<body>") :]


def test_a_kicker_week_is_graded_on_a_pool_of_20_with_top_5_hits(seasons):
    """About 28 kickers play a week: against the IDP pool of 50 every kicker week would be "partial" for ever."""
    _, _, eb, ek = seasons
    assert ek["slots"] == ["DL", "LB", "DB", "K"] and ek["n_eval"] == 50 and ek["k_weeks"] == [1, 2, 3, 4]
    json.dumps({k: v for k, v in ek.items() if not k.startswith("_")}, sort_keys=True, default=list)  # as build.py
    assert ek["pools"] == {"DL": {"pool": 50, "top": 12}, "LB": {"pool": 50, "top": 12},
                           "DB": {"pool": 50, "top": 12}, "K": {"pool": 20, "top": 5}}  # fmt: skip
    assert ek["sigma_out"]["K"] == 5.0 and ek["sigma_mu"]["K"] == 1.0  # config values: no fitted kicker swing
    w4 = ek["weekly"][3]
    k4 = w4["by_slot"]["K"]
    pool = k_pool(4)
    mu = {pid: k_mean(int(pid[1:]), 4) + 0.5 for pid in pool}  # the frozen number
    act = {f"k{i}": kpts(k_actual(i, 4)) for i in k_played(4)}
    assert (k4["n"], k4["partial"]) == (20, False) and w4["partial_slots"] == []
    assert k4["mae"] == pytest.approx(sum(abs(mu[p] - act[p]) for p in pool) / 20)
    assert k4["ref"]["plain"] == pytest.approx(sum(abs(mu[p] - 0.5 - act[p]) for p in pool) / 20)
    assert k4["top12"] == metrics.top_k_hits(mu, act, 5) and k4["top12"][1] == 5  # the top 5 of the 23 who played
    assert w4["by_slot"]["LB"]["top12"][1] == 12
    assert w4["k"] == {"late": 0, "backfill": 0, "rebuilt": 0, "fallback": 0, "n": 20, "joined_midweek": []}
    w2 = ek["weekly"][1]
    assert (w2["by_slot"]["K"]["n"], w2["by_slot"]["K"]["partial"]) == (15, True)  # 15 of 20: partial, against 20
    assert w2["by_slot"]["K"]["top12"] is None and w2["partial_slots"] == []  # `partial_slots` stays DL/LB/DB
    season = _view(ek, "season")
    assert season["slots"] == ["DL", "LB", "DB", "K"] and season["by_slot"]["K"]["n"] == 60  # weeks 1, 3, 4
    assert season["k"] == {"weeks": [1, 3, 4], "live_weeks": [4], "stand_in": [1, 3], "archive": [1], "partial": [2]}
    assert season["by_slot"]["K"]["weeks"] == 3 and season["by_slot"]["K"]["top12"][1] == 15
    assert season["by_slot"]["K"]["live_mae"] == pytest.approx(k4["mae"])  # week 4 alone showed kicker numbers
    assert _view(ek, "w2")["by_slot"]["K"]["partial"] and _view(ek, "w2")["k"]["weeks"] == [2]
    # a kicker's σ_pred: his frozen σ_μ and, with no swing of his own (`kicker_var` has no answer), σ_out,K
    pairs = metrics.pairwise(mu, dict.fromkeys(pool, (0.9**2 + 5.0**2) ** 0.5), {p: act[p] for p in pool})
    assert _view(ek, "w4")["by_slot"]["K"]["brier"] == pytest.approx(metrics.brier(pairs))
    assert ek["weekly"][2]["k"]["fallback"] == 20 and ek["weekly"][2]["fallback"] == eb["weekly"][2]["fallback"]
    assert ek["k_note"]["settled"] == 3 and ek["k_note"]["band"] and ek["k_note"]["weeks"] == 1  # week 4's band
    assert ek["k_note"]["live"] == 1 and set(ek["k_note"]) == {"r", "margin", "weeks", "band", "settled", "live"}
    assert ek["k_note"]["r"] == _view(ek, "season")["scoreboard"]["K"]["rows"][-1]["r"]["mean"]


def test_kicker_sources_are_scored_on_kickers_only_and_archive_rows_are_marked(seasons):
    _, _, eb, ek = seasons
    names = list(ek["season_by_source"])
    assert names == ["sleeper_proj", "espn_proj", "fp_ecr", "sleeper_k", "espn_k", "fp_k"]
    for name in names:  # a source has rows for its own group's slots, never the other's
        assert set(ek["season_by_source"][name]) <= ({"K"} if name.endswith("_k") else set(IDP))
    sk = ek["season_by_source"]["sleeper_k"]["K"]
    assert (sk["covered"], sk["pool"], sk["archive"], sk["weeks"]) == (60, 60, 20, 3)  # week 1 came from the archive
    assert "archive" not in ek["season_by_source"]["sleeper_proj"]["LB"]  # the IDP rows carry no new key
    w3 = ek["weekly"][2]
    covered = [p for p in k_pool(3) if int(p[1:]) in k_espn(3)]
    assert (w3["by_source"]["espn_k"]["K"]["covered"], w3["by_source"]["espn_k"]["K"]["pool"]) == (len(covered), 20)
    assert w3["k"]["joined_midweek"] == ["espn_k"] and w3["joined_midweek"] == eb["weekly"][2]["joined_midweek"]
    assert ek["weekly"][0]["k"]["backfill"] == N_K and ek["weekly"][0]["backfill"] == eb["weekly"][0]["backfill"]
    fp = ek["weekly"][3]["by_source"]["fp_k"]["K"]  # a kicker ranking is mapped to points on the kicker projections
    assert fp["covered"] == 20 and fp["mae"] is not None
    assert ek["source_labels"] == {"sleeper_proj": "Sleeper", "espn_proj": "ESPN", "fp_ecr": "FantasyPros",
                                   "sleeper_k": "Sleeper", "espn_k": "ESPN", "fp_k": "FantasyPros"}  # fmt: skip
    assert ek["sources"] == eb["sources"] and [x["name"] for x in ek["sources"]] == ["Sleeper", "ESPN", "FantasyPros"]
    assert {x["name"]: round(x["share"], 2) for x in ek["sources_k"]} == {
        "Sleeper": 0.3,
        "ESPN": 0.3,
        "FantasyPros": 0.4,
    }


def test_kicker_suggestions_and_the_kicker_band_are_counted_apart(seasons):
    _, _, eb, ek = seasons
    for key in ("w3", "w4", "season"):
        sb, sk = _view(eb, key)["suggestions"], _view(ek, key)["suggestions"]
        assert {k: sk[k] for k in sb} == sb  # the DL/LB/DB tallies and list: not one kicker badge in them
        assert all(e["slot"] != "K" for e in sk["list"]) and all(e["slot"] == "K" for e in sk["k"]["list"])
    k3 = _view(ek, "w3")["suggestions"]["k"]
    assert (k3["live"]["decided"], k3["live"]["badges"]["decided"]) == (1, 2)  # two badges over k2: one decision
    assert [e["counted"] for e in k3["list"]] == [e["name"] == "Kicker 3" for e in k3["list"]]  # the likelier badge
    assert ek["decisions_to_date_k"][2]["live"]["decided"] == 0 and ek["decisions_to_date_k"][4]["live"]["decided"] == 2
    assert ek["decisions_to_date"] == eb["decisions_to_date"] and ek["suggestions"] == eb["suggestions"]
    assert ek["tuesday_q"] == eb["tuesday_q"]  # six tagged kickers a week in the log, none counted
    sb, sk = _view(eb, "season")["scoreboard"], _view(ek, "season")["scoreboard"]
    assert set(sk) == {"DL", "LB", "DB", "all", "K"} and {k: sk[k] for k in sb} == sb
    rows = {r["key"]: r for r in sk["K"]["rows"]}
    assert sk["K"]["weeks"] == [3, 4] and rows["form"]["r"]["weeks"] == 1  # week 3 froze no kicker number
    assert rows["sleeper"]["r"]["weeks"] == 1  # the kicker sources' own parts, from week 4's frozen records
    assert _view(ek, "w4")["scoreboard"] is None and _view(ek, "season")["tier_order"]["K"]["weeks"] == 2
    assert sum(t["n"] for t in ek["tiers"]["K"]) == 2 * 23  # 24 shown a week, k23 didn't play


def test_kicker_shares_sit_beside_the_idp_ones(seasons):
    _, _, eb, ek = seasons
    assert ek["weights_history"] == eb["weights_history"]  # the kicker files and record change no IDP share
    hist = ek["weights_history_k"]
    assert [h["week"] for h in hist] == [1, 2, 3, 4] and [h["method"] for h in hist] == ["default"] * 2 + ["prior"] * 2
    assert hist[0]["shares"] == {"Sleeper": 1.0} and hist[2]["shares"] == {"Sleeper": 0.5, "ESPN": 0.5}
    assert hist[3]["shares"] == pytest.approx({"Sleeper": 0.3, "ESPN": 0.3, "FantasyPros": 0.4})
    assert hist[3]["reason"].startswith("0 evaluated week")
    assert ek["k_untrusted"] == ["Sleeper", "ESPN", "FantasyPros", "RotoBaller"]  # `[accuracy] untrusted`
    # measured live: archive rows of an untrusted source don't count, so Sleeper's weeks 1-2 are not in it
    now = ek["weights_now_k"]
    assert now is not None and now["weeks"] == [3, 4] and "Sleeper" in now["sources"]
    assert ek["weights_now"] == eb["weights_now"]


def test_the_page_lists_k_beside_the_three_positions(seasons):
    _, _, _, ek = seasons
    html = _html(ek)  # the build still passes the three IDP slots: the page takes K from the evaluation
    assert "<td>Sleeper</td><td>K</td>" in html and "<td>ESPN</td><td>K</td>" in html
    assert "<td><b>Our number</b></td><td>K</td>" in html and '<th class="num">K</th>' in html
    assert "<small>all from the archive</small>" in html and "<small>20 from the archive</small>" in html
    assert "DL, LB and DB suggestions paid off in" in html and ", kicker suggestions in 2 of 2;" in html
    assert "For the 50 best-projected players at DL, LB and DB each week" in html
    assert "at each position each week" not in html and "the three positions count as one week" not in html
    assert "DL, LB and DB count as one week; kickers are not in these rows" in html
    assert "(DB) and " in html and " (K) points on average." in html  # "a (DL), b (LB), c (DB) and d (K)"
    assert "for the 20 best-projected kickers each week (weeks 1, 3, 4)" in html
    assert "Weeks 1, 3 had no number on the page for some or all of these kickers" in html
    assert "Week 2 is only partly covered at K" in html and "ESPN kickers in week 3" in html
    assert "Live, DL, LB and DB:" in html and "Kickers, live:" in html
    assert "<b>Kickers</b> have their own sources and their own shares" in html
    assert "(week 4): Sleeper 30%, ESPN 30%, FantasyPros 40%." in html and "Kicker shares week by week" in html
    assert "(Sleeper, ESPN, FantasyPros, RotoBaller) are shown on this page, marked as from the archive" in html
    assert "do not count toward the kicker shares yet" in html
    assert "<b>DL, LB and DB.</b> Each source's share" in html and "Sleeper 34%, ESPN 28%, FantasyPros 38%" in html
    assert "our 5 best against the actual top 5" in html and "<small>archive</small>" in html
    w1 = html[html.index('id="vpanel-w1"') : html.index('id="vpanel-w2"')]
    assert "This week had no number on the page" in w1 and "from projections fetched afterwards" in w1


def test_a_kicker_week_without_final_stats_adds_only_its_shares(seasons, tmp_path):
    """The first kicker week, before it has results: nothing is graded yet, so the page has no K row; only the
    kicker shares in force are there to show."""
    base, _, eb, _ = seasons
    plain, first = tmp_path / "plain", tmp_path / "first"
    shutil.copytree(base, plain)
    wd = plain / "2026" / "week05"
    _write(wd / "consensus.json", {"sigma_out": {"DL": 5.6, "LB": 5.5, "DB": 5.2}})
    _write(wd / "weights.json", {"method": "prior", "alpha": 0.6, "from_weeks": [], "sources": {}})
    _write(
        wd / "sources" / "sleeper_proj.json", {"kind": "projection", "rows": {"lb1": {"stats": {"idp_tkl_solo": 3}}}}
    )
    shutil.copytree(plain, first)
    wd = first / "2026" / "week05"
    _write(wd / "sources" / "sleeper_k.json", {"kind": "projection", "rows": {"k1": {"stats": k_line(1, 5)}}})
    _write(wd / "weights.json", {**_read(wd / "weights.json"), "kickers": {"method": "prior", "alpha": 0.6}})
    ep, ef = stats.evaluate(plain, 2026, CFG), stats.evaluate(first, 2026, CFG)
    extra, diff = [], []
    _compare(ep, ef, (), extra, diff)
    assert diff == [] and sorted(extra) == [("k_untrusted",), ("weights_history_k",)]
    assert ef["weights_history_k"] == [{"week": 5, "method": "prior", "alpha": 0.6, "alpha_hat": None, "from_weeks": [],
                                        "reason": None, "shares": {"Sleeper": 1.0}, "both_groups": False}]  # fmt: skip
    assert [h["week"] for h in ef["weights_history"]] == [1, 2, 3, 4, 5]
    html = _html(ef)
    assert "<b>Kickers</b> have their own sources" in html and "(week 5): Sleeper 100%." in html
    assert '<th class="num">K</th>' not in html and "at each position each week" in html


def test_scoreboard_all_is_dl_lb_db_whatever_else_a_week_holds():
    def band(sign):
        return [{"pid": f"p{i}", "pts": float(10 - i), "played": True, "tue": float(10 - i),
                 "vals": {"sleeper": sign * float(i), "espn": float(10 - i), "blend": float(10 - i),
                          "weights": float(10 - i), "form": float(10 - i)}} for i in range(10)]  # fmt: skip

    idp = {w: scoreboard.week_stats({"LB": band(1), "DL": band(1)}) for w in (1, 2, 3)}
    both = {w: scoreboard.week_stats({"LB": band(1), "DL": band(1), "K": band(-1)}) for w in (1, 2, 3)}
    assert scoreboard.aggregate(both) == scoreboard.aggregate(idp)  # a K band in the week changes no scope
    k = scoreboard.aggregate(both, ("K",))["K"]
    assert next(r for r in k["rows"] if r["key"] == "sleeper")["r"]["mean"] == pytest.approx(1.0)
    frozen = {"a": {"mu": 8.0, "parts": {"sleeper_k": 7.0, "espn_k": 9.0, "sleeper_proj": 1.0}}}
    kinds = {"sleeper_k": "projection", "espn_k": "projection"}
    row = scoreboard.band_rows({"K": ["a"]}, frozen, {"a": 6.0}, {}, kinds, {}, 0.6, stats.K_RUNGS)["K"][0]
    assert (row["vals"]["sleeper"], row["vals"]["espn"], row["vals"]["blend"]) == (7.0, 9.0, 8.0)
    assert scoreboard.band_rows({"LB": ["a"]}, frozen, {}, {}, {}, {}, 0.6)["LB"][0]["vals"]["sleeper"] == 1.0


def test_tune_and_the_backtest_never_take_in_a_kicker(seasons, tmp_path):
    base, kick, _, _ = seasons
    rostered = {w: set() for w in WEEKS}
    plain, with_k = tune.load_weeks(base, 2026, rostered), tune.load_weeks(kick, 2026, rostered)
    assert [(w.week, w.values, w.kinds, w.slots, w.actual) for w in with_k] == [
        (w.week, w.values, w.kinds, w.slots, w.actual) for w in plain
    ]
    assert set(with_k[3].values) == {"sleeper_proj", "espn_proj", "fp_ecr"} and "k1" not in with_k[3].slots
    # the archive: a kicker is never stored, and a kicker row put there by hand is never loaded into a fit
    proj = [{"player_id": "a", "team": "NYJ", "player": {"position": "LB"}, "stats": {"idp_tkl_solo": 3.0}},
            {"player_id": "k", "team": "NYJ", "player": {"position": "K", "fantasy_positions": ["K"]},
             "stats": {"fgm_yds": 80.0, "idp_tkl_solo": 0.1}}]  # fmt: skip
    lines = {"a": {"gp": 1, "idp_tkl_solo": 4}, "k": {"gp": 1, "fgm_yds": 90.0, "idp_tkl_solo": 1}}

    class Sleeper:
        def projections(self, season, week):
            return proj, None

        def stats(self, season, week):
            return lines, None

    doc = backtest.fetch_week(Sleeper(), 2025, 1, {"k": {"position": "K"}}, SC)
    assert set(doc["rows"]) == {"a"}
    doc["rows"]["k"] = {"slot": "K", "team": "NYJ", "proj": {"idp_tkl_solo": 0.1}, "act": {"gp": 1}}
    _write(backtest.archive_dir(tmp_path, 2025) / "week01.json", doc)
    assert [r["pid"] for r in backtest.load(tmp_path, 2025, SC)] == ["a"]


def _two_weeks(data: Path, cache: Path, kickers: bool) -> Path:
    """test_sync_site's world synced in week 5 and again a week later, so week 5 has final stats to grade."""

    def client(week: int):
        r = iso.k_routes() if kickers else site.routes()
        proj5 = r[f"{API}/projections/nfl/2026/5"]
        if week == 6:
            r[f"{V1}/state/nfl"] = {"season": "2026", "season_type": "regular", "week": 6, "display_week": 6}
            r[f"{V1}/league/{site.LID}/matchups/6"] = []
            r[f"{API}/projections/nfl/2026/6"] = [{**x, "week": 6} for x in proj5]
            final = {x["player_id"]: {"gp": 1, "idp_tkl_solo": 3} for x in proj5 if x.get("opponent")}
            if kickers:
                final |= {
                    x["player_id"]: {"gp": 1, "xpm": 2, "fgm_yds": 80} for x in r[iso.K_ROUTE] if x.get("opponent")
                }
                r[f"{API}/projections/nfl/2026/6#K"] = [{**x, "week": 6} for x in r[iso.K_ROUTE]]
            r[f"{V1}/stats/nfl/regular/2026/5"] = final
        return (iso.KClient if kickers else site.FakeClient)(r)

    run_sync(site.CFG, data, cache, site.NOW, client=client(5), log=lambda m: None)
    run_sync(site.CFG, data, cache, site.NOW + timedelta(days=7), force=True, client=client(6), log=lambda m: None)
    return data


def test_synced_weeks_with_kickers_grade_the_same_idp_numbers(tmp_path):
    """The same proof on what the sync really writes (frozen records, badges, the tiers log and the weights
    record of a kicker week), not on files written by hand."""
    eb = stats.evaluate(_two_weeks(tmp_path / "b", tmp_path / "cb", False), 2026, site.CFG)
    ek = stats.evaluate(_two_weeks(tmp_path / "k", tmp_path / "ck", True), 2026, site.CFG)
    assert eb["weeks"] == ek["weeks"] == [4, 5] and _view(eb, "w5")["suggestions"]["live"]["decided"] > 0
    # the kicker source is switched on in both runs (so both hold a kicker weights record); only one has rows
    assert "slots" not in eb and [h["shares"] for h in eb["weights_history_k"]] == [{}, {}]
    assert [h["shares"] for h in ek["weights_history_k"]] == [{"Sleeper": 1.0}, {"Sleeper": 1.0}]
    extra, diff = [], []
    _compare({k: v for k, v in eb.items() if k != "weights_history_k"}, ek, (), extra, diff)
    assert diff == [] and extra and all(path[-1] in K_ONLY for path in extra)
    w5 = ek["weekly"][1]
    assert (
        ek["k_weeks"] == [5] and w5["by_slot"]["K"]["n"] == w5["k"]["n"] == w5["by_source"]["sleeper_k"]["K"]["covered"]
    )
    assert (
        0 < w5["k"]["n"] < 20 and w5["by_slot"]["K"]["partial"] and w5["k"]["fallback"] == 0
    )  # all had a frozen number
    k = _view(ek, "w5")["suggestions"]["k"]
    assert k["live"]["decided"] == 1 and {e["slot"] for e in k["list"]} == {"K"} and _view(ek, "w5")["tiers"]["K"]
    html = _html(ek)
    assert "<td>Sleeper</td><td>K</td>" in html and "Kickers, live:" in html and "(week 6): Sleeper 100%." in html
