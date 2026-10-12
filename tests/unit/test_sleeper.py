import json

import pytest

from sadlidp import sleeper as sl
from sadlidp.net import Response


class FakeClient:
    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[str] = []

    def get(self, url, **kw):
        self.calls.append(url)
        for prefix, body in self.routes.items():
            if url.startswith(prefix):
                if isinstance(body, int):
                    return Response(url=url, final_url=url, status=body)
                return Response(url=url, final_url=url, status=200, body=json.dumps(body).encode())
        return Response(url=url, final_url=url, status=404)


CFG = {"league": {"username": "lbburnsy", "names": ["SADL", "Slow Ass Draft League"]}}


def test_resolve_league_exact_name_match():
    client = FakeClient(
        {
            f"{sl.V1}/user/lbburnsy": {"user_id": "u1"},
            f"{sl.V1}/user/u1/leagues/nfl/2026": [
                {"name": "Slow Ass Draft League", "league_id": "L1"},
                {"name": "SADL mock", "league_id": "L2"},
            ],
            f"{sl.V1}/league/L1": {"league_id": "L1"},
        }
    )
    league, user_id = sl.resolve_league(sl.Sleeper(client), CFG, 2026)
    assert league == {"league_id": "L1"} and user_id == "u1"


def test_resolve_league_fails_loudly_on_zero_or_many():
    client = FakeClient(
        {
            f"{sl.V1}/user/lbburnsy": {"user_id": "u1"},
            f"{sl.V1}/user/u1/leagues/nfl/2026": [
                {"name": "SADL", "league_id": "a"},
                {"name": "sadl ", "league_id": "b"},
            ],
        }
    )
    with pytest.raises(sl.SleeperError, match="got 2"):
        sl.resolve_league(sl.Sleeper(client), CFG, 2026)


def test_resolve_league_uses_cached_user_id_and_override():
    client = FakeClient({f"{sl.V1}/user/lbburnsy": 404, f"{sl.V1}/league/X": {"league_id": "X"}})
    cfg = {"league": {**CFG["league"], "league_id": "X"}}
    assert sl.resolve_league(sl.Sleeper(client), cfg, 2026, cached_user_id="u9") == ({"league_id": "X"}, "u9")


def test_required_endpoint_failure_is_fatal():
    with pytest.raises(sl.SleeperError, match="state: HTTP 503"):
        sl.Sleeper(FakeClient({f"{sl.V1}/state/nfl": 503})).state()


def test_players_db_cached_per_day(tmp_path):
    client = FakeClient({f"{sl.V1}/players/nfl": {"1": {"position": "LB"}}})
    s = sl.Sleeper(client, cache_dir=tmp_path)
    assert s.players("2026-10-12") == {"1": {"position": "LB"}}
    assert s.players("2026-10-12") == {"1": {"position": "LB"}}
    assert len(client.calls) == 1
    s.players("2026-10-13")
    assert len(client.calls) == 2 and [p.name for p in tmp_path.iterdir()] == ["players_nfl_2026-10-13.json"]


def test_position_limits_and_my_roster():
    assert sl.position_limits({"position_limit_dl": 2, "position_limit_lb": 3}, fallback=2) == {
        "DL": 2,
        "LB": 3,
        "DB": 2,
    }
    rosters = [{"roster_id": 1, "owner_id": "x"}, {"roster_id": 7, "owner_id": "y", "co_owners": ["u1"]}]
    assert sl.my_roster(rosters, "u1")["roster_id"] == 7
    assert sl.my_roster(rosters, "nobody") is None
