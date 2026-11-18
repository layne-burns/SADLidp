"""The main page's position-first layout (SPEC §2) and its kicker rules, offline: hand-made week data through
build_site, the small helpers behind the This-week card, and one end-to-end sync with kickers."""

import json
import re

from test_kickers_isolation import KClient, k_routes
from test_sync_site import CFG, NOW

from sadlidp import league_tx
from sadlidp.site.build import _action, build_site, kicker_note, page_slots, report_card, source_groups, this_week
from sadlidp.sync import run_sync

IDP = ("DL", "LB", "DB")
SCORING = {"idp_tkl_solo": 2.0, "fgm_yds": 0.1, "xpm": 1.0, "fgmiss": -1.0, "xpmiss": -1.0}


def card(pid, name, slot, mu, **kw):
    """A player card as sync writes it (sync._player_card), with a one-source breakdown."""
    stats = [["FG yards", 60.0, False], ["missed FG", 0.3, True]] if slot == "K" else [["solo", 4.0, False]]
    part = {"source": "Sleeper", "kind": "projection", "pts": mu, "share": 1.0, "stats": stats, "late": False,
            "zero": False}  # fmt: skip
    out = {"id": pid, "name": name, "team": "DAL", "opp": "NYG", "slot": slot, "also": [], "mu": mu, "sigma": 1.0,
           "min": mu, "max": mu, "n": 1, "tier": 1, "flags": [], "unavailable": [], "kickoff": None, "badge": None,
           "mine": False, "late": False, "swing": None, "floor": None, "ceiling": None, "result": None,
           "breakdown": {"parts": [part], "mu": mu, "est": slot == "K"}}  # fmt: skip
    out.update(kw)
    return out


def page(week, slots=IDP):
    """A week's consensus.json with nothing rostered and nothing available at `slots`."""
    empty_pos = {"since_tuesday": None, "tiers": [], "tier_count": 0, "hidden": 0, "no_projection": 0, "played": [],
                 "played_hidden": 0}  # fmt: skip
    empty_mine = {"players": [], "count": 0, "starter_locked": False, "suggestions": [], "byes": [],
                  "played_suggestions": [], "played_suggestions_hidden": 0, "names": {}}  # fmt: skip
    return {
        "generated_at": "2026-11-17T19:56:00-05:00", "season": 2026, "week": week,
        "league": {"name": "Test League", "waiver_type": "FAAB", "limits": {s: 2 for s in IDP}},
        "positions": {s: dict(empty_pos) for s in slots},
        "my_idps": {s: {**empty_mine, "limit": None if s == "K" else 2, **({"open_spots": 0} if s == "K" else {})}
                    for s in slots},
        "last_week": None, "sigma_out": {s: 5.0 for s in slots}, "swing": {"lo": -0.8, "hi": 0.75},
        "sources": {"sleeper_proj": {"label": "Sleeper", "status": "ok", "rows_used": 9, "last_success": None}},
        "warnings": [],
    }  # fmt: skip


