"""PR 25, decision help (owner request 2026-11-17): what changed since Tuesday's list, the bye look-ahead,
collapsed suggestions, Tuesday's questionable tags and the FAAB context line (SPEC §6.6, §6.7)."""

import json
from pathlib import Path

from sadlidp import league_tx, storage
from sadlidp.pool import PlayerStatus
from sadlidp.schedule import Schedule
from sadlidp.sync import bye_lookahead, since_tuesday

ROOT = Path(__file__).parents[2]
SCHED = Schedule.from_json(json.loads((ROOT / "data" / "2026" / "schedule.json").read_text()))


def _idp(n):
    return {f"p{i}": {"full_name": f"Player {i}", "position": "LB", "team": "NYJ"} for i in range(n)}


def test_since_tuesday_lists_facts_about_tuesdays_top_five():
    pre = {"players": {f"p{i}": {"slot": "LB", "tier": 1, "mu": 20.0 - i, **({"flags": ["Q"]} if i == 4 else {})}
                       for i in range(8)}}  # fmt: skip
    cons = {f"p{i}": {"mu": 20.0 - i} for i in range(8)} | {"p2": {"mu": 15.5}, "p7": {"mu": 30.0}}
    status = {"p0": PlayerStatus(["Out"]), "p3": PlayerStatus([], ["Q"]), "p4": PlayerStatus([], ["Q"])}

    def claimed(pid):
        return "other" if pid == "p1" else None

    out = since_tuesday("LB", pre, ["p7", "p3", "p4", "p5", "p6"], cons, _idp(8),
                        lambda pid: status.get(pid, PlayerStatus()), claimed)  # fmt: skip
    by = {x["name"]: x["changes"] for x in out}
    assert by["Player 0"] == ["now Out"] and by["Player 1"] == ["claimed by another team"]
    assert by["Player 2"] == ["μ 18.0 → 15.5"] and by["Player 3"] == ["now questionable"]
    assert "Player 4" not in by  # already questionable on Tuesday: nothing new
    assert by["Player 7"] == ["new in the top 5 (Tuesday #8)"] and by["Player 6"] == ["new in the top 5 (Tuesday #7)"]
    assert since_tuesday("LB", None, [], {}, {}, lambda pid: PlayerStatus(), claimed) is None


def test_bye_lookahead_names_a_bye_in_the_next_three_weeks():
    team, bye = next((t, w) for w in range(6, 15) for t in ("NYJ", "GB", "CHI", "BAL", "DAL", "KC", "SF")
                     if SCHED.week_games(w) and SCHED.game_for(t, w) is None)  # fmt: skip
    players = {"a": {"full_name": "A", "team": team}, "ir": {"full_name": "IR", "team": team}}
    assert bye_lookahead(["a", "ir"], {"ir"}, players, SCHED, bye - 1) == [{"id": "a", "name": "A", "team": team,
                                                                            "week": bye}]  # fmt: skip
    assert bye_lookahead(["a"], set(), players, SCHED, bye) == []  # this week's bye isn't "ahead"
    assert bye_lookahead(["a"], set(), players, SCHED, bye - 3)[0]["week"] == bye


def _tx(data, leg, txs):
    storage.write_json(storage.week_dir(data, 2026, leg) / "transactions.json", {"leg": leg, "transactions": txs})


