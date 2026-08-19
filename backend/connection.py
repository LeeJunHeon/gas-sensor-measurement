"""
connection.py — WebSocket 연결 관리 + 브로드캐스트/상태·로그 push.
"""

import json

from fastapi import WebSocket

import logger
from state import state


class ConnectionManager:
    def __init__(self):
        self.active: set[WebSocket] = set()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.add(ws)
        # ★ '연결 직후'는 레시피를 포함하는 권위 있는 시점이다(state.snapshot 주석 참조).
        #   이걸 빼면 부팅 기본 레시피(3단계)가 화면에 도달하지 못한다.
        await self._send(ws, state.snapshot(include_recipe=True))
        # 기동 진단 재생: 서버 시작 시점엔 접속자가 없어 놓친 경고를 지금 전달한다.
        # 목록은 비우지 않는다(재접속·새로고침 때도 다시 보여야 한다).
        for n in state.startup_notices:
            await self._send(ws, {"type": "log", "msg": f"[프로그램 진단] {n['msg']}",
                                  "level": n.get("level", "warn")})

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
    logger.write(level, msg)   # 화면 로그와 함께 파일에도 기록(설정에 따라 on/off·레벨 필터)
    await manager.broadcast({"type": "log", "msg": msg, "level": level})
