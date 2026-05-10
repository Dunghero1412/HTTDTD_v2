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

# -*- coding: utf-8 -*-
"""
MAIN_virtual.py – Khởi động hệ thống virtual (không cần phần cứng).

Cách chạy:
    Terminal 1: python MAIN_virtual.py
    Terminal 2: python node-virtual.py
"""

import sys
import threading
from PyQt6.QtWidgets import QApplication, QMessageBox
from CONTROLLER_virtual import Controller
from GUI import MainWindow, SignalBridge


def main():
    controller = Controller()
    bridge     = SignalBridge()

    controller.set_score_callback(
        lambda text: bridge.score_updated.emit(text)
    )

    app = QApplication(sys.argv)
    app.setApplicationName("LoRa Controller – Virtual")

    try:
        controller.setup()
    except Exception as e:
        box = QMessageBox()
        box.setWindowTitle("Lỗi TCP server")
        box.setIcon(QMessageBox.Icon.Critical)
        box.setText(f"Không bind được port 9000:\n{e}\n\n"
                    f"Kiểm tra: sudo lsof -i :9000")
        box.exec()
        sys.exit(1)

    window = MainWindow(controller=controller, bridge=bridge)
    window.show()

    ctrl_thread = threading.Thread(target=controller.run,
                                   daemon=True, name="ControllerThread")
    ctrl_thread.start()

    controller._log("[MAIN] Virtual mode sẵn sàng")
    controller._log("[MAIN] Chạy 'python node-virtual.py' ở terminal khác")

    exit_code = app.exec()
    ctrl_thread.join(timeout=3.0)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
