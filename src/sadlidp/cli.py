"""Command line: the workflows call these (SPEC.md §4.3, §7).

sadlidp sync [--force] [--now ISO]   # gate, fetch, score, tier; writes data/<season>/week<NN>/
sadlidp build                        # render the latest week into site/
sadlidp backfill [--source NAME] [--weeks A-B] [--redo]   # past weeks from the sources' archives (§5.3)
sadlidp replay --weeks A-B --season YYYY                 # rebuild past weeks' pages from saved data
sadlidp replay --weeks 5 --season YYYY --fill-frozen     # add missing frozen records to a live week
sadlidp backfill --source S --weeks 5 --fill-gaps        # add archive rows a live week lacks (§5.3)
sadlidp tune --season YYYY                               # re-tier past weeks under candidate settings
sadlidp audit [--season YYYY]                            # read-only data checks (SPEC §7 Data audit)
sadlidp backtest fetch|run|dst-fetch|dst|patch-teams|cross --season YYYY   # archives: store them, test ideas on them
sadlidp gate [--now ISO]             # dry run: this week's windows and whether one is due
sadlidp leakcheck PATH... | --staged REPO
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from . import leakcheck
from .config import DEFAULT_CONFIG, load_alias, load_config
from .schedule import ET


def _now(value: str | None) -> datetime | None:
    if not value:
        return None
    t = datetime.fromisoformat(value)
    return t if t.tzinfo else t.replace(tzinfo=ET)


def _github_output(**values) -> None:
    """Step outputs for the workflow (no-op outside Actions)."""
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as fh:
            for k, v in values.items():
                fh.write(f"{k}={v}\n")


def cmd_sync(args: argparse.Namespace) -> int:
    from .sync import run_sync

    outcome = run_sync(
        load_config(args.config), data_dir=args.data_dir, cache_dir=args.cache_dir, now=_now(args.now), force=args.force
    )
    verb = "synced" if outcome.synced else "skipped"
    print(f"sync: {verb} ({outcome.reason}); season {outcome.season}, week {outcome.week}")
    for key, info in outcome.leagues.items():  # extra leagues (D26): rendered, skipped or failed, never fatal
        print(f"sync: league {key}: {info.get('status')}" + (f" ({info['reason']})" if info.get("reason") else ""))
    _github_output(
        synced=str(outcome.synced).lower(),
        season=outcome.season or "",
        week=outcome.week or "",
        window=outcome.window or ("forced" if args.force else ""),
    )
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    from .site.build import build_all

    print(f"build: wrote {build_all(args.data_dir, args.site_dir, load_config(args.config))}")  # every league (D26)
    return 0


def _weeks(spec: str) -> range:
    first, _, last = spec.partition("-")
    return range(int(first), int(last or first) + 1)


def cmd_backfill(args: argparse.Namespace) -> int:
    """Fetch past weeks from every source with an archive (SPEC §5.3); `--redo` refetches backfilled weeks."""
    from . import backfill, crosswalk
    from .impute import load_or_build_priors
    from .positions import slot_of
    from .sleeper import Sleeper, season_of
    from .sync import DEF_SOURCES, K_SOURCES, SIGNALS, SOURCES, build_matcher, fetch_schedule, make_client

    cfg = load_config(args.config)
    archives = {**SOURCES, **SIGNALS, **K_SOURCES, **DEF_SOURCES}  # the later groups take what budget is left
    if args.source and args.source not in archives:
        print(f"backfill: unknown source {args.source!r}; one of {', '.join(archives)}")
        return 2
    now = datetime.now(ET).replace(microsecond=0)
    client = make_client(cfg)
    sleeper = Sleeper(client, args.cache_dir)
    season = args.season or season_of(sleeper.state(), now.year)
    data_dir = Path(args.data_dir)
    sched, _ = fetch_schedule(client, cfg, season, data_dir)
    target = sched.target_week(now) or max((g.week for g in sched.games), default=0) + 1
    weeks = [w for w in (_weeks(args.weeks) if args.weeks else range(1, target)) if w < target]
    players = {str(k): v for k, v in sleeper.players(now.date().isoformat()).items()}
    tracked = {pid: p for pid, p in players.items() if slot_of(p)}  # IDPs, kickers, DEFs; each source its group's
    priors = load_or_build_priors(sleeper, data_dir, season, players)
    xwalk = crosswalk.load(client, args.cache_dir, now.date().isoformat())
    matcher = build_matcher(cfg, players, xwalk)
    done = backfill.run(
        archives,
        cfg,
        data_dir,
        season,
        weeks,
        now,
        client=client,
        sleeper=sleeper,
        players=tracked,
        matcher=matcher,
        matchers={g: build_matcher(cfg, players, xwalk, group=g) for g in ("k", "def")},
        priors=priors,
        only=args.source,
        redo=args.redo,
        fill_gaps=args.fill_gaps,
        sched=sched,
    )
    written = "; ".join(f"{name} weeks {ws}" for name, ws in done.items()) or "nothing written"
    print(f"backfill: season {season}, weeks before {target}: {written}")
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    """Rebuild past weeks' pages from saved data plus that week's Sleeper matchups (D22)."""
    from .replay import replay_week
    from .sleeper import Sleeper
    from .sync import make_client

    cfg = load_config(args.config)
    weeks = _weeks(args.weeks)
    data_dir = Path(args.data_dir)
    league = json.loads((data_dir / str(args.season) / "league.json").read_text())
    sleeper = Sleeper(make_client(cfg), args.cache_dir)
    players = {str(k): v for k, v in sleeper.players(datetime.now(ET).date().isoformat()).items()}
    for week in weeks:
        matchups, err = sleeper.matchups(league["league_id"], week)
        if not matchups:
            print(f"replay: week {week}: no matchups ({err}); skipped")
            continue
        out = replay_week(cfg, data_dir, args.season, week, matchups, players, log=print if args.fill_frozen else
                          lambda m: None, fill_frozen=args.fill_frozen,
                          refresh_rebuilt=args.refresh_rebuilt)  # fmt: skip
        print(f"replay: week {week} -> {out}")
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    """Read-only integrity checks over a season's saved weeks (SPEC §7). Writes nothing; always exits 0."""
    from . import audit

    cfg = load_config(args.config)
    data_dir = Path(args.data_dir)
    seasons = sorted(int(d.name) for d in data_dir.glob("[0-9]*") if (d / "league.json").exists())
    season = args.season or (seasons[-1] if seasons else datetime.now(ET).year)
    print(audit.report(audit.run(data_dir, season, cfg)))
    return 0


