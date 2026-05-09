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

import time
import math
import json
import queue
import socket
import threading
from datetime import datetime

# ==================== CẤU HÌNH ====================

# TCP port lắng nghe uplink từ các node-virtual
TCP_HOST     = "0.0.0.0"   # lắng nghe tất cả interface
TCP_PORT     = 9000         # node-virtual kết nối đến đây

# TCP port gửi downlink lệnh đến node-virtual
# node-virtual lắng nghe tại NODE_LISTEN_PORT = 9001
NODE_CMD_PORT = 9001

# File log và JSON (giữ nguyên)
LOG_FILE  = "score.txt"
JSON_FILE = "/opt/score.json"

# Cấu hình vòng điểm (giữ nguyên)
SCORING_RINGS = [
    (7.5,  10), (15.0,  9), (22.5,  8), (30.0,  7),
    (37.5,  6), (45.0,  5), (52.5,  4), (60.0,  3),
    (67.5,  2), (75.0,  1), (float('inf'), 0),
]

# ==================== LỚP CONTROLLER ====================

class Controller:
    """
    Controller virtual: thay UDP LoRa bằng TCP socket nội bộ.

    Giao tiếp với GUI (giữ nguyên hoàn toàn):
        log_queue (queue.Queue) : GUI poll 200ms để hiển thị log
        set_score_callback(fn)  : nhận bảng điểm khi cập nhật
        handle_button(name)     : GUI gọi khi bấm nút
    """

    def __init__(self):
        # Queue log thread-safe – GUI poll (giữ nguyên)
        self.log_queue = queue.Queue(maxsize=500)

        # Trạng thái EXTRA mode (giữ nguyên)
        self.extra_mode_active = False

        # Trạng thái toggle nút (giữ nguyên)
        self.button_states = {
            "NODE1": False, "NODE2": False, "NODE3": False,
            "NODE4": False, "NODE5": False,
            "A": False, "B": False, "C": False, "D": False,
            "EXTRA": False,
        }

        # ScoreDisplay (giữ nguyên)
        self.display = ScoreDisplay(log_fn=self._log)

        # Callback bảng điểm → GUI (giữ nguyên)
        self._score_callback = None

        # Cờ điều khiển vòng lặp
        self._running = False

        # ── MỚI: TCP server cho uplink ────────────────────────────────────
        # server socket lắng nghe các node-virtual kết nối
        self._tcp_server = None

        # Dict {addr_str: socket} – giữ tất cả kết nối node đang active
        # Key: "127.0.0.1:xxxxx", Value: socket object
        self._node_conns = {}
        self._conns_lock = threading.Lock()   # bảo vệ _node_conns

        # Buffer nhận dữ liệu theo từng connection
        # Key: addr_str, Value: str buffer chưa parse hết
        self._recv_bufs = {}

        # Queue nội bộ: TCP thread → run() thread (thread-safe)
        # TCP accept thread đẩy chuỗi data vào đây
        # run() poll queue này thay vì gọi socket trực tiếp
        self._data_queue = queue.Queue(maxsize=200)

        # ── MỚI: TCP connections đến từng node-virtual (downlink) ─────────
        # Key: node_host (str), Value: socket hoặc None
        # Hiện tại gửi đến localhost:9001 (1 node ảo cho đơn giản)
        # Nếu cần nhiều node trên nhiều máy khác nhau → thêm mapping host
        self._cmd_sock = None   # kết nối downlink đến node-virtual

    def set_score_callback(self, fn):
        """GUI đăng ký nhận bảng điểm khi cập nhật (giữ nguyên)."""
        self._score_callback = fn

    # ── Khởi tạo ──────────────────────────────────────────────────────────
    def setup(self):
        """
        Khởi tạo TCP server lắng nghe uplink từ node-virtual.

        THAY ĐỔI SO VỚI CONTROLLER.py SX1303:
            Thay vì: udp_sock.bind(UDP_IP, 1700)
            Bây giờ: tcp_server.bind(TCP_HOST, 9000) + listen()

        Khởi động thread chấp nhận kết nối TCP trong nền.
        """
        try:
            self._tcp_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._tcp_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._tcp_server.bind((TCP_HOST, TCP_PORT))
            self._tcp_server.listen(10)   # tối đa 20 node kết nối
            self._tcp_server.settimeout(1.0)
            self._log(f"[INIT] TCP server lắng nghe uplink tại port {TCP_PORT}")

            # Thread chấp nhận kết nối node mới (chạy daemon)
            accept_thread = threading.Thread(
                target=self._accept_loop,
                name="TCPAcceptLoop",
                daemon=True,
            )
            accept_thread.start()
            self._log("[INIT] Accept thread started – chờ node-virtual kết nối")

        except Exception as e:
            self._log(f"[ERROR] Khởi tạo TCP server: {e}")
            raise

    # ── Thread chấp nhận kết nối node mới ────────────────────────────────
    def _accept_loop(self):
        """
        Thread daemon: liên tục chấp nhận kết nối TCP mới từ node-virtual.
        Mỗi node kết nối → tạo thread riêng để đọc dữ liệu.
        """
        while self._running:
            try:
                conn, addr = self._tcp_server.accept()
                addr_str   = f"{addr[0]}:{addr[1]}"
                conn.settimeout(0.5)
                self._log(f"[NET] Node-virtual kết nối: {addr_str}")

                with self._conns_lock:
                    self._node_conns[addr_str] = conn
                    self._recv_bufs[addr_str]  = ""

                # Thread riêng đọc dữ liệu từ node này
                recv_thread = threading.Thread(
                    target=self._recv_loop,
                    args=(conn, addr_str),
                    name=f"RecvLoop-{addr_str}",
                    daemon=True,
                )
                recv_thread.start()

            except socket.timeout:
                continue   # timeout bình thường, tiếp tục vòng lặp
            except Exception as e:
                if self._running:
                    self._log(f"[ERROR] Accept loop: {e}")
                break

    def _recv_loop(self, conn, addr_str):
        """
        Thread đọc dữ liệu từ một node-virtual cụ thể.
        Đẩy từng chuỗi data hoàn chỉnh vào _data_queue.

        Tham số:
            conn     (socket): socket kết nối với node
            addr_str (str)   : địa chỉ node "ip:port"
        """
        buf = ""
        while self._running:
            try:
                chunk = conn.recv(512).decode('utf-8')
                if not chunk:
                    # Node đóng kết nối
                    self._log(f"[NET] Node ngắt kết nối: {addr_str}")
                    break
                buf += chunk

                # Mỗi gói tin kết thúc bằng '\n'
                while '\n' in buf:
                    line, buf = buf.split('\n', 1)
                    line = line.strip()
                    if line:
                        # Đẩy vào data_queue để run() xử lý
                        try:
                            self._data_queue.put_nowait(line)
                        except queue.Full:
                            self._log("[WARN] Data queue đầy – bỏ gói")

            except socket.timeout:
                continue   # timeout bình thường
            except Exception as e:
                if self._running:
                    self._log(f"[ERROR] Recv loop {addr_str}: {e}")
                break

        # Dọn dẹp khi node ngắt kết nối
        with self._conns_lock:
            self._node_conns.pop(addr_str, None)
            self._recv_bufs.pop(addr_str, None)
        try:
            conn.close()
        except Exception:
            pass

    # ── Vòng lặp chính ────────────────────────────────────────────────────
    def run(self):
        """
        Vòng lặp chính: đọc dữ liệu từ _data_queue và cập nhật điểm.
        MAIN_virtual.py chạy hàm này trong thread riêng.

        THAY ĐỔI SO VỚI CONTROLLER.py SX1303:
            Thay vì: _receive_data() → parse UDP Semtech JSON → base64_decode
            Bây giờ: _data_queue.get_nowait() → chuỗi plain text sẵn sàng parse
        """
        self._running = True
        self._log("[CTRL] Vòng lặp Controller Virtual bắt đầu (TCP mode)")
        self._log(f"[CTRL] Chờ node-virtual kết nối tại port {TCP_PORT}...")

        while self._running:
            try:
                # Đọc tất cả data trong queue (không chờ nếu rỗng)
                while True:
                    try:
                        data = self._data_queue.get_nowait()
                        self._log(f"[RX] Nhận: '{data}'")

                        node_name, x, y = self._parse_node_data(data)
                        if node_name:
                            self.display.update(node_name, x, y)
                            if self._score_callback:
                                self._score_callback(
                                    self.display.get_score_table()
                                )
                    except queue.Empty:
                        break   # hết dữ liệu trong queue

            except Exception as e:
                self._log(f"[ERROR] Vòng lặp: {e}")

            time.sleep(0.1)   # 100ms sleep – giảm CPU

        self._log("[CTRL] Vòng lặp Controller Virtual kết thúc")

    def stop(self):
        """Dừng vòng lặp, đóng tất cả socket."""
        self._running = False

        # Đóng TCP server
        if self._tcp_server:
            try:
                self._tcp_server.close()
            except Exception:
                pass

        # Đóng tất cả kết nối node
        with self._conns_lock:
            for conn in self._node_conns.values():
                try:
                    conn.close()
                except Exception:
                    pass
            self._node_conns.clear()

        # Đóng kết nối downlink
        if self._cmd_sock:
            try:
                self._cmd_sock.close()
            except Exception:
                pass

        self._log("[CTRL] Tất cả socket đã đóng")

    # ==================== ĐIỀU KHIỂN NÚT BẤM ====================

    def handle_button(self, btn_name):
        """
        Xử lý nút bấm từ GUI. Giữ nguyên 100% logic toggle/EXTRA.
        Chỉ thay send_command() bên trong → gửi TCP thay vì UDP Semtech.
        """
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

    # ==================== GIAO TIẾP TCP (thay UDP LoRa) ====================

    def send_command(self, node_name, command):
        """
        Gửi lệnh đến node-virtual qua TCP (thay vì UDP Semtech downlink).

        THAY ĐỔI SO VỚI CONTROLLER.py SX1303:
            Thay vì: đóng gói JSON txpk + base64 + struct header → sendto UDP
            Bây giờ: plain text "NODE1 UP\n" → sendall TCP

        Định dạng lệnh giữ nguyên: "NODE1 UP", "A DOWN", "EXTRA UP"
        node-virtual parse y hệt như NODE.py thật.

        Tham số:
            node_name (str): "NODE1"…"NODE5", "A","B","C","D","EXTRA"
            command   (str): "UP" hoặc "DOWN"
        """
        message = f"{node_name} {command}\n"

        # Thử kết nối downlink đến node-virtual nếu chưa có
        if self._cmd_sock is None:
            self._connect_downlink()

        if self._cmd_sock is None:
            self._log(f"[WARN] Không có kết nối downlink – lệnh '{message.strip()}' bị bỏ")
            return

        try:
            self._cmd_sock.sendall(message.encode('utf-8'))
            self._log(f"[TX] Gửi lệnh: '{message.strip()}'")
        except Exception as e:
            self._log(f"[ERROR] Gửi lệnh thất bại: {e} – thử kết nối lại")
            try:
                self._cmd_sock.close()
            except Exception:
                pass
            self._cmd_sock = None
            # Thử kết nối lại và gửi lần 2
            self._connect_downlink()
            if self._cmd_sock:
                try:
                    self._cmd_sock.sendall(message.encode('utf-8'))
                    self._log(f"[TX] Gửi lại thành công: '{message.strip()}'")
                except Exception as e2:
                    self._log(f"[ERROR] Gửi lại thất bại: {e2}")

    def _connect_downlink(self):
        """
        Tạo TCP connection đến node-virtual (port NODE_CMD_PORT).
        Non-blocking: thử 1 lần, nếu thất bại thì bỏ qua (node chưa chạy).
        """
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1.0)
            s.connect(("127.0.0.1", NODE_CMD_PORT))
            s.settimeout(None)
            self._cmd_sock = s
            self._log(f"[NET] Kết nối downlink đến node-virtual port {NODE_CMD_PORT} ✓")
        except ConnectionRefusedError:
            self._log(f"[WARN] node-virtual chưa chạy (port {NODE_CMD_PORT} bị từ chối)")
            self._cmd_sock = None
        except Exception as e:
            self._log(f"[WARN] Kết nối downlink: {e}")
            self._cmd_sock = None

    def _parse_node_data(self, data):
        """
        Parse "NODE1A, -26.30, 30.10" → ("NODE1A", -26.30, 30.10).
        Giữ nguyên hoàn toàn từ bản SX1303.
        """
        try:
            parts = data.split(',')
            if len(parts) < 3:
                self._log(f"[WARN] Dữ liệu thiếu trường: '{data}'")
                return (None, None, None)
            node_name = parts[0].strip().upper().replace(" ", "")
            x = float(parts[1].strip())
            y = float(parts[2].strip())
            return (node_name, x, y)
        except (ValueError, IndexError, AttributeError) as e:
            self._log(f"[ERROR] Parse '{data}': {e}")
            return (None, None, None)

    # ==================== TIỆN ÍCH (giữ nguyên) ====================

    def _log(self, message):
        """Ghi log ra file và đẩy vào queue GUI. Giữ nguyên từ bản cũ."""
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
        """Reset JSON về rỗng. Giữ nguyên từ bản cũ."""
        try:
            with open(JSON_FILE, 'w', encoding='utf-8') as f:
                json.dump({"rounds": []}, f, indent=2)
            self._log(f"[JSON] Đã xoá {JSON_FILE}")
        except Exception as e:
            self._log(f"[ERROR] Xoá JSON: {e}")

    def get_score_table(self):
        """Proxy lấy bảng điểm. Giữ nguyên."""
        return self.display.get_score_table()

    def reset_round(self):
        """Proxy reset vòng bắn. Giữ nguyên."""
        self.display.reset_round()
        self._log("[CTRL] Reset vòng bắn hoàn tất")


