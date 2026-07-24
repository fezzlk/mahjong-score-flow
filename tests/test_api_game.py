from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app, _game_sessions


@pytest.fixture(autouse=True)
def _clear_sessions():
    """Clear in-memory game sessions before each test."""
    _game_sessions.clear()
    yield
    _game_sessions.clear()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def game_id(client: TestClient) -> str:
    """Create a game and return its ID."""
    resp = client.post("/api/v1/games", json={
        "player_names": ["Alice", "Bob", "Carol", "Dave"],
    })
    assert resp.status_code == 201
    return resp.json()["game_id"]


# ---------------------------------------------------------------------------
# POST /api/v1/games
# ---------------------------------------------------------------------------

class TestCreateGame:
    def test_create_game(self, client: TestClient):
        resp = client.post("/api/v1/games", json={
            "player_names": ["A", "B", "C", "D"],
            "starting_points": 30000,
            "game_type": "east_south",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert "game_id" in data
        assert data["game_state"]["status"] == "active"
        assert len(data["game_state"]["players"]) == 4
        assert all(p["points"] == 30000 for p in data["game_state"]["players"])

    def test_create_game_default_values(self, client: TestClient):
        resp = client.post("/api/v1/games", json={
            "player_names": ["A", "B", "C", "D"],
        })
        assert resp.status_code == 201
        data = resp.json()
        assert all(p["points"] == 25000 for p in data["game_state"]["players"])

    def test_create_game_wrong_player_count(self, client: TestClient):
        resp = client.post("/api/v1/games", json={
            "player_names": ["A", "B", "C"],
        })
        assert resp.status_code == 422

    def test_create_game_too_many_players(self, client: TestClient):
        resp = client.post("/api/v1/games", json={
            "player_names": ["A", "B", "C", "D", "E"],
        })
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/v1/games/{game_id}
# ---------------------------------------------------------------------------

class TestGetGame:
    def test_get_game(self, client: TestClient, game_id: str):
        resp = client.get(f"/api/v1/games/{game_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["game_id"] == game_id
        assert data["status"] == "active"
        assert data["current_round"] == 0
        assert data["current_dealer"] == 0
        assert data["current_round_wind"] == "E"
        assert data["current_honba"] == 0
        assert data["current_kyotaku"] == 0
        assert data["rounds_played"] == 0

    def test_get_game_not_found(self, client: TestClient):
        fake_id = str(uuid.uuid4())
        resp = client.get(f"/api/v1/games/{fake_id}")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/v1/games/{game_id}/ron
# ---------------------------------------------------------------------------

class TestRonEndpoint:
    def test_ron(self, client: TestClient, game_id: str):
        resp = client.post(f"/api/v1/games/{game_id}/ron", json={
            "winner_seat": 1,
            "loser_seat": 2,
            "han": 3,
            "fu": 30,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["round_result"]["result_type"] == "ron"
        assert data["round_result"]["winner_seat"] == 1
        assert data["round_result"]["loser_seat"] == 2
        # Verify points changed
        players = {p["seat"]: p["points"] for p in data["game_state"]["players"]}
        assert players[1] > 25000
        assert players[2] < 25000

    def test_ron_with_riichi(self, client: TestClient, game_id: str):
        resp = client.post(f"/api/v1/games/{game_id}/ron", json={
            "winner_seat": 1,
            "loser_seat": 2,
            "han": 1,
            "fu": 30,
            "riichi_seats": [1],
        })
        assert resp.status_code == 200

    def test_ron_same_seat(self, client: TestClient, game_id: str):
        resp = client.post(f"/api/v1/games/{game_id}/ron", json={
            "winner_seat": 1,
            "loser_seat": 1,
            "han": 1,
            "fu": 30,
        })
        assert resp.status_code == 422

    def test_ron_not_found(self, client: TestClient):
        fake_id = str(uuid.uuid4())
        resp = client.post(f"/api/v1/games/{fake_id}/ron", json={
            "winner_seat": 1,
            "loser_seat": 2,
            "han": 1,
            "fu": 30,
        })
        assert resp.status_code == 404

    def test_ron_invalid_seat(self, client: TestClient, game_id: str):
        resp = client.post(f"/api/v1/games/{game_id}/ron", json={
            "winner_seat": 5,
            "loser_seat": 2,
            "han": 1,
            "fu": 30,
        })
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/v1/games/{game_id}/tsumo
# ---------------------------------------------------------------------------

class TestTsumoEndpoint:
    def test_tsumo(self, client: TestClient, game_id: str):
        resp = client.post(f"/api/v1/games/{game_id}/tsumo", json={
            "winner_seat": 0,
            "han": 2,
            "fu": 30,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["round_result"]["result_type"] == "tsumo"
        assert data["round_result"]["winner_seat"] == 0

    def test_tsumo_not_found(self, client: TestClient):
        fake_id = str(uuid.uuid4())
        resp = client.post(f"/api/v1/games/{fake_id}/tsumo", json={
            "winner_seat": 0,
            "han": 1,
            "fu": 30,
        })
        assert resp.status_code == 404

    def test_tsumo_invalid_seat(self, client: TestClient, game_id: str):
        resp = client.post(f"/api/v1/games/{game_id}/tsumo", json={
            "winner_seat": 5,
            "han": 1,
            "fu": 30,
        })
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/v1/games/{game_id}/draw
# ---------------------------------------------------------------------------

class TestDrawEndpoint:
    def test_draw(self, client: TestClient, game_id: str):
        resp = client.post(f"/api/v1/games/{game_id}/draw", json={
            "tenpai_seats": [0, 1],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["round_result"]["result_type"] == "draw"

    def test_draw_empty(self, client: TestClient, game_id: str):
        resp = client.post(f"/api/v1/games/{game_id}/draw", json={})
        assert resp.status_code == 200

    def test_draw_not_found(self, client: TestClient):
        fake_id = str(uuid.uuid4())
        resp = client.post(f"/api/v1/games/{fake_id}/draw", json={
            "tenpai_seats": [],
        })
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/v1/games/{game_id}/history
# ---------------------------------------------------------------------------

class TestHistoryEndpoint:
    def test_history_empty(self, client: TestClient, game_id: str):
        resp = client.get(f"/api/v1/games/{game_id}/history")
        assert resp.status_code == 200
        data = resp.json()
        assert data["rounds"] == []

    def test_history_after_rounds(self, client: TestClient, game_id: str):
        client.post(f"/api/v1/games/{game_id}/ron", json={
            "winner_seat": 1, "loser_seat": 2, "han": 1, "fu": 30,
        })
        client.post(f"/api/v1/games/{game_id}/draw", json={
            "tenpai_seats": [0],
        })
        resp = client.get(f"/api/v1/games/{game_id}/history")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["rounds"]) == 2
        assert data["rounds"][0]["result_type"] == "ron"
        assert data["rounds"][1]["result_type"] == "draw"

    def test_history_not_found(self, client: TestClient):
        fake_id = str(uuid.uuid4())
        resp = client.get(f"/api/v1/games/{fake_id}/history")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/v1/games/{game_id}
# ---------------------------------------------------------------------------

class TestDeleteGame:
    def test_delete_game(self, client: TestClient, game_id: str):
        resp = client.delete(f"/api/v1/games/{game_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "deleted"
        # Verify game is gone
        resp = client.get(f"/api/v1/games/{game_id}")
        assert resp.status_code == 404

    def test_delete_game_not_found(self, client: TestClient):
        fake_id = str(uuid.uuid4())
        resp = client.delete(f"/api/v1/games/{fake_id}")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------

class TestValidation:
    def test_ron_missing_required_fields(self, client: TestClient, game_id: str):
        resp = client.post(f"/api/v1/games/{game_id}/ron", json={})
        assert resp.status_code == 422

    def test_tsumo_missing_required_fields(self, client: TestClient, game_id: str):
        resp = client.post(f"/api/v1/games/{game_id}/tsumo", json={})
        assert resp.status_code == 422

    def test_ron_negative_han(self, client: TestClient, game_id: str):
        resp = client.post(f"/api/v1/games/{game_id}/ron", json={
            "winner_seat": 1,
            "loser_seat": 2,
            "han": 0,
            "fu": 30,
        })
        assert resp.status_code == 422

    def test_ron_fu_too_low(self, client: TestClient, game_id: str):
        resp = client.post(f"/api/v1/games/{game_id}/ron", json={
            "winner_seat": 1,
            "loser_seat": 2,
            "han": 1,
            "fu": 10,
        })
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Full game flow
# ---------------------------------------------------------------------------

class TestFullGameFlow:
    def test_full_east_only_game(self, client: TestClient):
        """Play a complete east-only game with 4 rounds.
        Uses 30000 starting points so someone is >= 30000 and no 南入 occurs."""
        resp = client.post("/api/v1/games", json={
            "player_names": ["Alice", "Bob", "Carol", "Dave"],
            "starting_points": 30000,
        })
        gid = resp.json()["game_id"]
        for _ in range(4):
            state = client.get(f"/api/v1/games/{gid}").json()
            dealer = state["current_dealer"]
            winner = (dealer + 1) % 4
            loser = (dealer + 2) % 4
            resp = client.post(f"/api/v1/games/{gid}/ron", json={
                "winner_seat": winner,
                "loser_seat": loser,
                "han": 1,
                "fu": 30,
            })
            assert resp.status_code == 200

        # Game should be finished
        state = client.get(f"/api/v1/games/{gid}").json()
        assert state["status"] == "finished"

        # History should have 4 rounds
        history = client.get(f"/api/v1/games/{gid}/history").json()
        assert len(history["rounds"]) == 4


class TestCreateGameDefaults:
    """Creating a game without player_names uses defaults."""

    def test_create_game_no_names(self, client: TestClient):
        resp = client.post("/api/v1/games", json={})
        assert resp.status_code == 201
        data = resp.json()
        names = [p["name"] for p in data["game_state"]["players"]]
        assert names == ["プレイヤー1", "プレイヤー2", "プレイヤー3", "プレイヤー4"]


class TestSeatClaim:
    """Claiming a seat sets the player name."""

    def test_claim_seat(self, client: TestClient, game_id: str):
        resp = client.post(f"/api/v1/games/{game_id}/seats/0/claim", json={"name": "太郎"})
        assert resp.status_code == 200
        state = resp.json()["game_state"]
        assert state["players"][0]["name"] == "太郎"

    def test_claim_seat_not_found(self, client: TestClient):
        fake_id = str(uuid.uuid4())
        resp = client.post(f"/api/v1/games/{fake_id}/seats/0/claim", json={"name": "太郎"})
        assert resp.status_code == 404

    def test_claim_seat_invalid_seat(self, client: TestClient, game_id: str):
        resp = client.post(f"/api/v1/games/{game_id}/seats/5/claim", json={"name": "太郎"})
        assert resp.status_code == 422

    def test_claim_seat_empty_name(self, client: TestClient, game_id: str):
        resp = client.post(f"/api/v1/games/{game_id}/seats/0/claim", json={"name": ""})
        assert resp.status_code == 422


class TestSeatSwap:
    """Swapping seats exchanges names and points."""

    def test_swap_seats(self, client: TestClient, game_id: str):
        # First set names
        client.post(f"/api/v1/games/{game_id}/seats/0/claim", json={"name": "太郎"})
        client.post(f"/api/v1/games/{game_id}/seats/1/claim", json={"name": "花子"})

        resp = client.post(f"/api/v1/games/{game_id}/seats/swap", json={"seat_a": 0, "seat_b": 1})
        assert resp.status_code == 200
        state = resp.json()["game_state"]
        assert state["players"][0]["name"] == "花子"
        assert state["players"][1]["name"] == "太郎"

    def test_swap_same_seat(self, client: TestClient, game_id: str):
        resp = client.post(f"/api/v1/games/{game_id}/seats/swap", json={"seat_a": 0, "seat_b": 0})
        assert resp.status_code == 422

    def test_swap_not_found(self, client: TestClient):
        fake_id = str(uuid.uuid4())
        resp = client.post(f"/api/v1/games/{fake_id}/seats/swap", json={"seat_a": 0, "seat_b": 1})
        assert resp.status_code == 404


class TestQRCode:
    """QR code endpoint returns a PNG image."""

    def test_qr_code(self, client: TestClient, game_id: str):
        resp = client.get(f"/api/v1/games/{game_id}/qr")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"
        assert len(resp.content) > 100  # should be a real image

    def test_qr_not_found(self, client: TestClient):
        fake_id = str(uuid.uuid4())
        resp = client.get(f"/api/v1/games/{fake_id}/qr")
        assert resp.status_code == 404
