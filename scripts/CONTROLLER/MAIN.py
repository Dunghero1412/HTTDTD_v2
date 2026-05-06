#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MAIN.py – File khởi chạy chính (SX1303 version)

📌 THAY ĐỔI SO VỚI BẢN SX1276:
    - Thêm khởi động lora_pkt_fwd (Semtech packet forwarder) như subprocess
      trước khi controller.setup() bind UDP socket
    - Thêm PKT_FWD_PATH, PKT_FWD_CONF để cấu hình đường dẫn
    - Thêm _start_packet_forwarder() và _stop_packet_forwarder()
    - GUI thread và Controller thread giữ nguyên hoàn toàn

🧵 KIẾN TRÚC THREAD/PROCESS:
    Process 1 (MAIN.py):
        Thread A (GUI)        : QApplication + event loop PyQt6
        Thread B (Controller) : vòng lặp nhận UDP LoRa
    Process 2 (subprocess):
        lora_pkt_fwd          : Semtech C packet forwarder
                                (SPI ↔ SX1303 ↔ UDP localhost:1700)

🔄 LUỒNG KHỞI ĐỘNG:
    1. Khởi động lora_pkt_fwd (subprocess, đợi 2s để ổn định)
    2. Tạo Controller + SignalBridge
    3. Đăng ký score_callback
    4. Tạo QApplication
    5. controller.setup() → bind UDP socket
    6. Tạo MainWindow
    7. Khởi động Controller thread
    8. QApplication.exec() → event loop

🔄 LUỒNG TẮT:
    MainWindow.closeEvent()
        → controller.stop()        (đóng UDP socket)
        → _stop_packet_forwarder() (terminate subprocess)
        → sys.exit()
