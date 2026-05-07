#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CONTROLLER.py – Lõi điều khiển (SX1303 gateway qua UDP)

📌 THAY ĐỔI SO VỚI PHIÊN BẢN SX1276:
    - Xoá import rpi_lora, UART_PORT, BAUD_RATE
    - setup()          : mở UDP socket thay vì UART LoRa
    - _receive_data()  : đọc UDP (Semtech packet forwarder format)
                         thay vì lora.read()
    - send_command()   : gửi UDP downlink JSON thay vì lora.send()
    - Thêm SF_MAP      : mapping hàng node → SF để gửi downlink
                         đúng kênh, gateway biết route về đúng node
    - Tất cả logic điểm, queue, callback giữ nguyên 100%

🏗️  KIẾN TRÚC:
    lora_pkt_fwd (C) ← SPI ← SX1303 hardware
          ↕ UDP localhost:1700
    CONTROLLER.py (Python) ← poll UDP socket
          ↓
    ScoreDisplay / log_queue / GUI callback

📡 SEMTECH UDP PROTOCOL:
    Uplink   (node→gateway→controller): PUSH_DATA  identifier=0x00
    Downlink (controller→gateway→node): PULL_RESP  identifier=0x03

📡 SF ROUTING:
    NODE_ROW=1 → SF6  → downlink datr="SF6BW125"
    NODE_ROW=2 → SF7  → downlink datr="SF7BW125"
    ...v.v.
    Controller gửi downlink với SF tương ứng → SX1303 route đúng node.
