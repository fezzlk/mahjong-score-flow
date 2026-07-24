from __future__ import annotations

import pytest

from app.game_session import (
    GameOptions,
    GameSession,
    PlayerState,
    RoundRecord,
    advance_round,
    apply_draw,
    apply_ron,
    apply_tsumo,
    create_game,
    get_dealer_seat,
    get_round_wind,
    undo_last,
    _base_points,
    _calc_base,
    _ceil100,
    _calc_ron_payment,
    _calc_tsumo_payments,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _new_session(starting_points: int = 25000, game_type: str = "east_only") -> GameSession:
    return create_game(["Alice", "Bob", "Carol", "Dave"],
                       starting_points=starting_points, game_type=game_type)


def _total_points(session: GameSession) -> int:
    return sum(p.points for p in session.players)


# ---------------------------------------------------------------------------
# Game creation
# ---------------------------------------------------------------------------

class TestCreateGame:
    def test_creates_four_players(self):
        session = _new_session()
        assert len(session.players) == 4
        for i, p in enumerate(session.players):
            assert p.seat == i
            assert p.points == 25000

    def test_initial_state(self):
        session = _new_session()
        assert session.status == "active"
        assert session.current_round == 0
        assert session.current_honba == 0
        assert session.current_kyotaku == 0
        assert session.rounds == []
        assert session.game_type == "east_only"

    def test_custom_starting_points(self):
        session = _new_session(starting_points=30000)
        assert all(p.points == 30000 for p in session.players)

    def test_requires_four_players(self):
        with pytest.raises(ValueError, match="4 player"):
            create_game(["A", "B", "C"])

    def test_east_south_game_type(self):
        session = _new_session(game_type="east_south")
        assert session.game_type == "east_south"


# ---------------------------------------------------------------------------
# Dealer / wind helpers
# ---------------------------------------------------------------------------

class TestDealerAndWind:
    def test_initial_dealer_is_seat_0(self):
        session = _new_session()
        assert get_dealer_seat(session) == 0

    def test_dealer_rotates_with_round(self):
        session = _new_session()
        session.current_round = 1
        assert get_dealer_seat(session) == 1
        session.current_round = 3
        assert get_dealer_seat(session) == 3

    def test_round_wind(self):
        session = _new_session()
        assert get_round_wind(session) == "E"
        session.current_round = 3
        assert get_round_wind(session) == "E"
        session.current_round = 4
        assert get_round_wind(session) == "S"
        session.current_round = 8
        assert get_round_wind(session) == "W"


# ---------------------------------------------------------------------------
# Base points calculation
# ---------------------------------------------------------------------------

class TestBasePoints:
    def test_mangan(self):
        assert _base_points(5, 30) == 2000
        assert _base_points(4, 40) == 2000
        assert _base_points(3, 70) == 2000

    def test_haneman(self):
        assert _base_points(6, 30) == 3000
        assert _base_points(7, 30) == 3000

    def test_baiman(self):
        assert _base_points(8, 30) == 4000

    def test_sanbaiman(self):
        assert _base_points(11, 30) == 6000

    def test_kazoe_yakuman(self):
        assert _base_points(13, 30) == 8000

    def test_normal(self):
        # 1 han 30 fu = 30 * 2^3 = 240
        assert _base_points(1, 30) == 240

    def test_calc_base_yakuman(self):
        assert _calc_base(1, 30, yakuman_multiplier=1) == 8000
        assert _calc_base(1, 30, yakuman_multiplier=2) == 16000

    def test_ceil100(self):
        assert _ceil100(100) == 100
        assert _ceil100(101) == 200
        assert _ceil100(1920) == 2000


# ---------------------------------------------------------------------------
# Ron
# ---------------------------------------------------------------------------

class TestRon:
    def test_non_dealer_ron(self):
        session = _new_session()
        # Seat 1 (non-dealer) rons seat 2, 1 han 30 fu
        # base = 240, ron = 240*4 = 960 -> ceil100 = 1000
        record = apply_ron(session, winner_seat=1, loser_seat=2, han=1, fu=30)
        assert record.result_type == "ron"
        assert record.winner_seat == 1
        assert record.loser_seat == 2
        assert session.players[1].points == 25000 + 1000
        assert session.players[2].points == 25000 - 1000
        assert _total_points(session) == 100000

    def test_dealer_ron(self):
        session = _new_session()
        # Seat 0 (dealer) rons seat 1, 1 han 30 fu
        # base = 240, ron = 240*6 = 1440 -> ceil100 = 1500
        record = apply_ron(session, winner_seat=0, loser_seat=1, han=1, fu=30)
        assert session.players[0].points == 25000 + 1500
        assert session.players[1].points == 25000 - 1500
        assert _total_points(session) == 100000

    def test_ron_with_honba(self):
        session = _new_session()
        session.current_honba = 2
        # honba bonus = 2 * 300 = 600
        record = apply_ron(session, winner_seat=1, loser_seat=2, han=1, fu=30)
        # base payment 1000 + 600 honba = 1600
        assert session.players[1].points == 25000 + 1600
        assert session.players[2].points == 25000 - 1600

    def test_ron_with_kyotaku(self):
        session = _new_session()
        session.current_kyotaku = 2
        record = apply_ron(session, winner_seat=1, loser_seat=2, han=1, fu=30)
        # Winner gets 1000 payment + 2000 kyotaku
        assert session.players[1].points == 25000 + 1000 + 2000
        assert session.players[2].points == 25000 - 1000
        # kyotaku resets
        assert session.current_kyotaku == 0

    def test_ron_with_riichi(self):
        session = _new_session()
        record = apply_ron(session, winner_seat=1, loser_seat=2, han=1, fu=30,
                           riichi_seats=[1, 3])
        # Seat 1 declares riichi: -1000, seat 3 declares riichi: -1000
        # Those go to kyotaku, then winner collects kyotaku
        # Payment: 1000 (ron) + 2000 (2 riichi sticks) = 3000 to winner
        # But riichi cost for seat 1 (the winner): -1000
        # Net for seat 1: +1000 (ron) + 2000 (kyotaku) - 1000 (riichi) = +2000
        # Seat 3: -1000 (riichi)
        assert session.players[1].points == 25000 + 2000
        assert session.players[2].points == 25000 - 1000
        assert session.players[3].points == 25000 - 1000
        assert session.current_kyotaku == 0

    def test_ron_mangan(self):
        session = _new_session()
        # Non-dealer mangan ron: base 2000, ron = 2000*4 = 8000
        record = apply_ron(session, winner_seat=1, loser_seat=2, han=5, fu=30)
        assert session.players[1].points == 25000 + 8000
        assert session.players[2].points == 25000 - 8000

    def test_ron_yakuman(self):
        session = _new_session()
        # Non-dealer yakuman ron: base 8000, ron = 8000*4 = 32000
        record = apply_ron(session, winner_seat=1, loser_seat=2, han=1, fu=30,
                           yakuman_multiplier=1)
        assert session.players[1].points == 25000 + 32000
        assert session.players[2].points == 25000 - 32000

    def test_ron_same_seat_error(self):
        session = _new_session()
        with pytest.raises(ValueError, match="same"):
            apply_ron(session, winner_seat=1, loser_seat=1, han=1, fu=30)

    def test_ron_invalid_seat(self):
        session = _new_session()
        with pytest.raises(ValueError, match="Invalid seat"):
            apply_ron(session, winner_seat=5, loser_seat=1, han=1, fu=30)

    def test_ron_game_not_active(self):
        session = _new_session()
        session.status = "finished"
        with pytest.raises(ValueError, match="not active"):
            apply_ron(session, winner_seat=1, loser_seat=2, han=1, fu=30)


# ---------------------------------------------------------------------------
# Tsumo
# ---------------------------------------------------------------------------

class TestTsumo:
    def test_non_dealer_tsumo(self):
        session = _new_session()
        # Seat 1 tsumo, 1 han 30 fu
        # base = 240
        # dealer pays ceil(240*2) = ceil(480) = 500
        # non-dealer pays ceil(240) = ceil(240) = 300
        record = apply_tsumo(session, winner_seat=1, han=1, fu=30)
        assert record.result_type == "tsumo"
        assert session.players[0].points == 25000 - 500  # dealer
        assert session.players[1].points == 25000 + 500 + 300 + 300  # winner
        assert session.players[2].points == 25000 - 300
        assert session.players[3].points == 25000 - 300
        assert _total_points(session) == 100000

    def test_dealer_tsumo(self):
        session = _new_session()
        # Seat 0 (dealer) tsumo, 1 han 30 fu
        # base = 240, each pays ceil(240*2) = 500
        record = apply_tsumo(session, winner_seat=0, han=1, fu=30)
        assert session.players[0].points == 25000 + 1500  # 500 * 3
        assert session.players[1].points == 25000 - 500
        assert session.players[2].points == 25000 - 500
        assert session.players[3].points == 25000 - 500
        assert _total_points(session) == 100000

    def test_tsumo_with_honba(self):
        session = _new_session()
        session.current_honba = 1
        # honba per player = 100
        record = apply_tsumo(session, winner_seat=1, han=1, fu=30)
        # dealer pays 500 + 100 = 600
        # non-dealer pays 300 + 100 = 400
        assert session.players[0].points == 25000 - 600
        assert session.players[1].points == 25000 + 600 + 400 + 400
        assert session.players[2].points == 25000 - 400
        assert session.players[3].points == 25000 - 400

    def test_tsumo_with_kyotaku(self):
        session = _new_session()
        session.current_kyotaku = 3
        record = apply_tsumo(session, winner_seat=1, han=1, fu=30)
        # Winner gets tsumo payments + 3000 kyotaku
        expected_winner = 25000 + 500 + 300 + 300 + 3000
        assert session.players[1].points == expected_winner
        assert session.current_kyotaku == 0

    def test_tsumo_mangan_non_dealer(self):
        session = _new_session()
        # Non-dealer mangan tsumo: base 2000
        # dealer pays ceil(2000*2) = 4000
        # non-dealer pays ceil(2000) = 2000
        record = apply_tsumo(session, winner_seat=1, han=5, fu=30)
        assert session.players[0].points == 25000 - 4000
        assert session.players[1].points == 25000 + 4000 + 2000 + 2000
        assert session.players[2].points == 25000 - 2000
        assert session.players[3].points == 25000 - 2000

    def test_tsumo_mangan_dealer(self):
        session = _new_session()
        # Dealer mangan tsumo: base 2000, each pays ceil(2000*2) = 4000
        record = apply_tsumo(session, winner_seat=0, han=5, fu=30)
        assert session.players[0].points == 25000 + 12000
        assert session.players[1].points == 25000 - 4000
        assert session.players[2].points == 25000 - 4000
        assert session.players[3].points == 25000 - 4000

    def test_tsumo_invalid_seat(self):
        session = _new_session()
        with pytest.raises(ValueError, match="Invalid seat"):
            apply_tsumo(session, winner_seat=4, han=1, fu=30)

    def test_tsumo_game_not_active(self):
        session = _new_session()
        session.status = "finished"
        with pytest.raises(ValueError, match="not active"):
            apply_tsumo(session, winner_seat=0, han=1, fu=30)


# ---------------------------------------------------------------------------
# Draw
# ---------------------------------------------------------------------------

class TestDraw:
    def test_draw_zero_tenpai(self):
        session = _new_session()
        record = apply_draw(session, tenpai_seats=[])
        assert record.result_type == "draw"
        # No point exchange
        assert all(p.points == 25000 for p in session.players)

    def test_draw_four_tenpai(self):
        session = _new_session()
        record = apply_draw(session, tenpai_seats=[0, 1, 2, 3])
        assert all(p.points == 25000 for p in session.players)

    def test_draw_one_tenpai(self):
        session = _new_session()
        record = apply_draw(session, tenpai_seats=[0])
        # Seat 0 gets 3000 (1000 from each noten)
        assert session.players[0].points == 25000 + 3000
        assert session.players[1].points == 25000 - 1000
        assert session.players[2].points == 25000 - 1000
        assert session.players[3].points == 25000 - 1000
        assert _total_points(session) == 100000

    def test_draw_two_tenpai(self):
        session = _new_session()
        record = apply_draw(session, tenpai_seats=[0, 1])
        # Each tenpai gets 1500, each noten pays 1500
        assert session.players[0].points == 25000 + 1500
        assert session.players[1].points == 25000 + 1500
        assert session.players[2].points == 25000 - 1500
        assert session.players[3].points == 25000 - 1500
        assert _total_points(session) == 100000

    def test_draw_three_tenpai(self):
        session = _new_session()
        record = apply_draw(session, tenpai_seats=[0, 1, 2])
        # Each tenpai gets 1000, noten pays 3000
        assert session.players[0].points == 25000 + 1000
        assert session.players[1].points == 25000 + 1000
        assert session.players[2].points == 25000 + 1000
        assert session.players[3].points == 25000 - 3000
        assert _total_points(session) == 100000

    def test_draw_with_riichi(self):
        session = _new_session()
        record = apply_draw(session, tenpai_seats=[0], riichi_seats=[0, 1])
        # Riichi: seat 0 -1000, seat 1 -1000 -> kyotaku stays
        # Tenpai: seat 0 +3000, seats 1,2,3 -1000 each
        # Net: seat 0: +3000 - 1000 = +2000
        # seat 1: -1000 - 1000 = -2000
        # seat 2: -1000
        # seat 3: -1000
        assert session.players[0].points == 25000 + 2000
        assert session.players[1].points == 25000 - 2000
        assert session.players[2].points == 25000 - 1000
        assert session.players[3].points == 25000 - 1000
        # Kyotaku remains from riichi
        assert session.current_kyotaku == 2

    def test_draw_game_not_active(self):
        session = _new_session()
        session.status = "finished"
        with pytest.raises(ValueError, match="not active"):
            apply_draw(session, tenpai_seats=[])


# ---------------------------------------------------------------------------
# Dealer rotation & honba
# ---------------------------------------------------------------------------

class TestAdvanceRound:
    def test_dealer_win_retains_and_increments_honba(self):
        session = _new_session()
        # Dealer (seat 0) wins ron
        apply_ron(session, winner_seat=0, loser_seat=1, han=1, fu=30)
        assert session.current_round == 0  # dealer stays
        assert get_dealer_seat(session) == 0
        assert session.current_honba == 1

    def test_non_dealer_win_rotates_and_resets_honba(self):
        session = _new_session()
        apply_ron(session, winner_seat=1, loser_seat=2, han=1, fu=30)
        assert session.current_round == 1
        assert get_dealer_seat(session) == 1
        assert session.current_honba == 0

    def test_draw_dealer_tenpai_retains(self):
        session = _new_session()
        apply_draw(session, tenpai_seats=[0])  # dealer tenpai
        assert session.current_round == 0
        assert session.current_honba == 1

    def test_draw_dealer_noten_rotates_and_increments_honba(self):
        session = _new_session()
        apply_draw(session, tenpai_seats=[1])  # dealer not tenpai
        assert session.current_round == 1
        assert session.current_honba == 1

    def test_honba_stacks_on_dealer_wins(self):
        session = _new_session()
        apply_ron(session, winner_seat=0, loser_seat=1, han=1, fu=30)
        apply_ron(session, winner_seat=0, loser_seat=1, han=1, fu=30)
        assert session.current_honba == 2
        assert session.current_round == 0

    def test_honba_resets_on_non_dealer_win(self):
        session = _new_session()
        # Dealer wins twice
        apply_ron(session, winner_seat=0, loser_seat=1, han=1, fu=30)
        apply_ron(session, winner_seat=0, loser_seat=1, han=1, fu=30)
        assert session.current_honba == 2
        # Non-dealer wins
        apply_ron(session, winner_seat=1, loser_seat=2, han=1, fu=30)
        assert session.current_honba == 0


# ---------------------------------------------------------------------------
# Game end
# ---------------------------------------------------------------------------

class TestGameEnd:
    def test_east_only_ends_after_4_rounds_when_someone_above_30000(self):
        # With 30000 starting points, someone is already >= 30000 so no extension
        session = _new_session(game_type="east_only", starting_points=30000)
        for _ in range(4):
            dealer = get_dealer_seat(session)
            winner = (dealer + 1) % 4
            loser = (dealer + 2) % 4
            apply_ron(session, winner_seat=winner, loser_seat=loser, han=1, fu=30)
        assert session.status == "finished"

    def test_east_only_extends_to_south_when_all_below_30000(self):
        # 25000 start, 1 han 30 fu = 1000 pt swaps, nobody reaches 30000 → 南入
        session = _new_session(game_type="east_only")
        for _ in range(4):
            dealer = get_dealer_seat(session)
            winner = (dealer + 1) % 4
            loser = (dealer + 2) % 4
            apply_ron(session, winner_seat=winner, loser_seat=loser, han=1, fu=30)
        assert session.status == "active"
        assert session.current_round == 4
        assert get_round_wind(session) == "S"

    def test_east_only_extension_ends_when_someone_reaches_30000(self):
        session = _new_session(game_type="east_only")
        # Play through East, all < 30000
        for _ in range(4):
            dealer = get_dealer_seat(session)
            winner = (dealer + 1) % 4
            loser = (dealer + 2) % 4
            apply_ron(session, winner_seat=winner, loser_seat=loser, han=1, fu=30)
        assert session.status == "active"
        # Now in 南入, give someone a big win to reach 30000
        apply_ron(session, winner_seat=1, loser_seat=2, han=5, fu=30)  # mangan 8000
        # Check if someone >= 30000
        if any(p.points >= 30000 for p in session.players):
            assert session.status == "finished"

    def test_east_only_extension_ends_at_south4_without_shanyu(self):
        session = _new_session(game_type="east_only")
        # Play through 8 rounds (East + South), nobody reaches 30000
        for _ in range(8):
            dealer = get_dealer_seat(session)
            winner = (dealer + 1) % 4
            loser = (dealer + 2) % 4
            apply_ron(session, winner_seat=winner, loser_seat=loser, han=1, fu=30)
        # Without shanyu, game ends at end of South
        assert session.status == "finished"

    def test_east_only_shanyu_extends_to_west(self):
        opts = GameOptions(shanyu=True)
        session = create_game(["A", "B", "C", "D"], starting_points=25000,
                              game_type="east_only", options=opts)
        for _ in range(8):
            dealer = get_dealer_seat(session)
            winner = (dealer + 1) % 4
            loser = (dealer + 2) % 4
            apply_ron(session, winner_seat=winner, loser_seat=loser, han=1, fu=30)
        assert session.status == "active"
        assert get_round_wind(session) == "W"

    def test_east_south_ends_after_8_rounds_when_someone_above_30000(self):
        session = _new_session(game_type="east_south", starting_points=30000)
        for _ in range(8):
            dealer = get_dealer_seat(session)
            winner = (dealer + 1) % 4
            loser = (dealer + 2) % 4
            apply_ron(session, winner_seat=winner, loser_seat=loser, han=1, fu=30)
        assert session.status == "finished"

    def test_east_south_no_extension_without_shanyu(self):
        session = _new_session(game_type="east_south")
        for _ in range(8):
            dealer = get_dealer_seat(session)
            winner = (dealer + 1) % 4
            loser = (dealer + 2) % 4
            apply_ron(session, winner_seat=winner, loser_seat=loser, han=1, fu=30)
        assert session.status == "finished"

    def test_east_south_shanyu_extends_to_west(self):
        opts = GameOptions(shanyu=True)
        session = create_game(["A", "B", "C", "D"], starting_points=25000,
                              game_type="east_south", options=opts)
        for _ in range(8):
            dealer = get_dealer_seat(session)
            winner = (dealer + 1) % 4
            loser = (dealer + 2) % 4
            apply_ron(session, winner_seat=winner, loser_seat=loser, han=1, fu=30)
        assert session.status == "active"
        assert get_round_wind(session) == "W"

    def test_game_not_finished_before_all_rounds(self):
        session = _new_session(game_type="east_only", starting_points=30000)
        for _ in range(3):
            dealer = get_dealer_seat(session)
            winner = (dealer + 1) % 4
            loser = (dealer + 2) % 4
            apply_ron(session, winner_seat=winner, loser_seat=loser, han=1, fu=30)
        assert session.status == "active"


# ---------------------------------------------------------------------------
# Round record
# ---------------------------------------------------------------------------

class TestRoundRecord:
    def test_ron_record_fields(self):
        session = _new_session()
        record = apply_ron(session, winner_seat=1, loser_seat=2, han=3, fu=30,
                           riichi_seats=[1])
        assert record.round_number == 0
        assert record.round_wind == "E"
        assert record.dealer_seat == 0
        assert record.honba == 0
        assert record.result_type == "ron"
        assert record.winner_seat == 1
        assert record.loser_seat == 2
        assert record.riichi_seats == [1]
        assert record.score_result == {"han": 3, "fu": 30, "yakuman_multiplier": 0}

    def test_tsumo_record_fields(self):
        session = _new_session()
        record = apply_tsumo(session, winner_seat=0, han=2, fu=40)
        assert record.result_type == "tsumo"
        assert record.winner_seat == 0
        assert record.loser_seat is None

    def test_draw_record_fields(self):
        session = _new_session()
        record = apply_draw(session, tenpai_seats=[0, 2], riichi_seats=[0])
        assert record.result_type == "draw"
        assert record.winner_seat is None
        assert record.riichi_seats == [0]

    def test_rounds_history_grows(self):
        session = _new_session()
        apply_ron(session, winner_seat=1, loser_seat=2, han=1, fu=30)
        assert len(session.rounds) == 1
        apply_draw(session, tenpai_seats=[])
        assert len(session.rounds) == 2


# ---------------------------------------------------------------------------
# Point conservation
# ---------------------------------------------------------------------------

class TestPointConservation:
    """Verify total points are conserved across various scenarios."""

    def test_ron_conserves_points(self):
        session = _new_session()
        apply_ron(session, winner_seat=1, loser_seat=2, han=3, fu=40)
        assert _total_points(session) == 100000

    def test_tsumo_conserves_points(self):
        session = _new_session()
        apply_tsumo(session, winner_seat=1, han=3, fu=40)
        assert _total_points(session) == 100000

    def test_draw_conserves_points(self):
        session = _new_session()
        apply_draw(session, tenpai_seats=[0, 1])
        assert _total_points(session) == 100000

    def test_riichi_conserves_points(self):
        """Riichi sticks move to kyotaku but total is preserved when someone wins."""
        session = _new_session()
        apply_draw(session, tenpai_seats=[0], riichi_seats=[0, 1])
        # 2000 in kyotaku
        assert _total_points(session) + session.current_kyotaku * 1000 == 100000
        # Now someone wins and collects kyotaku
        apply_ron(session, winner_seat=2, loser_seat=3, han=1, fu=30)
        assert _total_points(session) == 100000


# ---------------------------------------------------------------------------
# Undo
# ---------------------------------------------------------------------------

class TestUndo:
    """Test undo functionality."""

    def test_undo_ron_restores_points(self):
        session = _new_session()
        original_points = [p.points for p in session.players]
        apply_ron(session, winner_seat=1, loser_seat=2, han=3, fu=30)
        assert [p.points for p in session.players] != original_points
        removed = undo_last(session)
        assert removed is not None
        assert removed.result_type == "ron"
        assert [p.points for p in session.players] == original_points

    def test_undo_tsumo_restores_points(self):
        session = _new_session()
        original_points = [p.points for p in session.players]
        apply_tsumo(session, winner_seat=0, han=2, fu=30)
        removed = undo_last(session)
        assert removed is not None
        assert [p.points for p in session.players] == original_points

    def test_undo_draw_restores_points(self):
        session = _new_session()
        original_points = [p.points for p in session.players]
        apply_draw(session, tenpai_seats=[0, 1])
        removed = undo_last(session)
        assert removed is not None
        assert [p.points for p in session.players] == original_points

    def test_undo_restores_round_number(self):
        session = _new_session()
        assert session.current_round == 0
        apply_ron(session, winner_seat=1, loser_seat=0, han=1, fu=30)
        assert session.current_round == 1
        undo_last(session)
        assert session.current_round == 0

    def test_undo_restores_honba(self):
        session = _new_session()
        # Dealer win (seat 0) -> honba increments
        apply_ron(session, winner_seat=0, loser_seat=1, han=1, fu=30)
        assert session.current_honba == 1
        undo_last(session)
        assert session.current_honba == 0

    def test_undo_restores_kyotaku(self):
        session = _new_session()
        apply_draw(session, tenpai_seats=[0], riichi_seats=[0, 1])
        assert session.current_kyotaku == 2
        undo_last(session)
        assert session.current_kyotaku == 0

    def test_undo_empty_returns_none(self):
        session = _new_session()
        assert undo_last(session) is None

    def test_undo_restores_game_status(self):
        session = _new_session(starting_points=30000)
        # Play through all 4 rounds to finish (30000 start = someone >= 30000)
        for i in range(4):
            apply_ron(session, winner_seat=(i + 1) % 4, loser_seat=i, han=1, fu=30)
        assert session.status == "finished"
        undo_last(session)
        assert session.status == "active"

    def test_multiple_undo(self):
        session = _new_session()
        original = [p.points for p in session.players]
        apply_ron(session, winner_seat=1, loser_seat=0, han=1, fu=30)
        apply_ron(session, winner_seat=2, loser_seat=1, han=2, fu=30)
        assert len(session.rounds) == 2
        undo_last(session)
        assert len(session.rounds) == 1
        undo_last(session)
        assert len(session.rounds) == 0
        assert [p.points for p in session.players] == original
