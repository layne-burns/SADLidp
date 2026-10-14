"""PR 5: the RotoBaller and FantasyData rank adapters, late sources, the Thursday window and the rank-group
prior weights (SPEC §5.2, D13, §6.5)."""

import json
import re
from datetime import datetime
from pathlib import Path

from sadlidp import accuracy, gate
from sadlidp.config import load_config
from sadlidp.matching import Matcher
from sadlidp.net import Response
from sadlidp.schedule import ET, Schedule
from sadlidp.sources import fantasydata, rotoballer
from sadlidp.sources.base import OK, FetchContext
from sadlidp.sources.htmltables import tables
from sadlidp.sync import expected_by

ROOT = Path(__file__).parents[2]
FIX = ROOT / "tests" / "fixtures"
CFG = load_config(ROOT / "config" / "sadl.toml")
NOW = datetime(2026, 10, 14, 12, tzinfo=ET)
RSS = (FIX / "rotoballer_rss_trimmed.xml").read_text()
ARTICLE = (FIX / "rotoballer_week05_trimmed.html").read_text()
FD_DL = (FIX / "fantasydata_dl_week05_trimmed.html").read_text()
WEEK5 = "https://www.rotoballer.com/week-5-idp-rankings-streamers-start-sit-fantasy-football-2026/1959614"


class Web:
    """Serves {url: body | status}; anything else is a 404."""

    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def get(self, url, **kw):
        self.calls.append(url)
        body = self.pages.get(url, 404)
        if isinstance(body, int):
            return Response(url=url, final_url=url, status=body)
        return Response(url=url, final_url=url, status=200, body=body.encode())


def _players_from_article(html):
    """A Sleeper-like player for every ranked row of the article (name, team, slot as it lists them)."""
    aliases = json.loads((ROOT / "config" / "aliases" / "rotoballer_teams.json").read_text())
    players = {}
    for slot, (st, rows, _err) in rotoballer.ranking_tables(html).items():
        assert st == OK
        for _rank, player, team in rows:
            name = rotoballer.FLAG.sub("", player).strip()
            pid = f"{slot}{len(players)}"
            pos = {"DL": "DE", "LB": "LB", "DB": "S"}[slot]
            players[pid] = {"full_name": name, "position": pos, "fantasy_positions": [slot],
                            "team": aliases[team.lower()], "active": True}  # fmt: skip
    return players


def _ctx(client, week, players, kickoff_of=None):
    cfg = {"_root": CFG["_root"]}
    return FetchContext(client, None, 2026, week, NOW, players, cfg, Matcher(players), None, kickoff_of=kickoff_of)


def test_rss_discovery_finds_the_weeks_article_and_stops_past_the_last_page():
    feed = rotoballer.RSS.format(page=1)
    url, st, _ = rotoballer.find_article(_ctx(Web({feed: RSS}), 5, {}))
    assert (url, st) == (WEEK5, OK)
    web = Web({feed: RSS})  # page 2 answers 404: past the last page, so "not posted yet", not a failure
    url, st, err = rotoballer.find_article(_ctx(web, 6, {}))
    assert url is None and st == "not_yet_published" and "week-6" in err and len(web.calls) == 2
    assert rotoballer.find_article(_ctx(Web({feed: 503}), 5, {}))[1] == "blocked"


def test_article_tables_flags_teams_and_published_at():
    players = _players_from_article(ARTICLE)
    web = Web({rotoballer.RSS.format(page=1): RSS, WEEK5: ARTICLE})
    res = rotoballer.fetch(_ctx(web, 5, players))
    assert res.ok and res.published_at == "2026-10-08T09:40:26-04:00"
    assert set(res.lists) == {"DL", "LB", "DB"} and all(len(v) == 22 for v in res.lists.values())
    assert res.report["match"]["teamed_match_rate"] == 1.0 and not res.report["unresolved_teams"]
    turner = next(r for pid, r in res.rows.items() if players[pid]["full_name"] == "Dallas Turner")
    assert turner["ranks"] == {"DL": 1} and turner["team"] == "MIN"
    flagged = [r for r in res.rows.values() if r.get("flags")]
    assert flagged and all(set(r["flags"].values()) <= {"Q", "D", "O", "IR", "PUP", "NA", "SUS"} for r in flagged)