def cmd_tune(args: argparse.Namespace) -> int:
    """Measure the tiers against final scores under candidate z and source splits (read-only)."""
    from .sleeper import Sleeper
    from .sync import make_client
    from .tune import format_report, load_weeks, report

    cfg = load_config(args.config)
    data_dir = Path(args.data_dir)
    league = json.loads((data_dir / str(args.season) / "league.json").read_text())
    sleeper = Sleeper(make_client(cfg), args.cache_dir)
    rostered = {}
    for d in sorted((data_dir / str(args.season)).glob("week*/actuals.json")):
        week = int(d.parent.name.removeprefix("week"))
        matchups, err = sleeper.matchups(league["league_id"], week)
        if matchups:
            rostered[week] = {str(p) for m in matchups for p in m.get("players") or []}
        else:
            print(f"tune: week {week}: no matchups ({err}); skipped")
    weeks = load_weeks(data_dir, args.season, rostered)
    if not weeks:
        print("tune: no week with final stats, saved sources and rosters")
        return 1
    print(format_report(report(weeks, cfg)))
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    """Last season's Sleeper archive: `fetch` stores it compactly, `run` scores the standard checks."""
    from . import backtest, storage
    from .sleeper import Sleeper
    from .sync import make_client

    cfg = load_config(args.config)
    data_dir = Path(args.data_dir)
    season = args.season or datetime.now(ET).year - 1
    # scored under one league's settings: --scoring-season, else the next season's, else the newest saved
    have = sorted(int(d.name) for d in data_dir.glob("[0-9]*") if (d / "league.json").exists())
    scoring_season = args.scoring_season or (season + 1 if season + 1 in have else (have[-1] if have else None))
    league_path = data_dir / str(scoring_season) / "league.json"
    sc = (json.loads(league_path.read_text()) or {}).get("scoring_settings") or {} if league_path.exists() else {}
    if not sc:
        print(f"backtest: no league.json for {scoring_season} to score {season} with")
        return 1
    print(f"backtest: {season} scored under the {scoring_season} league's settings")
    if args.what == "dst-fetch":
        from . import dstbacktest

        print(f"backtest dst: wrote {dstbacktest.fetch(make_client(cfg), data_dir, season, cfg)}")
        return 0
    if args.what == "dst":
        from . import dstbacktest

        rep = dstbacktest.run(data_dir, season, sc)
        if not rep:
            print("backtest dst: no D/ST archive; run `sadlidp backtest dst-fetch` first")
            return 1
        print(dstbacktest.format_report(rep))
        storage.write_json(data_dir / str(season) / "dst_backtest.json", dstbacktest.summary(rep))
        return 0
    if args.what == "patch-teams":
        changed = backtest.patch_teams(Sleeper(make_client(cfg), args.cache_dir), data_dir, season)
        print(f"backtest: {season}: game-time teams set; rows whose team changed: {sum(changed.values())}")
        return 0
    if args.what == "fetch":
        sleeper = Sleeper(make_client(cfg), args.cache_dir)
        players = {str(k): v for k, v in sleeper.players(datetime.now(ET).date().isoformat()).items()}
        written = backtest.fetch(sleeper, data_dir, season, players, sc, redo=args.redo)
        print(f"backtest: {season}: wrote weeks {written or 'none (already archived)'}")
        return 0
    if args.what == "fp-fetch":
        from . import crosswalk
        from .positions import idp_slot_of as slot_of
        from .sync import build_matcher

        client = make_client(cfg)
        sleeper = Sleeper(client, args.cache_dir)
        day = datetime.now(ET).date().isoformat()
        players = {str(k): v for k, v in sleeper.players(day).items()}
        matcher = build_matcher(cfg, players, crosswalk.load(client, args.cache_dir, day))
        idp = {pid: p for pid, p in players.items() if slot_of(p)}
        written = backtest.fp_fetch(client, matcher, data_dir, season, idp, cfg)
        print(f"backtest: FantasyPros {season}: wrote weeks {written or 'none (already stored)'}")
        return 0
    if args.what == "blend":
        rows = backtest.load(data_dir, season, sc)
        for guard in (True, False):
            rep = backtest.blend([dict(r) for r in rows], data_dir, season, guard)
            print(json.dumps(rep, indent=1, default=str))
        return 0
    if args.what == "matchup":
        from . import matchup

        client = make_client(cfg)
        day = datetime.now(ET).date().isoformat()
        aliases = load_alias(cfg, "nflverse_teams")
        rows = []
        for s_ in (season - 2, season - 1, season):
            weekly = matchup.load(client, s_, args.cache_dir, day, aliases)
            if weekly is None:
                print(f"backtest matchup: no play-by-play for {s_}; left out")
                continue
            live = not backtest.archive_dir(data_dir, s_).exists()
            rows += backtest.matchup_rows(data_dir, s_, sc, weekly, live=live)
        print(json.dumps(backtest.matchup_report(rows), indent=1))
        return 0
    if args.what == "cross":
        fit = args.fit_season or season + 1
        test_rows, fit_rows = backtest.load(data_dir, season, sc), backtest.load(data_dir, fit, sc)
        if not test_rows or not fit_rows:
            print(f"backtest cross: need archives for {season} and {fit}")
            return 1
        print(backtest.format_cross(backtest.cross(test_rows, fit_rows, cfg, sc), season, fit))
        return 0
    rows = backtest.load(data_dir, season, sc)
    if not rows:
        print(
            f"backtest: no archive under {backtest.archive_dir(data_dir, season)}; run `sadlidp backtest fetch` first"
        )
        return 1
    print(backtest.format_report(backtest.report(rows, sc), season))
    return 0


