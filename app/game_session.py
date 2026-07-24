from __future__ import annotations

import copy
import random
import string
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4


WIND_LABELS = ["E", "S", "W", "N"]


@dataclass
class PlayerState:
    name: str
    points: int
    seat: int  # 0=East, 1=South, 2=West, 3=North (initial)


@dataclass
class RoundRecord:
    round_number: int
    round_wind: str
    dealer_seat: int
    honba: int
    result_type: Literal["ron", "tsumo", "draw", "chombo"]
    winner_seat: int | None = None
    loser_seat: int | None = None
    score_result: dict | None = None
    point_changes: dict[int, int] = field(default_factory=dict)
    riichi_seats: list[int] = field(default_factory=list)
    # For multi-ron (double/triple ron)
    winners: list[dict] | None = None  # [{seat, han, fu, yakuman_multiplier, payment}]


@dataclass
class GameSnapshot:
    """Snapshot for undo support."""
    players_points: list[int]
    current_round: int
    current_honba: int
    current_kyotaku: int
    status: Literal["active", "finished"]


@dataclass
class GameOptions:
    """Configurable game rules."""
    hakoire_end: bool = True     # 箱割れ即終了 (end game when a player goes below 0)
    shanyu: bool = False         # 西入あり (半荘戦で全員30000点未満なら西場へ延長)
    peinyu: bool = False         # 北入あり (西場でも全員30000点未満なら北場へ延長)


@dataclass
class GameSession:
    game_id: UUID
    players: list[PlayerState]
    rounds: list[RoundRecord] = field(default_factory=list)
    current_round: int = 0
    current_honba: int = 0
    current_kyotaku: int = 0
    status: Literal["active", "finished"] = "active"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    game_type: Literal["east_only", "east_south"] = "east_only"
    room_code: str = ""
    options: GameOptions = field(default_factory=GameOptions)
    _snapshots: list[GameSnapshot] = field(default_factory=list, repr=False)


def _generate_room_code() -> str:
    """Generate a 6-character alphanumeric room code."""
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=6))


def _take_snapshot(session: GameSession) -> None:
    """Save current state before a mutation for undo support."""
    snap = GameSnapshot(
        players_points=[p.points for p in session.players],
        current_round=session.current_round,
        current_honba=session.current_honba,
        current_kyotaku=session.current_kyotaku,
        status=session.status,
    )
    session._snapshots.append(snap)


def undo_last(session: GameSession) -> RoundRecord | None:
    """Undo the last round. Returns the removed round record, or None if nothing to undo."""
    if not session.rounds or not session._snapshots:
        return None
    snap = session._snapshots.pop()
    removed = session.rounds.pop()
    # Restore state
    for i, p in enumerate(session.players):
        p.points = snap.players_points[i]
    session.current_round = snap.current_round
    session.current_honba = snap.current_honba
    session.current_kyotaku = snap.current_kyotaku
    session.status = snap.status
    return removed


def create_game(player_names: list[str], starting_points: int = 25000,
                game_type: Literal["east_only", "east_south"] = "east_only",
                options: GameOptions | None = None) -> GameSession:
    """Create a new 4-player mahjong game session."""
    if len(player_names) != 4:
        raise ValueError("Exactly 4 player names are required")
    players = [PlayerState(name=name, points=starting_points, seat=i)
               for i, name in enumerate(player_names)]
    return GameSession(
        game_id=uuid4(),
        players=players,
        game_type=game_type,
        room_code=_generate_room_code(),
        options=options or GameOptions(),
    )


def get_dealer_seat(session: GameSession) -> int:
    """Get the current dealer seat (round 0 = seat 0, round 1 = seat 1, etc.)."""
    return session.current_round % 4


def get_round_wind(session: GameSession) -> str:
    """Get the current round wind."""
    wind_index = session.current_round // 4
    if wind_index >= len(WIND_LABELS):
        wind_index = len(WIND_LABELS) - 1
    return WIND_LABELS[wind_index]


def _base_points(han: int, fu: int) -> int:
    """Calculate base points from han and fu, mirroring hand_scoring logic."""
    if han >= 13:
        return 8000
    if han >= 11:
        return 6000
    if han >= 8:
        return 4000
    if han >= 6:
        return 3000
    if han == 5 or (han == 4 and fu >= 40) or (han == 3 and fu >= 70):
        return 2000
    return fu * (2 ** (han + 2))


def _calc_base(han: int, fu: int, yakuman_multiplier: int = 0) -> int:
    """Get effective base points considering yakuman."""
    if yakuman_multiplier > 0:
        return 8000 * yakuman_multiplier
    return _base_points(han, fu)


