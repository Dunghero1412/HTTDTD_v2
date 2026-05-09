#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
node-virtual.py – Node ảo chạy trên máy tính (không cần phần cứng)

📌 MỤC ĐÍCH:
    Thay thế hoàn toàn NODE.py thật khi phần cứng chưa về.
    Gửi dữ liệu đến CONTROLLER_virtual.py qua TCP socket nội bộ
    thay vì LoRa SX1276.

📡 GIAO TIẾP:
    node-virtual.py  ──TCP:9000──►  CONTROLLER_virtual.py
    Định dạng gói tin: "NODE1A, -26.30, 30.10\n"  (giữ nguyên như bản thật)
    Controller nhận → parse → tính điểm → hiển thị GUI như bình thường.

    Controller cũng gửi lệnh ngược lại qua cùng TCP port 9001:
    CONTROLLER_virtual → TCP:9001 → node-virtual (nhận lệnh UP/DOWN)

🖥️  GIAO DIỆN node-virtual:
    Terminal đơn giản, menu lựa chọn:
    ┌─────────────────────────────────────────────┐
    │  NODE: NODE1A  |  Trạng thái: WAITING       │
    │─────────────────────────────────────────────│
    │  [1] Bắn ngẫu nhiên trong vùng bia          │
    │  [2] Nhập toạ độ tay (x, y)                 │
    │  [3] Bắn tự động 3 viên (test nhanh)        │
    │  [4] Bắn Miss (ngoài hình)                  │
    │  [q] Thoát                                  │
    └─────────────────────────────────────────────┘

🔄 LUỒNG:
    1. Kết nối TCP đến CONTROLLER_virtual (port 9000)
    2. Lắng nghe lệnh từ controller (port 9001 thread riêng)
    3. Khi nhận "UP" → cho phép bắn
    4. Người dùng chọn chế độ → gửi "NODE1A, x, y" đến controller
    5. Khi nhận "DOWN" hoặc đủ 3 viên → khoá bắn
