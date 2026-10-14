"""Command line: the workflows call these (SPEC.md §4.3, §7).

sadlidp sync [--force] [--now ISO]   # gate, fetch, score, tier; writes data/<season>/week<NN>/
sadlidp build                        # render the latest week into site/
sadlidp backfill [--source NAME] [--weeks A-B] [--redo]   # past weeks from the sources' archives (§5.3)
sadlidp replay --weeks A-B --season YYYY                 # rebuild past weeks' pages from saved data
sadlidp replay --weeks 5 --season YYYY --fill-frozen     # add missing frozen records to a live week
sadlidp tune --season YYYY                               # re-tier past weeks under candidate settings
sadlidp backtest fetch|run|dst-fetch|dst --season YYYY   # last season's archive: store it, test ideas on it
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
from .config import DEFAULT_CONFIG, load_config
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
    _github_output(
        synced=str(outcome.synced).lower(),
        season=outcome.season or "",
        week=outcome.week or "",
        window=outcome.window or ("forced" if args.force else ""),
    )
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    from .site.build import build_site

    print(f"build: wrote {build_site(args.data_dir, args.site_dir, load_config(args.config))}")
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
    from .sync import SIGNALS, SOURCES, build_matcher, fetch_schedule, make_client

    cfg = load_config(args.config)
    archives = {**SOURCES, **SIGNALS}
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
    idp = {pid: p for pid, p in players.items() if slot_of(p)}
    priors = load_or_build_priors(sleeper, data_dir, season, players)
    matcher = build_matcher(cfg, players, crosswalk.load(client, args.cache_dir, now.date().isoformat()))
    done = backfill.run(
        archives,
        cfg,
        data_dir,
        season,
        weeks,
        now,
        client=client,
        sleeper=sleeper,
        players=idp,
        matcher=matcher,
        priors=priors,
        only=args.source,
        redo=args.redo,
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
                          lambda m: None, fill_frozen=args.fill_frozen)  # fmt: skip
        print(f"replay: week {week} -> {out}")
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
    sc = (json.loads((data_dir / str(season + 1) / "league.json").read_text()) or {}).get("scoring_settings") or {}
    if not sc:
        print(f"backtest: no league.json for {season + 1} to score {season} with")
        return 1
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
    if args.what == "fetch":
        sleeper = Sleeper(make_client(cfg), args.cache_dir)
        players = {str(k): v for k, v in sleeper.players(datetime.now(ET).date().isoformat()).items()}
        written = backtest.fetch(sleeper, data_dir, season, players, sc, redo=args.redo)
        print(f"backtest: {season}: wrote weeks {written or 'none (already archived)'}")
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
    p.add_argument("--source", help="only this source (e.g. espn_proj)")
    p.add_argument("--weeks", help="e.g. 1-5 (default: every week before the target week)")
    p.add_argument("--redo", action="store_true", help="refetch weeks backfilled before (live weeks are kept)")
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
    p.add_argument("--cache-dir", default=".cache")
    p.set_defaults(func=cmd_replay)
    p = sub.add_parser("tune", parents=[common], help="measure the tiers against final scores (read-only)")
    p.add_argument("--season", type=int, required=True)
    p.add_argument("--cache-dir", default=".cache")
    p.set_defaults(func=cmd_tune)
    p = sub.add_parser("backtest", parents=[common], help="last season's archive: fetch it, run the checks")
    p.add_argument("what", choices=["fetch", "run", "dst-fetch", "dst"])
    p.add_argument("--season", type=int, help="the archived season (default: last year)")
    p.add_argument("--redo", action="store_true", help="refetch weeks already archived")
    p.add_argument("--cache-dir", default=".cache")
    p.set_defaults(func=cmd_backtest)
    sub.add_parser("leakcheck", help="scan PATH... or --staged REPO for secrets")
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
