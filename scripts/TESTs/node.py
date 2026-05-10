#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
node-virtual.py – Node ảo chạy trên máy tính

KIẾN TRÚC ĐƠN GIẢN (1 kết nối TCP, 2 chiều):
    Node kết nối đến Controller tại port 9000.
    Trên cùng 1 connection:
        Node → Controller : "NODE1A, -26.30, 30.10\n"  (uplink data)
        Controller → Node : "NODE1 UP\n"                (downlink lệnh)

CÁCH CHẠY:
    # Terminal 1
    python MAIN_virtual.py

    # Terminal 2 (NODE1A – mặc định)
    python node-virtual.py

    # Terminal 3 (NODE2B – đổi 2 biến đầu file)
    # NODE_ROW=2, NODE_SUFFIX="B"
    python node-virtual.py
"""

import socket
import threading
import time
import random
import math

# ==================== CẤU HÌNH ====================
# ✓ Chỉnh 2 biến này cho mỗi node ảo

NODE_ROW    = 1      # 1 – 5
NODE_SUFFIX = "A"    # A / B / C / D
NODE_NAME   = f"NODE{NODE_ROW}{NODE_SUFFIX}"

CTRL_HOST   = "127.0.0.1"
CTRL_PORT   = 9000

BIA_HALF    = 50.0   # cm

CENTER_OFFSET_Y = {
    "A": 0.0, "B": 25.0, "C": 25.0, "D": 0.0,
}

# ==================== TRẠNG THÁI ====================

_active     = False    # True khi nhận lệnh UP
_shot_count = 0        # số viên đã bắn lượt này
_lock       = threading.Lock()
_running    = True
_conn       = None     # socket duy nhất (2 chiều)

# ==================== KẾT NỐI ====================

def connect():
    """Kết nối đến Controller, thử lại cho đến khi thành công."""
    global _conn
    attempt = 0
    while True:
        attempt += 1
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(3.0)
            s.connect((CTRL_HOST, CTRL_PORT))
            s.settimeout(None)   # chuyển sang blocking sau khi kết nối
            _conn = s
            print(f"[NET] ✓ Kết nối đến Controller {CTRL_HOST}:{CTRL_PORT}")
            return
        except ConnectionRefusedError:
            print(f"[NET] [{attempt}] Bị từ chối – chắc MAIN_virtual.py đang chạy? Thử lại sau 2s...")
            time.sleep(2)
        except socket.timeout:
            print(f"[NET] [{attempt}] Timeout – thử lại sau 2s...")
            time.sleep(2)
        except Exception as e:
            print(f"[NET] [{attempt}] Lỗi: {e} – thử lại sau 2s...")
            time.sleep(2)

# ==================== NHẬN LỆNH (thread) ====================

def _recv_thread():
    """
    Thread đọc lệnh từ Controller qua cùng TCP connection.
    Chạy daemon song song với vòng lặp input chính.

    Lệnh nhận được:
        "NODE1 UP\n"   → _active = True,  reset shot count
        "NODE1 DOWN\n" → _active = False
        "A UP\n"       → _active = True   (broadcast nhóm)
        "A DOWN\n"     → _active = False
        "EXTRA UP\n"   → _active = False  (khoá)
    """
    global _active, _shot_count, _running, _conn

    buf = ""
    while _running:
        try:
            # Đọc lệnh từ controller
            chunk = _conn.recv(256)
            if not chunk:
                print("[NET] Controller đóng kết nối – thử kết nối lại...")
                _conn.close()
                connect()
                buf = ""
                continue

            buf += chunk.decode('utf-8')

            # Xử lý từng dòng lệnh
            while '\n' in buf:
                line, buf = buf.split('\n', 1)
                line = line.strip()
                if not line:
                    continue

                parts  = line.upper().split()
                if len(parts) < 2:
                    continue

                target, action = parts[0], parts[1]

                # Kiểm tra lệnh có dành cho node này không
                is_for_me = (
                    target == NODE_NAME          or   # "NODE1A UP"
                    target == f"NODE{NODE_ROW}"  or   # "NODE1 UP"
                    target == NODE_SUFFIX        or   # "A UP"
                    target == "EXTRA"                 # broadcast khoá
                )

                if not is_for_me:
                    continue

                with _lock:
                    if action == "UP" and target != "EXTRA":
                        _active     = True
                        _shot_count = 0
                        print(f"\n[CMD] ← '{line}' → ACTIVATED")
                        print("[CMD] Nhập lệnh bắn:")
                    elif action == "DOWN" or target == "EXTRA":
                        _active = False
                        print(f"\n[CMD] ← '{line}' → DEACTIVATED")

        except Exception as e:
            if _running:
                print(f"[ERROR] Recv thread: {e}")
                # Thử kết nối lại
                try:
                    _conn.close()
                except Exception:
                    pass
                time.sleep(1)
                connect()
                buf = ""

# ==================== GỬI DỮ LIỆU ====================

def send_data(x, y):
    """
    Gửi toạ độ đạn về Controller qua TCP connection.
    Định dạng: "NODE1A, -26.30, 30.10\n"
    """
    global _conn
    message = f"{NODE_NAME}, {x:.2f}, {y:.2f}\n"
    try:
        _conn.sendall(message.encode('utf-8'))
        print(f"[TX] ✓ {message.strip()}")
    except Exception as e:
        print(f"[ERROR] Gửi thất bại: {e} – thử kết nối lại...")
        try:
            _conn.close()
        except Exception:
            pass
        connect()
        try:
            _conn.sendall(message.encode('utf-8'))
            print(f"[TX] ✓ Gửi lại thành công: {message.strip()}")
        except Exception as e2:
            print(f"[ERROR] Gửi lại thất bại: {e2}")

# ==================== LOGIC BẮN ====================

def _random_in_bia():
    """Toạ độ ngẫu nhiên trong bia (r ≤ 75cm)."""
    while True:
        x = random.uniform(-BIA_HALF, BIA_HALF)
        y = random.uniform(-BIA_HALF, BIA_HALF)
        if math.sqrt(x**2 + y**2) <= 75.0:
            return round(x, 2), round(y, 2)

def _random_near_center(radius=30.0):
    """Toạ độ ngẫu nhiên gần tâm vòng."""
    cy = CENTER_OFFSET_Y.get(NODE_SUFFIX, 0.0)
    while True:
        x = random.uniform(-radius, radius)
        y = random.uniform(cy - radius, cy + radius)
        if math.sqrt(x**2 + (y - cy)**2) <= radius:
            return (max(-BIA_HALF, min(BIA_HALF, round(x, 2))),
                    max(-BIA_HALF, min(BIA_HALF, round(y, 2))))

def _miss():
    """Toạ độ bắn trượt (r > 75cm so với tâm vòng)."""
    cy = CENTER_OFFSET_Y.get(NODE_SUFFIX, 0.0)
    while True:
        x = random.uniform(-BIA_HALF, BIA_HALF)
        y = random.uniform(-BIA_HALF, BIA_HALF)
        if math.sqrt(x**2 + (y - cy)**2) > 75.0:
            return round(x, 2), round(y, 2)

def do_shot(x, y):
    """Thực hiện 1 viên bắn: kiểm tra điều kiện, gửi data, cập nhật đếm."""
    global _active, _shot_count

    with _lock:
        if not _active:
            print("[WARN] Node chưa được kích hoạt – chờ lệnh UP từ Controller")
            return
        if _shot_count >= 3:
            print("[WARN] Đã đủ 3 viên – chờ lệnh UP tiếp theo")
            return
        # Ghi nhận viên bắn trước khi release lock
        _shot_count += 1
        count = _shot_count
        if count >= 3:
            _active = False

    # Gửi data ra ngoài lock để không block recv thread
    send_data(x, y)
    print(f"[SHOT] Viên {count}/3 | x={x}, y={y}")
    if count >= 3:
        print("[SHOT] Đủ 3 viên – lượt kết thúc, chờ lệnh UP tiếp theo")

# ==================== GIAO DIỆN TERMINAL ====================

def print_status():
    cy    = CENTER_OFFSET_Y.get(NODE_SUFFIX, 0.0)
    state = "ACTIVE ●" if _active else "WAITING ○"
    print("\n" + "═"*52)
    print(f"  NODE: {NODE_NAME:<10}  Trạng thái: {state}")
    print(f"  Viên: {_shot_count}/3          Tâm vòng: (0, {cy:+.0f}cm)")
    print("═"*52)
    print("  [1] Bắn ngẫu nhiên trong bia (r≤75cm)")
    print("  [2] Bắn gần tâm vòng         (r≤30cm)")
    print("  [3] Nhập toạ độ tay           (x, y)  ")
    print("  [4] Bắn tự động 3 viên        (test)  ")
    print("  [5] Bắn trượt                 (miss)  ")
    print("  [q] Thoát")
    print("─"*52)

def main():
    global _running

    print("="*52)
    print(f"  NODE VIRTUAL – {NODE_NAME}")
    print(f"  Controller: {CTRL_HOST}:{CTRL_PORT}")
    print("="*52)

    # Bước 1: kết nối TCP đến controller
    connect()

    # Bước 2: thread nhận lệnh từ controller (cùng connection)
    t = threading.Thread(target=_recv_thread, daemon=True, name="RecvThread")
    t.start()

    print(f"[INIT] {NODE_NAME} sẵn sàng – chờ lệnh UP từ Controller GUI")

    try:
        while True:
            print_status()
            try:
                choice = input("  Chọn: ").strip().lower()
            except EOFError:
                break

            if choice == 'q':
                break
            elif choice == '1':
                do_shot(*_random_in_bia())
            elif choice == '2':
                do_shot(*_random_near_center(30.0))
            elif choice == '3':
                try:
                    raw = input("  Nhập x y (cm, cách nhau dấu cách): ").split()
                    x, y = float(raw[0]), float(raw[1])
                    do_shot(x, y)
                except Exception:
                    print("[ERROR] Nhập sai định dạng")
            elif choice == '4':
                if not _active:
                    print("[WARN] Chưa được kích hoạt")
                else:
                    for _ in range(3):
                        if not _active:
                            break
                        do_shot(*_random_near_center(25.0))
                        time.sleep(0.3)
            elif choice == '5':
                do_shot(*_miss())
            else:
                print("[WARN] Lựa chọn không hợp lệ")

            time.sleep(0.1)

    except KeyboardInterrupt:
        print("\n[EXIT] Dừng node-virtual")
    finally:
        _running = False
        if _conn:
            try:
                _conn.close()
            except Exception:
                pass
        print("[EXIT] Đã đóng kết nối")

if __name__ == "__main__":
    main()
