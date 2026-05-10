#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CONTROLLER_virtual.py – Controller chạy không cần phần cứng LoRa

📌 MỤC ĐÍCH:
    Thay thế CONTROLLER.py (SX1303 UDP) khi phần cứng chưa về.
    Nhận dữ liệu từ node-virtual.py qua TCP socket nội bộ thay vì UDP LoRa.

📡 GIAO TIẾP:
    node-virtual.py ──TCP:9000──► CONTROLLER_virtual.py  (uplink)
    CONTROLLER_virtual ──TCP:9001──► node-virtual.py      (downlink lệnh)

    Định dạng uplink giữ nguyên: "NODE1A, -26.30, 30.10\n"
    → _parse_node_data() không đổi gì → ScoreDisplay không đổi gì
    → GUI không đổi gì

✂️  THAY ĐỔI SO VỚI CONTROLLER.py (SX1303):
    - Xoá: UDP socket, Semtech packet format, base64, struct, SF routing
    - Thêm: TCP server lắng nghe uplink tại port 9000
    - Thêm: TCP client gửi downlink lệnh đến node-virtual tại port 9001
    - setup()          → bind TCP server port 9000
    - _receive_data()  → đọc từ TCP buffer thay vì UDP PUSH_DATA
    - send_command()   → gửi plain text TCP thay vì JSON Semtech
    - Tất cả: ScoreDisplay, log_queue, GUI callback giữ nguyên 100%

🖥️  DÙNG VỚI:
    Terminal 1: python MAIN_virtual.py   (hoặc python CONTROLLER_virtual.py trực tiếp)
    Terminal 2: python node-virtual.py   (1 node mỗi terminal)
    Terminal 3: python node-virtual.py   (NODE_ROW=2, NODE_SUFFIX="B", v.v.)
"""
# -*- coding: utf-8 -*-
"""
CONTROLLER_virtual.py – Controller ảo (không cần phần cứng LoRa)

KIẾN TRÚC ĐƠN GIẢN (1 port, 2 chiều):
    Controller mở TCP server tại port 9000.
    Node-virtual kết nối vào port 9000 này.
    Trên cùng 1 connection:
        Node → Controller : "NODE1A, -26.30, 30.10\n"  (uplink data)
        Controller → Node : "NODE1 UP\n"                (downlink lệnh)

    Không cần port 9001 riêng nữa → đơn giản, không bị nhầm socket.
