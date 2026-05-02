#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RPi 5 Controller - Backend logic (giao tiếp LoRa, tính điểm, quản lý dữ liệu)
Chạy trong một thread riêng, giao tiếp với GUI qua hàng đợi (queue).
"""

import time
import math
import json
from datetime import datetime

# Thư viện LoRa
from raspi_lora import LoRa
from raspi_lorq.board_config import BOARD

# Queue dùng để giao tiếp giữa các thread
import queue

# ==================== CẤU HÌNH CHUNG ====================
UART_PORT = "/dev/ttyAMA1"
BAUD_RATE = 57600
LORA_FREQUENCY = 915
LOG_FILE = "score.txt"          # File log văn bản

# Các vòng điểm (bán kính cm, điểm)
SCORING_RINGS = [
    (7.5,  10), (15.0,  9), (22.5,  8), (30.0,  7), (37.5,  6),
    (45.0,  5), (52.5,  4), (60.0,  3), (67.5,  2), (75.0,  1),
    (float('inf'), 0)
]
MAX_RADIUS = 75
CONTROL_TIMEOUT = 60            # giây, không dùng ở backend nhưng giữ cho đồng bộ

# ==================== HÀM HỖ TRỢ ====================

def log_data(message, queue_out=None):
    """
    Ghi log ra file và gửi lên GUI (nếu có queue_out).
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_msg = f"[{timestamp}] {message}"

    # In ra console (vẫn giữ để debug)
    print(log_msg)

    # Ghi vào file
    with open(LOG_FILE, 'a') as f:
        f.write(log_msg + "\n")

    # Nếu có queue_out, gửi thông điệp log lên GUI
    if queue_out is not None:
        queue_out.put(('log', log_msg))

def calculate_distance(x, y):
    """Khoảng cách Euclidean từ tâm (0,0)"""
    return math.sqrt(x**2 + y**2)

def get_ring(distance):
    """Xác định vòng điểm dựa trên khoảng cách"""
    for radius, score in SCORING_RINGS:
        if distance <= radius:
            if score == 0:
                return 0, "Ngoài bia"
            return score, f"Vòng {score}"
    return 0, "Ngoài bia"

def calculate_score(x, y):
    """Tính điểm, khoảng cách, tên vòng"""
    dist = calculate_distance(x, y)
    score, ring = get_ring(dist)
    return {
        'score': score,
        'distance': round(dist, 2),
        'ring_name': ring,
        'x': x,
        'y': y
    }

def parse_node_data(data):
    """Parse dữ liệu từ Node: 'NODE1A, -26.3, 30.1' -> ('NODE1A', -26.3, 30.1)"""
    try:
        parts = data.split(',')
        if len(parts) < 3:
            return (None, None, None)
        node_name = parts[0].strip().upper().replace(" ", "")
        x = float(parts[1].strip())
        y = float(parts[2].strip())
        return (node_name, x, y)
    except (ValueError, IndexError, AttributeError):
        return (None, None, None)

def clear_score_json(file_path="/opt/score.json"):
    """Xoá nội dung file JSON (giữ cấu trúc rỗng)"""
    try:
        with open(file_path, 'w') as f:
            json.dump({"rounds": []}, f, indent=2)
        log_data(f"[JSON] Cleared {file_path}")
    except Exception as e:
        log_data(f"[ERROR] Clear JSON failed: {e}")

# ==================== LỚP QUẢN LÝ ĐIỂM ====================

