from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.room_manager import RoomManager
from app.game_session import (
    GameOptions,
    GameSession,
    apply_draw,
    apply_multi_ron,
    apply_ron,
    apply_tsumo,
    create_game,
    get_dealer_seat,
    get_round_wind,
    undo_last,
)
from app.schemas import (
    ClaimSeatRequest,
    CreateGameRequest,
    DrawRequest,
    GameOptionsResponse,
    GameRoundResponse,
    GameStateResponse,
    MultiRonRequest,
    PlayerStateResponse,
    RonRequest,
    RoundResultResponse,
    SwapSeatsRequest,
    TsumoRequest,
)

app = FastAPI(title="Mahjong Score Flow", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/game")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# --- Game session endpoints ---

_game_sessions: dict[UUID, GameSession] = {}
_room_code_to_game: dict[str, UUID] = {}
room_manager = RoomManager()


def _game_state_response(session: GameSession) -> GameStateResponse:
    dealer = get_dealer_seat(session)
    wind = get_round_wind(session)
    return GameStateResponse(
        game_id=session.game_id,
        status=session.status,
        players=[
            PlayerStateResponse(seat=p.seat, name=p.name, points=p.points)
            for p in session.players
        ],
        current_round=session.current_round,
        current_dealer=dealer,
        current_round_wind=wind,
        current_honba=session.current_honba,
        current_kyotaku=session.current_kyotaku,
        rounds_played=len(session.rounds),
        created_at=session.created_at,
        options=GameOptionsResponse(
            hakoire_end=session.options.hakoire_end,
            shanyu=session.options.shanyu,
            peinyu=session.options.peinyu,
        ),
    )


def _round_result_response(session: GameSession, record) -> RoundResultResponse:
    return RoundResultResponse(
        round_number=record.round_number,
        round_wind=record.round_wind,
        dealer_seat=record.dealer_seat,
        honba=record.honba,
        result_type=record.result_type,
        winner_seat=record.winner_seat,
        loser_seat=record.loser_seat,
        point_changes={str(k): v for k, v in record.point_changes.items()},
        player_points_after={str(p.seat): p.points for p in session.players},
    )


@app.post("/api/v1/games", response_model=GameRoundResponse, status_code=201)
def create_game_endpoint(req: CreateGameRequest) -> dict:
    options = GameOptions(
        hakoire_end=req.options.hakoire_end,
        shanyu=req.options.shanyu,
        peinyu=req.options.peinyu,
    )
    session = create_game(req.player_names, req.starting_points, req.game_type, options)
    _game_sessions[session.game_id] = session
    _room_code_to_game[session.room_code] = session.game_id
    room_manager.register_room(session.room_code, session.game_id)
    return {
        "game_id": session.game_id,
        "room_code": session.room_code,
        "round_result": RoundResultResponse(
            round_number=0,
            round_wind=get_round_wind(session),
            dealer_seat=get_dealer_seat(session),
            honba=0,
            result_type="init",
            point_changes={},
            player_points_after={str(p.seat): p.points for p in session.players},
        ),
        "game_state": _game_state_response(session),
    }


@app.get("/api/v1/games/{game_id}", response_model=GameStateResponse)
def get_game(game_id: UUID) -> GameStateResponse:
    session = _game_sessions.get(game_id)
    if not session:
        raise HTTPException(status_code=404, detail="game not found")
    return _game_state_response(session)


@app.post("/api/v1/games/{game_id}/ron", response_model=GameRoundResponse)
async def record_ron(game_id: UUID, req: RonRequest) -> dict:
    session = _game_sessions.get(game_id)
    if not session:
        raise HTTPException(status_code=404, detail="game not found")
    try:
        record = apply_ron(
            session, req.winner_seat, req.loser_seat,
            req.han, req.fu, req.yakuman_multiplier, req.riichi_seats,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    result = {
        "game_id": session.game_id,
        "round_result": _round_result_response(session, record),
        "game_state": _game_state_response(session),
    }
    await room_manager.broadcast_game_update(session.room_code, "ron", {
        "game_state": _game_state_response(session).model_dump(mode="json"),
        "round_result": _round_result_response(session, record).model_dump(mode="json"),
    })
    return result


@app.post("/api/v1/games/{game_id}/multi-ron", response_model=GameRoundResponse)
async def record_multi_ron(game_id: UUID, req: MultiRonRequest) -> dict:
    session = _game_sessions.get(game_id)
    if not session:
        raise HTTPException(status_code=404, detail="game not found")
    try:
        winners_dicts = [
            {"seat": w.seat, "han": w.han, "fu": w.fu, "yakuman_multiplier": w.yakuman_multiplier}
            for w in req.winners
        ]
        record = apply_multi_ron(session, req.loser_seat, winners_dicts, req.riichi_seats)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    result = {
        "game_id": session.game_id,
        "round_result": _round_result_response(session, record),
        "game_state": _game_state_response(session),
    }
    await room_manager.broadcast_game_update(session.room_code, "ron", {
        "game_state": _game_state_response(session).model_dump(mode="json"),
        "round_result": _round_result_response(session, record).model_dump(mode="json"),
    })
    return result


@app.post("/api/v1/games/{game_id}/tsumo", response_model=GameRoundResponse)
async def record_tsumo(game_id: UUID, req: TsumoRequest) -> dict:
    session = _game_sessions.get(game_id)
    if not session:
        raise HTTPException(status_code=404, detail="game not found")
    try:
        record = apply_tsumo(
            session, req.winner_seat,
            req.han, req.fu, req.yakuman_multiplier, req.riichi_seats,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    result = {
        "game_id": session.game_id,
        "round_result": _round_result_response(session, record),
        "game_state": _game_state_response(session),
    }
    await room_manager.broadcast_game_update(session.room_code, "tsumo", {
        "game_state": _game_state_response(session).model_dump(mode="json"),
        "round_result": _round_result_response(session, record).model_dump(mode="json"),
    })
    return result


@app.post("/api/v1/games/{game_id}/draw", response_model=GameRoundResponse)
async def record_draw(game_id: UUID, req: DrawRequest) -> dict:
    session = _game_sessions.get(game_id)
    if not session:
        raise HTTPException(status_code=404, detail="game not found")
    try:
        record = apply_draw(session, req.tenpai_seats, req.riichi_seats)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    result = {
        "game_id": session.game_id,
        "round_result": _round_result_response(session, record),
        "game_state": _game_state_response(session),
    }
    await room_manager.broadcast_game_update(session.room_code, "draw", {
        "game_state": _game_state_response(session).model_dump(mode="json"),
        "round_result": _round_result_response(session, record).model_dump(mode="json"),
    })
    return result


@app.get("/api/v1/games/{game_id}/history")
def get_game_history(game_id: UUID) -> dict:
    session = _game_sessions.get(game_id)
    if not session:
        raise HTTPException(status_code=404, detail="game not found")
    rounds = []
    for record in session.rounds:
        rounds.append({
            "round_number": record.round_number,
            "round_wind": record.round_wind,
            "dealer_seat": record.dealer_seat,
            "honba": record.honba,
            "result_type": record.result_type,
            "winner_seat": record.winner_seat,
            "loser_seat": record.loser_seat,
            "point_changes": {str(k): v for k, v in record.point_changes.items()},
            "riichi_seats": record.riichi_seats,
        })
    return {
        "game_id": str(session.game_id),
        "rounds": rounds,
        "game_state": _game_state_response(session).model_dump(mode="json"),
    }


@app.post("/api/v1/games/{game_id}/undo")
async def undo_round(game_id: UUID) -> dict:
    session = _game_sessions.get(game_id)
    if not session:
        raise HTTPException(status_code=404, detail="game not found")
    removed = undo_last(session)
    if removed is None:
        raise HTTPException(status_code=422, detail="nothing to undo")
    state = _game_state_response(session)
    await room_manager.broadcast_game_update(session.room_code, "undo", {
        "game_state": state.model_dump(mode="json"),
    })
    return {
        "status": "ok",
        "undone_round": removed.round_number,
        "game_state": state.model_dump(mode="json"),
    }


@app.post("/api/v1/games/{game_id}/seats/{seat}/claim")
async def claim_seat(game_id: UUID, seat: int, req: ClaimSeatRequest) -> dict:
    session = _game_sessions.get(game_id)
    if not session:
        raise HTTPException(status_code=404, detail="game not found")
    if seat < 0 or seat > 3:
        raise HTTPException(status_code=422, detail="seat must be 0-3")
    session.players[seat].name = req.name
    state = _game_state_response(session)
    await room_manager.broadcast_game_update(session.room_code, "seat_claimed", {
        "seat": seat,
        "name": req.name,
        "game_state": state.model_dump(mode="json"),
    })
    return {"status": "ok", "game_state": state.model_dump(mode="json")}


@app.post("/api/v1/games/{game_id}/seats/swap")
async def swap_seats(game_id: UUID, req: SwapSeatsRequest) -> dict:
    session = _game_sessions.get(game_id)
    if not session:
        raise HTTPException(status_code=404, detail="game not found")
    if req.seat_a == req.seat_b:
        raise HTTPException(status_code=422, detail="seats must be different")
    a, b = session.players[req.seat_a], session.players[req.seat_b]
    a.name, b.name = b.name, a.name
    a.points, b.points = b.points, a.points
    state = _game_state_response(session)
    await room_manager.broadcast_game_update(session.room_code, "seats_swapped", {
        "seat_a": req.seat_a,
        "seat_b": req.seat_b,
        "game_state": state.model_dump(mode="json"),
    })
    return {"status": "ok", "game_state": state.model_dump(mode="json")}


@app.get("/api/v1/games/{game_id}/qr")
def game_qr(game_id: UUID, request: Request) -> Response:
    session = _game_sessions.get(game_id)
    if not session:
        raise HTTPException(status_code=404, detail="game not found")
    import qrcode
    import qrcode.constants
    base_url = str(request.base_url).rstrip("/")
    join_url = f"{base_url}/game?room={session.room_code}"
    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=2)
    qr.add_data(join_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


@app.delete("/api/v1/games/{game_id}")
def delete_game(game_id: UUID) -> dict:
    session = _game_sessions.pop(game_id, None)
    if not session:
        raise HTTPException(status_code=404, detail="game not found")
    _room_code_to_game.pop(session.room_code, None)
    room_manager.remove_room(session.room_code)
    return {"status": "deleted", "game_id": str(game_id)}


@app.get("/api/v1/rooms/{room_code}")
def get_room(room_code: str) -> dict:
    game_id = _room_code_to_game.get(room_code.upper())
    if not game_id:
        raise HTTPException(status_code=404, detail="room not found")
    session = _game_sessions.get(game_id)
    if not session:
        raise HTTPException(status_code=404, detail="game not found")
    return {
        "room_code": room_code.upper(),
        "game_id": str(session.game_id),
        "game_state": _game_state_response(session).model_dump(mode="json"),
        "connected_players": room_manager.get_connected_players(room_code.upper()),
    }


@app.websocket("/ws/rooms/{room_code}")
async def room_websocket(websocket: WebSocket, room_code: str, player_name: str = ""):
    code = room_code.upper()
    if not await room_manager.connect(code, websocket, player_name):
        await websocket.close(code=4004, reason="room not found")
        return
    try:
        while True:
            data = await websocket.receive_text()
            # Client can send ping/pong or action requests
            msg = json.loads(data)
            if msg.get("type") == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
            elif msg.get("type") in ("riichi_toggle", "tenpai_toggle"):
                await room_manager.broadcast_game_update(code, msg["type"], {
                    "seat": msg.get("seat"),
                    "active": msg.get("active"),
                })
            elif msg.get("type") == "request_sync":
                game_id = room_manager.get_game_id(code)
                if game_id and game_id in _game_sessions:
                    session = _game_sessions[game_id]
                    state = _game_state_response(session)
                    await room_manager.broadcast_game_update(code, "sync", {
                        "game_state": state.model_dump(mode="json"),
                    })
    except WebSocketDisconnect:
        room_manager.disconnect(code, websocket)
        await room_manager.broadcast_game_update(code, "player_left", {
            "player_name": player_name,
            "connected_count": len(room_manager.get_connected_players(code)),
        })


@app.get("/game")
def game_ui() -> FileResponse:
    return FileResponse(STATIC_DIR / "game.html")