def kicker_page(week):
    """`page` with kickers: two of mine (the second on bye, so the only move is a straight swap for my starter),
    one waiver kicker with a range of his own, and a kicker source that hasn't posted."""
    d = page(week, (*IDP, "K"))
    mine = card("k1", "Mine One", "K", 8.4, mine=True, role="starter", reserve=False, taxi=False)
    off = card("k2", "Mine Two", "K", None, mine=True, role=None, reserve=False, taxi=False, unavailable=["bye"],
               breakdown={"parts": [{"source": "Sleeper", "missing": "no projection for him"}], "mu": None,
                          "est": False})  # fmt: skip
    badge = {"badge": "START", "over": "k1", "p": 0.612, "action": "swap", "drop": "k1", "reference": False,
             "why": []}  # fmt: skip
    waiver = card("k3", "Waiver Leg", "K", 10.9, swing=4.6, floor=7.2, ceiling=14.4, badge=badge)
    # a kicker never has a form term; if one ever reached the page it must not be shown
    waiver["breakdown"]["form"] = {"pts": 1.0, "snap": {"pts": 1.0, "share": 0.9, "avg": 0.8, "week": 10}}
    d["positions"]["K"].update(tiers=[{"tier": 1, "anchor_mu": 10.9, "players": [waiver]}], tier_count=1)
    d["my_idps"]["K"].update(players=[mine, off], count=2, names={"k1": "Mine One", "k2": "Mine Two"},
                             suggestions=[{**badge, "others": [], "player": {**waiver, "badge": None}}])  # fmt: skip
    d["kicker"] = {"sigma_out": 5.0, "one_point_edge": 0.556}
    d["sources"]["sleeper_k"] = {"label": "Sleeper", "group": "k", "status": "ok", "rows_used": 26}
    d["sources"]["rotoballer_k"] = {"label": "RotoBaller", "group": "k", "status": "not_yet_published",
                                    "pending": True, "rows_used": 0, "stale": True}  # fmt: skip
    return d


def write_weeks(data, pages, actuals=None):
    """Save hand-made weeks under `data`; `actuals` = {week: {pid: stat line}} settles those weeks."""
    for d in pages:
        wdir = data / "2026" / f"week{d['week']:02d}"
        wdir.mkdir(parents=True, exist_ok=True)
        (wdir / "consensus.json").write_text(json.dumps(d), encoding="utf-8")
    (data / "2026" / "league.json").write_text(json.dumps({"scoring_settings": SCORING, "settings": {}}))
    for week, lines in (actuals or {}).items():
        doc = {"week": week, "captured_at": "2026-11-24T10:00:00-05:00", "stats": lines}
        (data / "2026" / f"week{week:02d}" / "actuals.json").write_text(json.dumps(doc))


def between(html, start, end):
    return html[html.index(start) : html.index(end, html.index(start))]


def test_page_order_and_a_k_tab_only_where_the_week_has_kickers(tmp_path):
    """This week, then the sticky position bar and its panels, then the folded sections; the tabs work with no
    script (radios before the bar, general-sibling selectors, no :has()); a week without K shows no K at all."""
    prev = page(11)
    prev["my_idps"]["LB"].update(players=[card("a", "Line Backer", "LB", 8.0, mine=True, role="starter")], count=1)
    write_weeks(tmp_path / "d", [prev, kicker_page(12)], actuals={11: {"a": {"gp": 1, "idp_tkl_solo": 5}}})
    html = build_site(tmp_path / "d", tmp_path / "site").read_text(encoding="utf-8")
    order = ['id="now-h"', 'class="tabbar"', 'id="panel-DL"', 'id="lw-h"', ">How to read this page<", ">Sources<"]
    assert [html.index(x) for x in order] == sorted(html.index(x) for x in order)
    assert ":has(" not in html
    radios = '(<input type="radio" name="pos" id="tab-(DL|LB|DB|K)"( checked)?>){4}'
    labels = r'(<label for="tab-\w+">\w+</label>){4}'
    assert re.search(rf'<div class="tabs">\s*{radios}\s*<div class="tabbar">{labels}', html)
    for slot in (*IDP, "K"):
        assert f"#tab-{slot}:checked ~ #panel-{slot} {{ display: block; }}" in html
        assert f'#tab-{slot}:checked ~ .tabbar label[for="tab-{slot}"]' in html
    assert "<h1>SADL streamer</h1>" in html and "2026 Week 12" in html and 'href="stats.html"' in html
    # last week's page was synced before kickers: three tabs, and the word never appears
    old = (tmp_path / "site" / "week-2026-11.html").read_text(encoding="utf-8")
    assert re.findall(r'id="tab-(\w+)"', old) == list(IDP) and "panel-K" not in old
    assert "kicker" not in old.lower() and "<b>K</b>" not in old and "σ<sub>out</sub> DL 5.0, LB 5.0, DB 5.0)" in old
    assert page_slots(prev) == IDP and page_slots(kicker_page(12)) == (*IDP, "K")