class ScoreManager:
    """
    Quản lý điểm số của 15 node (NODE1A..NODE5C).
    Thay vì in ra console, các bảng điểm và log được gửi qua queue_out.
    """
    def __init__(self, queue_out=None):
        self.queue_out = queue_out
        # Cấu trúc dữ liệu điểm
        self.scores = {}
        suffixes = ['A', 'B', 'C']
        for i in range(1, 6):
            for suf in suffixes:
                key = f"NODE{i}{suf}"
                self.scores[key] = {
                    "x": None, "y": None, "score": None,
                    "ring_name": None, "shots": []
                }

    def update(self, node_name, x, y):
        """Cập nhật một viên đạn bắn trúng"""
        node_key = node_name.replace(" ", "").upper()
        if node_key not in self.scores:
            log_data(f"[WARN] Unknown node: {node_key}", self.queue_out)
            return

        # Tính điểm
        score_info = calculate_score(x, y)

        # Lưu vào node
        self.scores[node_key]["x"] = x
        self.scores[node_key]["y"] = y
        self.scores[node_key]["score"] = score_info['score']
        self.scores[node_key]["ring_name"] = score_info['ring_name']

        # Lưu lịch sử viên bắn
        shot = {
            'x': x, 'y': y,
            'score': score_info['score'],
            'ring': score_info['ring_name'],
            'distance': score_info['distance']
        }
        self.scores[node_key]["shots"].append(shot)

        # Ghi log điểm
        log_msg = f"[SCORE] {node_key}: ({x}, {y}) - {score_info['ring_name']} - {score_info['score']} điểm"
        log_data(log_msg, self.queue_out)

        # Lưu JSON và gửi bảng điểm cập nhật
        self._save_json()
        self._send_board()

    def _save_json(self):
        """Ghi file score_data.json"""
        try:
            data = {
                'timestamp': datetime.now().isoformat(),
                'rounds': []
            }
            for node_key, node_data in self.scores.items():
                for shot in node_data["shots"]:
                    data['rounds'].append({
                        'node': node_key,
                        'x': shot['x'],
                        'y': shot['y'],
                        'score': shot['score'],
                        'ring': shot['ring'],
                        'distance': shot['distance']
                    })
            with open('score_data.json', 'w') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            log_data(f"[ERROR] Save JSON failed: {e}", self.queue_out)

    def _send_board(self):
        """Gửi bảng điểm dưới dạng text lên GUI"""
        if self.queue_out is None:
            return
        # Tạo text bảng điểm giống như cũ
        lines = []
        lines.append("=" * 70)
        lines.append(f"BẢNG ĐIỂM – {datetime.now().strftime('%H:%M:%S')}")
        lines.append("=" * 70)

        for row_label, suffix in [("HÀNG 1", "A"), ("HÀNG 2", "B"), ("HÀNG 3", "C")]:
            lines.append(f"\n  {row_label} – Dãy {suffix}")
            lines.append("  " + "-" * 64)
            lines.append(f"  {'NODE':<10} {'Viên 1':>12} {'Viên 2':>12} {'Viên 3':>12} {'TỔNG':>6}")
            lines.append("  " + "-" * 64)

            row_total = 0
            for i in range(1, 6):
                key = f"NODE{i}{suffix}"
                shots = self.scores[key]["shots"]
                total = sum(s['score'] for s in shots)
                row_total += total

                def fmt(idx):
                    if idx < len(shots):
                        s = shots[idx]
                        return "Miss" if s['score'] == 0 else f"{s['score']}đ/{s['ring']}"
                    return "—"

                lines.append(f"  {key:<10} {fmt(0):>12} {fmt(1):>12} {fmt(2):>12} {total:>5}đ")

            lines.append(f"  {'Tổng dãy ' + suffix:<10} {'':>12} {'':>12} {'':>12} {row_total:>5}đ")

        lines.append("\n" + "=" * 70 + "\n")
        board_text = "\n".join(lines)
        self.queue_out.put(('board', board_text))

    def reset_round(self):
        """Pad miss cho những viên thiếu, xoá shots cho vòng tiếp theo"""
        for node_key in self.scores.keys():
            # Pad miss nếu chưa đủ 3 viên
            while len(self.scores[node_key]["shots"]) < 3:
                self.scores[node_key]["shots"].append({
                    'x': None, 'y': None,
                    'score': 0, 'ring': 'Miss', 'distance': None
                })
                log_data(f"[MISS] {node_key}: viên {len(self.scores[node_key]['shots'])} – 0 điểm", self.queue_out)
            # Xoá shots để bắt đầu vòng mới
            self.scores[node_key]["shots"] = []
            self.scores[node_key]["x"] = None
            self.scores[node_key]["y"] = None
            self.scores[node_key]["score"] = None
            self.scores[node_key]["ring_name"] = None
        self._save_json()
        self._send_board()

# ==================== LỚP CONTROLLER (THREAD) ====================

class Controller:
    """
    Backend điều khiển LoRa, nhận lệnh từ GUI, gửi kết quả qua queue.
    Chạy trong một thread riêng.
    """
    def __init__(self, cmd_queue, out_queue):
        """
        cmd_queue: queue nhận lệnh từ GUI (định dạng: {'type': 'send', 'node': str, 'command': str})
        out_queue: queue gửi dữ liệu lên GUI (log, board, ...)
        """
        self.cmd_queue = cmd_queue
        self.out_queue = out_queue
        self.lora = None
        self.score_manager = ScoreManager(queue_out=out_queue)
        self.running = True

    def _setup_lora(self):
        """Khởi tạo LoRa module"""
        try:
            self.lora = LoRa(BOARD.CN1, BOARD.CN1, baud=BAUD_RATE)
            self.lora.set_frequency(LORA_FREQUENCY)
            log_data(f"[INIT] LoRa ready at {LORA_FREQUENCY} MHz", self.out_queue)
        except Exception as e:
            log_data(f"[ERROR] LoRa init failed: {e}", self.out_queue)
            sys.exit(1)

    def _send_command(self, node_name, command):
        """Gửi lệnh qua LoRa"""
        try:
            message = f"{node_name} {command}"
            self.lora.send(message.encode())
            log_data(f"[TX] Sent: {message}", self.out_queue)
        except Exception as e:
            log_data(f"[ERROR] Failed to send: {e}", self.out_queue)

    def _receive_data(self):
        """Nhận dữ liệu từ LoRa, trả về string hoặc None"""
        try:
            if self.lora.is_rx_busy():
                return None
            payload = self.lora.read()
            if payload:
                data = payload.decode()
                log_data(f"[RX] Received: {data}", self.out_queue)
                return data
        except Exception as e:
            log_data(f"[ERROR] Receive error: {e}", self.out_queue)
        return None

    def run(self):
        """Vòng lặp chính của controller thread"""
        self._setup_lora()
        log_data("[CTRL] Controller thread started", self.out_queue)

        while self.running:
            # Xử lý lệnh từ GUI
            try:
                cmd = self.cmd_queue.get_nowait()
                if cmd['type'] == 'send':
                    self._send_command(cmd['node'], cmd['command'])
                elif cmd['type'] == 'reset_round':
                    self.score_manager.reset_round()
                    # Nếu muốn xoá JSON thêm: clear_score_json()
                elif cmd['type'] == 'exit':
                    self.running = False
                    break
            except queue.Empty:
                pass

            # Nhận dữ liệu LoRa
            data = self._receive_data()
            if data:
                node_name, x, y = parse_node_data(data)
                if node_name:
                    self.score_manager.update(node_name, x, y)

            time.sleep(0.05)  # Tránh CPU 100%

        # Dọn dẹp
        if self.lora:
            self.lora.close()
        log_data("[CTRL] Controller thread stopped", self.out_queue)