"""

import sys
import threading
import subprocess
import time
import os

from PyQt6.QtWidgets import QApplication, QMessageBox
from CONTROLLER import Controller
from GUI        import MainWindow, SignalBridge

# ==================== CẤU HÌNH PACKET FORWARDER ====================

# ✓ Đường dẫn đến thư mục chứa lora_pkt_fwd và file config
# Thay đổi nếu bạn đặt sx1302_hal ở thư mục khác
PKT_FWD_DIR  = os.path.expanduser("~/sx1302_hal/packet_forwarder")

# ✓ Tên file binary packet forwarder (đã compile từ sx1302_hal)
PKT_FWD_BIN  = "./lora_pkt_fwd"

# ✓ File cấu hình gateway (tần số, SF, kênh, v.v.)
# Thay bằng file config phù hợp với tần số bạn dùng:
#   global_conf.json.sx1250.EU868 → châu Âu 868MHz
#   global_conf.json.sx1250.US915 → Mỹ 915MHz  ← dùng cái này cho 915MHz
PKT_FWD_CONF = "global_conf.json.sx1250.US915"

# ✓ Thời gian chờ packet forwarder khởi động trước khi bind UDP
PKT_FWD_STARTUP_DELAY = 2.0   # giây

# ✓ Timeout khi tắt packet forwarder (giây)
PKT_FWD_SHUTDOWN_TIMEOUT = 5.0

# ── Process handle (toàn cục để _stop có thể truy cập) ────────────────────
_pkt_fwd_process = None


def _start_packet_forwarder():
    """
    Khởi động lora_pkt_fwd như subprocess.

    Luồng:
        1. Kiểm tra file binary và config tồn tại
        2. Chạy subprocess với Popen (không block)
        3. Chờ PKT_FWD_STARTUP_DELAY để ổn định
        4. Kiểm tra subprocess vẫn đang chạy (poll())

    Trả về:
        bool: True = khởi động thành công, False = thất bại
    """
    global _pkt_fwd_process

    # Kiểm tra binary tồn tại
    bin_path  = os.path.join(PKT_FWD_DIR, PKT_FWD_BIN.lstrip("./"))
    conf_path = os.path.join(PKT_FWD_DIR, PKT_FWD_CONF)

    if not os.path.isfile(bin_path):
        print(f"[MAIN] [ERROR] Không tìm thấy: {bin_path}")
        print(f"[MAIN] Chạy lệnh sau để build:\n"
              f"  cd ~/sx1302_hal && make clean all")
        return False

    if not os.path.isfile(conf_path):
        print(f"[MAIN] [ERROR] Không tìm thấy config: {conf_path}")
        return False

    try:
        print(f"[MAIN] Khởi động packet forwarder: "
              f"{PKT_FWD_BIN} -c {PKT_FWD_CONF}")

        _pkt_fwd_process = subprocess.Popen(
            [PKT_FWD_BIN, "-c", PKT_FWD_CONF],
            cwd=PKT_FWD_DIR,
            # stdout/stderr: in ra terminal để debug
            # Đổi thành subprocess.DEVNULL nếu muốn ẩn output
            stdout=sys.stdout,
            stderr=sys.stderr,
        )

        # Chờ packet forwarder khởi động và bind SPI
        print(f"[MAIN] Chờ {PKT_FWD_STARTUP_DELAY}s để packet forwarder ổn định...")
        time.sleep(PKT_FWD_STARTUP_DELAY)

        # Kiểm tra process vẫn còn sống
        if _pkt_fwd_process.poll() is not None:
            # poll() trả về exit code nếu đã thoát → lỗi
            print(f"[MAIN] [ERROR] Packet forwarder thoát sớm "
                  f"(exit code={_pkt_fwd_process.returncode})")
            return False

        print(f"[MAIN] Packet forwarder đang chạy (PID={_pkt_fwd_process.pid})")
        return True

    except Exception as e:
        print(f"[MAIN] [ERROR] Khởi động packet forwarder: {e}")
        return False


def _stop_packet_forwarder():
    """
    Dừng subprocess packet forwarder khi thoát ứng dụng.

    Luồng:
        1. Gửi SIGTERM (terminate gracefully)
        2. Chờ tối đa PKT_FWD_SHUTDOWN_TIMEOUT giây
        3. Nếu vẫn sống → gửi SIGKILL (force kill)
    """
    global _pkt_fwd_process

    if _pkt_fwd_process is None:
        return

    if _pkt_fwd_process.poll() is not None:
        # Đã tự thoát rồi
        return

    try:
        print(f"[MAIN] Dừng packet forwarder (PID={_pkt_fwd_process.pid})...")
        _pkt_fwd_process.terminate()   # SIGTERM

        try:
            _pkt_fwd_process.wait(timeout=PKT_FWD_SHUTDOWN_TIMEOUT)
            print("[MAIN] Packet forwarder đã dừng (SIGTERM)")
        except subprocess.TimeoutExpired:
            # Không chịu thoát → force kill
            print("[MAIN] [WARN] Packet forwarder không thoát – gửi SIGKILL")
            _pkt_fwd_process.kill()
            _pkt_fwd_process.wait()
            print("[MAIN] Packet forwarder đã bị kill (SIGKILL)")

    except Exception as e:
        print(f"[MAIN] [ERROR] Dừng packet forwarder: {e}")


# ==================== HÀM CHÍNH ====================

def main():
    """
    Khởi động toàn bộ hệ thống theo thứ tự:
        1. lora_pkt_fwd subprocess
        2. Controller + SignalBridge
        3. QApplication
        4. UDP socket (controller.setup())
        5. MainWindow
        6. Controller thread
        7. Qt event loop
    """

    # ── 1. Khởi động packet forwarder ──────────────────────────────────────
    # Phải chạy TRƯỚC khi controller bind UDP socket
    pkt_fwd_ok = _start_packet_forwarder()

    if not pkt_fwd_ok:
        # Hỏi người dùng có muốn tiếp tục không (chạy không có gateway)
        print("[MAIN] [WARN] Packet forwarder không khởi động được.")
        print("[MAIN] Tiếp tục không có gateway? (Ctrl+C để thoát)")
        try:
            time.sleep(3)
        except KeyboardInterrupt:
            sys.exit(1)

    # ── 2. Tạo Controller + SignalBridge ───────────────────────────────────
    controller = Controller()
    bridge     = SignalBridge()

    # ── 3. Đăng ký score callback ──────────────────────────────────────────
    # Controller emit signal khi có điểm mới → GUI thread cập nhật an toàn
    controller.set_score_callback(
        lambda score_text: bridge.score_updated.emit(score_text)
    )

    # ── 4. Tạo QApplication ────────────────────────────────────────────────
    app = QApplication(sys.argv)
    app.setApplicationName("LoRa Controller – SX1303")
    app.setApplicationVersion("3.0")

    # ── 5. Khởi tạo UDP socket ─────────────────────────────────────────────
    try:
        controller.setup()
    except Exception as e:
        # UDP bind lỗi → hiển thị thông báo + dừng packet forwarder
        err_box = QMessageBox()
        err_box.setWindowTitle("Lỗi khởi tạo UDP socket")
        err_box.setIcon(QMessageBox.Icon.Critical)
        err_box.setText(
            f"Không thể bind UDP socket tại {controller.UDP_IP if hasattr(controller, 'UDP_IP') else '127.0.0.1'}:1700\n\n"
            f"Chi tiết lỗi:\n{e}\n\n"
            f"Kiểm tra:\n"
            f"  • Packet forwarder đang chạy chưa?\n"
            f"  • Port 1700 có bị dùng bởi process khác không?\n"
            f"    (lệnh: sudo lsof -i :1700)"
        )
        err_box.exec()
        _stop_packet_forwarder()
        sys.exit(1)

    # ── 6. Tạo cửa sổ chính ────────────────────────────────────────────────
    window = MainWindow(controller=controller, bridge=bridge)
    window.show()

    # ── 7. Khởi động Controller thread ─────────────────────────────────────
    ctrl_thread = threading.Thread(
        target=controller.run,
        name="ControllerThread",
        daemon=True,   # tự tắt khi main thread thoát
    )
    ctrl_thread.start()

    controller._log(f"[MAIN] Controller thread started (SX1303 UDP mode)")
    controller._log(f"[MAIN] Packet forwarder PID: "
                    f"{_pkt_fwd_process.pid if _pkt_fwd_process else 'N/A'}")
    controller._log("[MAIN] Sẵn sàng nhận dữ liệu từ các node")

    # ── 8. Qt event loop (blocking) ────────────────────────────────────────
    exit_code = app.exec()
    # Từ đây: người dùng đã đóng cửa sổ
    # MainWindow.closeEvent() đã gọi controller.stop()

    # Dừng packet forwarder
    _stop_packet_forwarder()

    # Chờ controller thread dọn xong (tối đa 3s)
    ctrl_thread.join(timeout=3.0)
    if ctrl_thread.is_alive():
        print("[MAIN] [WARN] Controller thread chưa thoát sau 3s")

    print(f"[MAIN] Ứng dụng thoát với mã: {exit_code}")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
