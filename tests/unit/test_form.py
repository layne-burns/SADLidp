"""The form term (SPEC §6.4): history from earlier weeks' records and results, and its application to μ."""

import json

import pytest

from sadlidp import form

SC = {"idp_tkl_solo": 2.0, "idp_tkl_ast": 1.0, "idp_sack": 6.0}
COEF = {"k": 4.0, "miss": {"DL": 0.5, "LB": 0.4, "DB": 0.2}, "snap": {"DL": 8.0, "LB": 6.0, "DB": 4.0}}


def _week(season_dir, week, frozen=None, stats=None):
    wd = season_dir / f"week{week:02d}"
    wd.mkdir(parents=True, exist_ok=True)
    if frozen is not None:
        (wd / "frozen.json").write_text(json.dumps({"schema_version": 1, "players": frozen}))
    if stats is not None:
        (wd / "actuals.json").write_text(json.dumps({"week": week, "stats": stats}))


def test_coefficients_follow_the_config():
    assert form.coefficients({}) is None and form.coefficients({"form": {"enabled": False}}) is None
    c = form.coefficients({"form": {"enabled": True, "k": 3, "miss": {"DL": 0.5}, "snap": {"LB": 6}}})
    assert c == {"k": 3.0, "miss": {"DL": 0.5, "LB": 0.0, "DB": 0.0}, "snap": {"DL": 0.0, "LB": 6.0, "DB": 0.0}}


def test_history_reads_earlier_weeks_only_against_the_frozen_base(tmp_path):
    season = tmp_path / "2026"
    # week 1: a record for a (base 10, shown μ 11) and b; a scores 16, b didn't play, c played with no record
    _week(season, 1, {"a": {"mu": 11.0, "base": 10.0}, "b": {"mu": 5.0}},
          {"a": {"gp": 1, "idp_tkl_solo": 8, "def_snp": 30, "tm_def_snp": 60}, "b": {"gms_active": 0},
           "c": {"gp": 1, "idp_sack": 1, "def_snp": 10, "tm_def_snp": 50}})  # fmt: skip
    # week 2: no frozen record at all (an older rebuild): results still give snap shares; a's miss is vs `mu`
    _week(season, 2, None, {"a": {"gp": 1, "idp_tkl_solo": 2, "def_snp": 55, "tm_def_snp": 55}})
    _week(season, 3, {"a": {"mu": 9.0}}, {"a": {"gp": 1, "idp_tkl_solo": 1, "def_snp": 0, "tm_def_snp": 0}})
    _week(season, 4, {"a": {"mu": 9.0}}, {"a": {"gp": 1, "idp_tkl_solo": 9}})  # the week being synced: ignored
    h = form.history(tmp_path, 2026, 4, SC)
    assert h["a"]["misses"] == [pytest.approx(6.0), pytest.approx(-7.0)] and h["a"]["lsnap"] == pytest.approx(1.0)
    assert h["a"]["last"] == 2  # week 3's line had no team snaps: the last known share stands
    assert "b" not in h and h["c"] == {"misses": [], "lsnap": pytest.approx(0.2), "last": 1}
    assert form.history(tmp_path, 2026, 1, SC) == {}


def test_apply_shrinks_the_miss_and_centres_the_snap_share_on_the_position():
    cons = {f"d{i}": {"mu": 10.0 - i, "sigma": 1.0} for i in range(3)} | {"l1": {"mu": 8.0, "sigma": 1.0}}
    slots = {"d0": "DL", "d1": "DL", "d2": "DL", "l1": "LB"}
    hist = {
        "d0": {"misses": [4.0, 8.0], "lsnap": 0.9, "last": 3},  # mean +6, shrunk 2/6 → +2, × 0.5 = +1
        "d1": {"misses": [], "lsnap": 0.5, "last": 3},  # snap only: 0.5 − avg(0.9, 0.5) = −0.2 × 8 = −1.6
        "l1": {"misses": [-30.0], "lsnap": None, "last": None},  # −6 shrunk, × 0.4 = −2.4 … floored at −8
    }
    info = form.apply(cons, slots, hist, COEF)
    assert info == {"snap_avg": {"DL": 0.7, "LB": None, "DB": None}, "players": {"miss": 2, "snap": 2}}
    d0 = cons["d0"]
    assert d0["base"] == 10.0 and d0["mu"] == pytest.approx(10 + 1 + 8 * 0.2)
    assert d0["form"] == {"pts": pytest.approx(2.6), "miss": {"pts": 1.0, "games": 2, "mean": 6.0, "shrunk": 2.0},
                          "snap": {"pts": pytest.approx(1.6), "share": 0.9, "avg": 0.7, "week": 3}}  # fmt: skip
    assert cons["d1"]["mu"] == pytest.approx(9 - 1.6) and "miss" not in cons["d1"]["form"]
    assert cons["d2"] == {"mu": 8.0, "sigma": 1.0, "base": 8.0}  # no history: untouched, no form
    assert cons["l1"]["mu"] == pytest.approx(8 - 0.4 * 30 / 5) and "snap" not in cons["l1"]["form"]
    # a term can't take μ below 0
    cons = {"x": {"mu": 1.0}}
    form.apply(cons, {"x": "LB"}, {"x": {"misses": [-100.0], "lsnap": None, "last": None}}, COEF)
    assert cons["x"]["mu"] == 0.0 and cons["x"]["form"]["pts"] == -1.0