def test_week_picker_keeps_every_week_and_shows_the_current_one_first(tmp_path):
    write_weeks(tmp_path / "d", [page(w) for w in range(1, 13)])
    build_site(tmp_path / "d", tmp_path / "site")
    past = (tmp_path / "site" / "week-2026-03.html").read_text(encoding="utf-8")
    nav = between(past, '<nav class="weeks"', "</nav>")
    for w in range(1, 13):  # every week keeps its link and label, inside "all weeks"
        label = f"Week {w} · now" if w == 12 else f"Week {w}"
        assert re.search(rf'<a href="week-2026-{w:02d}\.html"[^>]*>{label}</a>', nav), w
    current = re.findall(r'<a href="([^"]+)" class="here" aria-current="page">', nav)
    assert current == ["week-2026-03.html"] * 2 and nav.count("aria-current") == 2  # the row, and the full list
    row = nav[: nav.index("<details>")]  # always on screen: previous, this week, next
    assert re.findall(r'href="week-2026-(\d+)\.html"', row) == ["02", "03", "04"]
    assert "‹ Wk 2" in row and "Wk 4 ›" in row and "all weeks ▾" in nav
    index = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    row = between(index, '<nav class="weeks"', "<details>")
    assert '<a href="week-2026-12.html" class="here" aria-current="page">Week 12 · now</a>' in row
    assert "Wk 11" in row and "›" not in row  # nothing after the latest week


def test_kicker_panel_card_and_notes(tmp_path):
    write_weeks(tmp_path / "d", [page(11), kicker_page(12)])
    html = build_site(tmp_path / "d", tmp_path / "site").read_text(encoding="utf-8")
    panel = between(html, 'id="panel-K"', '<details class="fold"')
    assert "2 rostered · roster full" in panel and "None" not in panel and "2/" not in panel  # no kicker limit
    assert "Add, and drop Mine One" in panel  # the straight swap for my starter
    assert "a kicker projected 1 point higher outscores the other only about 56% of the time" in panel
    assert "60.0 FG yards · 0.3 missed FG*" in panel and "* = our estimate" in panel
    # his range is explained by his kicks, never by sacks and turnovers; no form, no defensive snaps
    assert "It comes from how many field goals and extra points he is projected for (±4.6 here)." in panel
    assert 'class="band">range 7–14' in panel
    for idp_only in ("sacks and turnovers", "defensive snaps", "<b>Form</b>", 'class="band">form'):
        assert idp_only not in panel, idp_only
    assert "10.9 pts</b> = 10.9 from the sources (projections only this week).</div>" in panel
    now = between(html, 'id="now-h"', 'id="pos"')  # the This-week card
    assert '<span class="tag pos">K</span>' in now and "61% to outscore Mine One" in now
    assert "Add, and drop Mine One" in now and "No move suggested at: DL, LB, DB" in now
    assert "Mine Two (K) is on bye." in now and "No DL rostered." in now
    assert re.search(r"<b>K</b> Mine One 8\.4", now) and "<b>DL</b> —" in now
    assert "sources ▾" not in now and "sources ▾" in panel  # the breakdown stays in the position's own panel
    # the Sources fold: two groups, and its summary says what is missing
    assert "IDP sources" in html and "Kicker sources" in html
    assert "IDP 1 of 1 in · kickers 1 of 2 in · RotoBaller (kickers) not posted yet" in html
    assert "Kicker moves: the league sets no limit on kickers" in html