def test_faab_line_counts_claims_contests_and_free_agent_days(tmp_path):
    slots = {f"p{i}": "LB" for i in range(10)}
    wed = 1793203200000  # Wed 2026-10-28 12:00 ET, in ms
    claims = [{"type": "waiver", "status": "complete", "adds": {f"p{i}": 1}, "bid": b, "leg": 3}
              for i, b in enumerate((0, 1, 1, 4, 15))]  # fmt: skip
    claims.append({"type": "waiver", "status": "failed", "adds": {"p4": 2}, "bid": 9, "leg": 3})  # p4 contested
    claims.append({"type": "waiver", "status": "complete", "adds": {"qb": 1}, "bid": 30, "leg": 3})  # not an IDP
    fa = [{"type": "free_agent", "status": "complete", "adds": {"p6": 1}, "created": wed, "leg": 3}]
    faab100 = {"waiver_type": 2, "waiver_budget": 100}
    _tx(tmp_path, 3, claims[:4] + fa)
    assert league_tx.faab_summary(tmp_path, 2026, slots, faab100) is None  # 4 claims: too few to say
    _tx(tmp_path, 3, claims + fa)
    out = league_tx.faab_summary(tmp_path, 2026, slots, faab100)
    assert (out["claims"], out["won_token"], out["contested"], out["contested_pct"]) == (5, 3, 1, [15.0, 15.0])
    assert out["token_pct"] == 1.0 and out["fa_adds"] == 1 and out["fa_top_day"] is None  # one add: no "most often"
    _tx(tmp_path, 4, [{**fa[0], "leg": 4}, {**fa[0], "leg": 4, "adds": {"p7": 1}}])
    assert league_tx.faab_summary(tmp_path, 2026, slots, faab100)["fa_top_day"] == ("Wednesday", 3)
    assert league_tx.faab_summary(tmp_path, 2026, slots, {"waiver_type": 0, "waiver_budget": 100}) is None  # no FAAB


def test_faab_is_read_as_a_share_of_the_leagues_starting_budget(tmp_path):
    """Leagues start with different budgets (§6.7): the same bids are a different share of $1,000 or $50, and a
    token bid is at most 1% of the budget, or $1 if that is more."""
    slots = {f"p{i}": "LB" for i in range(10)}
    bids = (0, 5, 10, 11, 150)
    claims = [{"type": "waiver", "status": "complete", "adds": {f"p{i}": 1}, "bid": b, "leg": 2}
              for i, b in enumerate(bids)]  # fmt: skip
    claims.append({"type": "waiver", "status": "failed", "adds": {"p4": 3}, "bid": 120, "leg": 2})
    _tx(tmp_path, 2, claims)
    big = league_tx.faab_summary(tmp_path, 2026, slots, {"waiver_type": 2, "waiver_budget": 1000})
    assert big["token_pct"] == 1.0 and big["won_token"] == 3 and big["contested_pct"] == [15.0, 15.0]  # $0, $5, $10
    small = league_tx.faab_summary(tmp_path, 2026, slots, {"waiver_type": 2, "waiver_budget": 50})
    assert small["token_pct"] == 2.0 and small["won_token"] == 1  # $1 is the least a token bid can be: only $0
    assert small["budget"] == 50.0


def test_faab_line_never_counts_a_kicker(tmp_path):
    """Kickers are tracked too since 2026-11-17 (slot "K"): the line stays about IDP claims and IDP pickups."""
    slots = {f"p{i}": "LB" for i in range(10)} | {"k1": "K", "k2": "K"}
    faab100 = {"waiver_type": 2, "waiver_budget": 100}
    idp = [{"type": "waiver", "status": "complete", "adds": {f"p{i}": 1}, "bid": b, "leg": 3}
           for i, b in enumerate((0, 1, 1, 4, 15))]  # fmt: skip
    kick = [{"type": "waiver", "status": "complete", "adds": {"k1": 1}, "bid": 60, "leg": 3},
            {"type": "waiver", "status": "failed", "adds": {"k1": 2}, "bid": 50, "leg": 3},  # contested, at K
            {"type": "free_agent", "status": "complete", "adds": {"k2": 1}, "created": 1793203200000,
             "leg": 3}]  # fmt: skip
    _tx(tmp_path, 3, idp)
    before = league_tx.faab_summary(tmp_path, 2026, slots, faab100)
    _tx(tmp_path, 3, idp + kick)
    assert league_tx.faab_summary(tmp_path, 2026, slots, faab100) == before
    assert (before["claims"], before["contested"], before["fa_adds"]) == (5, 0, 0)
    _tx(tmp_path, 3, idp[:4] + kick)
    assert league_tx.faab_summary(tmp_path, 2026, slots, faab100) is None  # 4 IDP claims: the kicker one isn't a fifth