# ==================== HÀM TÍNH ĐIỂM (giữ nguyên) ====================

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


# ==================== LỚP QUẢN LÝ ĐIỂM (giữ nguyên) ====================

class ScoreDisplay:
    """Giữ nguyên hoàn toàn từ bản SX1303. Không thay đổi."""

    def __init__(self, log_fn=print):
        self._log = log_fn
        _empty = lambda: {"x": None, "y": None,
                          "score": None, "ring_name": None, "shots": []}
        self.scores = {
            f"NODE{i}{s}": _empty()
            for s in ("A", "B", "C")
            for i in range(1, 6)
        }

    def update(self, node_name, x, y):
        node_key = node_name.replace(" ", "").upper()
        if node_key not in self.scores:
            self._log(f"[WARN] Không nhận ra node: '{node_key}'")
            return
        result = calculate_score(x, y)
        self.scores[node_key].update({
            "x": x, "y": y,
            "score": result['score'],
            "ring_name": result['ring_name'],
        })
        if len(self.scores[node_key]["shots"]) < 3:
            self.scores[node_key]["shots"].append({
                'x': x, 'y': y,
                'score': result['score'],
                'ring': result['ring_name'],
                'distance': result['distance'],
            })
        self._log(f"[SCORE] {node_key}: ({x:.1f}, {y:.1f}) → "
                  f"{result['ring_name']} – {result['score']} điểm")
        self.save_to_json()

    def save_to_json(self, file_path=None):
        path = file_path or JSON_FILE
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

    def get_total_score(self, node_key):
        if node_key in self.scores:
            shots = self.scores[node_key]["shots"]
            if shots:
                return sum(s['score'] for s in shots)
        return 0

    def get_score_table(self):
        lines, sep = [], "=" * 70
        lines += [sep, "BẢNG ĐIỂM  –  " + datetime.now().strftime('%H:%M:%S'), sep]
        for row_label, suffix in [("HÀNG 1","A"),("HÀNG 2","B"),("HÀNG 3","C")]:
            lines += [f"\n  {row_label} – Dãy {suffix}", "  "+"-"*64,
                      f"  {'NODE':<10} {'Viên 1':>12} {'Viên 2':>12} "
                      f"{'Viên 3':>12} {'TỔNG':>6}", "  "+"-"*64]
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
                lines.append(f"  {key:<10} {fmt(0):>12} {fmt(1):>12} "
                             f"{fmt(2):>12} {total:>5}đ")
            lines.append(f"  {'Tổng dãy '+suffix:<10} {'':>12} {'':>12} "
                        f"{'':>12} {row_total:>5}đ")
        lines.append("\n" + sep)
        return "\n".join(lines)

    def reset_round(self):
        for node_key in self.scores:
            while len(self.scores[node_key]["shots"]) < 3:
                self.scores[node_key]["shots"].append(
                    {'x': None, 'y': None, 'score': 0,
                     'ring': 'Miss', 'distance': None})
                self._log(f"[MISS] {node_key}: "
                          f"viên {len(self.scores[node_key]['shots'])} – 0 điểm")
        self.save_to_json()
        for node_key in self.scores:
            self.scores[node_key].update(
                {"shots": [], "x": None, "y": None,
                 "score": None, "ring_name": None})
        self._log("[SCORE] Reset xong – sẵn sàng vòng mới")
