"""Phase 1 probe entry point (SPEC.md §8).

    python -m probe.probe --out out

Writes out/report.json, out/report.md, out/samples/* (public data only) and, when an
age key is configured, out/encrypted/*.age. Exits 0 even when individual probes fail:
the report is the deliverable.
"""

from __future__ import annotations

import argparse
import json
import traceback
from datetime import datetime, timezone
from pathlib import Path

from . import leakcheck
from . import sources_probe as sp
from .phttp import Client
from .report import render_markdown, stringify_keys
from .sleeper_probe import SleeperProbe


def _guarded(report: dict, name: str, fn, *args):
    try:
        return fn(*args)
    except Exception as e:  # noqa: BLE001 - one failing probe must not stop the others
        # Type and location only: exception messages can echo header values (e.g. a cookie).
        tb = traceback.extract_tb(e.__traceback__)[-1]
        report["sections"][name] = {"error": f"{type(e).__name__} ({Path(tb.filename).name}:{tb.lineno})"}
        return None


def redact_secrets(text: str) -> str:
    """Belt and braces: replace any secret (and its escaped forms) that slipped into the output."""
    for values in leakcheck.secret_values().values():
        for v in sorted(values, key=len, reverse=True):
            text = text.replace(v.decode("utf-8", errors="replace"), "REDACTED")
    return text


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(redact_secrets(json.dumps(stringify_keys(obj), indent=1, sort_keys=True, default=str)) + "\n")


def run(args) -> dict:
    now = datetime.fromisoformat(args.now) if args.now else datetime.now(timezone.utc)
    season_hint = now.year if now.month >= 3 else now.year - 1
    out = Path(args.out)
    client = Client()
    report: dict = {"generated_at": now.isoformat(), "probe_version": 2, "sections": {}}
    samples: dict[str, object] = {}
    try:
        _run_probes(args, now, season_hint, out, client, report, samples)
    except BaseException as e:
        report["aborted"] = type(e).__name__
        if not isinstance(e, Exception):
            _write(out, report, samples, client)
            raise
    _write(out, report, samples, client)
    return report


def _write(out: Path, report: dict, samples: dict, client: Client) -> None:
    report["request_count"] = client.request_count
    report["dead_hosts"] = sorted(client.dead_hosts)
    write_json(out / "report.json", report)
    for name, obj in samples.items():
        write_json(out / "samples" / name, obj)
    (out / "report.md").write_text(redact_secrets(render_markdown(report)))


def _run_probes(args, now, season_hint, out, client, report, samples) -> None:
    secs = report["sections"]
    res = _guarded(report, "schedule", sp.probe_schedule, client, season_hint, now)
    if res:
        secs["schedule"], sched_rows = res
        samples["schedule_season.json"] = [r for r in sched_rows if str(r.get("season")) == str(season_hint)]
    target_week = (secs.get("schedule") or {}).get("target_week")
    settled_week = (secs.get("schedule") or {}).get("settled_week")

    res = _guarded(report, "crosswalk", sp.probe_crosswalk, client)
    if res is not None:
        secs["crosswalk"] = res

    sleeper = SleeperProbe(client)
    res = _guarded(
        report, "sleeper", sleeper.run, args.username, args.league_name, season_hint, target_week, settled_week
    )
    if res:
        secs["sleeper"], sl_samples = res
        samples.update({f"sleeper_{k}": v for k, v in sl_samples.items()})
    sl = secs.get("sleeper") or {}
    season = sl.get("season") or season_hint
    week = target_week or (sl.get("weeks_used") or {}).get("week_used")

    res = _guarded(report, "espn_api", sp.probe_espn, client, season, week)
    if res:
        secs["espn_api"], espn_sample = res
        if espn_sample:
            samples["espn_api_sample.json"] = espn_sample

    secs["public_pages"] = {}
    for name, url in sp.PUBLIC_PAGES.items():
        res = _guarded(report, f"page_{name}", sp.probe_public_page, client, url)
        if res:
            secs["public_pages"][name], excerpt = res
            if excerpt:
                samples[f"excerpt_{sp.safe_slug(name)}.json"] = excerpt
    secs["index_pages"] = {}
    for name, (url, rx) in sp.INDEX_PAGES.items():
        res = _guarded(report, f"index_{name}", sp.probe_index, client, url, rx)
        if res:
            secs["index_pages"][name], excerpt = res
            if excerpt:
                samples[f"excerpt_{sp.safe_slug(name)}.json"] = excerpt

    res = _guarded(report, "fantasypros", sp.probe_fantasypros, client, out)
    if res is not None:
        secs["fantasypros"] = res
    res = _guarded(report, "odds_api", sp.probe_odds, client, now)
    if res is not None:
        secs["odds_api"] = res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="out")
    ap.add_argument("--username", default="lbburnsy")
    ap.add_argument("--league-name", action="append", default=None)
    ap.add_argument("--now", help="ISO timestamp override (testing)")
    args = ap.parse_args(argv)
    args.league_name = args.league_name or ["SADL", "Slow Ass Draft League"]
    report = run(args)
    print(f"probe: {report['request_count']} requests; report at {Path(args.out) / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
