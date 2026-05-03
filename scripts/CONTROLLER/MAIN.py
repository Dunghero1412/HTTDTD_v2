#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MAIN.py – File khởi chạy chính của hệ thống

📌 VAI TRÒ TRONG HỆ THỐNG 3 FILE:
    MAIN.py       → khởi động, quản lý thread   ← FILE NÀY
    CONTROLLER.py → LoRa, tính điểm, JSON
    GUI.py        → PyQt6 giao diện người dùng

🧵 KIẾN TRÚC THREAD:
    Thread 1 (Main/GUI thread):
        - Chạy QApplication và event loop Qt
        - Xử lý tất cả sự kiện giao diện
        - KHÔNG được block (sleep, I/O chậm)

    Thread 2 (Controller thread):
        - Chạy Controller.run() liên tục
        - Giao tiếp LoRa (blocking I/O → tách ra đây)
        - Gửi log vào queue, emit signal khi có điểm mới

🔄 LUỒNG KHỞI ĐỘNG:
    main()
    ├── Tạo Controller
    ├── Tạo SignalBridge
    ├── Đăng ký score_callback (signal thread-safe)
    ├── controller.setup() → khởi tạo LoRa
    ├── Tạo QApplication + MainWindow(controller, bridge)
    ├── Khởi động Controller thread (daemon=True)
    └── QApplication.exec() → vào event loop Qt (blocking cho đến khi đóng)

🔄 LUỒNG TẮT:
    Người dùng đóng cửa sổ
    ├── MainWindow.closeEvent() → controller.stop()
    ├── Controller thread thoát vòng lặp run()
    └── sys.exit(app.exec())
"""

# ── Thư viện chuẩn ────────────────────────────────────────────────────────────
import sys                    # sys.exit(), sys.argv
import threading              # Thread cho Controller

# ── PyQt6 ─────────────────────────────────────────────────────────────────────
from PyQt6.QtWidgets import QApplication, QMessageBox

# ── Các module trong cùng thư mục ─────────────────────────────────────────────
from CONTROLLER import Controller          # lõi điều khiển
from GUI        import MainWindow, SignalBridge  # giao diện PyQt6


def main():
    """
    Hàm khởi động chính. Thứ tự thực hiện:

    1.  Tạo Controller (chưa kết nối LoRa)
    2.  Tạo SignalBridge (cầu nối Qt signal thread-safe)
    3.  Đăng ký score_callback: khi controller có điểm mới
        → emit bridge.score_updated signal → GUI cập nhật ô bảng điểm
    4.  Tạo QApplication (bắt buộc trước khi tạo bất kỳ QWidget nào)
    5.  Khởi tạo LoRa (controller.setup())
        → nếu lỗi: hiển thị QMessageBox và thoát
    6.  Tạo MainWindow, truyền vào controller và bridge
    7.  Khởi động Controller thread (daemon=True: tự tắt khi main thread tắt)
    8.  Hiển thị cửa sổ + chạy event loop Qt
    """

    # ── 1. Tạo Controller ──────────────────────────────────────────────────
    # Controller chưa kết nối LoRa, chỉ khởi tạo cấu trúc dữ liệu
    controller = Controller()

    # ── 2. Tạo SignalBridge ────────────────────────────────────────────────
    # Cầu nối để truyền data từ controller thread → GUI thread an toàn
    bridge = SignalBridge()

    # ── 3. Đăng ký score callback ──────────────────────────────────────────
    # Khi controller nhận điểm mới → gọi lambda này → emit signal Qt
    # Signal Qt được Qt event loop xử lý trong GUI thread (an toàn)
    controller.set_score_callback(
        lambda score_text: bridge.score_updated.emit(score_text)
    )

    # ── 4. Tạo QApplication ────────────────────────────────────────────────
    # Phải tạo trước bất kỳ QWidget nào, kể cả QMessageBox
    app = QApplication(sys.argv)
    app.setApplicationName("LoRa Controller")
    app.setApplicationVersion("2.0")

    # ── 5. Khởi tạo LoRa ──────────────────────────────────────────────────
    try:
        controller.setup()
    except Exception as e:
        # LoRa lỗi → hiển thị thông báo rồi thoát
        # QMessageBox có thể dùng vì QApplication đã tạo ở bước 4
        err_box = QMessageBox()
        err_box.setWindowTitle("Lỗi khởi tạo LoRa")
        err_box.setIcon(QMessageBox.Icon.Critical)
        err_box.setText(
            f"Không thể kết nối LoRa module.\n\n"
            f"Chi tiết lỗi:\n{e}\n\n"
            f"Kiểm tra:\n"
            f"  • Cổng UART: /dev/ttyAMA1\n"
            f"  • Module SX1278 đã cắm chưa?\n"
            f"  • Cấp nguồn LoRa đúng chưa?"
        )
        err_box.exec()
        sys.exit(1)   # thoát với mã lỗi 1

    # ── 6. Tạo cửa sổ chính ───────────────────────────────────────────────
    # Truyền cả controller và bridge để GUI kết nối signal + gọi hàm
    window = MainWindow(controller=controller, bridge=bridge)
    window.show()   # hiển thị cửa sổ

    # ── 7. Khởi động Controller thread ────────────────────────────────────
    # daemon=True: khi GUI thread (main thread) thoát → thread này tự tắt
    # Không cần join() khi đóng app
    ctrl_thread = threading.Thread(
        target=controller.run,   # hàm vòng lặp nhận LoRa
        name="ControllerThread",
        daemon=True,             # tự tắt khi process thoát
    )
    ctrl_thread.start()

    # Log thông tin khởi động vào queue (GUI sẽ hiển thị sau khi poll)
    controller._log(f"[MAIN] Controller thread đã start (daemon={ctrl_thread.daemon})")
    controller._log("[MAIN] Giao diện đã sẵn sàng – Chờ lệnh từ người dùng")

    # ── 8. Chạy event loop Qt ─────────────────────────────────────────────
    # app.exec() blocking cho đến khi người dùng đóng cửa sổ
    # Khi đóng: MainWindow.closeEvent() → controller.stop() → thread thoát
    exit_code = app.exec()

    # Chờ controller thread dọn xong (tối đa 3 giây)
    ctrl_thread.join(timeout=3.0)
    if ctrl_thread.is_alive():
        # Thread chưa thoát sau 3s → log cảnh báo (không crash)
        print("[MAIN] [WARN] Controller thread chưa thoát sau 3s – buộc thoát")

    print(f"[MAIN] Ứng dụng thoát với mã: {exit_code}")
    sys.exit(exit_code)


# ==================== ĐIỂM VÀO CHƯƠNG TRÌNH ====================

if __name__ == "__main__":
    """
    Chạy trực tiếp: python MAIN.py
    Import từ file khác sẽ không tự động gọi main().
    """
    main()