def test_report_card_lists_the_kicker_after_the_idp_totals(tmp_path):
    prev = page(11, (*IDP, "K"))
    prev["my_idps"]["LB"].update(players=[card("a", "Line Backer", "LB", 8.0, mine=True, role="starter")], count=1)
    prev["my_idps"]["K"].update(players=[card("k1", "Mine One", "K", 8.1, mine=True, role="starter")], count=1)
    lines = {"a": {"gp": 1, "idp_tkl_solo": 5}, "k1": {"gp": 1, "fgm_yds": 90, "xpm": 3}}  # 10 and 12 points
    write_weeks(tmp_path / "d", [prev, kicker_page(12)], actuals={11: lines})
    html = build_site(tmp_path / "d", tmp_path / "site").read_text(encoding="utf-8")
    assert "Your IDPs scored <b>10</b> against <b>8</b> projected (1 who played and had a projection)." in html
    assert 'Your kicker: Mine One, 8.1 projected → scored <b>12</b> <small class="up">+3.9</small>' in html
    assert "Week 11: your IDPs scored 10 against 8 projected." in html  # the This-week card's line, IDP only too
    fin = {"state": "final"}
    data = {"my_idps": {"LB": {"players": [{"name": "A", "mu": 10.0, "result": {**fin, "pts": 18.0}}]},
                        "K": {"players": [{"name": "Kick", "mu": 8.0, "result": {**fin, "pts": 30.0}},
                                          {"name": "Stash", "mu": 7.0, "reserve": True, "result": fin}]}}}  # fmt: skip
    calls = [{"name": "W", "pts": 9.0, "over_pts": 1.0, "outcome": "hit", "slot": "K"},
             {"name": "X", "pts": 6.0, "over_pts": 5.0, "outcome": "hit", "slot": "LB"}]  # fmt: skip
    c = report_card(data, {"suggestions": {"list": calls}}, 11, False)
    assert (c["projected"], c["scored"], c["counted"]) == (10.0, 18.0, 1) and c["beat"]["name"] == "A"
    assert [x["name"] for x in c["players"]] == ["A"] and c["best_call"]["name"] == "X"
    assert [(x["name"], x["pts"], x["diff"]) for x in c["kickers"]] == [("Kick", 30.0, 22.0)]


def test_this_week_gathers_moves_flags_and_starters():
    d = kicker_page(12)
    ref = {"badge": "START", "over": "s", "p": 0.7, "action": "add", "drop": None, "reference": True,
           "why": ["starter_locked"], "player": card("w", "Waiver Dee", "DL", 9.0)}  # fmt: skip
    d["my_idps"]["DL"].update(
        players=[card("s", "Starter Dee", "DL", 7.0, role="starter", unavailable=["locked"]),
                 card("q", "Quest Dee", "DL", 5.0, role="other", flags=["Q"]),
                 card("ir", "Stash Dee", "DL", None, reserve=True, unavailable=["IR"])],
        suggestions=[ref], starter_locked=True, byes=[{"name": "Quest Dee", "team": "DAL", "week": 14}])  # fmt: skip
    d["my_idps"]["LB"].update(players=[card("o", "Out Bee", "LB", None, unavailable=["Out"])])
    now = this_week(d, page_slots(d))
    assert [(m["slot"], m["s"]["reference"]) for m in now["moves"]] == [("K", False), ("DL", True)]  # actionable first
    assert now["quiet"] == ["LB", "DB"]
    assert now["heads"] == [
        "Quest Dee (DL) is questionable.",
        "Out Bee (LB) can't play this week: Out.",
        "You have no playable LB this week.",
        "No DB rostered.",
        "Mine Two (K) is on bye.",
        "Your DL starter's game has started, so suggestions there are for reference.",
        "Bye ahead: Quest Dee (DAL) has a bye in week 14.",
    ]
    assert [(x["slot"], x["p"] and x["p"]["name"]) for x in now["starters"]] == [
        ("DL", "Starter Dee"), ("LB", None), ("DB", None), ("K", "Mine One")]  # fmt: skip
    d["my_idps"]["LB"]["starter_locked"] = True
    assert "Your DL and LB starters' games have started, so suggestions there are for reference." in this_week(
        d, page_slots(d))["heads"]  # fmt: skip


