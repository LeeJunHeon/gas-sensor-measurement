"""
connection.py — WebSocket 연결 관리 + 브로드캐스트/상태·로그 push.
"""

import json

from fastapi import WebSocket

from state import state


class ConnectionManager:
    def __init__(self):
        self.active: set[WebSocket] = set()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.add(ws)
        await self._send(ws, state.snapshot())
        await self._send(ws, {"type": "log", "msg": "화면 ↔ 서버 연결됨", "level": "ok"})

    def disconnect(self, ws: WebSocket):
        self.active.discard(ws)

    async def _send(self, ws: WebSocket, obj: dict):
        try:
            await ws.send_text(json.dumps(obj, ensure_ascii=False))
        except Exception:  # noqa: BLE001
            self.active.discard(ws)

    async def broadcast(self, obj: dict):
        if not self.active:
            return
        text = json.dumps(obj, ensure_ascii=False)
        dead = []
        for ws in list(self.active):
            try:
                await ws.send_text(text)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.active.discard(ws)


# 서버 전역에서 공유하는 단일 매니저 인스턴스
manager = ConnectionManager()


async def push_state(include_recipe: bool = False):
    await manager.broadcast(state.snapshot(include_recipe=include_recipe))


async def push_log(msg: str, level: str = "info"):
    await manager.broadcast({"type": "log", "msg": msg, "level": level})
