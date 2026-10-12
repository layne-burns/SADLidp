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


def test_a_claimed_suggestion_is_frozen_as_it_stood_not_withdrawn():
    log = weeklog.update_badge_log({}, {"w|START|s": _badge("s", SUN, p=0.72)}, NOW - timedelta(days=4))
    # Wednesday waivers: you claim W, so he leaves the pool and his badge disappears before lock
    wed = NOW - timedelta(days=3)
    log = weeklog.update_badge_log(log, {}, wed, claimed={"w": "me"}.get)
    e = log["w|START|s"]
    assert e["active"] is True and e["claimed_by"] == "me" and e["claimed_at"] == wed.isoformat()
    assert e["p"] == 0.72
    # frozen from then on: a later badge with the same key (W dropped back into the pool) changes nothing
    assert weeklog.update_badge_log(log, {"w|START|s": _badge("s", SUN, p=0.9)}, NOW) == log
    # not claimed by anyone: gone before lock means withdrawn
    other = weeklog.update_badge_log({}, {"w|START|s": _badge("s", SUN)}, NOW - timedelta(days=4))
    assert weeklog.update_badge_log(other, {}, wed, claimed=lambda pid: None)["w|START|s"]["active"] is False


def test_a_late_row_stores_its_kickoff_and_stays_frozen():
    kickoff = NOW - timedelta(days=2)
    rows, _ = storage.merge_rows({}, {"p": {"mu": 5.0}}, NOW, {"p": kickoff}.get, accept_late=True)
    assert rows["p"]["late"] is True and rows["p"]["kickoff"] == kickoff.isoformat()
    for later in (None, NOW + timedelta(days=1)):  # released, or traded to a later game
        again, _ = storage.merge_rows(rows, {"p": {"mu": 9.0}}, NOW, {"p": later}.get, accept_late=True)
        assert again["p"] == rows["p"]
        gone, _ = storage.merge_rows(rows, {}, NOW, {"p": later}.get, accept_late=True)
        assert gone["p"] == rows["p"]  # not deleted when the source stops listing him


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