def cmd_gate(args: argparse.Namespace) -> int:
    from . import gate, storage
    from .sync import fetch_schedule, make_client

    cfg = load_config(args.config)
    now = _now(args.now) or datetime.now(ET)
    season = args.season or (now.year if now.month >= 3 else now.year - 1)
    season_dir = Path(args.data_dir) / str(season)
    sched, source = fetch_schedule(make_client(cfg), cfg, season, Path(args.data_dir))
    settings = storage.read_json(season_dir / "league.json", {}).get("settings") or {"waiver_day_of_week": 2}
    week = sched.target_week(now)
    if week is None:
        print(f"gate: no upcoming {season} regular-season week (schedule: {source})")
        return 0
    done = set(storage.read_json(season_dir / "gate.json", {}).get("windows_done", []))
    wins = gate.windows(sched, season, week, settings, cfg.get("gate") or {})
    fmt = "%a %b %d %H:%M ET"
    for w in wins:
        state = "done" if w.id in done else "open now" if w.contains(now) else ""
        print(f"{w.id:<22} {w.open.astimezone(ET):{fmt}} -> {w.close.astimezone(ET):{fmt}}  {state}")
    hit = gate.due(now, wins, done)
    print(f"gate: {'due: ' + hit.id if hit else 'nothing due'} at {now.astimezone(ET):{fmt}} (schedule: {source})")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["leakcheck"]:
        return leakcheck.main(argv[1:])
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=str(DEFAULT_CONFIG))
    common.add_argument("--data-dir", default="data")
    parser = argparse.ArgumentParser(prog="sadlidp", description="Weekly IDP streaming tiers for SADL")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("sync", parents=[common], help="gate, fetch, score and tier the target week")
    p.add_argument("--force", action="store_true", help="skip the gate (the manual button)")
    p.add_argument("--cache-dir", default=".cache")
    p.add_argument("--now", help="pretend it is this ISO time (testing)")
    p.set_defaults(func=cmd_sync)
    p = sub.add_parser("build", parents=[common], help="render the latest week into a static site")
    p.add_argument("--site-dir", default="site")
    p.set_defaults(func=cmd_build)
    p = sub.add_parser("gate", parents=[common], help="show this week's sync windows (no Sleeper calls)")
    p.add_argument("--now", help="ISO time to evaluate (default: now)")
    p.add_argument("--season", type=int)
    p.set_defaults(func=cmd_gate)
    p = sub.add_parser("backfill", parents=[common], help="fetch past weeks from the sources' archives")
    p.add_argument("--source", help="only this source (e.g. espn_proj, or sleeper_k for the kickers)")
    p.add_argument("--weeks", help="e.g. 1-5 (default: every week before the target week)")
    p.add_argument("--redo", action="store_true", help="refetch weeks backfilled before (live weeks are kept)")
    p.add_argument("--fill-gaps", action="store_true",
                   help="add archive rows a live week lacks, if the archive matches its live rows")  # fmt: skip
    p.add_argument("--season", type=int)
    p.add_argument("--cache-dir", default=".cache")
    p.set_defaults(func=cmd_backfill)
    p = sub.add_parser("replay", parents=[common], help="rebuild past weeks' pages from saved data")
    p.add_argument("--weeks", required=True, help="e.g. 1-4")
    p.add_argument("--season", type=int, required=True)
    p.add_argument(
        "--fill-frozen",
        action="store_true",
        help="only add rebuilt records for players a live week's frozen record lacks (page kept)",
    )
    p.add_argument("--refresh-rebuilt", action="store_true",
                   help="with --fill-frozen: rebuild the records rebuilt before too (live records kept)")  # fmt: skip
    p.add_argument("--cache-dir", default=".cache")
    p.set_defaults(func=cmd_replay)
    p = sub.add_parser("audit", parents=[common], help="read-only data checks over a season's saved weeks")
    p.add_argument("--season", type=int, help="default: the newest season with a league.json")
    p.set_defaults(func=cmd_audit)
    p = sub.add_parser("tune", parents=[common], help="measure the tiers against final scores (read-only)")
    p.add_argument("--season", type=int, required=True)
    p.add_argument("--cache-dir", default=".cache")
    p.set_defaults(func=cmd_tune)
    p = sub.add_parser("backtest", parents=[common], help="last season's archive: fetch it, run the checks")
    p.add_argument(
        "what", choices=["fetch", "run", "dst-fetch", "dst", "patch-teams", "cross", "fp-fetch", "blend", "matchup"]
    )
    p.add_argument("--fit-season", type=int, help="cross: the season the comparison settings are fitted on")
    p.add_argument("--season", type=int, help="the archived season (default: last year)")
    p.add_argument("--redo", action="store_true", help="refetch weeks already archived")
    p.add_argument("--scoring-season", type=int,
                   help="score with this season's league.json (default: next season's, else newest)")  # fmt: skip
    p.add_argument("--cache-dir", default=".cache")
    p.set_defaults(func=cmd_backtest)
    sub.add_parser("leakcheck", help="scan PATH... or --staged REPO for secrets")
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