def test_action_texts_for_kickers_and_unchanged_for_idps():
    names = {"s": "My Starter", "b": "My Backup"}
    assert _action({"action": "swap", "drop": "s"}, names, "K", 0) == "Add, and drop My Starter"
    assert _action({"action": "add_drop", "drop": "b"}, names, "K", 0) == "Add, and drop My Backup"
    assert _action({"action": "add"}, names, "K", 2) == "Add; you have a free roster spot, so no drop is needed"
    assert "limit" not in _action({"action": "add"}, names, "K", None)  # room unknown: still no "limit" for a kicker
    assert _action({"action": "choose_drop"}, names, "K", 0) == "Add; your roster is full, so you choose the drop"
    # DL/LB/DB wording is exactly what it was, with or without the new arguments
    assert _action({"action": "add"}, names) == "Add; you're under the limit, so no drop is needed"
    assert _action({"action": "add"}, names, "LB", 0) == "Add; you're under the limit, so no drop is needed"
    assert _action({"action": "add_drop", "drop": "b"}, names, "DB") == "Add, and drop My Backup"
    assert _action({"action": "choose_drop"}, names, "DL").endswith("can't play this week, so you choose the drop")
    groups = source_groups({"a": {"rows_used": 3}, "b_k": {"group": "k", "rows_used": 0}, "c": {"stale": True}})
    assert [(g["key"], g["used"], [n for n, _ in g["out"]]) for g in groups] == [("idp", 1, ["c"]), ("k", 0, ["b_k"])]


def test_the_kicker_note_prints_a_measured_figure_only_from_weeks_the_page_showed():
    """Ten past weeks graded from the kicker archives alone give the Stats page its K rows, but the main page
    showed no kicker order in any of them: its note stays with the plain odds until four live weeks settle."""
    archive_only = {"r": 0.31, "margin": 0.2, "weeks": 10, "band": False, "settled": 10, "live": 0}
    assert kicker_note({"k_note": archive_only}) is None
    assert kicker_note({"k_note": {**archive_only, "weeks": 3, "band": True, "settled": 13, "live": 3}}) is None
    shown = {**archive_only, "weeks": 4, "band": True, "settled": 14, "live": 4}
    assert kicker_note({"k_note": shown}) == shown
    assert kicker_note({"k_note": {**shown, "r": None}}) is None
    assert kicker_note(None) is None and kicker_note({}) is None


def test_synced_week_with_kickers_renders_and_the_faab_line_stays_idp_only(tmp_path, monkeypatch):
    """End to end: a sync with kicker players and projections, then the page. The K tab is built from the
    week's own data, and the ids handed to the FAAB summary hold no kicker."""
    data = tmp_path / "d"
    run_sync(CFG, data, tmp_path / "c", NOW, client=KClient(k_routes()), log=lambda m: None)
    d = json.loads((data / "2026" / "week05" / "consensus.json").read_text(encoding="utf-8"))
    assert d["positions"]["K"]["tiers"] and d["my_idps"]["K"]["limit"] is None
    seen = {}
    real = league_tx.faab_summary

    def spy(data_dir, season, slots, settings):
        seen.update(slots)
        return real(data_dir, season, slots, settings)

    monkeypatch.setattr(league_tx, "faab_summary", spy)
    html = build_site(data, tmp_path / "site", CFG).read_text(encoding="utf-8")
    assert seen and set(seen.values()) <= set(IDP)
    panel = between(html, 'id="panel-K"', "</section>")
    k = d["my_idps"]["K"]
    assert f"{k['count']} rostered" in panel and f"{k['count']}/" not in panel
    assert "Kickers are hard to tell apart" in panel and "Tier 1" in panel and "FG yards" in panel
    assert "defensive snaps" not in panel and "sacks and turnovers" not in panel
    assert k["suggestions"], "the fixture's weak kicker should draw a suggestion"
    for s in k["suggestions"]:
        text = _action(s, k["names"], "K", k.get("open_spots"))
        assert text and "under the limit" not in text and text in html
    assert "Kicker sources" in html and "<td>Sleeper</td>" in html
