from datetime import date

from sadlidp import crosswalk as xw
from sadlidp.net import Response

CSV = """﻿mfl_id,fantasypros_id,sleeper_id,espn_id,name,fantasy_data_id
1,28013,13269,4837248,Fernando Mendoza,26740
2,NA,11632,4685522,Abdul Carter,NA
3,25368,NA,4432577,No Sleeper Id,NA
4, 19217 ,8126.0,,Micah Parsons,
5,3001,111,2582138,Twin A,NA
6,3002,222,2582138,Twin B,NA
7,3001,111,,Twin A again,
"""
TODAY = date(2026, 10, 12)  # ISO 2026-W42


class FakeClient:
    def __init__(self, response: Response):
        self.response = response
        self.calls: list[str] = []

    def get(self, url, **kw):
        self.calls.append(url)
        return self.response


def _ok(text=CSV):
    return Response(url=xw.URL, final_url=xw.URL, status=200, body=text.encode())


def test_parse_keeps_id_columns_and_skips_blanks():
    table = xw.parse(CSV)
    assert table["fantasypros_id"] == {"28013": "13269", "19217": "8126", "3001": "111", "3002": "222"}
    # 2582138 maps to two Sleeper ids: dropped as ambiguous
    assert table["espn_id"] == {"4837248": "13269", "4685522": "11632"}
    assert table["fantasy_data_id"] == {"26740": "13269"}
    assert xw.parse("fantasypros_id,sleeper_id\n") == {"fantasypros_id": {}}
    assert xw.parse("fantasypros_id,name\n1,x\n") == {}
    assert xw.parse("<html>rate limited</html>") == {}
    assert xw.parse("espn_id,sleeper_id\n5,6\n") == {"espn_id": {"5": "6"}}


def test_cache_name_is_the_iso_week():
    assert xw.cache_name(TODAY) == "db_playerids_2026-W42.csv"
    assert xw.cache_name(date(2027, 1, 1)) == "db_playerids_2026-W53.csv"  # ISO year, not calendar year
    assert xw.cache_name(date(2026, 1, 5)) == "db_playerids_2026-W02.csv"


def test_load_fetches_once_per_week_and_prunes_old_copies(tmp_path):
    (tmp_path / "db_playerids_2026-W41.csv").write_text("fantasypros_id,sleeper_id\n1,2\n")
    client = FakeClient(_ok())
    assert xw.load(client, tmp_path, TODAY) == xw.parse(CSV)
    assert client.calls == [xw.URL]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["db_playerids_2026-W42.csv"]
    assert xw.load(client, tmp_path, date(2026, 10, 18)) == xw.parse(CSV)  # same ISO week: no refetch
    assert client.calls == [xw.URL]


def test_load_falls_back_to_the_newest_cached_copy(tmp_path):
    (tmp_path / "db_playerids_2026-W39.csv").write_text("fantasypros_id,sleeper_id\n1,2\n")
    (tmp_path / "db_playerids_2026-W41.csv").write_text("fantasypros_id,sleeper_id\n1,3\n")
    failed = Response(url=xw.URL, final_url=xw.URL, status=None, error="timeout")
    assert xw.load(FakeClient(failed), tmp_path, TODAY) == {"fantasypros_id": {"1": "3"}}
    challenged = Response(url=xw.URL, final_url=xw.URL, status=403, challenged=True, body=b"Just a moment")
    assert xw.load(FakeClient(challenged), tmp_path, TODAY) == {"fantasypros_id": {"1": "3"}}
    garbage = FakeClient(_ok("<html>oops</html>"))  # a 200 that isn't the CSV is not cached
    assert xw.load(garbage, tmp_path, TODAY) == {"fantasypros_id": {"1": "3"}}
    assert not (tmp_path / "db_playerids_2026-W42.csv").exists()
    assert xw.load(FakeClient(failed), tmp_path / "empty", TODAY) == {}
