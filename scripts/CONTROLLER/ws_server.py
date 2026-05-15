#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ws_server.py – WebSocket server kết nối Controller ↔ Dashboard HTML

KIẾN TRÚC:
    CONTROLLER_virtual.py / CONTROLLER.py
        ↓ gọi ws_server.broadcast(msg)
    ws_server.py (asyncio WebSocket server :8765)
        ↓ push JSON
    dashboard.html (bất kỳ thiết bị nào trên WiFi)

TÍCH HỢP:
    Thêm vào MAIN_virtual.py hoặc MAIN.py:

        from ws_server import WSServer
        ws = WSServer()
        ws.start()   # chạy trong thread riêng

        # Trong Controller.set_score_callback:
        controller.set_score_callback(
            lambda text: [
                bridge.score_updated.emit(text),
                ws.send_score_update(text)    # ← thêm dòng này
            ]
        )

    Hoặc đơn giản hơn – gọi trực tiếp khi có hit mới:
        ws.broadcast_hit(node, x, y, score, ring, confidence)
"""

import asyncio
import json
import threading
import logging
from datetime import datetime

try:
    import websockets
except ImportError:
    print("[WS] Cần cài: pip install websockets")
    raise

log = logging.getLogger(__name__)

# ==================== CẤU HÌNH ====================

WS_HOST = "0.0.0.0"   # lắng nghe tất cả interface → xem được từ WiFi
WS_PORT = 8765


# ==================== WebSocket SERVER ====================

class WSServer:
    """
    WebSocket server chạy trong asyncio event loop riêng.
    Thread-safe: gọi broadcast() từ bất kỳ thread nào đều an toàn.
    """

    def __init__(self):
        self._clients  = set()      # set websocket connections
        self._loop     = None       # asyncio event loop
        self._thread   = None       # thread chứa event loop
        self._lock     = asyncio.Lock() if False else threading.Lock()

        # Cache state hiện tại để sync khi client kết nối mới
        self._state = {
            "scores": {},   # { "NODE1A": [{x,y,score,ring},...] }
            "logs":   [],   # list 50 log gần nhất
        }

    def start(self):
        """Khởi động server trong thread daemon riêng."""
        self._thread = threading.Thread(
            target=self._run_loop,
            name="WSServerThread",
            daemon=True,
        )
        self._thread.start()
        log.info(f"[WS] Server khởi động tại ws://{WS_HOST}:{WS_PORT}")

    def _run_loop(self):
        """Chạy asyncio event loop trong thread riêng."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._serve())

    async def _serve(self):
        """Asyncio coroutine: lắng nghe kết nối WebSocket."""
        async with websockets.serve(
            self._handle_client,
            WS_HOST,
            WS_PORT,
            ping_interval=20,
            ping_timeout=10,
        ):
            log.info(f"[WS] Listening at ws://0.0.0.0:{WS_PORT}")
            await asyncio.Future()   # chạy mãi

    async def _handle_client(self, websocket):
        """Xử lý một client kết nối mới."""
        addr = websocket.remote_address
        log.info(f"[WS] Client kết nối: {addr}")
        self._clients.add(websocket)

        # Gửi state hiện tại để sync
        try:
            await websocket.send(json.dumps({
                "type":   "state",
                "scores": self._state["scores"],
            }))
            # Gửi 20 log gần nhất
            for entry in self._state["logs"][-20:]:
                await websocket.send(json.dumps(entry))
        except Exception as e:
            log.warning(f"[WS] Sync state lỗi: {e}")

        # Giữ kết nối cho đến khi client ngắt
        try:
            async for _ in websocket:
                pass   # không cần nhận gì từ client
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self._clients.discard(websocket)
            log.info(f"[WS] Client ngắt kết nối: {addr}")

    # ── Public API (thread-safe) ──────────────────────────────────────────

    def broadcast_hit(self, node: str, x: float, y: float,
                      score: int, ring: str,
                      confidence: str = 'high'):
        """
        Gửi dữ liệu hit mới đến tất cả client.
        Gọi từ Controller thread khi có viên đạn hợp lệ.

        Tham số:
            node       : "NODE1A", "NODE2B", v.v.
            x, y       : tọa độ (cm)
            score      : điểm (0–10)
            ring       : "Vòng 7", "Bullseye", "Miss", v.v.
            confidence : "high" | "low"
        """
        msg = {
            "type":       "hit",
            "node":       node,
            "x":          round(x, 2),
            "y":          round(y, 2),
            "score":      score,
            "ring":       ring,
            "confidence": confidence,
            "ts":         datetime.now().strftime("%H:%M:%S"),
        }

        # Cập nhật state cache
        if node not in self._state["scores"]:
            self._state["scores"][node] = []
        if len(self._state["scores"][node]) < 3:
            self._state["scores"][node].append(
                {"x": x, "y": y, "score": score, "ring": ring}
            )

        self._broadcast_threadsafe(msg)

    def broadcast_log(self, text: str, level: str = 'ok'):
        """
        Gửi dòng log đến tất cả client.
        level: 'ok' | 'warn' | 'err' | 'data'
        """
        msg = {
            "type":  "log",
            "text":  text,
            "level": level,
            "ts":    datetime.now().strftime("%H:%M:%S"),
        }
        self._state["logs"].append(msg)
        if len(self._state["logs"]) > 200:
            self._state["logs"] = self._state["logs"][-200:]
        self._broadcast_threadsafe(msg)

    def broadcast_node_active(self, node_row: int, active: bool):
        """Cập nhật trạng thái active của hàng node (1–5)."""
        self._broadcast_threadsafe({
            "type":     "node_active",
            "node_row": node_row,
            "active":   active,
        })

    def broadcast_clear(self):
        """Xoá tất cả hits trên dashboard."""
        self._state["scores"] = {}
        self._broadcast_threadsafe({"type": "clear"})

    def _broadcast_threadsafe(self, msg: dict):
        """
        Gửi message đến tất cả client từ bất kỳ thread nào.
        Dùng loop.call_soon_threadsafe để an toàn giữa threads.
        """
        if self._loop is None or not self._clients:
            return
        try:
            self._loop.call_soon_threadsafe(
                asyncio.ensure_future,
                self._async_broadcast(json.dumps(msg)),
            )
        except Exception as e:
            log.warning(f"[WS] broadcast error: {e}")

    async def _async_broadcast(self, data: str):
        """Gửi đến tất cả client, bỏ qua client đã ngắt."""
        dead = set()
        for ws in set(self._clients):
            try:
                await ws.send(data)
            except Exception:
                dead.add(ws)
        self._clients -= dead


