```markdown
# HTTDTD - Hệ Thống Tính Điểm Tự Động Dùng Cho Bắn Súng

## DỰ ÁN ĐƯỢC TẠO BỞI Dunghero1412
## Người tạo dự án : Chiêm Dũng.
## Người bảo trì dự án : Chiêm Dũng.

**Dự án đã được đăng ký giấy phép MIT license – bất kỳ cá nhân, tổ chức hoặc đơn vị nào cũng đều được phép clone, chỉnh sửa và sử dụng mã nguồn**
```
---

<div align="center">

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://python.org)
[![Platform](https://img.shields.io/badge/Platform-RPi5%20%7C%20RPi%20Zero%202W%20%7C%20STM32F407-red.svg)]()

**Phát triển bởi [Chiêm Dũng](https://github.com/Dunghero1412)**

</div>

---

## 📋 Mục lục

- [Giới thiệu](#-giới-thiệu)
- [Nguyên lý hoạt động](#-nguyên-lý-hoạt-động)
- [Thuật toán Hybrid](#-thuật-toán-hybrid-wa--hyperbolic-refinement)
- [Phần cứng](#-phần-cứng)
- [Phần mềm](#-phần-mềm)
- [Cấu trúc thư mục](#-cấu-trúc-thư-mục)
- [Cài đặt](#-cài-đặt)
- [Khởi động hệ thống](#-khởi-động-hệ-thống)
- [Vận hành](#-vận-hành)
- [Định dạng log và kết quả](#-định-dạng-log-và-kết-quả)
- [Xử lý sự cố](#-xử-lý-sự-cố)
- [Tài liệu bổ sung](#-tài-liệu-bổ-sung)
- [Giấy phép](#-giấy-phép)
- [Liên hệ](#-liên-hệ)

---

## 🎯 Giới thiệu

**HTTDTD** là hệ thống tính điểm tự động cho các bài bắn súng AK trong huấn luyện quân sự. Hệ thống phát hiện và định vị điểm chạm của viên đạn theo thời gian thực bằng cách thu nhận **sóng N-Wave** — sóng áp suất siêu âm đặc trưng do viên đạn bay ở tốc độ cao (Mach ~2.1 ≈ 714 m/s) tạo ra.

**Điểm nổi bật của phiên bản mới (SX1303 LoRaWAN Gateway):**

- **Điều khiển 15 node đồng thời** (5 node × 3 dãy A/B/C) – mỗi node sử dụng một Spreading Factor riêng (SF6–SF10) để tránh xung đột gói tin.
- **SX1303 Gateway** kết nối với RPi 5 qua SPI, xử lý uplink/downlink chuyên nghiệp theo chuẩn **Semtech UDP packet forwarder**.
- **Giao diện đồ họa PyQt6** thay thế nút bấm GPIO: bảng điểm, cửa sổ log, 10 nút ảo (NODE1–5, A, B, C, D, EXTRA).
- **Kiến trúc 3 file** (`MAIN.py`, `CONTROLLER.py`, `GUI.py`) tách biệt hoàn toàn phần điều khiển và giao diện, dễ bảo trì và mở rộng.
- **Tốc độ âm thanh động** từ cảm biến BME280, cập nhật mỗi 60 giây – tăng độ chính xác định vị lên ±1 cm.
- **Thuật toán Hybrid 2 bước**: Weighted Average (nhanh) + Hyperbolic Refinement (chính xác), sai số tổng thể < 2 cm.

---

## ⚙️ Nguyên lý hoạt động


**Chuỗi sự kiện chi tiết** (đã được tối ưu):

| Bước | Thực hiện | Nội dung | Thời gian |
|------|-----------|----------|------------|
| 1 | Vật lý | Sóng N‑Wave đến 4 cảm biến Piezo | 0–10 ms |
| 2 | STM32 TIM2 | Capture timestamp 4 kênh, tick = 84 MHz (11.9 ns) | 5 μs |
| 3 | STM32 | Đóng gói 20 byte nhị phân, kéo PB0 (DATA_READY) HIGH | 1 μs |
| 4 | RPi Zero (Node) | Đọc SPI, chuẩn hóa Δt, Hybrid triangulation | ~2 ms |
| 5 | Node → SX1303 | Gửi tọa độ qua LoRa (SF riêng theo hàng) | ~50–150 ms |
| 6 | SX1303 → lora_pkt_fwd | UDP PUSH_DATA, base64 payload | <1 ms |
| 7 | CONTROLLER.py | Parse, cập nhật điểm, ghi JSON | <1 ms |
| 8 | GUI.py | Cập nhật bảng điểm, log | ~1 ms |

---

## 🧮 Thuật toán Hybrid (WA + Hyperbolic Refinement)

Hệ thống dùng phương pháp **Hybrid 2 bước** để cân bằng giữa tốc độ và độ chính xác.

### Bước 1 – Weighted Average (Ước tính nhanh)

Dùng chênh lệch khoảng cách âm thanh `Δd = Δt × c` để tính trọng số cho từng sensor:

```

weight_X = 1 / (|Δd_X| + ε)

```

Sensor nào có Δd nhỏ (sóng âm đến sớm) → gần đạn nhất → trọng số cao → kéo ước tính về phía đó.

- **Ưu điểm:** Cực nhanh (~0.5 ms), không phân kỳ.
- **Nhược điểm:** Sai số 5–20 cm vì chưa dùng thông tin TDOA.
- **Vai trò:** Cung cấp điểm khởi đầu tốt cho bước tiếp theo.

### Bước 2 – Hyperbolic Refinement (Tinh chỉnh)

Dùng `scipy.optimize.least_squares` để tối thiểu hóa **residuals**:

```

residual_X = (d_X − d_A)_lý_thuyết − (d_X − d_A)_đo_được

```

với `d_X` là khoảng cách từ điểm ước tính đến sensor X, và `Δd_đo = Δt_X × c(T)`.  
Hàm này hội tụ từ điểm WA trong 3–5 vòng lặp, sai số cuối < 2 cm.

**Công thức tốc độ âm theo nhiệt độ (cập nhật động từ BME280):**

```

c(T) = 331.3 × √(1 + T / 273.15)  [m/s]

```

**Ví dụ minh họa chi tiết:** Xem [`docs/EXAMPLE.md`](docs/EXAMPLE.md).

---

## 🔧 Phần cứng

### Controller – Raspberry Pi 5 + SX1303 Gateway HAT

| Linh kiện | Số lượng | Ghi chú |
|-----------|----------|---------|
| Raspberry Pi 5 (8GB) | 1 | Chạy Raspberry Pi OS 64-bit Desktop |
| SX1303 LoRaWAN Gateway HAT | 1 | Tần số 915 MHz (US915) |
| (Tuỳ chọn) Màn hình HDMI | 1 | Để xem giao diện PyQt6 |

### Node – RPi Zero 2W + STM32F407VG

| Linh kiện | Số lượng / Node | Ghi chú |
|-----------|-----------------|---------|
| Raspberry Pi Zero 2W | 1 | OS Lite 64-bit |
| STM32F407VG | 1 | 168 MHz, TIM2 32-bit, chạy firmware `scripts/STM32` |
| LoRa SX1276 | 1 | Giao tiếp UART, SF6–SF10 |
| BME280 | 1 | I²C, đo nhiệt độ, áp suất |
| Piezoelectric sensor | 4 | Gắn 4 góc bia 100×100 cm |
| Mạch pre‑amp + comparator | 4 | Dùng OPA2134 hoặc MCP6004 |

### Sơ đồ kết nối

#### Controller (RPi 5 + SX1303 HAT)

```

SX1303 HAT cắm trực tiếp lên header GPIO của RPi 5 (SPI và các chân điều khiển).
Không cần nối dây rời.

```

#### Node (RPi Zero 2W ↔ STM32F407VG)

```

RPi Zero 2W SPI0              STM32F407VG SPI2
─────────────────────────────────────────────
GPIO10 (MOSI) ──────────────── PB15 (SPI2_MOSI)
GPIO9  (MISO) ──────────────── PB14 (SPI2_MISO)
GPIO11 (SCLK) ──────────────── PB13 (SPI2_SCK)
GPIO8  (CE0)  ──────────────── PB12 (SPI2_NSS)
GPIO17 (INPUT)──────────────── PB0  (DATA_READY)

RPi Zero 2W I2C               BME280
─────────────────────────────────────────────
GPIO2  (SDA)  ──────────────── SDA
GPIO3  (SCL)  ──────────────── SCL
3.3V ───────────────────────── VCC, SDO→GND (addr 0x76)
GND ────────────────────────── GND

RPi Zero 2W UART              LoRa SX1276
─────────────────────────────────────────────
GPIO14 (TX)   ──────────────── RXD
GPIO15 (RX)   ──────────────── TXD
3.3V ───────────────────────── VCC
GND ────────────────────────── GND, M0, M1

STM32F407VG TIM2              Piezoelectric sensors
─────────────────────────────────────────────
PA0 (TIM2_CH1, AF01) ───────── Sensor A (góc Trái‑Trên)
PA1 (TIM2_CH2, AF01) ───────── Sensor B (góc Phải‑Trên)
PA2 (TIM2_CH3, AF01) ───────── Sensor C (góc Trái‑Dưới)
PA3 (TIM2_CH4, AF01) ───────── Sensor D (góc Phải‑Dưới)

```

> Sơ đồ chi tiết dạng SVG: [`docs/wiring_diagram_node_stm32.svg`](docs/wiring_diagram_node_stm32.svg).

---

## 💻 Phần mềm

### Controller – RPi 5

| Thư viện / Thành phần | Phiên bản | Mục đích |
|----------------------|-----------|----------|
| `sx1302_hal` | 2.1.0 | Packet forwarder `lora_pkt_fwd`, giao tiếp SX1303 |
| `PyQt6` | ≥ 6.5 | Giao diện đồ họa (MAIN.py + GUI.py) |
| `numpy`, `scipy` | ≥ 1.11 | Tái sử dụng cho controller (tính điểm) |
| `platform` (built‑in) | – | Định vị hệ điều hành |

### Node – RPi Zero 2W

| Thư viện | Phiên bản | Mục đích |
|----------|-----------|----------|
| `spidev` | ≥ 3.6 | Đọc SPI từ STM32 |
| `RPi.GPIO` | ≥ 0.7.1 | DATA_READY (GPIO17), CONTROL (GPIO20) |
| `pylorahat` | latest | LoRa SX1276 (giao tiếp UART) |
| `scipy` | ≥ 1.11 | Hyperbolic refinement |
| `adafruit-circuitpython-bme280` | ≥ 2.6 | Cảm biến nhiệt độ, áp suất |
| `numpy` | ≥ 1.24 | Tính toán ma trận |

> ⚠️ **Tất cả thư viện được tự động cài đặt bởi `setup.py`** – không cần chạy pip thủ công.

---

## 📁 Cấu trúc thư mục (phiên bản mới)

```

HTTDTD/
│
├── scripts/
│   ├── CONTROLLER/                     # Chạy trên RPi 5
│   │   ├── CONTROLLER.py               # Backend: LoRa, điểm, JSON
│   │   ├── GUI.py                      # Giao diện PyQt6
│   │   └── MAIN.py                     # Khởi tạo queue, thread, subprocess
│   │
│   ├── NODE-A/                         # Node dãy A (đợt 1)
│   │   └── NODE.py
│   ├── NODE-B/                         # Node dãy B (đợt 2)
│   │   └── NODE.py
│   ├── NODE-C/                         # Node dãy C (đợt 3)
│   │   └── NODE.py
│   ├── NODE-D/                         # Node dãy D (dự phòng)
│   │   └── NODE.py
│   │
│   ├── STM32F407VGT6/                  # Firmware cho STM32 1MB
│   │   └── firmware.elf
│   ├── STM32F407VET6/                  # Firmware cho STM32 512KB
│   │   └── firmware.elf
│   │
│   └── html/
│       └── score.html                  # Giao diện web (tuỳ chọn)
│
├── docs/                               # Tài liệu, sơ đồ
│   ├── EXAMPLE.md
│   ├── INSTALL_HARDWARE.md
│   └── wiring_diagram_*.svg
│
├── setup.py                            # Cài đặt tự động (cả lib + service)
└── README.md

```

---

## 🚀 Cài đặt

### Yêu cầu chung

- RPi 5 đã cài **Raspberry Pi OS 64‑bit Desktop** (có giao diện để chạy PyQt6)
- RPi Zero 2W cài **Raspberry Pi OS Lite 64‑bit**
- Đã build `sx1302_hal` và copy packet forwarder vào thư mục `~/sx1302_hal/packet_forwarder` (xem hướng dẫn riêng)
- STM32F407VG đã nạp firmware (xem bên dưới)
- Kết nối Internet để tải thư viện lần đầu

### Các bước cài đặt

**1. Clone repository**
```bash
git clone https://github.com/Dunghero1412/HTTDTD.git
cd HTTDTD
```

2. Cài đặt Controller (RPi 5)

```bash
sudo python3 setup.py install controller
```

Script sẽ copy 3 file CONTROLLER.py, GUI.py, MAIN.py vào /opt/, tạo systemd service rpi5-controller.service (chạy MAIN.py).

3. Cài đặt từng Node
Trên mỗi RPi Zero 2W, chạy:

```bash
# Ví dụ Node 1 dãy A
sudo python3 setup.py install node 1a

# Nếu cần flash STM32 (cắm ST‑Link)
sudo python3 setup.py install node 1a --flash-stm32=1   # VGT6 1MB
# hoặc
sudo python3 setup.py install node 1a --flash-stm32=2   # VET6 512KB
```

setup.py sẽ:

· Tự động cài thư viện Python cần thiết
· Bật I2C, SPI, UART trên RPi Zero
· Tạo systemd service rpi-nano-node1a.service
· (Nếu có --flash-stm32) flash firmware STM32 qua ST‑Link

4. Build và chạy packet forwarder (chỉ làm một lần trên Controller)

```bash
cd ~
git clone https://github.com/Lora-net/sx1302_hal.git
cd sx1302_hal
make clean all
# Sao chép binary vào thư mục mong muốn (mặc định MAIN.py tìm ở ~/sx1302_hal/packet_forwarder/)
cd packet_forwarder
cp lora_pkt_fwd /opt/
cp global_conf.json.sx1250.US915 /opt/global_conf.json
```

---

🔌 Khởi động hệ thống

Thứ tự bắt buộc: Packet forwarder → Controller → Các Node

Bước 1 – Khởi động packet forwarder (thủ công lần đầu)

```bash
cd /opt
./lora_pkt_fwd -c global_conf.json
```

Để chạy ngầm, có thể tạo systemd service riêng (khuyến nghị).

Bước 2 – Khởi động Controller

```bash
sudo systemctl start rpi5-controller.service
sudo systemctl status rpi5-controller.service
```

Hoặc chạy thủ công để debug:

```bash
cd /opt
python3 MAIN.py
```

Cửa sổ PyQt6 sẽ hiện ra với 3 ô: bảng điểm (trái), log (phải trên), các nút bấm (phải dưới).

Bước 3 – Khởi động từng Node

Trên mỗi RPi Zero 2W:

```bash
sudo systemctl start rpi-nano-node1a.service   # tuỳ tên node
```

Hoặc thủ công:

```bash
cd /opt
python3 NODE_NODE1A.py
```

Kiểm tra kết nối

· Trên GUI, log sẽ hiển thị [INIT] UDP socket lắng nghe..., [CTRL] Controller thread started.
· Khi Node gửi dữ liệu, log xuất hiện [RX] NODE1A, 29.0, 31.0 | SF6BW125 | RSSI=-78dBm.
· Bảng điểm tự động cập nhật.

---

🎮 Vận hành

Giao diện PyQt6 (Controller)

· Ô bảng điểm (trái): hiển thị điểm 5 node × 3 dãy (A, B, C) theo định dạng cột.
· Ô log/debug (phải trên): hiển thị timestamp, lệnh gửi, dữ liệu nhận, lỗi.
· Hàng nút trên (NODE1 → NODE5): bật/tắt riêng từng node.
· Hàng nút dưới (A, B, C, D, EX):
  · A, B, C, D: broadcast cho tất cả node trong nhóm đó (cùng dãy).
  · EX (EXTRA): chế độ bảo trì – khi bật, tất cả nút khác bị khoá, nút EX chuyển màu đỏ.

Cách bắn một lượt

1. Chọn node hoặc nhóm node bằng nút tương ứng (nút sẽ sáng xanh).
2. Hệ thống gửi lệnh UP qua LoRa → Node nhận, bật motor (GPIO20 HIGH).
3. Xạ thủ bắn (tối đa 3 viên). Mỗi viên → Node tính toạ độ, gửi về → Controller hiển thị điểm.
4. Sau 3 viên hoặc 60 giây, Node tự động gửi DOWN (hoặc người dùng bấm nút lần nữa để tắt sớm).
5. Điểm được lưu vào score_data.json và log.

Reset vòng bắn

Nút RESET ROUND (chưa có trong GUI mặc định, có thể thêm) sẽ pad miss cho những viên thiếu và xoá toàn bộ dữ liệu shots, bắt đầu vòng mới.

---

📄 Định dạng log và kết quả

File log (/opt/score.txt)

```
[2025-05-07 10:25:30] [CTRL] Controller thread started (SX1303 UDP mode)
[2025-05-07 10:25:32] [RX] NODE1A, 29.0, 31.0 | SF6BW125 | RSSI=-78dBm
[2025-05-07 10:25:32] [SCORE] NODE1A: (29.0, 31.0) - Vòng 5 - 5 điểm
[2025-05-07 10:25:32] [TX] Gửi: 'NODE1A 31.0'
```

File JSON (/opt/score.json)

```json
{
  "timestamp": "2025-05-07T10:25:32.123456",
  "rounds": [
    {"node": "NODE1A", "x": 29.0, "y": 31.0, "score": 5, "ring": "Vòng 5", "distance": 42.45}
  ]
}
```

---

🛠️ Xử lý sự cố

Lỗi UDP bind (Controller)

· Triệu chứng: [ERROR] Bind UDP socket: Address already in use
· Nguyên nhân: packet forwarder chưa chạy hoặc cổng 1700 bị chiếm.
· Khắc phục:
    sudo lsof -i :1700 → kill tiến trình cũ, sau đó khởi động lại packet forwarder.

Node không gửi được dữ liệu

· Kiểm tra SPI: spi.xfer2([0]*20) có trả về mảng 20 byte khác 0 không?
    Nếu toàn 0 → STM32 chưa sẵn sàng hoặc DATA_READY không kéo.
· Kiểm tra DATA_READY: dùng gpio read 17 (sau khi bắn) xem có HIGH không.
· Kiểm tra SF: Trong NODE.py, biến NODE_ROW phải khớp với hàng node thực tế (1–5) và NODE_SUFFIX phải là A/B/C/D.

GUI không hiển thị bảng điểm

· PyQt6 chưa cài: pip install PyQt6
· Chạy trên terminal không có DISPLAY: Phải dùng môi trường desktop hoặc export DISPLAY=:0
· SignalBridge chưa kết nối: Kiểm tra MAIN.py đã gọi set_score_callback chưa.

Packet forwarder không nhận gói từ node

· Kiểm tra tần số global_conf.json phải là 915 MHz.
· Node phải dùng đúng SF (SF6–SF10) và bandwidth 125 kHz.
· Kiểm tra anten và khoảng cách.

---

📚 Tài liệu bổ sung

File Nội dung
docs/EXAMPLE.md Bài toán mô phỏng thuật toán
docs/INSTALL_HARDWARE.md Hướng dẫn lắp đặt phần cứng chi tiết
docs/wiring_diagram_node_stm32.svg Sơ đồ nối dây Node

---

📜 Giấy phép

```
MIT License

Copyright (c) 2025 Chiêm Dũng (Dunghero1412)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions...

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND.
```

---

## 📬 Liên hệ

Nếu gặp sự cố hoặc có đóng góp, vui lòng liên hệ:

| Kênh | Địa chỉ |
|---|---|
| 📧 Email | [dhr1412.vn@gmail.com](mailto:dhr1412.vn@gmail.com) |
| 🐦 X (Twitter) | [@chiemdung171708](https://x.com/chiemdung171708) |
| 💻 GitHub | [Dunghero1412](https://github.com/Dunghero1412) |
| 🐛 Bug Report | [Issues](https://github.com/Dunghero1412/HTTDTD/issues) |

---

<div align="center">
  <sub>Made with ❤️ by Chiêm Dũng · HTTDTD v2.4 (SX1303 + PyQt6)</sub>
</div>
```

