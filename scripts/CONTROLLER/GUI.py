#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GUI cho Controller - PyQt6
Hiển thị bảng điểm, log, các nút điều khiển.
Giao tiếp với backend qua hai hàng đợi (cmd_queue, out_queue).
"""

import sys
import threading
import queue
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget,
                             QVBoxLayout, QHBoxLayout, QGridLayout,
                             QPushButton, QTextEdit, QLabel, QSplitter)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject

class Communicate(QObject):
    """Lớp trung gian để gửi tín hiệu từ luồng đọc queue vào GUI thread"""
    update_log = pyqtSignal(str)
    update_board = pyqtSignal(str)

class ControllerGUI(QMainWindow):
    """
    Cửa sổ chính với:
    - Bảng điểm (QTextEdit) bên trái
    - Khung log (QTextEdit) bên phải phía trên
    - Hai hàng nút bấm bên phải phía dưới
    """
    def __init__(self, cmd_queue, out_queue):
        super().__init__()
        self.cmd_queue = cmd_queue    # Hàng đợi gửi lệnh đến backend
        self.out_queue = out_queue    # Hàng đợi nhận dữ liệu từ backend
        self.comm = Communicate()
        self.init_ui()

        # Bắt đầu luồng đọc dữ liệu từ out_queue (từ backend)
        self.reader_thread = threading.Thread(target=self._read_out_queue, daemon=True)
        self.reader_thread.start()

        # Kết nối tín hiệu với các slot cập nhật giao diện
        self.comm.update_log.connect(self.append_log)
        self.comm.update_board.connect(self.set_board_text)

    def init_ui(self):
        """Thiết lập giao diện"""
        self.setWindowTitle("HTTDTD Controller - Bắn súng điện tử")
        self.setGeometry(100, 100, 1200, 700)

        # Widget trung tâm
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)

        # ===== Bên trái: Bảng điểm =====
        self.board_text = QTextEdit()
        self.board_text.setReadOnly(True)
        self.board_text.setFontFamily("Monospace")
        self.board_text.setFontPointSize(10)

        # ===== Bên phải: gồm log (trên) và các nút (dưới) =====
        right_splitter = QSplitter(Qt.Orientation.Vertical)

        # Khung log
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFontFamily("Monospace")
        self.log_text.setFontPointSize(9)
        right_splitter.addWidget(self.log_text)

        # Khung chứa các nút bấm
        button_widget = QWidget()
        button_layout = QVBoxLayout(button_widget)

        # Hàng 1: NODE1 -> NODE5
        row1_layout = QHBoxLayout()
        for i in range(1, 6):
            btn = QPushButton(f"NODE{i}")
            btn.clicked.connect(lambda checked, n=i: self._on_node_clicked(n))
            row1_layout.addWidget(btn)
        button_layout.addLayout(row1_layout)

        # Hàng 2: A, B, C, D, EXTRA
        row2_layout = QHBoxLayout()
        for label in ["A", "B", "C", "D", "EXTRA"]:
            btn = QPushButton(label)
            btn.clicked.connect(lambda checked, lbl=label: self._on_group_clicked(lbl))
            row2_layout.addWidget(btn)
        button_layout.addLayout(row2_layout)

        # Hàng phụ: nút Reset Round (xoá dữ liệu vòng hiện tại)
        reset_btn = QPushButton("RESET ROUND (MISS PAD)")
        reset_btn.clicked.connect(self._on_reset_round)
        button_layout.addWidget(reset_btn)

        right_splitter.addWidget(button_widget)

        # Tỷ lệ chiều cao log : nút = 2:1
        right_splitter.setStretchFactor(0, 2)
        right_splitter.setStretchFactor(1, 1)

        # Ghép hai khu vực chính
        main_layout.addWidget(self.board_text, stretch=3)
        main_layout.addWidget(right_splitter, stretch=2)

        # Hiển thị thông báo khởi động
        self.append_log("[GUI] Controller GUI started. Waiting for backend...")

    def _read_out_queue(self):
        """Luồng đọc dữ liệu từ out_queue và phát tín hiệu cập nhật giao diện"""
        while True:
            try:
                # Chờ tối đa 0.1s để không block
                data = self.out_queue.get(timeout=0.1)
                if data[0] == 'log':
                    self.comm.update_log.emit(data[1])
                elif data[0] == 'board':
                    self.comm.update_board.emit(data[1])
            except queue.Empty:
                pass
            except Exception as e:
                print(f"Reader thread error: {e}")

    def append_log(self, text):
        """Thêm dòng log vào khung log (chạy trong GUI thread)"""
        self.log_text.append(text)
        # Tự động cuộn xuống cuối
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def set_board_text(self, text):
        """Cập nhật bảng điểm (chạy trong GUI thread)"""
        self.board_text.setPlainText(text)

    def _on_node_clicked(self, node_number):
        """Xử lý khi nhấn nút NODE1..NODE5: gửi lệnh UP/DOWN"""
        # Gửi lệnh dạng NODE1 UP nếu node chưa active? Ở backend cần quản lý trạng thái.
        # Giả định mỗi lần nhấn là toggle UP/DOWN. Để đơn giản, gửi UP rồi lần sau DOWN?
        # Thực tế backend sẽ xử lý, ta gửi lệnh với node name.
        node_name = f"NODE{node_number}"
        self.cmd_queue.put({'type': 'send', 'node': node_name, 'command': 'UP'})
        # Lưu ý: backend không tự động gửi DOWN, nên cần quản lý trạng thái ở đây hoặc backend.
        # Để demo, ta cho phép gửi UP mỗi lần bấm. Bạn có thể cải tiến thêm.

    def _on_group_clicked(self, label):
        """Xử lý nút A, B, C, D, EXTRA"""
        # Với A, B, EXTRA, gửi lệnh trực tiếp
        # C, D có thể gửi tương tự hoặc để dành mở rộng
        if label in ['A', 'B', 'C', 'D', 'EXTRA']:
            self.cmd_queue.put({'type': 'send', 'node': label, 'command': 'UP'})
        else:
            self.append_log(f"[WARN] Unknown group: {label}")

    def _on_reset_round(self):
        """Reset vòng bắn: pad miss và xoá shots"""
        self.cmd_queue.put({'type': 'reset_round'})
        self.append_log("[GUI] Reset round requested")

    def closeEvent(self, event):
        """Đóng cửa sổ: gửi lệnh thoát cho backend"""
        self.cmd_queue.put({'type': 'exit'})
        event.accept()
