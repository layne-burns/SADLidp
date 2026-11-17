"""Research part 2 (full review 2026-11-17): the DL matchup signal measured after the fact from nflverse
play-by-play, and the FantasyPros past-season pull."""

import csv
import gzip
import io
import random

import pytest

from sadlidp import backtest, matchup


def _pbp(rows):
    buf = io.StringIO()
    w = csv.DictWriter(
        buf, fieldnames=["season", "season_type", "week", "posteam", "qb_dropback", "qb_spike", "qb_hit"]
    )
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return gzip.compress(buf.getvalue().encode())


def test_hit_rate_allowed_counts_dropbacks_and_shrinks_toward_the_league():
    plays = []
    for wk in (1, 2):
        for team, hits in (("NYJ", 8), ("LA", 2)):  # 20 dropbacks each a week; NYJ allows 8 hits, LA 2
            plays += [{"season": 2026, "season_type": "REG", "week": wk, "posteam": team, "qb_dropback": 1,
                       "qb_spike": 0, "qb_hit": int(i < hits)} for i in range(20)]  # fmt: skip
    plays.append({"season": 2026, "season_type": "REG", "week": 1, "posteam": "NYJ", "qb_dropback": 1,
                  "qb_spike": 1, "qb_hit": 1})  # a spike: left out  # fmt: skip
    plays.append({"season": 2026, "season_type": "POST", "week": 1, "posteam": "LA", "qb_dropback": 1,
                  "qb_spike": 0, "qb_hit": 1})  # postseason: left out  # fmt: skip
    weekly = matchup.counts(_pbp(plays), 2026, {"LA": "LAR"})
    assert weekly[1] == {"NYJ": [20, 8], "LAR": [20, 2]}
    rates = matchup.o_hit(weekly, 3)  # weeks 1-2: league 10/40 = 0.25, prior = 4 team-games of 20 = 80
    assert rates["NYJ"] == pytest.approx(100 * ((16 + 80 * 0.25) / (40 + 80) - 0.25))
    assert rates["LAR"] == pytest.approx(-rates["NYJ"]) and matchup.o_hit(weekly, 1) == {}
    assert matchup.shadow("@NYJ", rates, 0.30, 2.0)["pts"] == pytest.approx(round(0.30 * rates["NYJ"], 3))
    assert (
        matchup.shadow("@NYJ", {"NYJ": 50.0}, 0.30, 2.0)["pts"] == 2.0 and matchup.shadow("KC", rates, 0.3, 2) is None
    )


def test_matchup_report_recovers_a_planted_slope_and_scores_it_out_of_season():
    rng = random.Random(3)
    rows = []
    for season in (2024, 2025, 2026):
        for week in range(2, 12):
            for _ in range(40):
                x = rng.gauss(0, 2)
                rows.append({"season": season, "week": week, "x": x, "y": 0.4 * x + rng.gauss(0, 1)})
    rep = backtest.matchup_report(rows, frozen_coef=0.30)
    assert rep["slope"] == pytest.approx(0.4, abs=0.03) and rep["t"] > 10
    assert all(s["loso_dmae"] < 0 for s in rep["seasons"].values()) and rep["frozen"]["dmae"] < 0


def test_a_past_season_page_is_asked_for_by_year():
    class Client:
        def __init__(self):
            self.urls = []

        def get(self, url, **kw):
            self.urls.append(url)
            from sadlidp.net import Response

            return Response(url=url, final_url=url, status=200, body=b"var ecrData = {}")

    c = Client()
    yc = backtest._YearClient(c, 2025)
    yc.get("https://www.fantasypros.com/nfl/rankings/lb.php?week=5")
    assert c.urls == ["https://www.fantasypros.com/nfl/rankings/lb.php?week=5&year=2025"] and len(yc.pages) == 1
