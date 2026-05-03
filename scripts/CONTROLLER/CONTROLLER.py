#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CONTROLLER.py – Lõi điều khiển hệ thống (không có GPIO nút bấm)

📌 VAI TRÒ TRONG HỆ THỐNG 3 FILE:
    MAIN.py       → khởi động, quản lý thread
    CONTROLLER.py → LoRa, tính điểm, JSON   ← FILE NÀY
    GUI.py        → PyQt6 giao diện người dùng

🔄 GIAO TIẾP VỚI GUI:
    GUI  → CONTROLLER : Gọi trực tiếp handle_button() / send_command()
                        thông qua object được truyền từ MAIN.py
    CONTROLLER → GUI  : Đẩy message vào self.log_queue (queue thread-safe)
                        GUI polling queue này để hiển thị log/debug

✂️  THAY ĐỔI SO VỚI PHIÊN BẢN CŨ:
    - Xoá toàn bộ import RPi.GPIO và mọi thứ liên quan GPIO nút bấm
    - Xoá setup() phần GPIO, button_callback(), button_states, BUTTON_PINS
    - Thêm self.log_queue để gửi log sang GUI an toàn qua thread
    - Thêm set_score_callback() để GUI nhận bảng điểm khi có cập nhật
    - Thêm handle_button() thay thế button_callback() GPIO cũ
    - Hàm send_command() / receive_data() và logic điểm giữ nguyên 100%