"""

import time
import sys
import math
import json
import queue
import socket
import struct
import base64
import random
from datetime import datetime

# ==================== CẤU HÌNH CHUNG ====================

# UDP endpoint của Semtech packet forwarder (lora_pkt_fwd)
# Packet forwarder gửi uplink tới port này, nhận downlink từ port này
UDP_IP   = "127.0.0.1"
UDP_PORT = 1700         # port mặc định Semtech packet forwarder

# Tần số LoRa (MHz) – phải khớp global_conf.json của SX1303
LORA_FREQUENCY = 915.0

# Bandwidth (kHz) – phải khớp tất cả node và gateway config
LORA_BW = 125

# ✦ Mapping hàng node → Spreading Factor
# Phải khớp với NODE_SF_MAP trong NODE.py
# Controller dùng bảng này để gửi downlink đúng SF → đúng node
NODE_ROW_SF_MAP = {
    1: 6,    # NODE 1 → SF6
    2: 7,    # NODE 2 → SF7
    3: 8,    # NODE 3 → SF8
    4: 9,    # NODE 4 → SF9
    5: 10,   # NODE 5 → SF10
}

# ✦ Mapping tên node → SF để gửi downlink
# Tạo tự động từ NODE_ROW_SF_MAP: "NODE1x" → 6, "NODE2x" → 7, v.v.
# "x" là suffix A/B/C/D – SF chỉ phụ thuộc số hàng, không phụ thuộc nhóm
def _get_sf_for_node(node_name: str) -> int:
    """
    Tra SF cho node từ tên node.
    Ví dụ: "NODE1A" → row=1 → SF6
           "NODE3C" → row=3 → SF8

    Trả về SF mặc định (SF7) nếu không parse được.
    """
    try:
        # Lấy số sau "NODE": "NODE1A" → "1"
        row = int(node_name[4])   # ký tự thứ 4 (index 4) là số hàng
        return NODE_ROW_SF_MAP.get(row, 7)   # mặc định SF7
    except (IndexError, ValueError):
        return 7   # fallback an toàn

# File log
LOG_FILE  = "score.txt"
JSON_FILE = "/opt/score.json"

# Timeout điều khiển
CONTROL_TIMEOUT = 60

# Cấu hình vòng điểm
SCORING_RINGS = [
    (7.5,  10), (15.0,  9), (22.5,  8), (30.0,  7),
    (37.5,  6), (45.0,  5), (52.5,  4), (60.0,  3),
    (67.5,  2), (75.0,  1), (float('inf'), 0),
]
MAX_RADIUS = 75

# ==================== LỚP CONTROLLER CHÍNH ====================

class Controller:
    """
    Lõi điều khiển: giao tiếp SX1303 qua UDP, tính điểm, log.

    Giao tiếp với GUI:
        log_queue (queue.Queue) : GUI poll để hiển thị log
        set_score_callback(fn)  : nhận bảng điểm khi cập nhật
        handle_button(name)     : GUI gọi khi bấm nút
    """

    def __init__(self):
        # Queue log thread-safe (GUI poll mỗi 200ms)
        self.log_queue = queue.Queue(maxsize=500)

        # Trạng thái EXTRA mode
        self.extra_mode_active = False

        # Trạng thái toggle của từng nút (thay button_states GPIO cũ)
        self.button_states = {
            "NODE1": False, "NODE2": False, "NODE3": False,
            "NODE4": False, "NODE5": False,
            "A": False, "B": False, "C": False, "D": False,
            "EXTRA": False,
        }

        # UDP socket – khởi tạo trong setup()
        self.udp_sock = None

        # ScoreDisplay
        self.display = ScoreDisplay(log_fn=self._log)

        # Callback bảng điểm → GUI
        self._score_callback = None

        # Cờ điều khiển vòng lặp
        self._running = False

    def set_score_callback(self, fn):
        """GUI đăng ký để nhận bảng điểm khi có cập nhật."""
        self._score_callback = fn

    # ── Khởi tạo ──────────────────────────────────────────────────────────
    def setup(self):
        """
        Mở UDP socket lắng nghe packet forwarder.

        THAY ĐỔI SO VỚI BẢN SX1276:
            Thay vì:  self.lora = LoRa(BOARD.CN1, ...); lora.set_frequency()
            Bây giờ: self.udp_sock = socket.socket(UDP); sock.bind(port)

        Packet forwarder (lora_pkt_fwd) phải đang chạy trước khi gọi setup().
        Nếu không chạy, socket vẫn bind thành công nhưng không nhận được gói.
        """
        try:
            self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            # SO_REUSEADDR: cho phép bind lại port ngay sau khi thoát
            self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.udp_sock.bind((UDP_IP, UDP_PORT))
            # Timeout 0.5s: _receive_data() không block vô hạn
            self.udp_sock.settimeout(0.5)
            self._log(f"[INIT] UDP socket lắng nghe tại {UDP_IP}:{UDP_PORT}")
            self._log(f"[INIT] SF map: NODE1→SF6, NODE2→SF7, "
                      f"NODE3→SF8, NODE4→SF9, NODE5→SF10")
        except Exception as e:
            self._log(f"[ERROR] Bind UDP socket: {e}")
            raise

    # ── Vòng lặp chính ────────────────────────────────────────────────────
    def run(self):
        """
        Vòng lặp nhận dữ liệu từ packet forwarder qua UDP.
        MAIN.py chạy hàm này trong thread riêng.

        Mỗi 100ms:
            1. _receive_data() → đọc 1 gói UDP (non-blocking, timeout 0.5s)
            2. _parse_node_data() → tách node_name, x, y
            3. display.update() → tính điểm, lưu JSON
            4. score_callback() → GUI refresh bảng điểm
        """
        self._running = True
        self._log("[CTRL] Vòng lặp Controller bắt đầu (SX1303 UDP mode)")

        while self._running:
            try:
                data = self._receive_data()
                if data:
                    node_name, x, y = self._parse_node_data(data)
                    if node_name:
                        self.display.update(node_name, x, y)
                        if self._score_callback:
                            self._score_callback(self.display.get_score_table())
            except Exception as e:
                self._log(f"[ERROR] Vòng lặp: {e}")

            time.sleep(0.1)

        self._log("[CTRL] Vòng lặp Controller kết thúc")

    def stop(self):
        """Dừng vòng lặp và đóng UDP socket."""
        self._running = False
        if self.udp_sock:
            try:
                self.udp_sock.close()
                self._log("[CTRL] UDP socket đã đóng")
            except Exception as e:
                self._log(f"[ERROR] Đóng socket: {e}")

    # ==================== ĐIỀU KHIỂN NÚT BẤM ====================

    def handle_button(self, btn_name):
        """
        Xử lý nút bấm từ GUI. Logic toggle/EXTRA giữ nguyên 100%.

        Tham số:
            btn_name (str): "NODE1"…"NODE5", "A","B","C","D","EXTRA"
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

    # ==================== GIAO TIẾP LoRa (UDP) ====================

    def send_command(self, node_name, command):
        """
        Gửi lệnh xuống node qua SX1303 gateway (UDP downlink).

        THAY ĐỔI SO VỚI BẢN SX1276:
            Thay vì: self.lora.send(message.encode())
            Bây giờ: đóng gói JSON theo chuẩn Semtech rồi gửi UDP

        Cơ chế SF routing:
            - node_name = "NODE1A" → row=1 → SF=6 → datr="SF6BW125"
            - node_name = "A"/"B"/"C"/"D" → broadcast tất cả SF (gửi 5 gói)
            - node_name = "EXTRA" → broadcast SF7 (safe default)

        Tham số:
            node_name (str): "NODE1"…"NODE5", "A","B","C","D","EXTRA"
            command   (str): "UP" hoặc "DOWN"
        """
        message = f"{node_name} {command}"

        # Xác định SF(s) cần gửi
        if node_name in ("A", "B", "C", "D"):
            # Broadcast nhóm → gửi tất cả 5 SF (mỗi hàng node nhận được)
            sf_list = list(NODE_ROW_SF_MAP.values())
        elif node_name == "EXTRA":
            # EXTRA broadcast → gửi tất cả SF để chắc chắn mọi node nhận
            sf_list = list(NODE_ROW_SF_MAP.values())
        else:
            # Lệnh cụ thể cho NODE1–5: chỉ gửi SF của hàng đó
            sf_list = [_get_sf_for_node(node_name)]

        for sf in sf_list:
            self._send_udp_downlink(message, sf)

        self._log(f"[TX] Gửi: '{message}' | SF={sf_list}")

    def _send_udp_downlink(self, message: str, sf: int):
        """
        Đóng gói và gửi 1 gói downlink Semtech UDP cho 1 SF cụ thể.

        Cấu trúc gói Semtech UDP downlink:
            Header (4 bytes):
                [0] = protocol version = 0x02
                [1][2] = random token (2 bytes)
                [3] = identifier = 0x03 (PULL_RESP)
            Payload: JSON string theo chuẩn Semtech txpk

        Tham số:
            message (str): chuỗi lệnh, ví dụ "NODE1A UP"
            sf      (int): Spreading Factor (6–10)
        """
        try:
            # Encode payload thành base64 (chuẩn Semtech)
            payload_b64 = base64.b64encode(
                message.encode('utf-8')
            ).decode('ascii')

            # Cấu hình downlink theo chuẩn Semtech txpk
            txpk = {
                "imme": True,                        # gửi ngay lập tức
                "freq": LORA_FREQUENCY,              # tần số MHz
                "rfch": 0,                           # RF chain 0
                "powe": 20,                          # TX power (dBm)
                "modu": "LORA",                      # modulation
                "datr": f"SF{sf}BW{LORA_BW}",       # "SF6BW125", "SF7BW125", v.v.
                "codr": "4/5",                       # coding rate
                "ipol": True,                        # invert polarity (LoRaWAN downlink)
                "size": len(message),                # payload length
                "data": payload_b64,                 # payload base64
            }

            json_bytes = json.dumps({"txpk": txpk}).encode('utf-8')

            # Header Semtech: version=0x02 | token(2B random) | PULL_RESP=0x03
            import random
            token  = random.randint(0, 0xFFFF)
            header = struct.pack(">BHB", 0x02, token, 0x03)

            self.udp_sock.sendto(header + json_bytes, (UDP_IP, UDP_PORT))

        except Exception as e:
            self._log(f"[ERROR] Gửi UDP downlink SF{sf}: {e}")

    def _receive_data(self):
        """
        Nhận 1 gói uplink từ packet forwarder qua UDP.

        THAY ĐỔI SO VỚI BẢN SX1276:
            Thay vì: lora.is_rx_busy() + lora.read()
            Bây giờ: udp_sock.recvfrom() + parse Semtech PUSH_DATA

        Cấu trúc PUSH_DATA (uplink):
            Header 4 bytes: [version][token_H][token_L][0x00=PUSH_DATA]
            Byte 4–11: Gateway EUI (8 bytes)
            Byte 12+:  JSON payload chứa danh sách gói rxpk

        Trả về:
            str  : payload string từ node, ví dụ "NODE1A, -26.3, 30.1"
            None : không có dữ liệu, timeout, hoặc gói không phải uplink
        """
        try:
            data, addr = self.udp_sock.recvfrom(4096)

            # Kiểm tra độ dài tối thiểu (4 header + 8 EUI = 12 bytes)
            if len(data) < 12:
                return None

            # Byte thứ 3 (index 3) = identifier
            # 0x00 = PUSH_DATA (uplink từ node)
            # 0x02 = PULL_DATA (gateway poll, bỏ qua)
            identifier = data[3]
            if identifier != 0x00:
                return None   # không phải uplink, bỏ qua

            # Gửi PUSH_ACK lại cho packet forwarder (chuẩn Semtech)
            # Header PUSH_ACK: [version][token_H][token_L][0x01]
            ack = struct.pack(">BHB", 0x02,
                              struct.unpack(">H", data[1:3])[0], 0x01)
            self.udp_sock.sendto(ack, addr)

            # Parse JSON payload (byte 12 trở đi)
            json_str = data[12:].decode('utf-8')
            packet   = json.loads(json_str)

            # Duyệt qua các gói RF trong mảng "rxpk"
            for rxpk in packet.get("rxpk", []):
                # Payload của node được base64-encode bởi gateway
                raw_bytes = base64.b64decode(rxpk["data"])
                raw_str   = raw_bytes.decode('utf-8').strip()

                # Log thêm SF nhận được để debug
                datr = rxpk.get("datr", "?")   # ví dụ "SF6BW125"
                rssi = rxpk.get("rssi", "?")
                self._log(f"[RX] {raw_str} | {datr} | RSSI={rssi}dBm")

                return raw_str   # trả về chuỗi đầu tiên nhận được

        except socket.timeout:
            return None   # bình thường – không có gói trong 0.5s
        except (json.JSONDecodeError, KeyError, UnicodeDecodeError) as e:
            self._log(f"[WARN] Parse UDP packet: {e}")
            return None
        except Exception as e:
            self._log(f"[ERROR] Nhận UDP: {e}")
            return None

        return None

    def _parse_node_data(self, data):
        """
        Parse chuỗi "NODE1A, -26.3, 30.1" → ("NODE1A", -26.3, 30.1).
        Giữ nguyên hoàn toàn từ bản SX1276.
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

    # ==================== TIỆN ÍCH ====================

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
        """Reset file JSON về cấu trúc rỗng. Giữ nguyên từ bản cũ."""
        try:
            with open(JSON_FILE, 'w', encoding='utf-8') as f:
                json.dump({"rounds": []}, f, indent=2)
            self._log(f"[JSON] Đã xoá {JSON_FILE}")
        except Exception as e:
            self._log(f"[ERROR] Xoá JSON: {e}")

    def get_score_table(self):
        """Proxy lấy bảng điểm string cho GUI."""
        return self.display.get_score_table()

    def reset_round(self):
        """Proxy reset vòng bắn."""
        self.display.reset_round()
        self._log("[CTRL] Reset vòng bắn hoàn tất")


# ==================== HÀM TÍNH ĐIỂM ====================

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


# ==================== LỚP QUẢN LÝ ĐIỂM ====================

class ScoreDisplay:
    """Giữ nguyên hoàn toàn từ bản SX1276. Không thay đổi."""

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
        for row_label, suffix in [("HÀNG 1","A"), ("HÀNG 2","B"), ("HÀNG 3","C")]:
            lines += [f"\n  {row_label} – Dãy {suffix}", "  " + "-"*64,
                      f"  {'NODE':<10} {'Viên 1':>12} {'Viên 2':>12} "
                      f"{'Viên 3':>12} {'TỔNG':>6}", "  " + "-"*64]
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
