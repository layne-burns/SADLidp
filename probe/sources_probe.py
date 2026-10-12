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
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import analysis as an
from . import structure as st
from .phttp import Client, base_domain

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
FP_LOGIN_URLS = [
    "https://secure.fantasypros.com/accounts/login/",
    "https://www.fantasypros.com/accounts/login/",
]
LOGIN_BOT_MARKERS = {
    "recaptcha": r"g-recaptcha|recaptcha/api\.js|grecaptcha",
    "hcaptcha": r"hcaptcha",
    "turnstile": r"challenges\.cloudflare\.com/turnstile|cf-turnstile",
    "cf_challenge": r"cf-challenge|cf_chl_|just a moment",
}
PRIVATE_DOMAINS = {"fantasypros.com"}
PUBLIC_PAGES = {
    "fantasynerds_idp_weekly": "https://www.fantasynerds.com/nfl/idp-weekly-projections",
    "razzball_idp_ros": "https://football.razzball.com/projections-idp-restofseason/",
    "razzball_idp_weekly": "https://football.razzball.com/projections-idp-weekly/",
    "espn_idp_rankings_article": "https://www.espn.com/fantasy/football/story/_/page/FFWeeklyPlayerRank26-49804332/nfl-fantasy-football-rankings-2026-idp",
}
GUESSED_URLS = {"razzball_idp_ros", "razzball_idp_weekly"}  # a 404 here means "wrong URL", not "no source"
INDEX_PAGES = {
    # name: (index url, link regex) -> follow the first matching same-site link
    "idpguru": ("https://idpguru.com/", r"week.*(projection|ranking)|(projection|ranking).*week"),
    "dynastynerds_idp": ("https://www.dynastynerds.com/idp/", r"week[-\s]?\d+.*(idp|rank|start)"),
    "rotoballer_idp": ("https://www.rotoballer.com/?s=idp+rankings+week", r"idp.*rank.*week|week.*idp.*rank"),
    "thehuddle_idp": ("https://www.thehuddle.com/", r"week[-\s]?\d+.*idp|idp.*week[-\s]?\d+"),
    "razzball_idp": ("https://football.razzball.com/", r"idp|individual[- ]defen"),
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


def _host_domain(url: str) -> str:
    return base_domain(urllib.parse.urlsplit(url).hostname or "")


def _fetch_public_page(client: Client, url: str) -> tuple[dict, str | None]:
    r = client.get(url)
    rep = _req_summary(r)
    if _host_domain(r.final_url) in PRIVATE_DOMAINS:
        return {"url": r.url, "status": r.status, "error": "redirected to a private source; not inspected"}, None
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
    links = st.matching_links(html, link_rx, limit=20) if html else []
    for link in links:
        link["href"] = urllib.parse.urljoin(url, link["href"])
    site = _host_domain(url)
    same_site = [
        lk
        for lk in links
        if urllib.parse.urlsplit(lk["href"]).scheme in ("http", "https") and _host_domain(lk["href"]) == site
    ]
    rep["matching_links"] = same_site[:10]
    rep["offsite_matches_ignored"] = len(links) - len(same_site)
    if not same_site:
        return rep, None
    href = urllib.parse.quote(same_site[0]["href"], safe=":/?#[]@!$&'()*+,;=%")
    follow, excerpt = probe_public_page(client, href)
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
    """Structure only: markers, week labels, table headers/row counts, embedded-JSON shape.

    Header labels are reported only for all-<th> header rows; anything else may be data.
    """
    rep = {
        "markers": st.markers(html, PAGE_MARKERS),
        "script_markers": st.markers(html, SCRIPT_MARKERS),
        "week_labels": st.week_labels(html)[:10],
        "tables": [
            {
                "headers": t["headers"] if t["headers_all_th"] else None,
                "header_cells": len(t["headers"]),
                "row_count": t["row_count"],
            }
            for t in st.html_tables(html)[:10]
        ],
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


def fp_cookie(env=None) -> tuple[str, str | None]:
    """FP_COOKIE normalised to one header value, or ("", reason) if unusable. Never echoes it."""
    raw = (os.environ if env is None else env).get("FP_COOKIE", "")
    cookie = "; ".join(p.strip() for p in re.split(r"[;\r\n]+", raw) if p.strip())
    if cookie.lower().startswith("cookie:"):
        cookie = cookie[len("cookie:") :].strip()
    if cookie and (re.search(r"[\x00-\x1f\x7f]", cookie) or not cookie.isascii()):
        return "", "FP_COOKIE contains control or non-ASCII characters; re-paste the cookie value on one line"
    return cookie, None


def fp_credentials(env=None) -> tuple[str, str] | None:
    env = os.environ if env is None else env
    email, password = env.get("FP_EMAIL", "").strip(), env.get("FP_PASSWORD", "")
    return (email, password) if email and password else None


def probe_fp_login(client: Client, creds: tuple[str, str] | None) -> tuple[dict, dict | None]:
    """Can a script log in to FantasyPros from this runner? Reports field names and booleans only.

    Returns (report, cookie jar if the login verifiably succeeded).
    """
    rep: dict = {"credentials_configured": bool(creds)}
    jar: dict[str, str] = {}
    form, page = None, None
    for url in FP_LOGIN_URLS:
        page = client.get(url, jar=jar)
        info = {"status": page.status, "error": page.error, "final_path": urllib.parse.urlsplit(page.final_url).path}
        if page.ok and _is_html(page):
            html = page.text()
            form = st.login_form(html)
            info["bot_markers"] = st.markers(html, LOGIN_BOT_MARKERS)
            info["form"] = (
                {
                    "action_path": urllib.parse.urlsplit(form["action"]).path or "(same page)",
                    "method": form["method"],
                    "fields": form["fields"],
                    "has_hidden_token": bool(form["hidden"]),
                }
                if form
                else None
            )
            info["cookie_names_set"] = sorted(jar)
        rep.setdefault("login_pages", {})[url] = info
        if form:
            break
    if not creds or not form or not form["password_field"]:
        rep["attempted"] = False
        return rep, None
    email, password = creds
    fields = dict(form["hidden"])
    fields[form["user_field"] or "username"] = email
    fields[form["password_field"]] = password
    action = urllib.parse.urljoin(page.final_url, form["action"] or page.final_url)
    origin = "{0.scheme}://{0.netloc}".format(urllib.parse.urlsplit(page.final_url))
    resp = client.post_form(action, fields, jar=jar, headers={"Referer": page.final_url, "Origin": origin})
    del fields
    rep["attempted"] = True
    rep["login_post"] = {
        "status": resp.status,
        "error": resp.error,
        "final_path": urllib.parse.urlsplit(resp.final_url).path,
        "cookie_names": sorted(jar),
    }
    check = client.get(FP_PAGES["rankings_idp"], jar=jar)
    marks = st.markers(check.text(), PAGE_MARKERS) if check.ok and _is_html(check) else {}
    rep["after_login"] = {
        "status": check.status,
        "logout_link": marks.get("logout_link"),
        "login_prompt": marks.get("login_prompt"),
        "bot_challenge": marks.get("bot_challenge"),
    }
    rep["login_succeeded"] = bool(marks.get("logout_link"))
    return rep, (jar if rep["login_succeeded"] else None)


def probe_fantasypros(client: Client, out_dir: Path) -> dict:
    cookie, cookie_error = fp_cookie()
    recipient = age_recipient()
    sec: dict = {
        "cookie_configured": bool(cookie),
        "encryption_configured": bool(recipient),
        "pages": {},
    }
    if cookie_error:
        sec["cookie_error"] = cookie_error
    sec["auto_login"], jar = probe_fp_login(client, fp_credentials())
    modes: list[tuple[str, dict]] = [("public", {})]
    if cookie:
        modes.append(("with_cookie", {"headers": {"Cookie": cookie}}))
    if jar:
        modes.append(("auto_login", {"jar": jar}))
    for name, url in FP_PAGES.items():
        page: dict = {}
        for mode, kwargs in modes:
            r = client.get(url, **kwargs)
            rep = {
                "status": r.status,
                "error": r.error,
                "final_path": urllib.parse.urlsplit(r.final_url).path,
                "bytes": len(r.body),
            }
            if r.ok and _is_html(r):
                rep.update(fp_structure(r.text()))
                if mode != "public" and recipient:
                    rep["encrypted_sample"] = age_encrypt(
                        r.body, recipient, out_dir / "encrypted" / f"fantasypros_{name}_{mode}.html.age"
                    )
            page[mode] = rep
            del r
        sec["pages"][name] = page
    return sec


# ---------------------------------------------------------------- ESPN, Odds


def probe_espn(client: Client, season: int, week: int | None) -> tuple[dict, dict | None]:
    sec: dict = {"week_used": week}
    sample = None
    # 00/10 = season actual/projected splits; 11{season}{week} = that week's projection, 01 = last week's actual
    extra_splits = [f"11{season}{week}", f"01{season}{week - 1}"] if week else []
    filters = {
        "players": {
            "filterSlotIds": {"value": ESPN_IDP_SLOT_IDS},
            "filterStatsForTopScoringPeriodIds": {
                "value": 3,
                "additionalValue": [f"00{season}", f"10{season}", *extra_splits],
            },
            "sortPercOwned": {"sortAsc": False, "sortPriority": 1},
            "limit": 150,
        }
    }
    for label, extra in (("default", ""), ("scoring_period", f"&scoringPeriodId={week}")):
        if label == "scoring_period" and not week:
            sec[label] = {"skipped": "week unknown"}
            continue
        r = client.get(ESPN_URL.format(season=season) + extra, headers={"X-Fantasy-Filter": json.dumps(filters)})
        rep = _req_summary(r)
        if r.ok:
            try:
                payload = r.json()
                rep["shape"] = an.espn_shape(payload, week) if week else None
                rep["key_paths"] = st.json_key_paths(payload, max_depth=5, max_paths=120)
                if label == "default":
                    sample = {"players": (payload.get("players") or [])[:5]} if isinstance(payload, dict) else None
            except ValueError:
                rep["error"] = "not JSON"
        sec[label] = rep
    return sec, sample


def _iso(value) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def probe_odds(client: Client, now: datetime) -> dict:
    key = os.environ.get("ODDS_API_KEY", "").strip()
    if not key:
        return {"status": "skipped", "reason": "ODDS_API_KEY not configured"}
    if not re.fullmatch(r"[A-Za-z0-9]{16,64}", key):
        return {"status": "skipped", "reason": "ODDS_API_KEY malformed"}
    sec: dict = {}
    cutoff = now.astimezone(timezone.utc) + timedelta(minutes=30)
    r = client.get(
        f"{ODDS_BASE}/events?apiKey={key}&commenceTimeFrom={cutoff.strftime('%Y-%m-%dT%H:%M:%SZ')}",
        redact=("apiKey",),
    )
    sec["events"] = _req_summary(r) | {"quota": {k: v for k, v in r.headers.items() if k.startswith("x-requests")}}
    try:
        events = r.json() if r.ok else []
    except ValueError:
        events = []
    sec["events"]["count"] = len(events) if isinstance(events, list) else 0
    upcoming = sorted(
        (e for e in (events if isinstance(events, list) else []) if (t := _iso(e.get("commence_time"))) and t > cutoff),
        key=lambda e: e["commence_time"],
    )
    sec["events"]["in_play_or_past_skipped"] = sec["events"]["count"] - len(upcoming)
    if not upcoming:
        sec["event_odds"] = {"skipped": "no upcoming event"}
        return sec
    ev = upcoming[0]
    r = client.get(
        f"{ODDS_BASE}/events/{ev['id']}/odds?apiKey={key}&regions=us&markets={ODDS_MARKETS}&oddsFormat=american",
        redact=("apiKey",),
    )
    rep = _req_summary(r) | {
        "commence_time": ev.get("commence_time"),
        "quota": {k: v for k, v in r.headers.items() if k.startswith("x-requests")},
    }
    try:
        books = ((r.json() or {}).get("bookmakers") or []) if r.ok else None
    except ValueError:
        books, rep["error"] = None, "not JSON"
    if books is not None:
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
