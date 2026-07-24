from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, conint


class GameOptionsRequest(BaseModel):
    hakoire_end: bool = True     # 箱割れ即終了
    shanyu: bool = False         # 西入あり
    peinyu: bool = False         # 北入あり


class CreateGameRequest(BaseModel):
    player_names: list[str] = Field(
        default=["プレイヤー1", "プレイヤー2", "プレイヤー3", "プレイヤー4"],
        min_length=4,
        max_length=4,
    )
    starting_points: int = 25000
    game_type: Literal["east_only", "east_south"] = "east_only"
    options: GameOptionsRequest = Field(default_factory=GameOptionsRequest)


class ClaimSeatRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=20)


class SwapSeatsRequest(BaseModel):
    seat_a: conint(ge=0, le=3)
    seat_b: conint(ge=0, le=3)


class PlayerStateResponse(BaseModel):
    seat: int
    name: str
    points: int


class GameOptionsResponse(BaseModel):
    hakoire_end: bool
    shanyu: bool
    peinyu: bool


class GameStateResponse(BaseModel):
    game_id: UUID
    status: Literal["active", "finished"]
    players: list[PlayerStateResponse]
    current_round: int
    current_dealer: int
    current_round_wind: str
    current_honba: int
    current_kyotaku: int
    rounds_played: int
    created_at: datetime
    options: GameOptionsResponse | None = None


class RonRequest(BaseModel):
    winner_seat: conint(ge=0, le=3)
    loser_seat: conint(ge=0, le=3)
    han: conint(ge=1)
    fu: conint(ge=20) = 30
    yakuman_multiplier: int = 0
    riichi_seats: list[conint(ge=0, le=3)] = Field(default_factory=list)


class MultiRonWinner(BaseModel):
    seat: conint(ge=0, le=3)
    han: conint(ge=1)
    fu: conint(ge=20) = 30
    yakuman_multiplier: int = 0


class MultiRonRequest(BaseModel):
    loser_seat: conint(ge=0, le=3)
    winners: list[MultiRonWinner] = Field(..., min_length=1, max_length=3)
    riichi_seats: list[conint(ge=0, le=3)] = Field(default_factory=list)


class TsumoRequest(BaseModel):
    winner_seat: conint(ge=0, le=3)
    han: conint(ge=1)
    fu: conint(ge=20) = 30
    yakuman_multiplier: int = 0
    riichi_seats: list[conint(ge=0, le=3)] = Field(default_factory=list)


class DrawRequest(BaseModel):
    tenpai_seats: list[conint(ge=0, le=3)] = Field(default_factory=list)
    riichi_seats: list[conint(ge=0, le=3)] = Field(default_factory=list)


class RoundResultResponse(BaseModel):
    round_number: int
    round_wind: str
    dealer_seat: int
    honba: int
    result_type: str
    winner_seat: int | None = None
    loser_seat: int | None = None
    point_changes: dict[str, int]
    player_points_after: dict[str, int]


class GameRoundResponse(BaseModel):
    game_id: UUID
    room_code: str | None = None
    round_result: RoundResultResponse
    game_state: GameStateResponse
