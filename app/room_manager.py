from __future__ import annotations

import asyncio
import json
from uuid import UUID

from fastapi import WebSocket

from app.game_session import GameSession


class RoomManager:
    """Manages WebSocket rooms for real-time game sync."""

    def __init__(self) -> None:
        # room_code -> list of (websocket, player_name)
        self._connections: dict[str, list[tuple[WebSocket, str]]] = {}
        # room_code -> game_id
        self._room_games: dict[str, UUID] = {}

    def register_room(self, room_code: str, game_id: UUID) -> None:
        self._room_games[room_code] = game_id
        if room_code not in self._connections:
            self._connections[room_code] = []

    async def connect(self, room_code: str, websocket: WebSocket, player_name: str) -> bool:
        if room_code not in self._connections:
            return False
        await websocket.accept()
        self._connections[room_code].append((websocket, player_name))
        await self._broadcast_system(room_code, {
            "type": "player_joined",
            "player_name": player_name,
            "connected_count": len(self._connections[room_code]),
        })
        return True

    def disconnect(self, room_code: str, websocket: WebSocket) -> None:
        if room_code not in self._connections:
            return
        self._connections[room_code] = [
            (ws, name) for ws, name in self._connections[room_code] if ws is not websocket
        ]

    async def broadcast_game_update(self, room_code: str, event_type: str, data: dict) -> None:
        message = {"type": event_type, **data}
        await self._broadcast(room_code, message)

    async def _broadcast_system(self, room_code: str, message: dict) -> None:
        await self._broadcast(room_code, message)

    async def _broadcast(self, room_code: str, message: dict) -> None:
        if room_code not in self._connections:
            return
        dead: list[tuple[WebSocket, str]] = []
        text = json.dumps(message, default=str)
        for ws, name in self._connections[room_code]:
            try:
                await ws.send_text(text)
            except Exception:
                dead.append((ws, name))
        for item in dead:
            self._connections[room_code].remove(item)

    def get_game_id(self, room_code: str) -> UUID | None:
        return self._room_games.get(room_code)

    def get_connected_players(self, room_code: str) -> list[str]:
        if room_code not in self._connections:
            return []
        return [name for _, name in self._connections[room_code]]

    def remove_room(self, room_code: str) -> None:
        self._connections.pop(room_code, None)
        self._room_games.pop(room_code, None)
