"""Render the probe report as Markdown, answering each SPEC.md §8 Phase 1 'Verify' item."""

from __future__ import annotations

import json

GUESSED_URLS = {
    "razzball_idp_ros",
    "razzball_idp_weekly",
    "razzball_idp_weekly_rankings",
}  # a 404 means wrong URL, not "no source"


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
    out += ["| " + " | ".join(str(c).replace("|", "\\|").replace("\n", " ") for c in r) + " |" for r in rows]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- sections


def _d14(d14: dict) -> str:
    if not d14:
        return "_No D14 data (league not resolved)._\n"
    if d14.get("inconclusive") or d14.get("error"):
        return f"**Inconclusive:** {d14.get('inconclusive') or d14.get('error')}\n"
    rows = []
    for rule, variants in d14.get("rules", {}).items():
        for variant, v in variants.items():
            rows.append([rule, variant, len(v["violations"]), v["max_seen"]])
    md = f"Limits read from settings: `{d14.get('limits')}`\n\n**Verdict:** {d14.get('verdict')}\n\n"
    md += _table(["rule", "roster subset", "rosters over a limit", "max count seen"], rows)
    md += (
        "\nA violation rules a hypothesis out (unless eligibility changed after the add). 0 violations under "
        "several rules is inconclusive unless a *discriminating* roster exists. `all_eligible` counts a "
        "dual-eligible player at every slot he can fill.\n\n"
    )
    disc = d14.get("discriminating") or []
    if disc:
        md += _table(
            ["roster", "subset", "slot", "limit", "counts by rule"],
            [[d["roster_id"], d["subset"], d["slot"], d["limit"], d["counts"]] for d in disc[:30]],
        )
    for rule, variants in d14.get("rules", {}).items():
        viol = variants.get("all", {}).get("violations") or []
        if viol:
            md += _details(f"violations under '{rule}' (all rostered)", viol)
    dual = d14.get("dual_eligible_rostered", [])
    md += f"\nDual-eligible rostered players: **{len(dual)}**\n\n"
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
    md += (
        "\nPlayers whose `position` and `fantasy_positions[0]` map to different slots: "
        f"**{d14.get('players_where_position_and_fantasy_first_disagree')}**\n"
    )
    return md


def _d14_replay(rep: dict) -> str:
    if not rep:
        return ""
    rows = []
    for rule, variants in rep.get("rules", {}).items():
        for v, o in variants.items():
            rows.append([rule, v, o["adds_checked"], o["at_limit"], len(o["over"])])
    md = (
        f"\n### Add-time replay (in-season adds, leg ≥ {rep.get('min_leg')})\n"
        "Sleeper enforces limits when a player is added (trades and the draft can exceed them), so the "
        "enforced rule never shows an add that ends over the limit.\n\n"
        f"**Rules consistent with every add (IR excluded):** {rep.get('consistent_rules_excl_reserve')}\n\n"
    )
    md += _table(["rule", "roster subset", "adds checked", "ended exactly at limit", "ended over limit"], rows)
    for rule, variants in rep.get("rules", {}).items():
        over = variants.get("excl_reserve", {}).get("over") or []
        if over:
            md += _details(f"adds that would be over the limit under '{rule}' (IR excluded)", over)
    return md