class _Sleeper:
    def __init__(self, legs):
        self.legs, self.calls = legs, []

    def transactions(self, league_id, leg):
        self.calls.append(leg)
        return (self.legs[leg], None) if leg in self.legs else (None, "HTTP 404")


def test_sync_legs_keeps_two_current_legs_and_backfills_a_few(tmp_path):
    from datetime import datetime

    raw = {"transaction_id": "1", "type": "waiver", "status": "complete", "adds": {"p1": 3},
           "settings": {"waiver_bid": 2}, "created": 1, "metadata": {"notes": "x"}}  # fmt: skip
    sl = _Sleeper({leg: [raw] for leg in range(1, 10)})
    out = league_tx.sync_legs(sl, "L", tmp_path, 2026, 9, datetime(2026, 11, 3))
    assert sorted(out) == [1, 2, 3, 8, 9]  # the two current legs, then three missing ones
    doc = storage.read_json(storage.week_dir(tmp_path, 2026, 8) / "transactions.json")
    assert doc["transactions"] == [{**{k: raw.get(k) for k in league_tx.KEEP}, "bid": 2}]  # compact, no metadata
    assert sorted(league_tx.sync_legs(sl, "L", tmp_path, 2026, 9, datetime(2026, 11, 3))) == [4, 5, 6, 8, 9]


def test_faab_shares_read_honestly_on_the_page():
    """§6.7: the token cut is never stated lower than the one counted, a range never collapses to "15–15%",
    and small shares keep a decimal."""
    assert league_tx.cut_text(1.0) == "1" and league_tx.cut_text(100 / 75) == "1.4" and league_tx.cut_text(2.5) == "2.5"
    assert league_tx.cut_text(100 / 30) == "3.4"  # $1 of $30 is 3.33%: rounded up, never down
    assert league_tx.range_text(1.5, 2.5) == "1.5–2.5" and league_tx.range_text(0.0, 15.0) == "0–15"
    assert league_tx.range_text(14.6, 15.4) == "14.6–15.4" and league_tx.range_text(15.0, 15.0) == "15"
    assert league_tx.pct_text(0.04) == "<0.1" and league_tx.pct_text(12.5) == "13" and league_tx.pct_text(0.25) == "0.3"


def _faab_line(faab):
    from jinja2 import Environment, PackageLoader

    env = Environment(loader=PackageLoader("sadlidp.site", "templates"), autoescape=True)
    src = env.loader.get_source(env, "index.html.j2")[0]
    i = src.index("{% if faab is defined and faab %}")
    j = src.index("{% endif %}</p>{% endif %}", i) + len("{% endif %}</p>{% endif %}")
    import re

    return re.sub(r"\\s+", " ", re.sub(r"<[^>]+>", "", env.from_string(src[i:j]).render(faab=faab))).strip()


def test_the_faab_line_in_a_thousand_dollar_league_with_one_free_agent_add(tmp_path):
    slots = {f"p{i}": "LB" for i in range(10)}
    bids = (0, 5, 10, 15, 25)
    claims = [{"type": "waiver", "status": "complete", "adds": {f"p{i}": 1}, "bid": b, "leg": 2}
              for i, b in enumerate(bids)]  # fmt: skip
    claims += [{"type": "waiver", "status": "failed", "adds": {"p3": 2}, "bid": 9, "leg": 2},
               {"type": "waiver", "status": "failed", "adds": {"p4": 2}, "bid": 20, "leg": 2},
               {"type": "free_agent", "status": "complete", "adds": {"p8": 1}, "created": 1793203200000,
                "leg": 2}]  # fmt: skip
    _tx(tmp_path, 2, claims)
    faab = league_tx.faab_summary(tmp_path, 2026, slots, {"waiver_type": 2, "waiver_budget": 1000})
    line = _faab_line(faab)
    assert "3 of 5 claims were won with a token bid (at most 1% of the starting budget)" in line
    assert "2 were contested, won at 1.5–2.5% of it" in line and "(Starting budget $1000.)" in line
    assert line.endswith("1 IDP was picked up as a free agent.")  # one add: singular, no "most often"
