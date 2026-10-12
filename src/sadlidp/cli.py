"""Command line: the workflows call these (SPEC.md §4.3, §7).

sadlidp sync [--force] [--now ISO]   # gate, fetch, score, tier; writes data/<season>/week<NN>/
sadlidp build                        # render the latest week into site/
sadlidp gate [--now ISO]             # dry run: this week's windows and whether one is due
sadlidp leakcheck PATH... | --staged REPO
"""

from __future__ import annotations

import argparse
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

    print(f"build: wrote {build_site(args.data_dir, args.site_dir)}")
    return 0


def cmd_gate(args: argparse.Namespace) -> int:
    from . import gate, storage
    from .net import Client
    from .sync import fetch_schedule

    cfg = load_config(args.config)
    now = _now(args.now) or datetime.now(ET)
    season = args.season or (now.year if now.month >= 3 else now.year - 1)
    season_dir = Path(args.data_dir) / str(season)
    sched, source = fetch_schedule(Client(), cfg, season, Path(args.data_dir))
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
    sub.add_parser("leakcheck", help="scan PATH... or --staged REPO for secrets")
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