def _gt(gt: dict) -> str:
    if not gt:
        return "_No ground-truth data._\n"
    if gt.get("error"):
        return f"Week {gt.get('week')}: **{gt['error']}**\n"
    md = f"Week **{gt.get('week')}**, rostered IDPs with Sleeper points: **{gt.get('rostered_idp_in_players_points')}**\n\n"
    rows = []
    for label in ("v1", "api"):
        g = gt.get(label)
        if g:
            rows.append(
                [
                    label,
                    g["players_compared"],
                    f"{g['nontrivial_match_as_delivered']}/{g['nontrivial_compared']}",
                    f"{g['nontrivial_match_with_derived']}/{g['nontrivial_compared']}",
                    round(g["max_abs_diff_as_delivered"], 3),
                    round(g["max_abs_diff_with_derived"], 3),
                    g.get("bonus_hits"),
                ]
            )
    md += _table(
        [
            "stats endpoint",
            "compared",
            "exact, non-trivial (as delivered)",
            "exact, non-trivial (derived bonuses)",
            "max diff",
            "max diff (derived)",
            "bonus hits",
        ],
        rows,
    )
    md += "\n_Non-trivial = the player scored or recorded a stat; idle bench players match trivially._\n"
    for label in ("v1", "api"):
        if (gt.get(label) or {}).get("mismatches"):
            md += _details(f"mismatches ({label})", gt[label]["mismatches"])
    md += _details("relevant scoring keys (recorded by IDPs that week)", gt.get("relevant_keys"))
    md += _details("bonus consistency", gt.get("bonus_consistency"))
    return md


def _source_rows(pages: dict) -> list[list]:
    rows = []
    for name, rep in pages.items():
        tables = rep.get("tables") or []
        first = tables[0] if tables else {}
        followed = rep.get("followed") or {}
        status = rep.get("status")
        if name in GUESSED_URLS and status == 404:
            status = "404 (guessed URL)"
        rows.append(
            [
                name,
                status,
                rep.get("error") or "",
                len(tables),
                ", ".join((first.get("headers") or [])[:10]),
                first.get("row_count", ""),
                rep.get("week_labels", ""),
                {k: v for k, v in (rep.get("markers") or {}).items() if v},
                (followed.get("final_url") or followed.get("url") or "") if followed else "",
            ]
        )
    return rows


def _fp(fp: dict) -> str:
    login = fp.get("auto_login") or {}
    pages = login.get("login_pages") or {}
    md = (
        f"\n### FantasyPros (structure only)\ncookie configured: **{fp.get('cookie_configured')}** · "
        f"login credentials configured: **{login.get('credentials_configured')}** · "
        f"encryption configured: **{fp.get('encryption_configured')}**"
        + (f" · cookie problem: **{fp['cookie_error']}**" if fp.get("cookie_error") else "")
        + "\n\n#### Auto-login feasibility\n"
    )
    md += _table(
        ["question", "answer"],
        [
            [
                "login page reachable / form found",
                {u: (i.get("status"), bool(i.get("form"))) for u, i in pages.items()},
            ],
            [
                "bot protection on login page",
                {u: {k: v for k, v in (i.get("bot_markers") or {}).items() if v} for u, i in pages.items()},
            ],
            ["login attempted", login.get("attempted")],
            ["login refused (safety check)", login.get("refused")],
            ["login POST", login.get("login_post")],
            ["after login", login.get("after_login")],
            ["**login succeeded**", login.get("login_succeeded")],
        ],
    )
    md += _details("login form fields (names/types only)", {u: i.get("form") for u, i in pages.items()})
    rows = []
    for name, modes in (fp.get("pages") or {}).items():
        for mode, rep in modes.items():
            ecr = rep.get("ecrData") or {}
            rows.append(
                [
                    name,
                    mode,
                    rep.get("status"),
                    rep.get("error") or "",
                    rep.get("final_path"),
                    {k: v for k, v in (rep.get("markers") or {}).items() if v},
                    rep.get("week_labels"),
                    [(t.get("headers") or [])[:8] for t in (rep.get("tables") or [])[:2]],
                    ecr.get("players", ""),
                ]
            )
    md += "\n#### Pages\n" + _table(
        ["page", "mode", "status", "error", "final path", "markers", "week labels", "table headers", "ecrData players"],
        rows,
    )
    return md


