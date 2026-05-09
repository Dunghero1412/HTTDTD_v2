#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MAIN_virtual.py – Khởi động Controller + GUI (không cần phần cứng)

THAY ĐỔI SO VỚI MAIN.py (SX1303):
    - Import CONTROLLER_virtual thay vì CONTROLLER
    - Bỏ _start_packet_forwarder() / _stop_packet_forwarder()
    - Bỏ QMessageBox lỗi LoRa → thay bằng lỗi TCP bind
    - Tất cả phần GUI, thread, signal giữ nguyên 100%

CÁCH CHẠY:
    Terminal 1: python MAIN_virtual.py
    Terminal 2: python node-virtual.py              (NODE1A)
    Terminal 3: python node-virtual.py              (NODE2B sau khi đổi config)
"""

import sys
import threading
from PyQt6.QtWidgets import QApplication, QMessageBox
from CONTROLLER_virtual import Controller
from GUI import MainWindow, SignalBridge


def main():
    # Tạo Controller virtual
    controller = Controller()
    bridge     = SignalBridge()

    # Đăng ký score callback → signal Qt thread-safe
    controller.set_score_callback(
        lambda score_text: bridge.score_updated.emit(score_text)
    )

    # QApplication phải tạo trước mọi QWidget
    app = QApplication(sys.argv)
    app.setApplicationName("LoRa Controller – Virtual Mode")
    app.setApplicationVersion("3.0-virtual")

    # Khởi tạo TCP server
    try:
        controller.setup()
    except Exception as e:
        err_box = QMessageBox()
        err_box.setWindowTitle("Lỗi khởi tạo TCP server")
        err_box.setIcon(QMessageBox.Icon.Critical)
        err_box.setText(
            f"Không thể bind TCP server tại port 9000.\n\n"
            f"Chi tiết: {e}\n\n"
            f"Kiểm tra port 9000 có đang bị dùng không:\n"
            f"  sudo lsof -i :9000"
        )
        err_box.exec()
        sys.exit(1)

    # Tạo cửa sổ chính
    window = MainWindow(controller=controller, bridge=bridge)
    window.show()

    # Khởi động Controller thread
    ctrl_thread = threading.Thread(
        target=controller.run,
        name="ControllerThread",
        daemon=True,
    )
    ctrl_thread.start()

    controller._log("[MAIN] Virtual mode – không cần phần cứng LoRa")
    controller._log(f"[MAIN] Chờ node-virtual kết nối tại TCP port 9000")
    controller._log("[MAIN] Chạy: python node-virtual.py ở terminal khác")

    # Qt event loop
    exit_code = app.exec()

    # Dọn dẹp
    ctrl_thread.join(timeout=3.0)
    print(f"[MAIN] Thoát với mã: {exit_code}")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
