#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MAIN - Khởi chạy Controller backend (thread) và GUI (PyQt6).
Sử dụng hàng đợi để giao tiếp giữa hai thread.
"""

import sys
import threading
import queue
from PyQt6.QtWidgets import QApplication
from CONTROLLER import Controller   # file CONTROLLER.py
from GUI import ControllerGUI

def main():
    # Tạo hai hàng đợi
    cmd_queue = queue.Queue()    # GUI -> Controller (lệnh điều khiển)
    out_queue = queue.Queue()    # Controller -> GUI (log, bảng điểm)

    # Tạo đối tượng Controller (back-end)
    controller = Controller(cmd_queue, out_queue)

    # Khởi chạy controller trong một thread riêng
    ctrl_thread = threading.Thread(target=controller.run, daemon=True)
    ctrl_thread.start()

    # Khởi chạy GUI (chạy trong main thread của PyQt)
    app = QApplication(sys.argv)
    gui = ControllerGUI(cmd_queue, out_queue)
    gui.show()

    # Chạy vòng lặp sự kiện của Qt
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