"""

# ── Thư viện chuẩn ────────────────────────────────────────────────────────────
import time                          # delay, timeout
import sys                           # sys.exit()
import math                          # sqrt() tính khoảng cách
import json                          # lưu score_data.json
import queue                         # Queue thread-safe để gửi log sang GUI
from datetime import datetime        # timestamp ngày giờ

# ── Thư viện LoRa ─────────────────────────────────────────────────────────────
from rpi_lora import LoRa            # giao tiếp LoRa SX1278
from rpi_lora.board_config import BOARD  # pin mapping bo mạch

# ==================== CẤU HÌNH CHUNG ====================

# Cổng UART nối LoRa module (RPi 5 dùng /dev/ttyAMA1)
UART_PORT  = "/dev/ttyAMA1"

# Tốc độ baud – phải khớp với cấu hình của tất cả Node
BAUD_RATE  = 57600

# Tần số LoRa (MHz) – phải khớp với tất cả Node
LORA_FREQUENCY = 915

# File lưu log văn bản (append mỗi lần chạy)
LOG_FILE   = "score.txt"

# File JSON cho HTML visualizer (ghi đè sau mỗi lần cập nhật)
JSON_FILE  = "/opt/score.json"

# Timeout điều khiển: sau UP nếu 60s không nhận đủ 3 viên → tự OFF
CONTROL_TIMEOUT = 60

# Cấu hình vòng tính điểm: (bán kính tối đa cm, điểm)
SCORING_RINGS = [
    (7.5,  10),          # Bullseye
    (15.0,  9),
    (22.5,  8),
    (30.0,  7),
    (37.5,  6),
    (45.0,  5),
    (52.5,  4),
    (60.0,  3),
    (67.5,  2),
    (75.0,  1),
    (float('inf'), 0),   # Ngoài bia
]

MAX_RADIUS = 75  # Bán kính vòng 1 ngoài cùng (cm)


# ==================== LỚP CONTROLLER CHÍNH ====================

class Controller:
    """
    Lớp trung tâm điều phối toàn bộ logic phía backend.

    Thuộc tính public quan trọng:
        log_queue   (queue.Queue) : GUI đọc queue này để hiển thị log/debug
        display     (ScoreDisplay): object quản lý điểm, GUI gọi để lấy data
        extra_mode_active (bool)  : trạng thái EXTRA mode (GUI dùng để tô màu nút)

    Phương thức public (GUI gọi trực tiếp):
        setup()                          → khởi tạo LoRa (gọi 1 lần)
        run()                            → vòng lặp nhận LoRa (chạy trong thread)
        stop()                           → dừng vòng lặp + đóng LoRa
        handle_button(btn_name)          → xử lý nút bấm từ GUI
        send_command(node_name, command) → gửi lệnh LoRa
        get_score_table()                → trả về string bảng điểm
        reset_round()                    → reset vòng bắn
        set_score_callback(fn)           → đăng ký callback khi có điểm mới
    """

    def __init__(self):
        # ── Queue log thread-safe ──────────────────────────────────────────
        # Dùng để gửi log từ controller thread → GUI thread an toàn
        # GUI poll queue này mỗi 200ms (QTimer) để hiển thị lên ô log
        # maxsize=500: tránh tràn bộ nhớ nếu GUI chậm hoặc bị block
        self.log_queue = queue.Queue(maxsize=500)

        # ── Trạng thái EXTRA mode ──────────────────────────────────────────
        # True  → tất cả nút bị khoá, chỉ nút EX (EXTRA) hoạt động
        # False → hoạt động bình thường
        self.extra_mode_active = False

        # ── Trạng thái toggle của từng nút ────────────────────────────────
        # Thay thế button_states dict của phiên bản GPIO cũ
        # True = đang ở trạng thái UP (đã bấm lần 1)
        # False = đang ở trạng thái DOWN (chưa bấm hoặc đã bấm lần 2)
        self.button_states = {
            "NODE1": False, "NODE2": False, "NODE3": False,
            "NODE4": False, "NODE5": False,
            "A": False, "B": False, "C": False, "D": False,
            "EXTRA": False,
        }

        # ── LoRa object ────────────────────────────────────────────────────
        self.lora = None   # được tạo trong setup()

        # ── ScoreDisplay ───────────────────────────────────────────────────
        # Truyền self._log vào để ScoreDisplay cũng dùng chung kênh log
        self.display = ScoreDisplay(log_fn=self._log)

        # ── Callback bảng điểm ─────────────────────────────────────────────
        # GUI đăng ký một hàm qua set_score_callback()
        # Mỗi khi điểm được cập nhật → callback(string_bảng_điểm)
        self._score_callback = None

        # ── Cờ điều khiển vòng lặp run() ──────────────────────────────────
        self._running = False

    # ── Đăng ký callback bảng điểm ────────────────────────────────────────
    def set_score_callback(self, fn):
        """
        GUI gọi hàm này để nhận thông báo khi điểm thay đổi.

        Tham số:
            fn (callable): nhận 1 tham số str (bảng điểm đã render)
                           GUI dùng để update QLabel / QTextEdit bên trái
        """
        self._score_callback = fn

    # ── Khởi tạo phần cứng ────────────────────────────────────────────────
    def setup(self):
        """
        Khởi tạo kết nối LoRa. Gọi một lần từ MAIN.py trước khi start thread.
        Nếu lỗi → log rồi raise để MAIN.py hiển thị thông báo lên GUI.
        """
        try:
            # Tạo đối tượng LoRa với cổng UART và baud rate đã cấu hình
            self.lora = LoRa(BOARD.CN1, BOARD.CN1, baud=BAUD_RATE)
            # Đặt tần số hoạt động (phải khớp với các Node)
            self.lora.set_frequency(LORA_FREQUENCY)
            self._log(f"[INIT] LoRa sẵn sàng tại {LORA_FREQUENCY} MHz, {BAUD_RATE} baud")
        except Exception as e:
            self._log(f"[ERROR] LoRa khởi tạo thất bại: {e}")
            raise   # MAIN.py bắt exception này và hiển thị lỗi lên GUI

    # ── Vòng lặp chính (chạy trong thread riêng) ──────────────────────────
    def run(self):
        """
        Vòng lặp nhận dữ liệu LoRa và cập nhật điểm.
        MAIN.py chạy hàm này trong threading.Thread riêng biệt với GUI thread.

        Luồng xử lý mỗi iteration (100ms):
            1. _receive_data()    → lấy raw string từ LoRa (non-blocking)
            2. _parse_node_data() → tách (node_name, x, y)
            3. display.update()   → tính điểm, lưu JSON
            4. _score_callback()  → thông báo GUI refresh bảng điểm
            5. time.sleep(0.1)    → nhường CPU, giảm busy-waiting
        """
        self._running = True
        self._log("[CTRL] Vòng lặp Controller bắt đầu")

        while self._running:
            try:
                # Thử nhận 1 gói dữ liệu từ LoRa
                data = self._receive_data()

                if data:
                    # Parse chuỗi nhận được thành 3 phần
                    node_name, x, y = self._parse_node_data(data)

                    if node_name:
                        # Cập nhật điểm (cũng ghi JSON bên trong)
                        self.display.update(node_name, x, y)

                        # Gọi callback để GUI refresh ô bảng điểm
                        if self._score_callback:
                            self._score_callback(self.display.get_score_table())

            except Exception as e:
                # Không crash vòng lặp – chỉ log lỗi và tiếp tục
                self._log(f"[ERROR] Ngoại lệ trong vòng lặp: {e}")

            # Sleep 100ms: đủ nhanh cho LoRa, đủ nhẹ cho CPU
            time.sleep(0.1)

        self._log("[CTRL] Vòng lặp Controller kết thúc")

    def stop(self):
        """
        Dừng vòng lặp run() và đóng kết nối LoRa.
        MAIN.py gọi hàm này khi người dùng đóng cửa sổ GUI.
        """
        self._running = False   # run() sẽ thoát sau sleep tiếp theo
        if self.lora:
            try:
                self.lora.close()
                self._log("[CTRL] Kết nối LoRa đã đóng")
            except Exception as e:
                self._log(f"[ERROR] Đóng LoRa: {e}")

    # ==================== ĐIỀU KHIỂN NÚT BẤM ====================

    def handle_button(self, btn_name):
        """
        Xử lý sự kiện bấm nút từ GUI.
        Thay thế hoàn toàn button_callback() của phiên bản GPIO cũ.
        Logic toggle và EXTRA mode giữ nguyên 100%.

        Tham số:
            btn_name (str): "NODE1"…"NODE5", "A","B","C","D","EXTRA"

        Luồng xử lý:
            - Nếu EXTRA mode đang bật → chỉ chấp nhận nút EXTRA để tắt
            - Nếu bình thường          → toggle UP/DOWN và gửi lệnh LoRa
        """

        # ── EXTRA mode đang active ─────────────────────────────────────────
        if self.extra_mode_active:
            if btn_name == "EXTRA":
                # Tắt EXTRA mode
                self.extra_mode_active = False
                self.button_states["EXTRA"] = False
                self.send_command("EXTRA", "DOWN")
                self._log("[CONTROL] EXTRA mode TẮT")
                # Xoá JSON khi thoát EXTRA (giữ nguyên hành vi bản cũ)
                self._clear_score_json()
            else:
                # Khoá tất cả nút khác
                self._log(f"[WARNING] '{btn_name}' bị khoá – EXTRA mode đang bật")
            return  # thoát sớm

        # ── Chế độ bình thường ────────────────────────────────────────────
        current = self.button_states.get(btn_name, False)

        if btn_name == "EXTRA":
            if not current:
                # Bấm lần 1 → bật EXTRA mode
                self.extra_mode_active = True
                self.button_states["EXTRA"] = True
                self.send_command("EXTRA", "UP")
                self._log("[CONTROL] EXTRA mode BẬT")
            else:
                # Bấm lần 2 → tắt EXTRA mode
                self.extra_mode_active = False
                self.button_states["EXTRA"] = False
                self.send_command("EXTRA", "DOWN")
                self._log("[CONTROL] EXTRA mode TẮT")
                self._clear_score_json()

        else:
            # NODE1–5, A, B, C, D: toggle UP ↔ DOWN mỗi lần bấm
            if not current:
                # Chưa UP → gửi UP
                self.send_command(btn_name, "UP")
                self.button_states[btn_name] = True
                self._log(f"[CONTROL] {btn_name} → UP")
            else:
                # Đã UP → gửi DOWN
                self.send_command(btn_name, "DOWN")
                self.button_states[btn_name] = False
                self._log(f"[CONTROL] {btn_name} → DOWN")

    # ==================== GIAO TIẾP LoRa ====================

    def send_command(self, node_name, command):
        """
        Gửi lệnh điều khiển đến Node qua LoRa.

        Định dạng: "<node_name> <command>"
        Ví dụ:    "NODE1 UP", "A DOWN", "EXTRA UP"

        Tham số:
            node_name (str): "NODE1"…"NODE5", "A","B","C","D","EXTRA"
            command   (str): "UP" hoặc "DOWN"
        """
        try:
            message = f"{node_name} {command}"      # ghép chuỗi lệnh
            self.lora.send(message.encode('utf-8'))  # encode → bytes rồi gửi
            self._log(f"[TX] Đã gửi: '{message}'")
        except Exception as e:
            self._log(f"[ERROR] Gửi thất bại '{node_name} {command}': {e}")

    def _receive_data(self):
        """
        Nhận một gói dữ liệu từ LoRa (non-blocking).

        Trả về:
            str  : dữ liệu đã decode, ví dụ "NODE1A, -26.3, 30.1"
            None : nếu không có dữ liệu, LoRa bận, hoặc lỗi
        """
        try:
            if self.lora.is_rx_busy():
                return None      # LoRa đang nhận, chưa đọc được

            payload = self.lora.read()   # trả về bytes hoặc None

            if payload:
                data = payload.decode('utf-8')  # bytes → str
                self._log(f"[RX] Nhận được: '{data}'")
                return data

        except Exception as e:
            self._log(f"[ERROR] Nhận dữ liệu LoRa: {e}")

        return None

    def _parse_node_data(self, data):
        """
        Parse chuỗi dữ liệu thành (node_name, x, y).

        Định dạng đầu vào: "NODE1A, -26.3, 30.1"
        Đầu ra:            ("NODE1A", -26.3, 30.1)

        Trả về (None, None, None) nếu parse thất bại.
        """
        try:
            parts = data.split(',')
            if len(parts) < 3:
                # Dữ liệu không đủ trường
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
        """
        Ghi log đồng thời vào file và queue GUI.

        Luồng ghi:
            1. Thêm timestamp vào đầu message
            2. Ghi vào LOG_FILE (append, UTF-8)
            3. put_nowait() vào log_queue → GUI poll và hiển thị

        Thread-safe: queue.Queue.put_nowait() không cần lock ngoài.
        Khi queue đầy → pop message cũ nhất để nhường chỗ message mới.
        """
        timestamp   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_message = f"[{timestamp}] {message}"

        # Ghi file log
        try:
            with open(LOG_FILE, 'a', encoding='utf-8') as f:
                f.write(log_message + "\n")
        except Exception:
            pass   # không crash nếu file không ghi được (ví dụ: quyền)

        # Đẩy vào queue cho GUI
        try:
            self.log_queue.put_nowait(log_message)
        except queue.Full:
            # Queue đầy: bỏ message cũ nhất rồi thêm message mới
            try:
                self.log_queue.get_nowait()
                self.log_queue.put_nowait(log_message)
            except Exception:
                pass

    def _clear_score_json(self):
        """
        Ghi lại JSON_FILE với cấu trúc rỗng {"rounds": []}.
        Gọi sau khi thoát EXTRA mode để reset dữ liệu cho đợt bắn mới.
        """
        try:
            with open(JSON_FILE, 'w', encoding='utf-8') as f:
                json.dump({"rounds": []}, f, indent=2)
            self._log(f"[JSON] Đã xoá dữ liệu trong {JSON_FILE}")
        except Exception as e:
            self._log(f"[ERROR] Xoá JSON thất bại: {e}")

    def get_score_table(self):
        """Proxy: GUI gọi để lấy bảng điểm string bất kỳ lúc nào."""
        return self.display.get_score_table()

    def reset_round(self):
        """Proxy: GUI gọi để reset vòng bắn."""
        self.display.reset_round()
        self._log("[CTRL] Reset vòng bắn hoàn tất")


# ==================== HÀM TÍNH ĐIỂM ====================

def calculate_distance(x, y):
    """
    Khoảng cách Euclidean từ tâm bia (0,0) đến (x,y).
    Công thức: r = √(x² + y²)
    """
    return math.sqrt(x**2 + y**2)


def get_ring(distance):
    """
    Tra bảng SCORING_RINGS: trả về (điểm, tên_vòng) theo khoảng cách.
    """
    for radius, score in SCORING_RINGS:
        if distance <= radius:
            return (score, "Ngoài bia") if score == 0 else (score, f"Vòng {score}")
    return (0, "Ngoài bia")


def calculate_score(x, y):
    """
    Trả về dict đầy đủ thông tin điểm: score, distance, ring_name, x, y.
    """
    distance         = calculate_distance(x, y)
    score, ring_name = get_ring(distance)
    return {
        'score':     score,
        'distance':  round(distance, 2),
        'ring_name': ring_name,
        'x': x,
        'y': y,
    }


# ==================== LỚP QUẢN LÝ ĐIỂM ====================

class ScoreDisplay:
    """
    Quản lý điểm số 15 node (NODE1–5 × dãy A/B/C).

    Thay đổi so với bản cũ:
        - Thêm get_score_table() trả về string (thay vì print ra console)
        - log_fn được inject từ Controller để dùng chung kênh log/queue
    """

    def __init__(self, log_fn=print):
        """
        Tham số:
            log_fn (callable): hàm ghi log (mặc định print,
                               thực tế là Controller._log)
        """
        self._log = log_fn

        # Hàm tạo dict rỗng cho một node
        _empty = lambda: {
            "x": None, "y": None,
            "score": None, "ring_name": None,
            "shots": []   # list viên bắn, tối đa 3 viên mỗi dãy
        }

        # Khởi tạo 15 node (5 × 3 dãy A/B/C)
        self.scores = {
            # Dãy A – đợt bắn 1
            "NODE1A": _empty(), "NODE2A": _empty(), "NODE3A": _empty(),
            "NODE4A": _empty(), "NODE5A": _empty(),
            # Dãy B – đợt bắn 2
            "NODE1B": _empty(), "NODE2B": _empty(), "NODE3B": _empty(),
            "NODE4B": _empty(), "NODE5B": _empty(),
            # Dãy C – đợt bắn 3
            "NODE1C": _empty(), "NODE2C": _empty(), "NODE3C": _empty(),
            "NODE4C": _empty(), "NODE5C": _empty(),
        }

    def update(self, node_name, x, y):
        """
        Cập nhật tọa độ và điểm cho một node, rồi lưu JSON.

        Tham số:
            node_name (str): "NODE1A", "NODE2B", v.v.
            x, y (float)  : tọa độ điểm đạn (cm)
        """
        node_key = node_name.replace(" ", "").upper()

        if node_key not in self.scores:
            self._log(f"[WARN] Không nhận ra node: '{node_key}'")
            return

        # Tính điểm từ tọa độ
        result = calculate_score(x, y)

        # Cập nhật trạng thái hiện tại của node
        self.scores[node_key]["x"]         = x
        self.scores[node_key]["y"]         = y
        self.scores[node_key]["score"]     = result['score']
        self.scores[node_key]["ring_name"] = result['ring_name']

        # Thêm viên bắn vào lịch sử (giới hạn tối đa 3 viên/dãy)
        if len(self.scores[node_key]["shots"]) < 3:
            self.scores[node_key]["shots"].append({
                'x':        x,
                'y':        y,
                'score':    result['score'],
                'ring':     result['ring_name'],
                'distance': result['distance'],
            })

        self._log(
            f"[SCORE] {node_key}: ({x:.1f}, {y:.1f}) → "
            f"{result['ring_name']} – {result['score']} điểm"
        )

        # Ghi JSON sau mỗi cập nhật để HTML visualizer refresh được
        self.save_to_json()

    def save_to_json(self, file_path=None):
        """
        Ghi toàn bộ shots vào file JSON.
        Định dạng: {"timestamp": "...", "rounds": [...]}
        """
        path = file_path or JSON_FILE
        try:
            data = {
                'timestamp': datetime.now().isoformat(),
                'rounds': [
                    {
                        'node':     nk,
                        'x':        shot['x'],
                        'y':        shot['y'],
                        'score':    shot['score'],
                        'ring':     shot['ring'],
                        'distance': shot['distance'],
                    }
                    # list comprehension duyệt tất cả node + shots
                    for nk, nd in self.scores.items()
                    for shot in nd["shots"]
                ]
            }
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self._log(f"[ERROR] Ghi JSON thất bại: {e}")

    def get_total_score(self, node_key):
        """Tổng điểm 3 viên của một node (0–30)."""
        if node_key in self.scores:
            shots = self.scores[node_key]["shots"]
            if shots:
                return sum(s['score'] for s in shots)
        return 0

    def get_score_table(self):
        """
        Render bảng điểm thành string đã định dạng.
        GUI hiển thị chuỗi này vào ô bảng điểm bên trái.
        Thay thế hàm display() print ra console của bản cũ.

        Trả về:
            str: bảng điểm hoàn chỉnh, các dòng cách nhau bằng '\\n'
        """
        lines = []
        sep   = "=" * 70

        lines.append(sep)
        lines.append("BẢNG ĐIỂM  –  " + datetime.now().strftime('%H:%M:%S'))
        lines.append(sep)

        # Duyệt qua 3 hàng A, B, C
        for row_label, suffix in [("HÀNG 1", "A"),
                                   ("HÀNG 2", "B"),
                                   ("HÀNG 3", "C")]:
            lines.append(f"\n  {row_label} – Dãy {suffix}")
            lines.append("  " + "-" * 64)
            lines.append(
                f"  {'NODE':<10} {'Viên 1':>12} {'Viên 2':>12} "
                f"{'Viên 3':>12} {'TỔNG':>6}"
            )
            lines.append("  " + "-" * 64)

            row_total = 0
            for i in range(1, 6):
                key   = f"NODE{i}{suffix}"
                shots = self.scores[key]["shots"]
                total = self.get_total_score(key)
                row_total += total

                def fmt(idx, _shots=shots):
                    """Định dạng ô điểm: '8đ/Vòng 8' hoặc 'Miss' hoặc '—'."""
                    if idx < len(_shots):
                        s = _shots[idx]
                        return "Miss" if s['score'] == 0 \
                               else f"{s['score']}đ/{s['ring']}"
                    return "—"   # chưa có dữ liệu

                lines.append(
                    f"  {key:<10} {fmt(0):>12} {fmt(1):>12} "
                    f"{fmt(2):>12} {total:>5}đ"
                )

            # Dòng tổng của cả hàng
            lines.append(
                f"  {'Tổng dãy ' + suffix:<10} {'':>12} {'':>12} "
                f"{'':>12} {row_total:>5}đ"
            )

        lines.append("\n" + sep)
        return "\n".join(lines)

    def reset_round(self):
        """
        Pad miss cho viên còn thiếu rồi clear shots cho vòng tiếp theo.
        Giữ nguyên logic từ phiên bản cũ.
        """
        for node_key in self.scores.keys():
            # Đệm thêm viên miss nếu chưa đủ 3
            while len(self.scores[node_key]["shots"]) < 3:
                self.scores[node_key]["shots"].append({
                    'x': None, 'y': None,
                    'score': 0, 'ring': 'Miss', 'distance': None
                })
                self._log(
                    f"[MISS] {node_key}: "
                    f"viên {len(self.scores[node_key]['shots'])} – 0 điểm"
                )

        # Lưu JSON với dữ liệu pad miss
        self.save_to_json()

        # Reset tất cả để chuẩn bị vòng mới
        for node_key in self.scores.keys():
            self.scores[node_key]["shots"]     = []
            self.scores[node_key]["x"]         = None
            self.scores[node_key]["y"]         = None
            self.scores[node_key]["score"]     = None
            self.scores[node_key]["ring_name"] = None

        self._log("[SCORE] Reset xong – sẵn sàng vòng bắn mới")