def _ceil100(value: int) -> int:
    """Ceiling to nearest 100."""
    return ((value + 99) // 100) * 100


def _calc_ron_payment(base: int, is_dealer: bool) -> int:
    """Calculate total ron payment (before honba)."""
    multiplier = 6 if is_dealer else 4
    return _ceil100(base * multiplier)


def _calc_tsumo_payments(base: int, is_dealer_winner: bool) -> tuple[int, int]:
    """Calculate tsumo payments: (dealer_pay, non_dealer_pay).

    If dealer wins: each non-dealer pays ceil(base*2).
    If non-dealer wins: dealer pays ceil(base*2), non-dealers pay ceil(base).
    Returns (dealer_pay, non_dealer_pay) from the perspective of payers.
    """
    if is_dealer_winner:
        each = _ceil100(base * 2)
        return each, each  # dealer_pay unused; all pay same
    dealer_pay = _ceil100(base * 2)
    non_dealer_pay = _ceil100(base)
    return dealer_pay, non_dealer_pay


def _apply_riichi(session: GameSession, riichi_seats: list[int],
                  point_changes: dict[int, int]) -> None:
    """Deduct riichi costs and add to kyotaku."""
    for seat in riichi_seats:
        point_changes[seat] = point_changes.get(seat, 0) - 1000
        session.current_kyotaku += 1


def apply_ron(session: GameSession, winner_seat: int, loser_seat: int,
              han: int, fu: int, yakuman_multiplier: int = 0,
              riichi_seats: list[int] | None = None) -> RoundRecord:
    """Apply a ron win and record the round."""
    if session.status != "active":
        raise ValueError("Game is not active")
    if winner_seat == loser_seat:
        raise ValueError("Winner and loser cannot be the same player")
    if not (0 <= winner_seat <= 3 and 0 <= loser_seat <= 3):
        raise ValueError("Invalid seat number")

    _take_snapshot(session)

    riichi_seats = riichi_seats or []
    point_changes: dict[int, int] = {i: 0 for i in range(4)}

    # Apply riichi deductions first
    _apply_riichi(session, riichi_seats, point_changes)

    dealer_seat = get_dealer_seat(session)
    is_dealer = winner_seat == dealer_seat
    base = _calc_base(han, fu, yakuman_multiplier)
    payment = _calc_ron_payment(base, is_dealer)

    # Honba bonus
    honba_bonus = session.current_honba * 300

    # Loser pays, winner receives
    total_payment = payment + honba_bonus
    point_changes[loser_seat] -= total_payment
    point_changes[winner_seat] += total_payment

    # Winner collects kyotaku
    kyotaku_bonus = session.current_kyotaku * 1000
    point_changes[winner_seat] += kyotaku_bonus

    # Apply point changes
    for seat, change in point_changes.items():
        session.players[seat].points += change

    round_record = RoundRecord(
        round_number=session.current_round,
        round_wind=get_round_wind(session),
        dealer_seat=dealer_seat,
        honba=session.current_honba,
        result_type="ron",
        winner_seat=winner_seat,
        loser_seat=loser_seat,
        score_result={"han": han, "fu": fu, "yakuman_multiplier": yakuman_multiplier},
        point_changes=dict(point_changes),
        riichi_seats=list(riichi_seats),
    )
    session.rounds.append(round_record)

    # Reset kyotaku after win
    session.current_kyotaku = 0

    # Advance round
    dealer_won = winner_seat == dealer_seat
    advance_round(session, dealer_won)

    return round_record


def apply_multi_ron(session: GameSession, loser_seat: int,
                    winners: list[dict],
                    riichi_seats: list[int] | None = None) -> RoundRecord:
    """Apply a double or triple ron.

    winners: list of {seat, han, fu, yakuman_multiplier} dicts.

    Rules:
    - Each winner gets paid independently by the loser.
    - Honba bonus (300 × honba) goes to each winner.
    - Kyotaku goes to the winner closest to loser in turn order (counter-clockwise).
    - Dealer retains if dealer is among the winners.
    """
    if session.status != "active":
        raise ValueError("Game is not active")
    if len(winners) < 1 or len(winners) > 3:
        raise ValueError("Number of winners must be 1-3")
    winner_seats = [w["seat"] for w in winners]
    if loser_seat in winner_seats:
        raise ValueError("Winner and loser cannot be the same player")
    if not all(0 <= s <= 3 for s in winner_seats + [loser_seat]):
        raise ValueError("Invalid seat number")
    if len(set(winner_seats)) != len(winner_seats):
        raise ValueError("Duplicate winner seats")

    _take_snapshot(session)

    riichi_seats = riichi_seats or []
    point_changes: dict[int, int] = {i: 0 for i in range(4)}

    _apply_riichi(session, riichi_seats, point_changes)

    dealer_seat = get_dealer_seat(session)
    honba_bonus = session.current_honba * 300

    # Find kyotaku recipient: winner closest to loser in turn order (counter-clockwise)
    def turn_distance(from_seat: int, to_seat: int) -> int:
        return (to_seat - from_seat) % 4

    kyotaku_recipient = min(winner_seats, key=lambda s: turn_distance(loser_seat, s))

    winners_info = []
    for w in winners:
        seat = w["seat"]
        is_dealer = seat == dealer_seat
        base = _calc_base(w["han"], w["fu"], w.get("yakuman_multiplier", 0))
        payment = _calc_ron_payment(base, is_dealer)
        total = payment + honba_bonus

        point_changes[loser_seat] -= total
        point_changes[seat] += total

        winners_info.append({
            "seat": seat,
            "han": w["han"],
            "fu": w["fu"],
            "yakuman_multiplier": w.get("yakuman_multiplier", 0),
            "payment": total,
        })

    # Kyotaku to closest winner
    kyotaku_bonus = session.current_kyotaku * 1000
    point_changes[kyotaku_recipient] += kyotaku_bonus

    # Apply point changes
    for seat, change in point_changes.items():
        session.players[seat].points += change

    round_record = RoundRecord(
        round_number=session.current_round,
        round_wind=get_round_wind(session),
        dealer_seat=dealer_seat,
        honba=session.current_honba,
        result_type="ron",
        winner_seat=winner_seats[0],  # primary winner for backward compat
        loser_seat=loser_seat,
        score_result={"multi_ron": True, "winners": winners_info},
        point_changes=dict(point_changes),
        riichi_seats=list(riichi_seats),
        winners=winners_info,
    )
    session.rounds.append(round_record)

    session.current_kyotaku = 0

    dealer_won = dealer_seat in winner_seats
    advance_round(session, dealer_won)

    return round_record


def apply_tsumo(session: GameSession, winner_seat: int,
                han: int, fu: int, yakuman_multiplier: int = 0,
                riichi_seats: list[int] | None = None) -> RoundRecord:
    """Apply a tsumo win and record the round."""
    if session.status != "active":
        raise ValueError("Game is not active")
    if not (0 <= winner_seat <= 3):
        raise ValueError("Invalid seat number")

    _take_snapshot(session)

    riichi_seats = riichi_seats or []
    point_changes: dict[int, int] = {i: 0 for i in range(4)}

    # Apply riichi deductions first
    _apply_riichi(session, riichi_seats, point_changes)

    dealer_seat = get_dealer_seat(session)
    is_dealer_winner = winner_seat == dealer_seat
    base = _calc_base(han, fu, yakuman_multiplier)
    dealer_pay, non_dealer_pay = _calc_tsumo_payments(base, is_dealer_winner)

    # Honba bonus: honba * 100 per payer
    honba_per_player = session.current_honba * 100

    total_received = 0
    for seat in range(4):
        if seat == winner_seat:
            continue
        if is_dealer_winner:
            # All non-dealers pay equal
            pay = non_dealer_pay + honba_per_player
        else:
            if seat == dealer_seat:
                pay = dealer_pay + honba_per_player
            else:
                pay = non_dealer_pay + honba_per_player
        point_changes[seat] -= pay
        total_received += pay

    point_changes[winner_seat] += total_received

    # Winner collects kyotaku
    kyotaku_bonus = session.current_kyotaku * 1000
    point_changes[winner_seat] += kyotaku_bonus

    # Apply point changes
    for seat, change in point_changes.items():
        session.players[seat].points += change

    round_record = RoundRecord(
        round_number=session.current_round,
        round_wind=get_round_wind(session),
        dealer_seat=dealer_seat,
        honba=session.current_honba,
        result_type="tsumo",
        winner_seat=winner_seat,
        score_result={"han": han, "fu": fu, "yakuman_multiplier": yakuman_multiplier},
        point_changes=dict(point_changes),
        riichi_seats=list(riichi_seats),
    )
    session.rounds.append(round_record)

    # Reset kyotaku after win
    session.current_kyotaku = 0

    # Advance round
    dealer_won = winner_seat == dealer_seat
    advance_round(session, dealer_won)

    return round_record


def apply_draw(session: GameSession, tenpai_seats: list[int] | None = None,
               riichi_seats: list[int] | None = None) -> RoundRecord:
    """Apply a draw (流局) and record the round."""
    if session.status != "active":
        raise ValueError("Game is not active")

    _take_snapshot(session)

    tenpai_seats = list(dict.fromkeys(tenpai_seats or []))
    riichi_seats = list(dict.fromkeys(riichi_seats or []))
    point_changes: dict[int, int] = {i: 0 for i in range(4)}

    # Apply riichi deductions
    _apply_riichi(session, riichi_seats, point_changes)

    # Tenpai/noten payments
    num_tenpai = len(tenpai_seats)
    noten_seats = [s for s in range(4) if s not in tenpai_seats]

    if 1 <= num_tenpai <= 3:
        total_pool = 3000
        tenpai_receive = total_pool // num_tenpai
        noten_pay = total_pool // len(noten_seats)
        for seat in tenpai_seats:
            point_changes[seat] += tenpai_receive
        for seat in noten_seats:
            point_changes[seat] -= noten_pay

    # Apply point changes
    for seat, change in point_changes.items():
        session.players[seat].points += change

    dealer_seat = get_dealer_seat(session)

    round_record = RoundRecord(
        round_number=session.current_round,
        round_wind=get_round_wind(session),
        dealer_seat=dealer_seat,
        honba=session.current_honba,
        result_type="draw",
        point_changes=dict(point_changes),
        riichi_seats=list(riichi_seats),
    )
    session.rounds.append(round_record)

    # Kyotaku stays on table after draw (riichi sticks remain)

    # Dealer tenpai means dealer retains
    dealer_tenpai = dealer_seat in tenpai_seats
    advance_round(session, dealer_won=dealer_tenpai)

    return round_record


def _check_hakoire(session: GameSession) -> bool:
    """Check if any player has gone below 0 points (箱割れ).
    Returns True if hakoire detected and game should end.
    """
    if not session.options.hakoire_end:
        return False
    return any(p.points < 0 for p in session.players)


def _get_base_max_rounds(session: GameSession) -> int:
    """Get the base number of rounds (before extensions)."""
    return 4 if session.game_type == "east_only" else 8


def _is_in_extension(session: GameSession) -> bool:
    """Check if we are currently in an extended round (南入/西入/北入)."""
    return session.current_round >= _get_base_max_rounds(session)


def _anyone_above_target(session: GameSession, target: int = 30000) -> bool:
    """Check if any player has >= target points."""
    return any(p.points >= target for p in session.players)


def _all_below_target(session: GameSession, target: int = 30000) -> bool:
    """Check if all players are below target points."""
    return all(p.points < target for p in session.players)


def advance_round(session: GameSession, dealer_won: bool) -> None:
    """Advance to next round after a result.

    If dealer won (or tenpai in draw), dealer stays and honba increments.
    Otherwise, dealer rotates and honba resets (for wins) or increments (for draws).

    Extension rules:
    - 東風戦: ends after East 4. If all < 30000 → 南入. 南入 ends when someone ≥ 30000.
    - 半荘戦: ends after South 4. If shanyu and all < 30000 → 西入. 西入 ends when someone ≥ 30000.
    - If peinyu and still all < 30000 after West 4 → 北入. 北入 ends when someone ≥ 30000.
    """
    # Check hakoire (bust) - if enabled and a player went below 0, end game
    if _check_hakoire(session):
        session.status = "finished"
        return

    # If we are in an extension round, check if someone reached 30000
    if _is_in_extension(session) and _anyone_above_target(session):
        session.status = "finished"
        return

    if dealer_won:
        # Dealer retains, honba increments
        session.current_honba += 1
    else:
        # Dealer rotates
        session.current_round += 1
        # For non-dealer win, honba resets to 0
        # For draw with dealer noten, honba increments
        last_round = session.rounds[-1] if session.rounds else None
        if last_round and last_round.result_type == "draw":
            session.current_honba += 1
        else:
            session.current_honba = 0

        # Check game end at round boundaries (every 4 rounds)
        if session.current_round % 4 == 0:
            base_max = _get_base_max_rounds(session)
            if session.current_round < base_max:
                # Still within base rounds, continue
                pass
            elif session.current_round == base_max:
                # End of base game - check extension
                # 東風戦: 南入 always allowed; 半荘戦: 西入 requires shanyu
                if session.game_type == "east_only":
                    can_extend = True
                else:
                    can_extend = session.options.shanyu
                if can_extend and _all_below_target(session):
                    pass  # extend
                else:
                    session.status = "finished"
            elif session.current_round == base_max + 4:
                # End of first extension - check further extension
                # 東風戦: 西入 requires shanyu; 半荘戦: 北入 requires peinyu
                if session.game_type == "east_only":
                    can_extend = session.options.shanyu
                else:
                    can_extend = session.options.peinyu
                if can_extend and _all_below_target(session):
                    pass  # extend
                else:
                    session.status = "finished"
            elif session.current_round == base_max + 8:
                # End of second extension
                # 東風戦: 北入 requires peinyu
                if session.game_type == "east_only" and session.options.peinyu and _all_below_target(session):
                    pass  # extend
                else:
                    session.status = "finished"
            else:
                # Beyond all possible extensions
                session.status = "finished"
