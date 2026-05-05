#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NODE.py – RPi Zero 2W Node (SX1276, SF riêng theo hàng)

📌 THAY ĐỔI SO VỚI PHIÊN BẢN CŨ:
    - Thêm cấu hình NODE_ROW (1–5) để xác định SF tương ứng
    - Thêm NODE_SF_MAP: NODE 1→SF6, NODE 2→SF7, ..., NODE 5→SF10
    - setup(): gọi lora.set_spreading_factor(SF) sau khi set frequency
    - Tất cả phần còn lại (SPI, GPIO, TDOA, triangulation) giữ nguyên 100%

📡 SƠ ĐỒ SF:
    NODE 1 (hàng 1) → SF6   (tốc độ cao nhất, tầm ngắn nhất)
    NODE 2 (hàng 2) → SF7
    NODE 3 (hàng 3) → SF8
    NODE 4 (hàng 4) → SF9
    NODE 5 (hàng 5) → SF10

💡 TẠI SAO DÙNG SF KHÁC NHAU?
    SX1303 gateway có 8 kênh demodulator đồng thời, mỗi kênh
    lắng nghe trên một SF khác nhau. Khi NODE1 gửi SF6 và NODE2
    gửi SF7 cùng lúc → SX1303 nhận cả hai mà không collision.