def render_markdown(report: dict) -> str:
    s = report.get("sections", {})
    sl = s.get("sleeper") or {}
    lg = sl.get("league") or {}
    md = [f"# Probe report\n\nGenerated {report.get('generated_at')} · {report.get('request_count')} requests\n"]
    if report.get("aborted"):
        md.append(f"**Run aborted early ({report['aborted']}); results below are partial.**\n")
    if report.get("dead_hosts"):
        md.append(f"Hosts skipped after repeated timeouts: {report['dead_hosts']}\n")
    errs = {k: v["error"] for k, v in s.items() if isinstance(v, dict) and isinstance(v.get("error"), str)}
    if errs or sl.get("errors"):
        md.append("## Errors\n" + _details("probe errors", {"sections": errs, "sleeper": sl.get("errors")}))

    md.append("## 1. League\n")
    md.append(
        _table(
            ["field", "value"],
            [
                [
                    "league",
                    f"{lg.get('name')} ({lg.get('league_id')}) status={lg.get('status')} season={lg.get('season')}",
                ],
                ["candidates", (sl.get("league_match") or {}).get("candidates")],
                ["my roster_id", sl.get("my_roster_id")],
                ["roster_positions", lg.get("roster_positions")],
                ["position limits", lg.get("position_limits")],
                ["waiver settings", lg.get("waiver_settings")],
                ["roster fields", sl.get("roster_fields")],
            ],
        )
    )
    md.append(_details("SADL scoring_settings (non-zero)", lg.get("scoring_nonzero")))
    md.append(_details("full league settings", lg.get("settings")))

    md.append("\n## 2. D14: which position counts toward the limit?\n" + _d14(sl.get("d14")))
    md.append(_d14_replay(sl.get("d14_add_time") or {}))

    md.append("\n## 3. Waiver timing\n" + _details("observed waiver processing", sl.get("waivers")))
    md.append(
        _details(
            "failed waiver claims: Sleeper's reasons (D14 evidence if they cite limits)", sl.get("failed_waiver_notes")
        )
    )

    rollover = [[k, v["first"], v["last"], v["n"]] for k, v in (sl.get("rollover") or {}).items()]
    md.append(
        "\n## 4. Week rollover\n"
        + _table(["source", "value"], [[k, v] for k, v in (sl.get("weeks_used") or {}).items()])
        + "\nRollover N→N+1 lies between leg N's last and leg N+1's first free-agent/trade transaction:\n\n"
        + _table(["leg", "first", "last", "n"], rollover)
    )
    md.append(_details("Sleeper /state/nfl", sl.get("state")))

    md.append("\n## 5. Injury codes (IDP players on NFL teams)\n" + _details("tallies", sl.get("injuries")))

    md.append("\n## 6. Scoring ground truth and bonus semantics\n" + _gt(sl.get("ground_truth") or {}))
    if sl.get("ground_truth_prev_week"):
        md.append(
            "\nNo threshold-bonus hit that week, so the previous week was checked too:\n\n"
            + _gt(sl["ground_truth_prev_week"])
        )

    proj = sl.get("projections") or {}
    md.append(f"\n## 7. Sleeper projections (week {proj.get('week')})\n")
    if proj.get("error"):
        md.append(f"**{proj['error']}**\n")
    rows = []
    for variant in ("slot_positions", "granular_positions"):
        p = proj.get(variant) or {}
        rows.append(
            [
                variant,
                p.get("error") or p.get("rows"),
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
    md.append(
        _table(
            [
                "source",
                "status",
                "error",
                "tables",
                "first table headers",
                "rows",
                "week labels",
                "markers",
                "followed",
            ],
            _source_rows(s.get("public_pages") or {}) + _source_rows(s.get("index_pages") or {}),
        )
    )
    md.append(
        _details(
            "index pages: matching same-site links",
            {k: v.get("matching_links") for k, v in (s.get("index_pages") or {}).items()},
        )
    )
    md.append(_details("ESPN fantasy API (IDP slots)", s.get("espn_api")))
    md.append(_fp(s.get("fantasypros") or {}))
    md.append("\n### The Odds API\n" + _details("props availability", s.get("odds_api")))
    return "\n".join(md)