def test_players_whose_game_came_before_the_article_are_dropped_but_stay_in_the_list():
    players = _players_from_article(ARTICLE)
    early = next(pid for pid, p in players.items() if p["full_name"] == "Dallas Turner")
    published = datetime(2026, 10, 8, 9, 40, tzinfo=ET)
    web = Web({rotoballer.RSS.format(page=1): RSS, WEEK5: ARTICLE})
    kick = lambda pid: datetime(2026, 10, 2, 20, 15, tzinfo=ET) if pid == early else published.replace(day=11)  # noqa: E731
    res = rotoballer.fetch(_ctx(web, 5, players, kick))
    assert early not in res.rows and res.lists["DL"][early] == 1 and res.report["dropped_after_kickoff"] == 1


def test_a_table_under_the_wrong_title_fails_only_its_list():
    bad = ARTICLE.replace("Top Fantasy Linebackers of the Week", "Waiver Wire Notes")
    players = _players_from_article(ARTICLE)
    res = rotoballer.fetch(_ctx(Web({rotoballer.RSS.format(page=1): RSS, WEEK5: bad}), 5, players))
    assert res.ok and set(res.lists) == {"DL", "DB"}
    assert res.report["lists"]["LB"]["status"] == "parse_error"


def test_titles_set_in_bold_count_as_the_heading():
    html = (
        "<p>Intro.</p><p><strong>Top Fantasy Linebackers of the Week</strong></p><table><tr><th>Rank</th></tr></table>"
    )
    (t,) = tables(html)
    assert "Linebackers" in t.before and t.heading == ""


def _fd_players():
    players = {}
    for i, fd in enumerate(re.findall(r"-fantasy/(\d+)'", FD_DL)):
        players[f"s{i}"] = {"full_name": f"Someone {i}", "position": "DE", "fantasy_positions": ["DL"],
                            "team": "HOU", "active": True, "fantasy_data_id": fd}  # fmt: skip
    return players


def test_fantasydata_matches_by_id_and_reads_each_list_alone():
    players = _fd_players()
    web = Web({fantasydata.URL.format(page="dl"): FD_DL})  # LB and DB pages unreachable
    res = fantasydata.fetch(_ctx(web, 5, players))
    assert res.ok and set(res.lists) == {"DL"} and len(res.rows) == 22
    assert res.report["match"]["by_method"] == {"source_id": 22}  # every row through Sleeper's fantasy_data_id
    assert res.rows["s0"]["ranks"] == {"DL": 1} and res.report["lists"]["LB"]["status"] == "failed"


def test_fantasydata_showing_another_week_is_not_posted_yet():
    pages = {s: FD_DL.replace("Week 5 DL", f"Week 5 {s}") for s in fantasydata.LISTS}
    web = Web({fantasydata.URL.format(page=s.lower()): html for s, html in pages.items()})
    res = fantasydata.fetch(_ctx(web, 6, _fd_players()))
    assert res.status == "not_yet_published" and res.week == 5 and not res.rows


def test_expected_by_and_the_thursday_window():
    sched = Schedule.from_json(json.loads((ROOT / "data" / "2026" / "schedule.json").read_text()))
    assert expected_by("Thu 12:00", sched, 6) == datetime(2026, 10, 15, 12, tzinfo=ET)  # TNF week: that morning
    assert expected_by("Wed 12:00", sched, 6) == datetime(2026, 10, 14, 12, tzinfo=ET)
    assert expected_by(None, sched, 6) is None and expected_by("someday", sched, 6) is None
    wins = {w.id: w for w in gate.windows(sched, 2026, 6, {"waiver_day_of_week": 2}, CFG["gate"])}
    thu = wins["2026-w06-thursday"]
    assert thu.open == datetime(2026, 10, 15, 11, tzinfo=ET)
    assert thu.close == datetime(2026, 10, 15, 19, 15, tzinfo=ET)


def test_rank_group_priors_are_the_starting_shares_and_the_shrink_target():
    assert accuracy.source_weights({"sources": {}}, CFG) == {"fp_ecr": 2.0, "rotoballer": 1.0, "fantasydata": 1.0}
    m = {"from_weeks": [1, 2, 3, 4], "alpha_hat_raw": None, "alpha_n": 0,
         "sources": {"fp_ecr": {"kind": "rank", "n": 4, "mean_r": 1.0},
                     "rotoballer": {"kind": "rank", "n": 4, "mean_r": 1.0}}}  # fmt: skip
    rec = accuracy.weights(m, CFG)
    assert rec["sources"]["fp_ecr"]["w"] == 2.0 and rec["sources"]["rotoballer"]["w"] == 1.0  # equal skill: priors
    m["sources"]["rotoballer"]["mean_r"] = 0.5  # twice as accurate: its share grows, halfway after 4 weeks
    rec = accuracy.weights(m, CFG)
    assert rec["sources"]["rotoballer"]["w"] > 1.0 > rec["sources"]["fp_ecr"]["w"] / 2