📍 PIN ASSIGNMENT (RPi Zero 2W - BCM mode):
┌─────────────────────────────────────────────┐
│ GPIO17 (BCM) → DATA_READY input (STM32 PB0) │
│ GPIO20 (BCM) → CONTROL output (motor relay) │
│ GPIO10 → MISO (SPI0) - dữ liệu từ STM32     │
│ GPIO9  → MOSI (SPI0) - không dùng           │
│ GPIO11 → SCLK (SPI0) - clock                │
│ GPIO8  → CE0 (CS) - chip select             │
└─────────────────────────────────────────────┘
"""

# ==================== NHẬP THƯ VIỆN ====================

import RPi.GPIO as GPIO          # điều khiển GPIO (DATA_READY, CONTROL)
import time                      # delay, timeout, timestamp
import sys                       # sys.exit()
import math                      # sqrt(), trig
import spidev                    # giao tiếp SPI với STM32
from rpi_lora import LoRa        # LoRa SX1276 transceiver
from rpi_lora.board_config import BOARD  # pin mapping bo mạch
from datetime import datetime    # timestamp log
import numpy as np               # tính toán ma trận (hyperbolic)
from scipy.optimize import least_squares  # tinh chỉnh tọa độ

# BME280 – đọc nhiệt độ để tính tốc độ âm thanh động
import board
import busio
import adafruit_bme280.advanced as adafruit_bme280
import threading                 # thread cập nhật BME280 định kỳ

# ==================== CẤU HÌNH NODE ====================

# ✓ SỐ HÀNG NODE: chỉnh thành 1, 2, 3, 4 hoặc 5 tuỳ từng RPi
# Đây là biến DUY NHẤT phải thay đổi khi deploy sang RPi khác
# NODE_ROW = 1 → NODE 1 → dùng SF6
# NODE_ROW = 2 → NODE 2 → dùng SF7
# ... v.v.
NODE_ROW = 1   # ← THAY ĐỔI GIÁ TRỊ NÀY CHO TỪNG NODE (1–5)

# ✓ Hậu tố nhóm bia: "A", "B", "C" hoặc "D"
# Mỗi RPi trong một hàng thuộc về nhóm bia khác nhau
# NODE_SUFFIX = "A" → dãy A (đợt bắn 1)
NODE_SUFFIX = "A"  # ← THAY ĐỔI GIÁ TRỊ NÀY CHO TỪNG NODE (A/B/C/D)

# ✓ Tên Node đầy đủ: tự động tạo từ NODE_ROW + NODE_SUFFIX
# Ví dụ: NODE_ROW=1, NODE_SUFFIX="A" → "NODE1A"
NODE_NAME = f"NODE{NODE_ROW}{NODE_SUFFIX}"

# ==================== CẤU HÌNH SF ====================

# ✓ Bảng mapping hàng → Spreading Factor
# SX1276 hỗ trợ SF6–SF12. SF thấp = nhanh hơn, tầm ngắn hơn.
# SX1303 gateway lắng nghe đồng thời nhiều SF → không collision.
#
# SF6  → Time on Air ~72ms  @ BW125 SF6  DR5 (nhanh nhất)
# SF7  → Time on Air ~128ms @ BW125 SF7  DR4
# SF8  → Time on Air ~230ms @ BW125 SF8  DR3
# SF9  → Time on Air ~410ms @ BW125 SF9  DR2
# SF10 → Time on Air ~740ms @ BW125 SF10 DR1
NODE_SF_MAP = {
    1: 6,    # NODE 1 hàng 1 → SF6
    2: 7,    # NODE 2 hàng 2 → SF7
    3: 8,    # NODE 3 hàng 3 → SF8
    4: 9,    # NODE 4 hàng 4 → SF9
    5: 10,   # NODE 5 hàng 5 → SF10
}

# ✓ SF thực tế của node này (tra từ bảng trên)
NODE_SF = NODE_SF_MAP[NODE_ROW]

# ✓ Bandwidth (kHz) – cố định 125kHz cho tất cả node
# Phải khớp với cấu hình SX1303 gateway
LORA_BW = 125

# ✓ Coding Rate – cố định 4/5
LORA_CR = 5   # 4/5 = cr=5 trong rpi_lora

# ==================== CẤU HÌNH CHUNG ====================

# GPIO pins
DATA_READY_PIN = 17    # input: STM32 PB0 báo có dữ liệu SPI
CONTROL_PIN    = 20    # output: điều khiển motor/relay

# LoRa
LORA_FREQ = 915        # MHz – phải khớp với SX1303 gateway

# SPI cho STM32
SPI_BUS    = 0
SPI_DEVICE = 0
SPI_SPEED  = 10500000  # 10.5 MHz (STM32 SPI2 @ 42MHz / 4)

# Tọa độ 4 cảm biến piezo trên bia (cm), tâm bia = (0,0)
SENSOR_POSITIONS = {
    'A': (-50, -50),   # góc trái dưới
    'B': (-50,  50),   # góc trái trên
    'C': ( 50,  50),   # góc phải trên
    'D': ( 50, -50),   # góc phải dưới
}

# Timing
CONTROL_TIMEOUT          = 60     # giây – timeout sau lệnh UP
SENSOR_DETECTION_WINDOW  = 0.05   # giây (legacy, giữ cho reference)

# STM32 TIM2 clock
STM32_CLK_FREQ  = 84e6            # 84 MHz (APB1 × 2, PSC=0)
TICK_TO_SECONDS = 1.0 / STM32_CLK_FREQ  # ~11.905 ns/tick

# BME280
BME280_I2C_ADDR      = 0x76
BME280_UPDATE_INTERVAL = 60       # giây

# Hybrid triangulation
WEIGHTED_AVG_ITERATIONS    = 10
WEIGHTED_AVG_LEARNING_RATE = 0.15
ENABLE_HYPERBOLIC          = True
HYPERBOLIC_MAX_ITERATIONS  = 100
HYPERBOLIC_TOLERANCE       = 1e-6

# Log
LOG_FILE = "/opt/score.txt"

# ==================== TỐC ĐỘ ÂM THANH ====================

def calc_sound_speed(temp_celsius: float) -> float:
    """Tính vận tốc âm thanh (m/s) theo nhiệt độ Celsius."""
    return 331.3 * math.sqrt(1.0 + temp_celsius / 273.15)

SOUND_SPEED_DEFAULT = 340.0        # m/s (fallback khi BME280 lỗi)
sound_speed = SOUND_SPEED_DEFAULT  # biến động, cập nhật từ BME280
TICK_TO_CM  = sound_speed * 100 * TICK_TO_SECONDS  # cm/tick

# ==================== KHỞI TẠO HARDWARE ====================

# Khai báo trước, khởi tạo trong setup()
spi          = None
lora         = None
bme280_sensor = None

def setup():
    """
    Khởi tạo toàn bộ phần cứng: GPIO, SPI, LoRa (với SF riêng), BME280.

    THAY ĐỔI SO VỚI BẢN CŨ:
        Sau khi set frequency, gọi thêm:
            lora.set_spreading_factor(NODE_SF)
            lora.set_bandwidth(LORA_BW)
            lora.set_coding_rate(LORA_CR)
        để node dùng đúng SF đã cấu hình.
    """
    global spi, lora, bme280_sensor

    # ── GPIO ──────────────────────────────────────────────────────────────
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)

    # DATA_READY (GPIO17): input, edge detection trong wait_for_data_ready()
    GPIO.setup(DATA_READY_PIN, GPIO.IN)

    # CONTROL (GPIO20): output, mặc định LOW (motor OFF)
    GPIO.setup(CONTROL_PIN, GPIO.OUT)
    GPIO.output(CONTROL_PIN, GPIO.LOW)
    print(f"[INIT] GPIO ready "
          f"(DATA_READY=GPIO{DATA_READY_PIN}, CTRL=GPIO{CONTROL_PIN})")

    # ── SPI ───────────────────────────────────────────────────────────────
    spi = spidev.SpiDev()
    spi.open(SPI_BUS, SPI_DEVICE)
    spi.max_speed_hz = SPI_SPEED
    spi.mode = 0b00              # Mode 0 (CPOL=0, CPHA=0) khớp STM32
    print(f"[INIT] SPI ready tại {SPI_SPEED / 1e6:.1f} MHz (Mode 0)")

    # ── LoRa SX1276 ───────────────────────────────────────────────────────
    lora = LoRa(BOARD.CN1, BOARD.CN1)

    # Tần số – phải khớp với SX1303 gateway
    lora.set_frequency(LORA_FREQ)

    # ✦ MỚI: Spreading Factor riêng theo hàng node ─────────────────────────
    # SX1276 hỗ trợ set_spreading_factor(sf) với sf = 6..12
    # SX1303 gateway lắng nghe đồng thời nhiều SF trên các kênh demodulator
    lora.set_spreading_factor(NODE_SF)

    # ✦ MỚI: Bandwidth – phải khớp với global_conf.json của gateway
    # rpi_lora: set_bandwidth(bw_khz) nhận giá trị kHz
    lora.set_bandwidth(LORA_BW)

    # ✦ MỚI: Coding Rate – 4/5 (tham số cr=5 trong rpi_lora)
    lora.set_coding_rate(LORA_CR)

    print(f"[INIT] LoRa ready: {LORA_FREQ} MHz | "
          f"SF{NODE_SF} | BW{LORA_BW}kHz | CR4/{LORA_CR}")
    print(f"[INIT] Node: {NODE_NAME} (hàng {NODE_ROW}, nhóm {NODE_SUFFIX})")

    # ── BME280 (I2C) ───────────────────────────────────────────────────────
    try:
        i2c = busio.I2C(board.SCL, board.SDA)
        bme280_sensor = adafruit_bme280.Adafruit_BME280_I2C(
            i2c, address=BME280_I2C_ADDR
        )
        temp = bme280_sensor.temperature
        _apply_sound_speed(temp)
        print(f"[INIT] BME280 ready – T={temp:.1f}°C → "
              f"sound_speed={sound_speed:.2f} m/s")
    except Exception as e:
        bme280_sensor = None
        print(f"[WARN] BME280 lỗi: {e} – fallback {SOUND_SPEED_DEFAULT} m/s")

    # Thread cập nhật nhiệt độ định kỳ (daemon → tự tắt khi main thoát)
    t = threading.Thread(target=_sound_speed_update_loop, daemon=True)
    t.start()
    print(f"[INIT] Sound speed update thread started "
          f"(mỗi {BME280_UPDATE_INTERVAL}s)")


# ── BME280 helpers ────────────────────────────────────────────────────────

def _apply_sound_speed(temp_celsius: float):
    """Cập nhật sound_speed và TICK_TO_CM từ nhiệt độ mới."""
    global sound_speed, TICK_TO_CM
    sound_speed = calc_sound_speed(temp_celsius)
    TICK_TO_CM  = sound_speed * 100 * TICK_TO_SECONDS


def update_sound_speed() -> float | None:
    """
    Đọc nhiệt độ BME280, cập nhật sound_speed.
    Trả về nhiệt độ (°C) hoặc None nếu lỗi.
    Khi lỗi: giữ nguyên giá trị sound_speed trước đó (không reset).
    """
    global bme280_sensor
    if bme280_sensor is None:
        return None
    try:
        temp = bme280_sensor.temperature
        _apply_sound_speed(temp)
        print(f"[BME280] T={temp:.1f}°C → sound_speed={sound_speed:.2f} m/s")
        return temp
    except Exception as e:
        print(f"[WARN] BME280 read error: {e} – "
              f"giữ sound_speed={sound_speed:.2f} m/s")
        return None


def _sound_speed_update_loop():
    """Thread nền: cập nhật sound_speed mỗi BME280_UPDATE_INTERVAL giây."""
    while True:
        time.sleep(BME280_UPDATE_INTERVAL)
        update_sound_speed()


# ==================== BIẾN TRẠNG THÁI ====================

control_active   = False   # True khi đã nhận lệnh UP, đang đo
control_timeout  = None    # thời điểm hết hạn (time.time() + 60s)
impact_count     = 0       # số viên đã phát hiện trong lượt hiện tại
extra_mode_active = False  # True khi chế độ bảo trì đang bật
current_bia_type = "A"     # loại bia hiện tại

# ==================== HÀM HỖ TRỢ ====================

def log_data(message):
    """
    Ghi log ra console và file LOG_FILE (append).
    Thêm timestamp tự động vào đầu mỗi dòng.
    """
    timestamp   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_message = f"[{timestamp}] {message}"
    print(log_message)
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(log_message + "\n")
    except Exception as e:
        print(f"[WARN] Không ghi được log file: {e}")

# ==================== SPI / STM32 ====================

def read_stm32_timestamps():
    """
    Đọc 20 bytes từ STM32 qua SPI, parse thành dict timestamps.

    Định dạng SPI buffer (20 bytes):
        [ID_A][TS_A×4] [ID_B][TS_B×4] [ID_C][TS_C×4] [ID_D][TS_D×4]
        ID = ASCII ('A'=65, 'B'=66, 'C'=67, 'D'=68)
        TS = 32-bit big-endian (tick STM32 TIM2)

    Chuẩn hoá: trừ timestamp A để lấy Δt (A làm tham chiếu t=0).

    Trả về:
        dict: {'A': 0.0, 'B': Δt_B, 'C': Δt_C, 'D': Δt_D} (giây)
        None: nếu lỗi SPI hoặc parse
    """
    try:
        # 20 bytes dummy → trigger STM32 gửi lại spi_tx_buffer
        response = spi.xfer2([0x00] * 20)

        timestamps = {}
        for i in range(4):
            offset    = i * 5
            sensor_id = chr(response[offset])   # ASCII → ký tự 'A'–'D'

            # 4 bytes big-endian → 32-bit tick
            ts_raw = ((response[offset + 1] << 24) |
                      (response[offset + 2] << 16) |
                      (response[offset + 3] <<  8) |
                       response[offset + 4])

            ts_seconds          = ts_raw * TICK_TO_SECONDS
            timestamps[sensor_id] = ts_seconds
            print(f"  [CH{i+1}] Sensor {sensor_id}: "
                  f"Raw={ts_raw}, Time={ts_seconds*1e6:.3f}µs")

        # Chuẩn hoá: A làm tham chiếu (t_A = 0)
        if 'A' in timestamps:
            t_ref = timestamps['A']
            for key in timestamps:
                timestamps[key] -= t_ref

        return timestamps

    except Exception as e:
        print(f"[ERROR] Đọc STM32 SPI: {e}")
        return None


def wait_for_data_ready(timeout=2.0):
    """
    Chờ cạnh lên (RISING) trên GPIO17 (DATA_READY từ STM32).

    Dùng GPIO.wait_for_edge() để không busy-wait tốn CPU.

    Tham số:
        timeout (float): thời gian chờ tối đa (giây)

    Trả về:
        bool: True = nhận được cạnh lên, False = timeout
    """
    # Nếu GPIO đã HIGH sẵn (STM32 kéo lên trước khi ta chờ)
    if GPIO.input(DATA_READY_PIN) == GPIO.HIGH:
        print("[DATA_READY] Tín hiệu đã ở mức HIGH")
        return True

    timeout_ms = int(timeout * 1000)
    channel = GPIO.wait_for_edge(
        DATA_READY_PIN, GPIO.RISING, timeout=timeout_ms
    )
    if channel is not None:
        print("[DATA_READY] Nhận cạnh lên từ STM32")
        return True

    print(f"[TIMEOUT] Không có DATA_READY trong {timeout:.1f}s")
    return False


def detect_impact():
    """
    Chờ DATA_READY (100ms timeout) rồi đọc timestamps từ STM32.

    Timeout ngắn để không block main loop quá lâu.
    Main loop vẫn tiếp tục nhận lệnh LoRa và kiểm tra timeout.

    Trả về:
        dict: timestamps đã chuẩn hoá, hoặc None nếu không có tín hiệu
    """
    if wait_for_data_ready(timeout=0.1):
        detections = read_stm32_timestamps()
        if detections:
            return detections
    return None

# ==================== TRIANGULATION ====================

def triangulation_weighted_average(detections):
    """
    BƯỚC 1: Ước tính nhanh vị trí bằng Weighted Average.

    Trọng số:
        Sensor A (tham chiếu, Δt=0): weight = 1 / (dist_to_A + ε)
        Sensor X (X≠A):              weight = 1 / (|Δd_X| + ε)
            với Δd_X = detections[X] × sound_speed_cm

    Giải thích: sensor nào phát hiện sớm hơn (Δd nhỏ hơn) → gần
    đạn hơn → trọng số cao hơn.
    """
    SOUND_SPEED_CMS = sound_speed * 100   # cm/s (từ BME280)

    x, y = 0.0, 0.0   # bắt đầu tại tâm bia, tránh thiên lệch

    print(f"[HYBRID-STEP1] Weighted Average – khởi đầu ({x:.2f}, {y:.2f})")

    for iteration in range(WEIGHTED_AVG_ITERATIONS):
        dist = {s: math.sqrt((x - sx)**2 + (y - sy)**2)
                for s, (sx, sy) in SENSOR_POSITIONS.items()}

        weights = {}
        for s in SENSOR_POSITIONS:
            if s == 'A':
                weights[s] = 1.0 / (dist[s] + 1e-6)
            else:
                delta_d    = detections[s] * SOUND_SPEED_CMS
                weights[s] = 1.0 / (abs(delta_d) + 1e-6)

        total_weight = sum(weights.values())
        x_new = sum(weights[s] * SENSOR_POSITIONS[s][0]
                    for s in SENSOR_POSITIONS) / total_weight
        y_new = sum(weights[s] * SENSOR_POSITIONS[s][1]
                    for s in SENSOR_POSITIONS) / total_weight

        # Smooth update để tránh oscillation
        x = x + (x_new - x) * WEIGHTED_AVG_LEARNING_RATE
        y = y + (y_new - y) * WEIGHTED_AVG_LEARNING_RATE

    # Clamp trong biên bia
    x = max(-50, min(50, x))
    y = max(-50, min(50, y))

    print(f"[HYBRID-STEP1] Weighted Average – kết quả ({x:.2f}, {y:.2f})")
    return x, y


def triangulation_hyperbolic_refinement(detections, x_init, y_init):
    """
    BƯỚC 2: Tinh chỉnh chính xác bằng Hyperbolic Least Squares.

    Minimize tổng bình phương sai số giữa hiệu khoảng cách lý thuyết
    và hiệu khoảng cách đo được (từ Δt × c).

    Sử dụng kết quả Weighted Average làm điểm khởi đầu.

    Trả về:
        tuple (x, y): tọa độ tinh chỉnh (cm)
    """
    print(f"[HYBRID-STEP2] Hyperbolic – khởi đầu "
          f"({x_init:.2f}, {y_init:.2f})")

    SOUND_SPEED_CMS = sound_speed * 100

    def residuals(pos):
        x_est, y_est = pos
        distances = {
            s: np.sqrt((x_est - sx)**2 + (y_est - sy)**2)
            for s, (sx, sy) in SENSOR_POSITIONS.items()
        }
        distance_diffs_measured = {
            s: (detections[s] - detections['A']) * SOUND_SPEED_CMS
            for s in SENSOR_POSITIONS
        }
        errors = []
        for s in ['B', 'C', 'D']:
            d_A      = distances['A']
            d_sensor = distances[s]
            # Lý thuyết: d_sensor - d_A = Δd_measured[s]
            diff_theoretical = d_sensor - d_A
            diff_measured    = distance_diffs_measured[s]
            errors.append(diff_theoretical - diff_measured)
        return errors

    try:
        result = least_squares(
            residuals,
            x0=[x_init, y_init],
            max_nfev=HYPERBOLIC_MAX_ITERATIONS,
            ftol=HYPERBOLIC_TOLERANCE,
            xtol=HYPERBOLIC_TOLERANCE,
        )
        x_ref, y_ref = result.x
        x_ref = max(-50, min(50, x_ref))
        y_ref = max(-50, min(50, y_ref))
        print(f"[HYBRID-STEP2] Hyperbolic – kết quả ({x_ref:.2f}, {y_ref:.2f})")
        return x_ref, y_ref

    except Exception as e:
        print(f"[WARN] Hyperbolic thất bại: {e} – giữ kết quả step 1")
        return x_init, y_init


def triangulation(detections):
    """
    Hybrid triangulation: Weighted Average → (Hyperbolic Refinement).

    Trả về:
        tuple (x, y) (cm) hoặc (None, None) nếu lỗi
    """
    try:
        x, y = triangulation_weighted_average(detections)
        if ENABLE_HYPERBOLIC:
            x, y = triangulation_hyperbolic_refinement(detections, x, y)
        return round(x, 2), round(y, 2)
    except Exception as e:
        print(f"[ERROR] Triangulation: {e}")
        return None, None

# ==================== LoRa TX / RX ====================

def send_coordinates(x, y):
    """
    Gửi tọa độ viên đạn về Controller qua LoRa SX1276.

    Định dạng (giữ nguyên từ bản cũ): "NODE1A, -26.30, 30.10"
    Controller parse bằng parse_node_data() không thay đổi.

    Gói tin được gửi trên SF được cấu hình (NODE_SF).
    SX1303 gateway nhận và forward lên Controller qua UDP.

    Tham số:
        x (float): tọa độ X (cm)
        y (float): tọa độ Y (cm)
    """
    # Định dạng dữ liệu giữ nguyên hoàn toàn từ bản cũ
    message = f"{NODE_NAME}, {x:.2f}, {y:.2f}"
    try:
        lora.send(message.encode('utf-8'))
        log_data(f"[TX] SF{NODE_SF} | {message}")
    except Exception as e:
        log_data(f"[ERROR] Gửi LoRa: {e}")


def receive_command():
    """
    Kiểm tra lệnh từ Controller qua LoRa (non-blocking).

    Định dạng lệnh (giữ nguyên từ bản cũ):
        "NODE1A UP", "A DOWN", "EXTRA UP", "B UP", v.v.

    Controller gửi downlink qua SX1303 → SX1276 nhận.
    SX1276 nhận trên cùng SF đã cấu hình.

    Trả về:
        str: "ACTIVATED", "DEACTIVATED", "EXTRA_ON", "EXTRA_OFF"
        None: không có lệnh
    """
    global control_active, control_timeout, impact_count
    global extra_mode_active, current_bia_type

    try:
        if lora.is_rx_busy():
            return None

        payload = lora.read()
        if not payload:
            return None

        data  = payload.decode('utf-8').strip()
        parts = data.split()

        if len(parts) < 2:
            return None

        node_command = parts[0].upper()
        action       = parts[1].upper()

        # ── EXTRA mode ────────────────────────────────────────────────────
        if node_command == "EXTRA":
            if action == "UP":
                extra_mode_active = True
                control_active    = False
                GPIO.output(CONTROL_PIN, GPIO.HIGH)
                print(f"[EXTRA] Mode ON – GPIO{CONTROL_PIN} HIGH")
                return "EXTRA_ON"
            elif action == "DOWN":
                extra_mode_active = False
                control_active    = False
                GPIO.output(CONTROL_PIN, GPIO.LOW)
                print(f"[EXTRA] Mode OFF – GPIO{CONTROL_PIN} LOW")
                return "EXTRA_OFF"

        # Khoá khi EXTRA đang bật
        if extra_mode_active:
            return None

        # ── Broadcast A / B / C / D ───────────────────────────────────────
        # Lệnh nhóm: bất kỳ node nào thuộc nhóm đó đều phản hồi
        if node_command in ("A", "B", "C", "D"):
            current_bia_type = node_command
            if action == "UP":
                control_active  = True
                control_timeout = time.time() + CONTROL_TIMEOUT
                impact_count    = 0
                GPIO.output(CONTROL_PIN, GPIO.HIGH)
                print(f"[CONTROL] BROADCAST {node_command} UP – Activated")
                return "ACTIVATED"
            elif action == "DOWN":
                control_active = False
                GPIO.output(CONTROL_PIN, GPIO.LOW)
                print(f"[CONTROL] BROADCAST {node_command} DOWN – Deactivated")
                return "DEACTIVATED"

        # ── Lệnh riêng cho node này ────────────────────────────────────────
        if node_command == NODE_NAME:
            if action == "UP":
                control_active  = True
                control_timeout = time.time() + CONTROL_TIMEOUT
                impact_count    = 0
                GPIO.output(CONTROL_PIN, GPIO.HIGH)
                print(f"[CONTROL] {NODE_NAME} UP – Activated")
                return "ACTIVATED"
            elif action == "DOWN":
                control_active = False
                GPIO.output(CONTROL_PIN, GPIO.LOW)
                print(f"[CONTROL] {NODE_NAME} DOWN – Deactivated")
                return "DEACTIVATED"

        # Lệnh cho node khác → bỏ qua
        return None

    except Exception as e:
        print(f"[ERROR] receive_command: {e}")
        return None

# ==================== VÒNG LẶP CHÍNH ====================

def main():
    """Vòng lặp chính của Node."""
    global control_active, control_timeout, impact_count, extra_mode_active

    try:
        setup()

        print("=" * 60)
        print(f"NODE STARTED – {NODE_NAME} | SF{NODE_SF} | {LORA_FREQ}MHz")
        print("=" * 60)

        while True:
            # Luôn kiểm tra lệnh LoRa trong mỗi vòng lặp
            receive_command()

            # ── Chế độ hoạt động bình thường ──────────────────────────────
            if control_active and not extra_mode_active:

                if time.time() > control_timeout:
                    # Hết 60s – tự động tắt
                    control_active = False
                    GPIO.output(CONTROL_PIN, GPIO.LOW)
                    print("[TIMEOUT] Hết 60s – tự động tắt")

                else:
                    # Còn thời gian – phát hiện viên đạn
                    detections = detect_impact()

                    if detections:
                        impact_count += 1
                        print(f"[IMPACT] Phát hiện viên #{impact_count}")

                        x, y = triangulation(detections)

                        if x is not None and y is not None:
                            print(f"[RESULT] Tọa độ: x={x}, y={y}")
                            send_coordinates(x, y)

                        if impact_count >= 3:
                            # Đủ 3 viên – kết thúc lượt
                            control_active = False
                            GPIO.output(CONTROL_PIN, GPIO.LOW)
                            print("[COMPLETE] Đủ 3 viên – kết thúc lượt")

            # ── Chế độ EXTRA – chờ lệnh DOWN ──────────────────────────────
            elif extra_mode_active:
                pass   # GPIO đã HIGH, chỉ chờ receive_command() xử lý

            # Sleep 100ms – giảm CPU, không bỏ sót lệnh LoRa
            time.sleep(0.1)

    except KeyboardInterrupt:
        print("\nNode dừng bởi người dùng (Ctrl+C)")

    except Exception as e:
        print(f"[ERROR] {e}")

    finally:
        GPIO.output(CONTROL_PIN, GPIO.LOW)
        GPIO.cleanup()
        spi.close()
        lora.close()
        print("Cleanup hoàn tất")


if __name__ == "__main__":
    main()