"""

import time
import math
import json
import queue
import socket
import threading
from datetime import datetime

# ==================== CẤU HÌNH ====================

TCP_HOST  = "0.0.0.0"
TCP_PORT  = 9000

LOG_FILE  = "score.txt"
JSON_FILE = "/opt/score.json"

SCORING_RINGS = [
    (7.5,  10), (15.0,  9), (22.5,  8), (30.0,  7),
    (37.5,  6), (45.0,  5), (52.5,  4), (60.0,  3),
    (67.5,  2), (75.0,  1), (float('inf'), 0),
]

# ==================== LỚP CONTROLLER ====================

class Controller:

    def __init__(self):
        self.log_queue = queue.Queue(maxsize=500)
        self.extra_mode_active = False
        self.button_states = {
            "NODE1": False, "NODE2": False, "NODE3": False,
            "NODE4": False, "NODE5": False,
            "A": False, "B": False, "C": False, "D": False,
            "EXTRA": False,
        }
        self.display         = ScoreDisplay(log_fn=self._log)
        self._score_callback = None
        self._running        = True   # True ngay từ đầu

        # TCP server
        self._tcp_server  = None

        # Dict addr_str → socket (tất cả node đang kết nối)
        self._clients     = {}
        self._clients_lock = threading.Lock()

        # Queue data từ node → vòng lặp run()
        self._data_queue  = queue.Queue(maxsize=500)

    def set_score_callback(self, fn):
        self._score_callback = fn

    # ── Setup ──────────────────────────────────────────────────────────────
    def setup(self):
        """Bind TCP server và khởi động accept thread."""
        self._tcp_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._tcp_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._tcp_server.bind((TCP_HOST, TCP_PORT))
        self._tcp_server.listen(20)
        self._tcp_server.settimeout(1.0)
        self._log(f"[INIT] TCP server tại port {TCP_PORT} – chờ node-virtual...")

        # Thread chấp nhận kết nối mới từ node
        t = threading.Thread(target=self._accept_loop, daemon=True,
                             name="AcceptLoop")
        t.start()

    def _accept_loop(self):
        """Chấp nhận kết nối TCP từ node-virtual, tạo recv thread cho mỗi node."""
        while self._running:
            try:
                conn, addr = self._tcp_server.accept()
                addr_str   = f"{addr[0]}:{addr[1]}"
                conn.settimeout(None)   # blocking – recv thread tự xử lý
                self._log(f"[NET] Node kết nối: {addr_str}")
                with self._clients_lock:
                    self._clients[addr_str] = conn
                # Thread đọc data từ node này
                t = threading.Thread(
                    target=self._recv_loop,
                    args=(conn, addr_str),
                    daemon=True,
                    name=f"Recv-{addr_str}",
                )
                t.start()
            except socket.timeout:
                continue
            except Exception as e:
                if self._running:
                    self._log(f"[ERROR] Accept: {e}")

    def _recv_loop(self, conn, addr_str):
        """
        Đọc liên tục từ một node.
        Mỗi dòng kết thúc '\n' là một gói data hoàn chỉnh.
        Đẩy vào _data_queue để run() xử lý.
        """
        buf = ""
        while self._running:
            try:
                chunk = conn.recv(1024)
                if not chunk:
                    # Node ngắt kết nối
                    self._log(f"[NET] Node ngắt kết nối: {addr_str}")
                    break
                buf += chunk.decode('utf-8')
                # Tách từng dòng hoàn chỉnh
                while '\n' in buf:
                    line, buf = buf.split('\n', 1)
                    line = line.strip()
                    if line:
                        self._data_queue.put_nowait(line)
            except Exception as e:
                if self._running:
                    self._log(f"[ERROR] Recv {addr_str}: {e}")
                break
        # Dọn dẹp
        with self._clients_lock:
            self._clients.pop(addr_str, None)
        try:
            conn.close()
        except Exception:
            pass

    # ── Vòng lặp chính ────────────────────────────────────────────────────
    def run(self):
        """Poll _data_queue và cập nhật điểm. Chạy trong thread riêng."""
        self._log("[CTRL] Controller Virtual bắt đầu (TCP mode)")

        while self._running:
            # Xử lý hết data trong queue mỗi vòng lặp
            while True:
                try:
                    data = self._data_queue.get_nowait()
                    self._log(f"[RX] '{data}'")
                    node_name, x, y = self._parse_node_data(data)
                    if node_name:
                        self.display.update(node_name, x, y)
                        if self._score_callback:
                            self._score_callback(self.display.get_score_table())
                except queue.Empty:
                    break
                except Exception as e:
                    self._log(f"[ERROR] Xử lý data: {e}")

            time.sleep(0.1)

        self._log("[CTRL] Controller Virtual dừng")

    def stop(self):
        """Dừng tất cả."""
        self._running = False
        if self._tcp_server:
            try:
                self._tcp_server.close()
            except Exception:
                pass
        with self._clients_lock:
            for conn in self._clients.values():
                try:
                    conn.close()
                except Exception:
                    pass
            self._clients.clear()
        self._log("[CTRL] Đã đóng tất cả kết nối")

    # ==================== ĐIỀU KHIỂN NÚT BẤM ====================

    def handle_button(self, btn_name):
        """Giữ nguyên logic toggle/EXTRA."""
        if self.extra_mode_active:
            if btn_name == "EXTRA":
                self.extra_mode_active = False
                self.button_states["EXTRA"] = False
                self.send_command("EXTRA", "DOWN")
                self._log("[CONTROL] EXTRA mode TẮT")
                self._clear_score_json()
            else:
                self._log(f"[WARNING] '{btn_name}' bị khoá – EXTRA mode bật")
            return

        current = self.button_states.get(btn_name, False)

        if btn_name == "EXTRA":
            if not current:
                self.extra_mode_active = True
                self.button_states["EXTRA"] = True
                self.send_command("EXTRA", "UP")
                self._log("[CONTROL] EXTRA mode BẬT")
            else:
                self.extra_mode_active = False
                self.button_states["EXTRA"] = False
                self.send_command("EXTRA", "DOWN")
                self._log("[CONTROL] EXTRA mode TẮT")
                self._clear_score_json()
        else:
            if not current:
                self.send_command(btn_name, "UP")
                self.button_states[btn_name] = True
                self._log(f"[CONTROL] {btn_name} → UP")
            else:
                self.send_command(btn_name, "DOWN")
                self.button_states[btn_name] = False
                self._log(f"[CONTROL] {btn_name} → DOWN")

    # ==================== GỬI LỆNH XUỐNG NODE ====================

    def send_command(self, node_name, command):
        """
        Gửi lệnh xuống tất cả node đang kết nối qua cùng TCP connection.
        Định dạng: "NODE1 UP\n"
        """
        message = f"{node_name} {command}\n"
        with self._clients_lock:
            clients_snapshot = dict(self._clients)

        if not clients_snapshot:
            self._log(f"[WARN] Không có node nào kết nối – lệnh '{message.strip()}' bị bỏ")
            return

        dead = []
        for addr_str, conn in clients_snapshot.items():
            try:
                conn.sendall(message.encode('utf-8'))
                self._log(f"[TX] → {addr_str}: '{message.strip()}'")
            except Exception as e:
                self._log(f"[ERROR] Gửi lệnh đến {addr_str}: {e}")
                dead.append(addr_str)

        # Xoá node chết
        if dead:
            with self._clients_lock:
                for addr_str in dead:
                    self._clients.pop(addr_str, None)

    # ==================== TIỆN ÍCH ====================

    def _parse_node_data(self, data):
        """Parse "NODE1A, -26.30, 30.10" → ("NODE1A", -26.30, 30.10)."""
        try:
            parts = data.split(',')
            if len(parts) < 3:
                self._log(f"[WARN] Dữ liệu thiếu trường: '{data}'")
                return (None, None, None)
            node_name = parts[0].strip().upper().replace(" ", "")
            x = float(parts[1].strip())
            y = float(parts[2].strip())
            return (node_name, x, y)
        except Exception as e:
            self._log(f"[ERROR] Parse '{data}': {e}")
            return (None, None, None)

    def _log(self, message):
        timestamp   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_message = f"[{timestamp}] {message}"
        try:
            with open(LOG_FILE, 'a', encoding='utf-8') as f:
                f.write(log_message + "\n")
        except Exception:
            pass
        try:
            self.log_queue.put_nowait(log_message)
        except queue.Full:
            try:
                self.log_queue.get_nowait()
                self.log_queue.put_nowait(log_message)
            except Exception:
                pass

    def _clear_score_json(self):
        try:
            with open(JSON_FILE, 'w', encoding='utf-8') as f:
                json.dump({"rounds": []}, f, indent=2)
            self._log(f"[JSON] Đã xoá {JSON_FILE}")
        except Exception as e:
            self._log(f"[ERROR] Xoá JSON: {e}")

    def get_score_table(self):
        return self.display.get_score_table()

    def reset_round(self):
        self.display.reset_round()
        self._log("[CTRL] Reset vòng bắn hoàn tất")


# ==================== TÍNH ĐIỂM ====================

def calculate_distance(x, y):
    return math.sqrt(x**2 + y**2)

def get_ring(distance):
    for radius, score in SCORING_RINGS:
        if distance <= radius:
            return (score, "Ngoài bia") if score == 0 else (score, f"Vòng {score}")
    return (0, "Ngoài bia")

def calculate_score(x, y):
    distance         = calculate_distance(x, y)
    score, ring_name = get_ring(distance)
    return {'score': score, 'distance': round(distance, 2),
            'ring_name': ring_name, 'x': x, 'y': y}


# ==================== SCORE DISPLAY ====================

class ScoreDisplay:
    """Giữ nguyên hoàn toàn."""

    def __init__(self, log_fn=print):
        self._log = log_fn
        _e = lambda: {"x": None, "y": None,
                      "score": None, "ring_name": None, "shots": []}
        self.scores = {f"NODE{i}{s}": _e()
                       for s in ("A", "B", "C") for i in range(1, 6)}

    def update(self, node_name, x, y):
        key = node_name.replace(" ", "").upper()
        if key not in self.scores:
            self._log(f"[WARN] Không nhận ra node: '{key}'")
            return
        result = calculate_score(x, y)
        self.scores[key].update({"x": x, "y": y,
                                  "score": result['score'],
                                  "ring_name": result['ring_name']})
        if len(self.scores[key]["shots"]) < 3:
            self.scores[key]["shots"].append({
                'x': x, 'y': y, 'score': result['score'],
                'ring': result['ring_name'], 'distance': result['distance'],
            })
        self._log(f"[SCORE] {key}: ({x:.1f},{y:.1f}) → "
                  f"{result['ring_name']} – {result['score']}đ")
        self.save_to_json()

    def save_to_json(self, path=None):
        path = path or JSON_FILE
        try:
            data = {
                'timestamp': datetime.now().isoformat(),
                'rounds': [
                    {'node': nk, 'x': s['x'], 'y': s['y'],
                     'score': s['score'], 'ring': s['ring'],
                     'distance': s['distance']}
                    for nk, nd in self.scores.items()
                    for s in nd["shots"]
                ]
            }
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self._log(f"[ERROR] Ghi JSON: {e}")

    def get_total_score(self, key):
        shots = self.scores.get(key, {}).get("shots", [])
        return sum(s['score'] for s in shots) if shots else 0

    def get_score_table(self):
        lines, sep = [], "=" * 70
        lines += [sep, "BẢNG ĐIỂM  –  " + datetime.now().strftime('%H:%M:%S'), sep]
        for label, suffix in [("HÀNG 1","A"),("HÀNG 2","B"),("HÀNG 3","C")]:
            lines += [f"\n  {label} – Dãy {suffix}", "  "+"-"*64,
                      f"  {'NODE':<10}{'Viên 1':>12}{'Viên 2':>12}"
                      f"{'Viên 3':>12}{'TỔNG':>6}", "  "+"-"*64]
            row_total = 0
            for i in range(1, 6):
                key   = f"NODE{i}{suffix}"
                shots = self.scores[key]["shots"]
                total = self.get_total_score(key)
                row_total += total
                def fmt(idx, _s=shots):
                    if idx < len(_s):
                        s = _s[idx]
                        return "Miss" if s['score']==0 else f"{s['score']}đ/{s['ring']}"
                    return "—"
                lines.append(f"  {key:<10}{fmt(0):>12}{fmt(1):>12}"
                             f"{fmt(2):>12}{total:>5}đ")
            lines.append(f"  {'Tổng dãy '+suffix:<10}{'':>12}{'':>12}"
                        f"{'':>12}{row_total:>5}đ")
        lines.append("\n" + sep)
        return "\n".join(lines)

    def reset_round(self):
        for key in self.scores:
            while len(self.scores[key]["shots"]) < 3:
                self.scores[key]["shots"].append(
                    {'x': None, 'y': None, 'score': 0,
                     'ring': 'Miss', 'distance': None})
        self.save_to_json()
        for key in self.scores:
            self.scores[key].update({"shots": [], "x": None, "y": None,
                                     "score": None, "ring_name": None})
        self._log("[SCORE] Reset xong – sẵn sàng vòng mới")