# ==================== SINGLETON ====================

_ws_server = None

def get_ws_server() -> WSServer:
    """Trả về singleton WSServer instance."""
    global _ws_server
    if _ws_server is None:
        _ws_server = WSServer()
    return _ws_server


# ==================== STANDALONE TEST ====================

if __name__ == "__main__":
    """
    Chạy độc lập để test dashboard:
        python ws_server.py
    Sau đó mở dashboard.html trong browser.
    Server tự động gửi dữ liệu giả mỗi 1.5s.
    """
    import time
    import math
    import random

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    ws = WSServer()
    ws.start()

    print("=" * 50)
    print("WS Test Server chạy tại ws://localhost:8765")
    print("Mở dashboard.html trong browser để xem")
    print("Ctrl+C để dừng")
    print("=" * 50)

    SCORING_RINGS = [
        (7.5,10),(15,9),(22.5,8),(30,7),(37.5,6),
        (45,5),(52.5,4),(60,3),(67.5,2),(75,1)
    ]

    def get_score(x, y, offset_y=0):
        d = math.sqrt(x**2 + (y-offset_y)**2)
        for r, s in SCORING_RINGS:
            if d <= r: return s, f"Vòng {s}" if s < 10 else "Bullseye"
        return 0, "Miss"

    demos = [
        ("NODE1A", 28.0,  17.0,  0),
        ("NODE2A",-15.0,  8.0,   0),
        ("NODE3A",  5.0, -20.0,  0),
        ("NODE4A", -3.0,  12.0,  0),
        ("NODE5A", 40.0,  -5.0,  0),
        ("NODE1B",  8.0,  30.0, 25),
        ("NODE2B", -5.0,  22.0, 25),
        ("NODE3B",  3.0,  26.0, 25),
        ("NODE1C",-10.0,  28.0, 25),
        ("NODE2C",  2.0,  25.0, 25),
    ]

    time.sleep(1.5)
    ws.broadcast_log("Test server khởi động", "ok")
    ws.broadcast_log(f"Gửi {len(demos)} điểm thử nghiệm...", "warn")

    for i, (node, x, y, off) in enumerate(demos):
        time.sleep(1.5)
        row = int(node[4])
        ws.broadcast_node_active(row, True)

        score, ring = get_score(x, y, off)
        conf = "high" if score >= 7 else "low"
        ws.broadcast_hit(node, x, y, score, ring, conf)
        ws.broadcast_log(
            f"{node}: ({x:.1f},{y:.1f})cm → {ring} [{score}đ]", "data"
        )

        if i > 0:
            ws.broadcast_node_active(int(demos[i-1][0][4]), False)

    ws.broadcast_log("Demo hoàn tất", "ok")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nDừng server")
