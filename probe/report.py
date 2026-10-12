"""Render the probe report as Markdown, answering each SPEC.md §8 Phase 1 'Verify' item."""

from __future__ import annotations

import json


def stringify_keys(obj):
    """JSON-safe copy with string keys (tallies can have None/int keys, which break sort_keys)."""
    if isinstance(obj, dict):
        return {("null" if k is None else str(k)): stringify_keys(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [stringify_keys(v) for v in obj]
    if isinstance(obj, set):
        return sorted(stringify_keys(v) for v in obj)
    return obj


def _j(obj, limit: int = 4000) -> str:
    text = json.dumps(stringify_keys(obj), indent=1, sort_keys=True, default=str)
    return text if len(text) <= limit else text[:limit] + "\n… (truncated; see report.json)"


def _details(summary: str, obj) -> str:
    return f"<details><summary>{summary}</summary>\n\n```json\n{_j(obj)}\n```\n</details>\n"


def _table(headers: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(str(c).replace("|", "\\|") for c in r) + " |" for r in rows]
    return "\n".join(out) + "\n"


def _d14(d14: dict) -> str:
    if not d14:
        return "_No D14 data (league not resolved)._\n"
    rows = []
    for rule, variants in d14.get("rules", {}).items():
        for variant, v in variants.items():
            rows.append([rule, variant, len(v["violations"]), v["max_seen"]])
    md = f"Limits read from settings: `{d14.get('limits')}`\n\n"
    md += _table(["rule", "roster subset", "rosters over a limit", "max count seen"], rows)
    md += (
        "\nA rule that Sleeper enforces should show **0** violations; a rule with violations is not "
        "the one Sleeper counts (unless eligibility changed after the add). Identical results mean the "
        "rosters don't discriminate between the rules.\n\n"
    )
    dual = d14.get("dual_eligible_rostered", [])
    md += f"Dual-eligible rostered players: **{len(dual)}**\n\n"
    if dual:
        md += _table(
            ["roster", "player", "team", "position", "fantasy_positions", "reserve", "taxi"],
            [
                [
                    d["roster_id"],
                    d["name"],
                    d["team"],
                    d["position"],
                    d["fantasy_positions"],
                    d["in_reserve"],
                    d["in_taxi"],
                ]
                for d in dual
            ],
        )
    md += _details(
        "fantasy_positions orderings among dual-eligible players", d14.get("dual_fantasy_positions_orderings")
    )
    md += f"\nPlayers whose `position` and `fantasy_positions[0]` map to different slots: **{d14.get('players_where_position_and_fantasy_first_disagree')}**\n"
    return md


def _gt(gt: dict) -> str:
    if not gt:
        return "_No ground-truth data._\n"
    md = f"Week **{gt.get('week')}**, rostered IDPs with Sleeper points: **{gt.get('rostered_idp_in_players_points')}**\n\n"
    rows = []
    for label in ("v1", "api"):
        g = gt.get(label)
        if g:
            rows.append(
                [
                    label,
                    g["players_compared"],
                    g["match_as_delivered"],
                    g["match_with_derived_bonuses"],
                    round(g["max_abs_diff_as_delivered"], 3),
                    round(g["max_abs_diff_with_derived"], 3),
                ]
            )
    md += _table(
        [
            "stats endpoint",
            "compared",
            "exact (as delivered)",
            "exact (derived bonuses)",
            "max diff",
            "max diff (derived)",
        ],
        rows,
    )
    for label in ("v1", "api"):
        if gt.get(label, {}).get("mismatches"):
            md += _details(f"mismatches ({label})", gt[label]["mismatches"])
    return md


def _source_rows(pages: dict) -> list[list]:
    rows = []
    for name, rep in pages.items():
        tables = rep.get("tables") or []
        first = tables[0] if tables else {}
        followed = rep.get("followed") or {}
        rows.append([
            name, rep.get("status"), rep.get("error") or "", len(tables),
            ", ".join(first.get("headers", [])[:10]), first.get("row_count", ""),
            rep.get("week_labels", ""), {k: v for k, v in (rep.get("markers") or {}).items() if v},
            followed.get("final_url", "") if followed else "",
        ])  # fmt: skip
    return rows


def render_markdown(report: dict) -> str:
    s = report.get("sections", {})
    sl = s.get("sleeper") or {}
    lg = sl.get("league") or {}
    md = [f"# Probe report\n\nGenerated {report.get('generated_at')} · {report.get('request_count')} requests\n"]

    errs = {k: v["error"] for k, v in s.items() if isinstance(v, dict) and "error" in v and isinstance(v["error"], str)}
    if errs or sl.get("errors"):
        md.append("## Errors\n" + _details("probe errors", {"sections": errs, "sleeper": sl.get("errors")}))

    md.append("## 1. League\n")
    md.append(_table(["field", "value"], [
        ["league", f"{lg.get('name')} ({lg.get('league_id')}) status={lg.get('status')} season={lg.get('season')}"],
        ["candidates", (sl.get("league_match") or {}).get("candidates")],
        ["my roster_id", sl.get("my_roster_id")],
        ["roster_positions", lg.get("roster_positions")],
        ["position limits", lg.get("position_limits")],
        ["waiver settings", lg.get("waiver_settings")],
        ["roster fields", sl.get("roster_fields")],
    ]))  # fmt: skip
    md.append(_details("SADL scoring_settings (non-zero)", lg.get("scoring_nonzero")))
    md.append(_details("full league settings", lg.get("settings")))

    md.append("\n## 2. D14 — which position counts toward the limit?\n" + _d14(sl.get("d14")))
    md.append("\n## 3. Waiver timing\n" + _details("observed waiver processing", sl.get("waivers")))
    md.append(
        "\n## 4. Week rollover\n"
        + _table(["source", "value"], [[k, v] for k, v in (sl.get("weeks_used") or {}).items()])
    )
    md.append(_details("Sleeper /state/nfl", sl.get("state")))
    md.append("\n## 5. Injury codes (IDP players on NFL teams)\n" + _details("tallies", sl.get("injuries")))
    md.append("\n## 6. Scoring ground truth and bonus semantics\n" + _gt(sl.get("ground_truth") or {}))
    md.append(_details("relevant scoring keys (recorded by IDPs that week)", sl.get("relevant_keys")))
    md.append(_details("bonus consistency", sl.get("bonus_consistency")))

    proj = sl.get("projections") or {}
    md.append(f"\n## 7. Sleeper projections (week {proj.get('week')})\n")
    rows = []
    for variant in ("slot_positions", "granular_positions"):
        p = proj.get(variant) or {}
        rows.append(
            [
                variant,
                p.get("rows"),
                p.get("with_gp"),
                p.get("with_opponent"),
                p.get("companies"),
                p.get("positions"),
                p.get("idp_tkl_solo_noninteger_share"),
            ]
        )
    md.append(
        _table(["query", "rows", "with gp", "with opponent", "company", "positions", "non-integer solo share"], rows)
    )
    md.append(_details("key fill rates (slot query)", (proj.get("slot_positions") or {}).get("key_fill_rates")))
    md.append(
        "\n### Past-week projections (week 1)\n" + _details("week 1 retrievability", sl.get("past_projections_week1"))
    )
    md.append(_details("stats endpoints", sl.get("stats_endpoints")))

    md.append("\n## 8. Schedule and ID crosswalk\n")
    md.append(_details("nflverse schedule", s.get("schedule")))
    md.append(_details("DynastyProcess crosswalk", s.get("crosswalk")))

    md.append("\n## 9. Candidate sources\n")
    md.append(_table(
        ["source", "status", "error", "tables", "first table headers", "rows", "week labels", "markers", "followed"],
        _source_rows(s.get("public_pages") or {}) + _source_rows(s.get("index_pages") or {}),
    ))  # fmt: skip
    md.append(_details("ESPN fantasy API (IDP slots)", s.get("espn_api")))

    fp = s.get("fantasypros") or {}
    md.append(
        f"\n### FantasyPros (structure only)\ncookie configured: **{fp.get('cookie_configured')}** · "
        f"encryption configured: **{fp.get('encryption_configured')}**\n\n"
    )
    rows = []
    for name, modes in (fp.get("pages") or {}).items():
        for mode, rep in modes.items():
            ecr = rep.get("ecrData") or {}
            rows.append(
                [
                    name,
                    mode,
                    rep.get("status"),
                    rep.get("final_path"),
                    {k: v for k, v in (rep.get("markers") or {}).items() if v},
                    rep.get("week_labels"),
                    [t["headers"][:8] for t in (rep.get("tables") or [])[:2]],
                    ecr.get("players", ""),
                ]
            )
    md.append(
        _table(
            ["page", "mode", "status", "final path", "markers", "week labels", "table headers", "ecrData players"], rows
        )
    )
    md.append("\n### The Odds API\n" + _details("props availability", s.get("odds_api")))
    return "\n".join(md)
