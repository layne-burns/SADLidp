from datetime import UTC, datetime, timedelta

from sadlidp import storage, weeklog

NOW = datetime(2026, 10, 17, 18, 0, tzinfo=UTC)  # Saturday
THU = (NOW - timedelta(days=2)).isoformat()
SUN = (NOW + timedelta(days=1)).isoformat()


def _badge(over, lock_at, p=0.7):
    return {"w": "w", "badge": "START", "over": over, "p": p, "lock_at": lock_at}


def test_a_new_comparison_is_a_new_suggestion_and_the_old_one_is_withdrawn():
    log = weeklog.update_badge_log({}, {"w|START|s1": _badge("s1", SUN)}, NOW - timedelta(days=4))
    # S1 ruled out Saturday: the comparison is now against S2, who played Thursday (lock passed)
    log = weeklog.update_badge_log(log, {"w|START|s2": _badge("s2", THU)}, NOW)
    assert log["w|START|s1"]["active"] is False  # withdrawn before its lock
    assert "w|START|s2" not in log  # first seen after its lock: never issued


def test_issued_badge_is_frozen_at_lock():
    log = weeklog.update_badge_log({}, {"k": _badge("s", SUN, p=0.65)}, NOW)
    after = NOW + timedelta(days=2)
    assert weeklog.update_badge_log(log, {"k": _badge("s", SUN, p=0.9)}, after) == log
    assert weeklog.update_badge_log(log, {}, after) == log  # gone after lock: still issued


def test_a_record_stays_frozen_when_the_player_is_cut_or_traded_after_playing():
    kickoff = NOW - timedelta(days=2)
    rows, _ = storage.merge_rows({}, {"p": {"mu": 5.0}}, kickoff - timedelta(hours=1), {"p": kickoff}.get)
    assert rows["p"]["kickoff"] == kickoff.isoformat()
    # released (no team, so no kickoff) or traded to a later game: the stored kickoff keeps him frozen
    for later in (None, NOW + timedelta(days=1)):
        again, _ = storage.merge_rows(rows, {"p": {"mu": 9.0}}, NOW, {"p": later}.get)
        assert again["p"]["mu"] == 5.0


def test_outcome_rules():
    def fin(pts):
        return {"pts": pts, "state": "final"}

    assert weeklog.outcome(fin(10), "s", fin(8)) == "hit"
    assert weeklog.outcome(fin(5), "s", fin(8)) == "miss"
    assert weeklog.outcome(fin(5), None, None) == "hit"  # an empty slot counts as 0
    assert weeklog.outcome({"pts": None, "state": "dnp"}, "s", fin(8)) == "void"
    assert weeklog.outcome({"pts": 3, "state": "live"}, "s", fin(8)) is None
