#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GUI.py – Giao diện PyQt6 cho hệ thống điều khiển bắn đạn thật

📌 VAI TRÒ TRONG HỆ THỐNG 3 FILE:
    MAIN.py       → khởi động, quản lý thread
    CONTROLLER.py → LoRa, tính điểm, JSON
    GUI.py        → PyQt6 giao diện người dùng   ← FILE NÀY

🖼️  BỐ CỤC GIAO DIỆN (3 vùng lớn):
    ┌──────────────────────┬─────────────────────┐
    │                      │  [LOG / DEBUG]      │
    │   [BẢNG ĐIỂM]        │  (ô phải trên)      │
    │   (ô trái – lớn nhất)│─────────────────────│
    │                      │  [NÚT NODE 1–5]     │
    │                      │  [NÚT A B C D EX]   │
    └──────────────────────┴─────────────────────┘

🔄 GIAO TIẾP VỚI CONTROLLER:
    - Nhận object Controller từ MAIN.py qua __init__
    - Poll log_queue mỗi 200ms (QTimer) → hiển thị lên ô log
    - Nhận bảng điểm qua score_callback đã đăng ký trong MAIN.py
    - Gọi controller.handle_button(name) khi nút được bấm
"""

# ── PyQt6 ─────────────────────────────────────────────────────────────────────
from PyQt6.QtWidgets import (
    QMainWindow,       # cửa sổ chính của ứng dụng
    QWidget,           # widget cơ bản (container)
    QHBoxLayout,       # layout ngang
    QVBoxLayout,       # layout dọc
    QGridLayout,       # layout lưới (dùng cho hàng nút)
    QTextEdit,         # ô văn bản nhiều dòng (bảng điểm + log)
    QPushButton,       # nút bấm
    QLabel,            # nhãn văn bản
    QSplitter,         # thanh chia kéo được giữa 2 vùng
    QFrame,            # khung viền
)
from PyQt6.QtCore import (
    Qt,                # hằng số Qt (alignment, v.v.)
    QTimer,            # bộ đếm thời gian cho polling
    pyqtSignal,        # tín hiệu Qt (signal)
    QObject,           # lớp cơ sở để dùng signal
)
from PyQt6.QtGui import (
    QFont,             # font chữ
    QColor,            # màu sắc
    QPalette,          # bảng màu widget
    QTextCursor,       # điều khiển con trỏ trong QTextEdit
)

# ── Thư viện chuẩn ────────────────────────────────────────────────────────────
import queue           # để đọc log_queue từ Controller

# ==================== BRIDGE SIGNAL (thread-safe update GUI) ====================

class SignalBridge(QObject):
    """
    Cầu nối thread-safe giữa Controller thread và GUI thread.

    PyQt6 yêu cầu tất cả cập nhật UI phải xảy ra trong GUI thread.
    Controller chạy trong thread riêng → không được gọi Qt widgets trực tiếp.
    Giải pháp: Controller emit signal → Qt event loop nhận → GUI thread cập nhật.

    Signals:
        score_updated (str) : phát khi có dữ liệu bảng điểm mới
        log_received  (str) : phát khi có dòng log mới (dự phòng, chủ yếu dùng QTimer poll)
    """
    score_updated = pyqtSignal(str)   # tham số: string bảng điểm đã render
    log_received  = pyqtSignal(str)   # tham số: dòng log đơn lẻ


# ==================== CỬA SỔ CHÍNH ====================

class MainWindow(QMainWindow):
    """
    Cửa sổ chính của ứng dụng điều khiển.

    Nhận vào:
        controller (Controller): object từ MAIN.py
        bridge     (SignalBridge): cầu nối signal từ MAIN.py

    Chức năng:
        - Hiển thị bảng điểm (ô trái)
        - Hiển thị log/debug (ô phải trên)
        - 2 hàng nút bấm ảo (ô phải dưới)
        - Poll log_queue mỗi 200ms
    """

    def __init__(self, controller, bridge):
        """
        Tham số:
            controller (Controller): object điều khiển chính
            bridge     (SignalBridge): signal bridge từ MAIN.py
        """
        super().__init__()

        # Lưu tham chiếu đến controller và bridge
        self.controller = controller
        self.bridge     = bridge

        # ── Kết nối signal bảng điểm → slot cập nhật UI ───────────────────
        # Khi Controller gọi score_callback → emit signal → _on_score_updated()
        # được gọi trong GUI thread (an toàn)
        self.bridge.score_updated.connect(self._on_score_updated)

        # ── Cấu hình cửa sổ ───────────────────────────────────────────────
        self.setWindowTitle("Hệ thống điều khiển bắn đạn thật – LoRa Controller")
        self.setMinimumSize(1280, 720)    # kích thước tối thiểu
        self.resize(1400, 800)            # kích thước mặc định

        # ── Style sheet toàn cục ──────────────────────────────────────────
        self._apply_stylesheet()

        # ── Xây dựng giao diện ────────────────────────────────────────────
        self._build_ui()

        # ── QTimer poll log_queue (200ms) ─────────────────────────────────
        # Không dùng signal cho log vì log rất nhiều → dùng timer gom lại
        self._log_timer = QTimer(self)
        self._log_timer.timeout.connect(self._poll_log_queue)
        self._log_timer.start(200)   # 200ms = 5 lần/giây, đủ realtime

        # Hiển thị bảng điểm rỗng ban đầu
        self._on_score_updated(self.controller.get_score_table())

    # ==================== STYLE ====================

    def _apply_stylesheet(self):
        """
        Áp dụng stylesheet tối (dark theme) cho toàn bộ ứng dụng.
        Màu sắc lấy cảm hứng từ màn hình quân sự: nền tối, chữ xanh lá.
        """
        self.setStyleSheet("""
            /* ── Nền ứng dụng ──────────────────────────── */
            QMainWindow, QWidget {
                background-color: #0d1117;
                color: #c9d1d9;
                font-family: 'Courier New', 'Liberation Mono', monospace;
            }

            /* ── Ô văn bản (bảng điểm + log) ───────────── */
            QTextEdit {
                background-color: #0d1117;
                color: #39ff14;
                border: 1px solid #30363d;
                border-radius: 4px;
                font-family: 'Courier New', monospace;
                font-size: 13px;
                padding: 6px;
                selection-background-color: #1f6feb;
            }

            /* ── Nhãn tiêu đề các ô ────────────────────── */
            QLabel#section_title {
                color: #58a6ff;
                font-size: 11px;
                font-weight: bold;
                letter-spacing: 2px;
                padding: 4px 6px;
                background-color: #161b22;
                border-bottom: 1px solid #30363d;
            }

            /* ── Nút NODE 1–5 (hàng trên) ──────────────── */
            QPushButton#node_btn {
                background-color: #21262d;
                color: #c9d1d9;
                border: 1px solid #30363d;
                border-radius: 6px;
                font-size: 14px;
                font-weight: bold;
                min-height: 48px;
                min-width: 90px;
            }
            QPushButton#node_btn:hover {
                background-color: #30363d;
                border-color: #58a6ff;
                color: #58a6ff;
            }
            QPushButton#node_btn[active="true"] {
                background-color: #1f6feb;
                border-color: #58a6ff;
                color: #ffffff;
            }

            /* ── Nút nhóm A–D (hàng dưới) ──────────────── */
            QPushButton#group_btn {
                background-color: #21262d;
                color: #c9d1d9;
                border: 1px solid #30363d;
                border-radius: 6px;
                font-size: 14px;
                font-weight: bold;
                min-height: 48px;
                min-width: 90px;
            }
            QPushButton#group_btn:hover {
                background-color: #30363d;
                border-color: #3fb950;
                color: #3fb950;
            }
            QPushButton#group_btn[active="true"] {
                background-color: #238636;
                border-color: #3fb950;
                color: #ffffff;
            }

            /* ── Nút EXTRA (EX) ─────────────────────────── */
            QPushButton#extra_btn {
                background-color: #21262d;
                color: #c9d1d9;
                border: 1px solid #30363d;
                border-radius: 6px;
                font-size: 14px;
                font-weight: bold;
                min-height: 48px;
                min-width: 90px;
            }
            QPushButton#extra_btn:hover {
                background-color: #30363d;
                border-color: #f85149;
                color: #f85149;
            }
            QPushButton#extra_btn[active="true"] {
                background-color: #b91c1c;
                border-color: #f85149;
                color: #ffffff;
            }

            /* ── Splitter ───────────────────────────────── */
            QSplitter::handle {
                background-color: #30363d;
                width: 3px;
            }
            QSplitter::handle:hover {
                background-color: #58a6ff;
            }

            /* ── Khung viền section ─────────────────────── */
            QFrame#section_frame {
                border: 1px solid #30363d;
                border-radius: 6px;
                background-color: #161b22;
            }
        """)

    # ==================== XÂY DỰNG UI ====================

    def _build_ui(self):
        """
        Xây dựng layout chính của cửa sổ.

        Cấu trúc:
            QSplitter (ngang)
            ├── LEFT  : ô bảng điểm (chiếm ~55% width)
            └── RIGHT : QVBoxLayout
                        ├── ô log/debug (~55% height của right)
                        └── ô nút bấm  (~45% height của right)
        """

        # Widget trung tâm
        central = QWidget()
        self.setCentralWidget(central)

        # Layout gốc có padding 8px
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(0)

        # ── Splitter ngang chia trái/phải ─────────────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(4)
        root_layout.addWidget(splitter)

        # ── VÙng trái: BẢNG ĐIỂM ──────────────────────────────────────────
        left_frame  = self._build_score_panel()
        splitter.addWidget(left_frame)

        # ── Vùng phải: LOG + NÚT BẤM ──────────────────────────────────────
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(4, 0, 0, 0)
        right_layout.setSpacing(6)

        # Ô log
        log_frame = self._build_log_panel()
        right_layout.addWidget(log_frame, stretch=55)   # 55% chiều cao

        # Ô nút bấm
        btn_frame = self._build_button_panel()
        right_layout.addWidget(btn_frame, stretch=45)   # 45% chiều cao

        splitter.addWidget(right_widget)

        # Tỷ lệ trái:phải = 55:45
        splitter.setSizes([770, 630])

    # ── Ô bảng điểm (trái) ────────────────────────────────────────────────
    def _build_score_panel(self):
        """
        Tạo panel bên trái: tiêu đề + QTextEdit chỉ đọc cho bảng điểm.
        Font monospace để các cột căn thẳng hàng.
        """
        frame = QFrame()
        frame.setObjectName("section_frame")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Tiêu đề ô
        title = QLabel("◉  BẢNG ĐIỂM")
        title.setObjectName("section_title")
        layout.addWidget(title)

        # QTextEdit hiển thị bảng điểm – read-only
        self.score_display = QTextEdit()
        self.score_display.setReadOnly(True)    # chỉ đọc, không chỉnh sửa
        self.score_display.setObjectName("score_display")
        # Font monospace lớn hơn cho dễ đọc
        self.score_display.setFont(QFont("Courier New", 13))

        layout.addWidget(self.score_display)
        return frame

    # ── Ô log/debug (phải trên) ───────────────────────────────────────────
    def _build_log_panel(self):
        """
        Tạo panel log bên phải phía trên: tiêu đề + QTextEdit append-only.
        Tự động scroll xuống cuối khi có dòng log mới.
        """
        frame = QFrame()
        frame.setObjectName("section_frame")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Tiêu đề ô
        title = QLabel("◉  LOG / DEBUG / THÔNG TIN NODE")
        title.setObjectName("section_title")
        layout.addWidget(title)

        # QTextEdit hiển thị log – read-only, append từ dưới lên
        self.log_display = QTextEdit()
        self.log_display.setReadOnly(True)
        self.log_display.setObjectName("log_display")
        self.log_display.setFont(QFont("Courier New", 11))

        layout.addWidget(self.log_display)
        return frame

    # ── Ô nút bấm (phải dưới) ─────────────────────────────────────────────
    def _build_button_panel(self):
        """
        Tạo panel nút bấm với 2 hàng:
            Hàng 1: NODE 1 | NODE 2 | NODE 3 | NODE 4 | NODE 5
            Hàng 2: A      | B      | C      | D      | EX

        Mỗi nút là QPushButton có toggle state, màu thay đổi khi bấm.
        """
        frame = QFrame()
        frame.setObjectName("section_frame")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(8, 4, 8, 8)
        layout.setSpacing(6)

        # Tiêu đề ô
        title = QLabel("◉  ĐIỀU KHIỂN")
        title.setObjectName("section_title")
        layout.addWidget(title)

        # Grid layout cho 2 hàng nút
        grid = QGridLayout()
        grid.setSpacing(8)        # khoảng cách giữa các nút
        layout.addLayout(grid)
        layout.addStretch()       # đẩy nút lên trên, không bị giãn xuống

        # ── Hàng 1: NODE 1 → 5 ────────────────────────────────────────────
        # Dict lưu tham chiếu đến các QPushButton để toggle màu sau
        self.node_buttons = {}

        node_defs = [
            ("NODE 1", "NODE1"),
            ("NODE 2", "NODE2"),
            ("NODE 3", "NODE3"),
            ("NODE 4", "NODE4"),
            ("NODE 5", "NODE5"),
        ]

        for col, (label, key) in enumerate(node_defs):
            btn = QPushButton(label)
            btn.setObjectName("node_btn")
            btn.setCheckable(False)   # toggle được quản lý thủ công qua controller
            # Lambda capture 'key' đúng (tránh closure bug trong vòng lặp)
            btn.clicked.connect(lambda checked, k=key: self._on_button_clicked(k))
            grid.addWidget(btn, 0, col)   # hàng 0, cột col
            self.node_buttons[key] = btn

        # ── Hàng 2: A, B, C, D, EX ───────────────────────────────────────
        self.group_buttons = {}

        group_defs = [
            ("A",    "A",     "group_btn"),
            ("B",    "B",     "group_btn"),
            ("C",    "C",     "group_btn"),
            ("D",    "D",     "group_btn"),
            ("EX",   "EXTRA", "extra_btn"),   # nút EXTRA màu đỏ khi active
        ]

        for col, (label, key, obj_name) in enumerate(group_defs):
            btn = QPushButton(label)
            btn.setObjectName(obj_name)
            btn.clicked.connect(lambda checked, k=key: self._on_button_clicked(k))
            grid.addWidget(btn, 1, col)   # hàng 1, cột col
            self.group_buttons[key] = btn

        # Giãn đều các cột
        for col in range(5):
            grid.setColumnStretch(col, 1)

        return frame

    # ==================== XỬ LÝ SỰ KIỆN ====================

    def _on_button_clicked(self, btn_name):
        """
        Xử lý khi người dùng bấm một nút trên GUI.

        Luồng:
            1. Gọi controller.handle_button(btn_name) → gửi lệnh LoRa
            2. Đọc lại button_states từ controller
            3. Cập nhật visual (màu nút) theo trạng thái mới
            4. Nếu EXTRA mode bật → tô đỏ toàn bộ nút còn lại (locked)

        Tham số:
            btn_name (str): "NODE1"…"NODE5", "A","B","C","D","EXTRA"
        """
        # Gọi controller xử lý logic (gửi LoRa, cập nhật state)
        self.controller.handle_button(btn_name)

        # Refresh màu tất cả nút theo trạng thái mới từ controller
        self._refresh_button_styles()

    def _refresh_button_styles(self):
        """
        Cập nhật màu sắc tất cả nút bấm theo trạng thái hiện tại
        trong controller.button_states và controller.extra_mode_active.

        Cơ chế:
            - Nút đang UP (active=True)  → nền xanh (node) hoặc xanh lá (group)
            - Nút đang DOWN (active=False) → nền tối mặc định
            - EXTRA đang bật → tất cả nút bị khóa hiển thị màu mờ
            - Nút EXTRA đang bật → nền đỏ
        """
        states       = self.controller.button_states
        extra_active = self.controller.extra_mode_active

        # ── Cập nhật nút NODE 1–5 ────────────────────────────────────────
        for key, btn in self.node_buttons.items():
            is_active = states.get(key, False)
            is_locked = extra_active  # bị khoá khi EXTRA mode bật

            # Dùng Qt property để stylesheet selector [active="true"] hoạt động
            btn.setProperty("active", "true" if is_active else "false")

            # Khi bị khoá → giảm opacity bằng cách override style
            if is_locked and not is_active:
                btn.setStyleSheet("opacity: 0.4;")
            else:
                btn.setStyleSheet("")   # dùng lại stylesheet gốc

            # Bắt buộc Qt repaint lại widget sau khi đổi property
            btn.style().unpolish(btn)
            btn.style().polish(btn)

        # ── Cập nhật nút nhóm A–D và EX ──────────────────────────────────
        for key, btn in self.group_buttons.items():
            is_active = states.get(key, False)
            is_locked = extra_active and (key != "EXTRA")

            btn.setProperty("active", "true" if is_active else "false")

            if is_locked:
                btn.setStyleSheet("opacity: 0.4;")
            else:
                btn.setStyleSheet("")

            btn.style().unpolish(btn)
            btn.style().polish(btn)

    # ── Nhận bảng điểm mới từ signal ──────────────────────────────────────
    def _on_score_updated(self, score_text):
        """
        Slot nhận signal score_updated từ SignalBridge.
        Cập nhật nội dung ô bảng điểm bên trái.
        Chạy trong GUI thread → an toàn.

        Tham số:
            score_text (str): bảng điểm đã render từ get_score_table()
        """
        self.score_display.setPlainText(score_text)
        # Di chuyển con trỏ về đầu để hiển thị từ đầu bảng
        cursor = self.score_display.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        self.score_display.setTextCursor(cursor)

    # ── Poll log queue ─────────────────────────────────────────────────────
    def _poll_log_queue(self):
        """
        Gọi bởi QTimer mỗi 200ms.
        Đọc tất cả message trong log_queue của controller và append vào ô log.
        Giới hạn 50 dòng/lần poll để tránh lag UI.

        Cơ chế scroll: luôn cuộn xuống cuối sau khi append.
        """
        max_per_poll = 50   # tránh UI lag khi log burst nhiều
        count = 0

        while count < max_per_poll:
            try:
                # Lấy 1 message từ queue (không blocking)
                msg = self.controller.log_queue.get_nowait()
                # Append vào cuối ô log
                self.log_display.append(msg)
                count += 1
            except queue.Empty:
                break   # hết message trong queue

        # Cuộn xuống cuối nếu có message mới
        if count > 0:
            scrollbar = self.log_display.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    # ── Xử lý đóng cửa sổ ────────────────────────────────────────────────
    def closeEvent(self, event):
        """
        Override sự kiện đóng cửa sổ (X button hoặc Alt+F4).
        Dừng QTimer trước khi đóng để tránh callback sau khi widget bị destroy.
        Gọi controller.stop() để dừng LoRa an toàn.

        Tham số:
            event (QCloseEvent): sự kiện đóng cửa sổ từ Qt
        """
        # Dừng timer poll log để tránh truy cập widget đã bị destroy
        self._log_timer.stop()

        # Dừng vòng lặp controller và đóng LoRa
        self.controller.stop()

        # Chấp nhận sự kiện đóng → cửa sổ thực sự đóng
        event.accept()
