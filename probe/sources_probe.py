"""Candidate-source probes (SPEC.md §5.2) plus schedule and ID crosswalk.

FantasyPros is private (D16): only a structure report is produced, never values,
names, page text or headers; raw bodies are kept in memory and, if an age key is
configured, written only as age-encrypted files.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.parse
from collections import Counter
from pathlib import Path

from . import analysis as an
from . import structure as st
from .phttp import Client

PAGE_MARKERS = {
    "login_prompt": r"\b(sign|log)\s?in\b",
    "logout_link": r"\b(sign|log)\s?out\b|/logout",
    "paywall_or_upgrade": r"\bupgrade\b|\bgo premium\b|\bsubscribe\b|\bunlock\b|premium[-_ ]?(lock|only|content)",
    "bot_challenge": r"cf-challenge|captcha|attention required|just a moment|access denied",
}
SCRIPT_MARKERS = {
    "next_data": r"__NEXT_DATA__",
    "nuxt": r"__NUXT__",
    "ecrData": r"\becrData\b",
    "json_ld": r"application/ld\+json",
}
FP_PAGES = {
    "rankings_idp": "https://www.fantasypros.com/nfl/rankings/idp.php",
    "rankings_dl": "https://www.fantasypros.com/nfl/rankings/dl.php",
    "rankings_lb": "https://www.fantasypros.com/nfl/rankings/lb.php",
    "rankings_db": "https://www.fantasypros.com/nfl/rankings/db.php",
    "projections_idp": "https://www.fantasypros.com/nfl/projections/idp.php",
    "projections_dl": "https://www.fantasypros.com/nfl/projections/dl.php",
    "projections_lb": "https://www.fantasypros.com/nfl/projections/lb.php",
    "projections_db": "https://www.fantasypros.com/nfl/projections/db.php",
}
PUBLIC_PAGES = {
    "fantasynerds_idp_weekly": "https://www.fantasynerds.com/nfl/idp-weekly-projections",
    "razzball_idp_ros": "https://football.razzball.com/projections-idp-restofseason/",
    "razzball_idp_weekly": "https://football.razzball.com/projections-idp-weekly/",
    "espn_idp_rankings_article": "https://www.espn.com/fantasy/football/story/_/page/FFWeeklyPlayerRank26-49804332/nfl-fantasy-football-rankings-2026-idp",
}
INDEX_PAGES = {
    # name: (index url, link regex) -> follow the first matching weekly link
    "idpguru": ("https://idpguru.com/", r"week.*(projection|ranking)|(projection|ranking).*week"),
    "dynastynerds_idp": ("https://www.dynastynerds.com/idp/", r"week[-\s]?\d+.*(idp|rank|start)"),
    "rotoballer_idp": ("https://www.rotoballer.com/?s=idp+rankings+week", r"idp.*rank.*week|week.*idp.*rank"),
    "thehuddle_idp": ("https://www.thehuddle.com/", r"idp"),
}
SCHEDULE_URLS = [
    "https://github.com/nflverse/nfldata/raw/master/data/games.csv",
    "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv",
]
CROSSWALK_URL = "https://github.com/dynastyprocess/data/raw/master/files/db_playerids.csv"
ESPN_URL = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leaguedefaults/3?view=kona_player_info"
ESPN_IDP_SLOT_IDS = [8, 9, 10, 11, 12, 13, 14, 15]  # DT, DE, LB, DL, CB, S, DB, DP (to be verified)
ODDS_BASE = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl"
ODDS_MARKETS = "player_tackles_assists,player_solo_tackles,player_sacks"


def _req_summary(r) -> dict:
    return {
        "url": r.url,
        "final_url": r.final_url,
        "status": r.status,
        "error": r.error,
        "bytes": len(r.body),
        "content_type": r.headers.get("content-type"),
    }


def _is_html(r) -> bool:
    return "html" in (r.headers.get("content-type") or "") or r.body[:200].lstrip().lower().startswith(
        (b"<!doctype", b"<html")
    )


# ---------------------------------------------------------------- public pages


def _fetch_public_page(client: Client, url: str) -> tuple[dict, str | None]:
    r = client.get(url)
    rep = _req_summary(r)
    if not (r.ok and _is_html(r)):
        return rep, None
    html = r.text()
    rep["markers"] = st.markers(html, PAGE_MARKERS)
    rep["script_markers"] = st.markers(html, SCRIPT_MARKERS)
    rep["week_labels"] = st.week_labels(html)[:10]
    rep["tables"] = st.html_tables(html)[:10]
    return rep, html


def probe_public_page(client: Client, url: str) -> tuple[dict, list | None]:
    rep, html = _fetch_public_page(client, url)
    return rep, (st.table_excerpts(html)[:5] if html else None)


def probe_index(client: Client, url: str, link_rx: str) -> tuple[dict, list | None]:
    rep, html = _fetch_public_page(client, url)
    links = st.matching_links(html, link_rx) if html else []
    for link in links:
        link["href"] = urllib.parse.urljoin(url, link["href"])
    rep["matching_links"] = links
    if not links:
        return rep, None
    follow, excerpt = probe_public_page(client, links[0]["href"])
    rep["followed"] = follow
    return rep, excerpt


# ---------------------------------------------------------------- FantasyPros (private)


def age_recipient() -> str | None:
    key = os.environ.get("FP_DATA_KEY", "").strip()
    if not key or not shutil.which("age-keygen"):
        return None
    res = subprocess.run(["age-keygen", "-y"], input=key, capture_output=True, text=True, check=False)
    out = res.stdout.strip().splitlines()
    return out[0] if res.returncode == 0 and out and out[0].startswith("age1") else None


def age_encrypt(data: bytes, recipient: str, path: Path) -> bool:
    if not shutil.which("age"):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    res = subprocess.run(["age", "-r", recipient, "-o", str(path)], input=data, capture_output=True, check=False)
    return res.returncode == 0


def fp_structure(html: str) -> dict:
    """Structure only: markers, week labels, table headers/row counts, embedded-JSON shape."""
    rep = {
        "markers": st.markers(html, PAGE_MARKERS),
        "script_markers": st.markers(html, SCRIPT_MARKERS),
        "week_labels": st.week_labels(html)[:10],
        "tables": [{"headers": t["headers"], "row_count": t["row_count"]} for t in st.html_tables(html)[:10]],
    }
    ecr = st.find_js_json(html, "ecrData")
    if isinstance(ecr, dict):
        players = ecr.get("players") or []
        rep["ecrData"] = {
            "top_level_keys": sorted(ecr.keys()),
            "players": len(players),
            "player_keys": sorted({k for p in players[:50] if isinstance(p, dict) for k in p}),
            "player_position_counts": dict(
                Counter(p.get("player_position_id") for p in players if isinstance(p, dict)).most_common()
            ),
            "scalar_meta": {
                k: ecr[k]
                for k in ("week", "year", "scoring", "position_id", "ranking_type_name", "total_experts")
                if k in ecr and isinstance(ecr[k], (int, str))
            },
        }
    return rep


def probe_fantasypros(client: Client, out_dir: Path) -> dict:
    cookie = os.environ.get("FP_COOKIE", "").strip()
    recipient = age_recipient()
    sec: dict = {
        "cookie_configured": bool(cookie),
        "encryption_configured": bool(recipient),
        "pages": {},
    }
    for name, url in FP_PAGES.items():
        page: dict = {}
        modes = [("public", {})] + ([("authenticated", {"Cookie": cookie})] if cookie else [])
        for mode, headers in modes:
            r = client.get(url, headers=headers)
            rep = {
                "status": r.status,
                "error": r.error,
                "final_path": urllib.parse.urlsplit(r.final_url).path,
                "bytes": len(r.body),
            }
            if r.ok and _is_html(r):
                rep.update(fp_structure(r.text()))
                if mode == "authenticated" and recipient:
                    rep["encrypted_sample"] = age_encrypt(
                        r.body, recipient, out_dir / "encrypted" / f"fantasypros_{name}.html.age"
                    )
            page[mode] = rep
            del r
        sec["pages"][name] = page
    return sec


# ---------------------------------------------------------------- ESPN, Odds


def probe_espn(client: Client, season: int, week: int | None) -> tuple[dict, dict | None]:
    sec: dict = {}
    sample = None
    filters = {
        "players": {
            "filterSlotIds": {"value": ESPN_IDP_SLOT_IDS},
            "filterStatsForTopScoringPeriodIds": {"value": 3, "additionalValue": [f"00{season}", f"10{season}"]},
            "sortPercOwned": {"sortAsc": False, "sortPriority": 1},
            "limit": 150,
        }
    }
    for label, extra in (("default", ""), ("scoring_period", f"&scoringPeriodId={week}" if week else "")):
        r = client.get(ESPN_URL.format(season=season) + extra, headers={"X-Fantasy-Filter": json.dumps(filters)})
        rep = _req_summary(r)
        if r.ok:
            try:
                payload = r.json()
                rep["shape"] = an.espn_shape(payload, week or 1)
                rep["key_paths"] = st.json_key_paths(payload, max_depth=5, max_paths=120)
                if label == "default":
                    sample = {"players": (payload.get("players") or [])[:5]} if isinstance(payload, dict) else None
            except ValueError:
                rep["error"] = "not JSON"
        sec[label] = rep
    return sec, sample


def probe_odds(client: Client) -> dict:
    key = os.environ.get("ODDS_API_KEY", "").strip()
    if not key:
        return {"status": "skipped", "reason": "ODDS_API_KEY not configured"}
    sec: dict = {}
    r = client.get(f"{ODDS_BASE}/events?apiKey={key}", redact=("apiKey",))
    sec["events"] = _req_summary(r) | {"quota": {k: v for k, v in r.headers.items() if k.startswith("x-requests")}}
    try:
        events = r.json() if r.ok else []
    except ValueError:
        events = []
    sec["events"]["count"] = len(events)
    if not events:
        return sec
    ev = events[0]
    r = client.get(
        f"{ODDS_BASE}/events/{ev['id']}/odds?apiKey={key}&regions=us&markets={ODDS_MARKETS}&oddsFormat=american",
        redact=("apiKey",),
    )
    rep = _req_summary(r) | {
        "commence_time": ev.get("commence_time"),
        "quota": {k: v for k, v in r.headers.items() if k.startswith("x-requests")},
    }
    if r.ok:
        books = (r.json() or {}).get("bookmakers") or []
        per_market: dict = {}
        for b in books:
            for m in b.get("markets") or []:
                pm = per_market.setdefault(m.get("key"), {"bookmakers": 0, "outcomes": 0, "players": set()})
                pm["bookmakers"] += 1
                pm["outcomes"] += len(m.get("outcomes") or [])
                pm["players"].update(o.get("description") for o in m.get("outcomes") or [])
        rep["markets"] = {
            k: {"bookmakers": v["bookmakers"], "outcomes": v["outcomes"], "players": len(v["players"])}
            for k, v in per_market.items()
        }
    sec["event_odds"] = rep
    return sec


# ---------------------------------------------------------------- schedule, crosswalk


def probe_schedule(client: Client, season: int, now) -> tuple[dict, list[dict]]:
    for url in SCHEDULE_URLS:
        r = client.get(url)
        if r.ok:
            rows = an.parse_csv(r.text())
            return {"source": _req_summary(r)} | an.schedule_summary(rows, season, now), rows
    return {"source": _req_summary(r), "error": "schedule unavailable"}, []


def probe_crosswalk(client: Client) -> dict:
    r = client.get(CROSSWALK_URL)
    rep = _req_summary(r)
    if r.ok:
        rep |= an.crosswalk_coverage(an.parse_csv(r.text()))
    return rep


def safe_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", name.lower())