"""

import socket
import threading
import time
import random
import math
import sys

# ==================== CẤU HÌNH ====================

# ✓ Thay đổi 2 biến này cho mỗi node ảo
NODE_ROW    = 1      # 1–5
NODE_SUFFIX = "A"    # A / B / C / D
NODE_NAME   = f"NODE{NODE_ROW}{NODE_SUFFIX}"

# TCP endpoint của CONTROLLER_virtual
CTRL_HOST = "127.0.0.1"
CTRL_PORT = 9000     # controller lắng nghe uplink tại đây

# TCP port node-virtual lắng nghe lệnh từ controller (downlink)
NODE_LISTEN_PORT = 9001  # controller gửi "NODE1 UP", "A UP" đến đây

# Kích thước bia (cm)
BIA_HALF = 50.0

# Tâm vòng điểm theo suffix
CENTER_OFFSET_Y = {
    "A": 0.0,    # tâm = tâm bia
    "B": 25.0,   # tâm dịch lên +25cm (vị trí hình người)
    "C": 25.0,
    "D": 0.0,    # NODE D – chưa định nghĩa, dùng tâm chuẩn
}

# ==================== TRẠNG THÁI ====================

# True khi controller đã gửi UP → cho phép bắn
_active      = False
# Số viên đã bắn trong lượt hiện tại
_shot_count  = 0
# Lock để tránh race condition giữa thread nhận lệnh và thread chính
_state_lock  = threading.Lock()
# Socket kết nối đến controller (uplink)
_sock_up     = None
# Cờ dừng thread
_running     = True

# ==================== KẾT NỐI TCP ====================

def connect_to_controller():
    """
    Tạo TCP connection đến CONTROLLER_virtual.
    Thử lại mỗi 2s cho đến khi thành công.

    Trả về:
        socket object đã kết nối
    """
    global _sock_up
    while True:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((CTRL_HOST, CTRL_PORT))
            _sock_up = s
            print(f"[NET] Đã kết nối đến Controller tại {CTRL_HOST}:{CTRL_PORT}")
            return s
        except ConnectionRefusedError:
            print(f"[NET] Controller chưa sẵn sàng – thử lại sau 2s...")
            time.sleep(2)
        except Exception as e:
            print(f"[NET] Lỗi kết nối: {e} – thử lại sau 2s...")
            time.sleep(2)


def send_data(x, y):
    """
    Gửi tọa độ đạn đến controller qua TCP.
    Định dạng giữ nguyên như NODE.py thật: "NODE1A, -26.30, 30.10\n"

    Tham số:
        x, y (float): toạ độ điểm đạn (cm)
    """
    global _sock_up
    message = f"{NODE_NAME}, {x:.2f}, {y:.2f}\n"
    try:
        _sock_up.sendall(message.encode('utf-8'))
        print(f"[TX] Đã gửi: {message.strip()}")
    except Exception as e:
        print(f"[ERROR] Gửi dữ liệu thất bại: {e}")
        # Thử kết nối lại
        try:
            _sock_up.close()
        except Exception:
            pass
        connect_to_controller()

# ==================== NHẬN LỆNH TỪ CONTROLLER ====================

def _command_listener():
    """
    Thread lắng nghe lệnh từ CONTROLLER_virtual qua TCP port NODE_LISTEN_PORT.
    Chạy daemon trong nền, tự tắt khi chương trình thoát.

    Lệnh nhận:
        "NODE1 UP"   → _active = True
        "NODE1 DOWN" → _active = False
        "A UP"       → _active = True (broadcast nhóm)
        "A DOWN"     → _active = False
        "EXTRA UP"   → _active = False (khoá)
    """
    global _active, _shot_count

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind(("0.0.0.0", NODE_LISTEN_PORT))
        server.listen(1)
        server.settimeout(1.0)
        print(f"[CMD] Lắng nghe lệnh tại port {NODE_LISTEN_PORT}")
    except Exception as e:
        print(f"[WARN] Không mở được port lệnh {NODE_LISTEN_PORT}: {e}")
        return

    buf = ""
    conn = None

    while _running:
        # Chấp nhận kết nối từ controller nếu chưa có
        if conn is None:
            try:
                conn, addr = server.accept()
                conn.settimeout(0.5)
                print(f"[CMD] Controller kết nối từ {addr}")
            except socket.timeout:
                continue
            except Exception:
                continue

        # Đọc dữ liệu từ kết nối hiện tại
        try:
            chunk = conn.recv(256).decode('utf-8')
            if not chunk:
                # Controller đóng kết nối
                conn.close()
                conn = None
                continue
            buf += chunk
        except socket.timeout:
            continue
        except Exception:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
            conn = None
            continue

        # Xử lý từng lệnh (ngăn cách bằng '\n')
        while '\n' in buf:
            line, buf = buf.split('\n', 1)
            line = line.strip()
            if not line:
                continue

            parts = line.upper().split()
            if len(parts) < 2:
                continue

            target, action = parts[0], parts[1]

            # Kiểm tra lệnh có dành cho node này không
            is_for_me = (
                target == NODE_NAME or          # lệnh cụ thể: "NODE1A UP"
                target == f"NODE{NODE_ROW}" or  # lệnh theo hàng: "NODE1 UP"
                target == NODE_SUFFIX or        # lệnh theo nhóm: "A UP"
                target == "EXTRA"               # broadcast tất cả
            )

            if not is_for_me:
                continue

            with _state_lock:
                if action == "UP" and target != "EXTRA":
                    _active     = True
                    _shot_count = 0
                    print(f"\n[CMD] ← {line} → ACTIVATED")
                    print(f"[CMD] Sẵn sàng bắn – nhập lệnh:")
                elif action == "DOWN" or target == "EXTRA":
                    _active = False
                    print(f"\n[CMD] ← {line} → DEACTIVATED")

    server.close()

# ==================== LOGIC BẮN ====================

def _random_shot_in_bia():
    """
    Tạo toạ độ ngẫu nhiên trong vòng tròn bán kính 75cm (trong bia).
    Dùng phân phối đều trên đĩa tròn (không phải hình vuông).

    Trả về:
        tuple (x, y) float (cm)
    """
    while True:
        # Phân phối đều trên đĩa tròn bằng rejection sampling
        x = random.uniform(-BIA_HALF, BIA_HALF)
        y = random.uniform(-BIA_HALF, BIA_HALF)
        if math.sqrt(x**2 + y**2) <= 75.0:
            return round(x, 2), round(y, 2)


def _random_shot_near_center(radius=30.0):
    """
    Tạo toạ độ ngẫu nhiên gần tâm vòng (trong bán kính radius cm).
    Tâm vòng tính theo CENTER_OFFSET_Y của node hiện tại.

    Tham số:
        radius (float): bán kính khu vực bắn (cm)
    """
    cy = CENTER_OFFSET_Y.get(NODE_SUFFIX, 0.0)
    while True:
        x = random.uniform(-radius, radius)
        y = random.uniform(cy - radius, cy + radius)
        if math.sqrt(x**2 + (y - cy)**2) <= radius:
            # Clamp trong biên bia
            x = max(-BIA_HALF, min(BIA_HALF, x))
            y = max(-BIA_HALF, min(BIA_HALF, y))
            return round(x, 2), round(y, 2)


def _miss_shot():
    """
    Tạo toạ độ bắn trượt – ngoài bán kính 75cm nhưng trong bia 100×100cm.
    """
    while True:
        x = random.uniform(-BIA_HALF, BIA_HALF)
        y = random.uniform(-BIA_HALF, BIA_HALF)
        cy = CENTER_OFFSET_Y.get(NODE_SUFFIX, 0.0)
        if math.sqrt(x**2 + (y - cy)**2) > 75.0:
            return round(x, 2), round(y, 2)


def _do_shot(x, y):
    """
    Thực hiện 1 viên bắn: kiểm tra trạng thái, gửi dữ liệu, tăng bộ đếm.

    Tham số:
        x, y (float): toạ độ (cm)
    """
    global _shot_count, _active

    with _state_lock:
        if not _active:
            print("[WARN] Node chưa được kích hoạt (chờ lệnh UP từ Controller)")
            return

        if _shot_count >= 3:
            print("[WARN] Đã đủ 3 viên trong lượt này")
            return

    send_data(x, y)

    with _state_lock:
        _shot_count += 1
        count = _shot_count
        print(f"[SHOT] Viên {count}/3 | x={x}, y={y}")

        if count >= 3:
            _active = False
            print("[SHOT] Đủ 3 viên – lượt kết thúc, chờ lệnh UP tiếp theo")

# ==================== GIAO DIỆN TERMINAL ====================

def _print_header():
    """In header trạng thái node."""
    cy    = CENTER_OFFSET_Y.get(NODE_SUFFIX, 0.0)
    state = "ACTIVE ●" if _active else "WAITING ○"
    print("\n" + "═" * 52)
    print(f"  NODE: {NODE_NAME:<10} Trạng thái: {state}")
    print(f"  Viên: {_shot_count}/3    Tâm vòng: (0, {cy:+.0f}cm)")
    print("═" * 52)


def _print_menu():
    """In menu lựa chọn."""
    print("  [1] Bắn ngẫu nhiên trong bia (r≤75cm)")
    print("  [2] Bắn gần tâm vòng       (r≤30cm)")
    print("  [3] Nhập toạ độ tay         (x, y)")
    print("  [4] Bắn tự động 3 viên      (test nhanh)")
    print("  [5] Bắn trượt               (ngoài vòng)")
    print("  [q] Thoát")
    print("─" * 52)


def _input_coordinates():
    """Đọc toạ độ (x, y) từ người dùng."""
    try:
        raw = input("  Nhập x y (cách nhau dấu cách, đơn vị cm): ")
        parts = raw.strip().split()
        x = float(parts[0])
        y = float(parts[1])
        x = max(-BIA_HALF, min(BIA_HALF, x))
        y = max(-BIA_HALF, min(BIA_HALF, y))
        return x, y
    except (ValueError, IndexError):
        print("[ERROR] Định dạng không hợp lệ – nhập lại")
        return None, None


def main():
    """Vòng lặp chính của node-virtual."""
    global _running

    print("=" * 52)
    print(f"  NODE VIRTUAL – {NODE_NAME}")
    print(f"  Controller: {CTRL_HOST}:{CTRL_PORT}")
    print(f"  Lệnh vào:   port {NODE_LISTEN_PORT}")
    print("=" * 52)

    # Kết nối uplink đến controller
    connect_to_controller()

    # Khởi động thread nhận lệnh từ controller (downlink)
    cmd_thread = threading.Thread(
        target=_command_listener,
        name="CmdListener",
        daemon=True,
    )
    cmd_thread.start()

    print(f"[INIT] {NODE_NAME} sẵn sàng – chờ lệnh UP từ Controller")

    try:
        while True:
            _print_header()
            _print_menu()

            try:
                choice = input("  Chọn: ").strip().lower()
            except EOFError:
                break

            if choice == 'q':
                break

            elif choice == '1':
                x, y = _random_shot_in_bia()
                _do_shot(x, y)

            elif choice == '2':
                x, y = _random_shot_near_center(radius=30.0)
                _do_shot(x, y)

            elif choice == '3':
                x, y = _input_coordinates()
                if x is not None:
                    _do_shot(x, y)

            elif choice == '4':
                # Tự động bắn 3 viên liên tiếp
                if not _active:
                    print("[WARN] Chưa được kích hoạt")
                else:
                    for i in range(3):
                        if not _active and i > 0:
                            break
                        x, y = _random_shot_near_center(radius=25.0)
                        _do_shot(x, y)
                        if i < 2:
                            time.sleep(0.5)   # delay giữa các viên

            elif choice == '5':
                x, y = _miss_shot()
                _do_shot(x, y)

            else:
                print("[WARN] Lựa chọn không hợp lệ")

            time.sleep(0.2)

    except KeyboardInterrupt:
        print("\n[EXIT] Dừng node-virtual")

    finally:
        _running = False
        if _sock_up:
            try:
                _sock_up.close()
            except Exception:
                pass
        print("[EXIT] Đã đóng kết nối")


if __name__ == "__main__":
    main()
