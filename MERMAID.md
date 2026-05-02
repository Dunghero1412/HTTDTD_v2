## sơ đồ giao tiếp giữa GUI <-> CONTROLLER qua Queue

```mermaid
sequenceDiagram
    participant MAIN as MAIN.py
    participant CTRL as Controller Thread
    participant GUI as GUI Thread
    participant LORA as LoRa Module
    participant NODE as Node (RPi Nano)

    MAIN->>MAIN: Tạo cmd_queue, out_queue
    MAIN->>CTRL: Khởi tạo Controller(cmd_queue, out_queue)
    MAIN->>CTRL: start() → bắt đầu thread
    MAIN->>GUI: Khởi tạo ControllerGUI(cmd_queue, out_queue)
    MAIN->>GUI: show() → hiển thị cửa sổ
    MAIN->>MAIN: app.exec() → vòng lặp sự kiện Qt

    CTRL->>CTRL: _setup_lora() → kết nối LoRa
    CTRL->>GUI: out_queue.put(('log', 'LoRa ready'))
    GUI->>GUI: Cập nhật khung Log

    loop Vòng lặp chính của Controller
        CTRL->>CTRL: Kiểm tra cmd_queue (lệnh từ GUI)
        CTRL->>CTRL: Gọi _receive_data() → nhận từ LoRa
        alt Có dữ liệu từ Node
            LORA-->>CTRL: Dữ liệu (NODE1A, -26, 30)
            CTRL->>CTRL: parse_node_data(), update score
            CTRL->>GUI: out_queue.put(('board', board_text))
            CTRL->>GUI: out_queue.put(('log', log_msg))
            GUI->>GUI: Cập nhật bảng điểm + log
        end

        alt Có lệnh từ GUI
            GUI->>CTRL: cmd_queue.put({'type': 'send', ...})
            CTRL->>CTRL: _send_command() qua LoRa
            CTRL->>LORA: Gửi lệnh đến Node
        end
    end
```

---

## sơ đồ luồng khởi động hệ thống.

```mermaid
sequenceDiagram
    participant MAIN as MAIN.py
    participant CTRL as Controller Thread
    participant GUI as GUI Thread
    participant LORA as LoRa Module
    participant NODE as Node (RPi Nano)

    MAIN->>MAIN: Tạo cmd_queue, out_queue
    MAIN->>CTRL: Khởi tạo Controller(cmd_queue, out_queue)
    MAIN->>CTRL: start() → bắt đầu thread
    MAIN->>GUI: Khởi tạo ControllerGUI(cmd_queue, out_queue)
    MAIN->>GUI: show() → hiển thị cửa sổ
    MAIN->>MAIN: app.exec() → vòng lặp sự kiện Qt

    CTRL->>CTRL: _setup_lora() → kết nối LoRa
    CTRL->>GUI: out_queue.put(('log', 'LoRa ready'))
    GUI->>GUI: Cập nhật khung Log

    loop Vòng lặp chính của Controller
        CTRL->>CTRL: Kiểm tra cmd_queue (lệnh từ GUI)
        CTRL->>CTRL: Gọi _receive_data() → nhận từ LoRa
        alt Có dữ liệu từ Node
            LORA-->>CTRL: Dữ liệu (NODE1A, -26, 30)
            CTRL->>CTRL: parse_node_data(), update score
            CTRL->>GUI: out_queue.put(('board', board_text))
            CTRL->>GUI: out_queue.put(('log', log_msg))
            GUI->>GUI: Cập nhật bảng điểm + log
        end

        alt Có lệnh từ GUI
            GUI->>CTRL: cmd_queue.put({'type': 'send', ...})
            CTRL->>CTRL: _send_command() qua LoRa
            CTRL->>LORA: Gửi lệnh đến Node
        end
    end
```

---

## sơ đồ kiến trúc tổng thể.

```mermaid
flowchart TB
    subgraph MAIN["MAIN.py (Khởi chạy)"]
        M1[Tạo queue]
        M2[Khởi tạo Controller]
        M3[Khởi tạo GUI]
    end

    subgraph CTRL["CONTROLLER.py (Backend)"]
        C1[LoRa Giao tiếp]
        C2[Score Manager]
        C3[Log & JSON Writer]
    end

    subgraph GUI["GUI.py (Frontend)"]
        G1[PyQt6 Window]
        G2[Bảng điểm QTextEdit]
        G3[Log QTextEdit]
        G4[Hàng nút bấm]
    end

    subgraph QUEUE["Queue"]
        Q1[cmd_queue]
        Q2[out_queue]
    end

    G4 -->|"send/reset"| Q1
    Q1 --> C1
    C1 -->|"LoRa"| NODE[Node]
    NODE -->|"Tọa độ"| C1
    C1 --> C2
    C2 --> C3
    C2 --> Q2
    Q2 --> G2
    Q2 --> G3
```

---

## so sánh kiến trúc cũ và mới.

```mermaid
flowchart LR
    subgraph OLD["🔴 Phiên bản cũ (GPIO)"]
        direction TB
        O1[Nút bấm GPIO] --> O2[button_callback]
        O2 --> O3[send_command LoRa]
        O4[Loop while True] --> O5[receive_data]
        O5 --> O6[ScoreDisplay.update]
        O6 --> O7[print ra console]
    end

    subgraph NEW["🟢 Phiên bản mới (GUI)"]
        direction TB
        N1[Nút bấm GUI] --> N2[cmd_queue.put]
        N2 --> N3[Controller Thread]
        N3 --> N4[_send_command LoRa]
        
        N5[LoRa] --> N6[_receive_data]
        N6 --> N7[ScoreManager.update]
        N7 --> N8[out_queue.put]
        N8 --> N9[GUI update]
    end
```

---

## sơ đồ chi tiết về quá trình xử lý trong controller thread.

```mermaid
flowchart TD
    START([Controller.run]) --> INIT[Khởi tạo LoRa]
    INIT --> LOOP{while running}
    
    LOOP --> CHECK_CMD{cmd_queue có lệnh?}
    CHECK_CMD -->|Có| PROCESS_CMD{Xử lý lệnh}
    PROCESS_CMD -->|send| SEND[Gửi LoRa UP/DOWN]
    PROCESS_CMD -->|reset_round| RESET[ScoreManager.reset_round]
    PROCESS_CMD -->|exit| EXIT[Thoát vòng lặp]
    
    PROCESS_CMD --> NO_CMD
    CHECK_CMD -->|Không| NO_CMD[Kiểm tra LoRa]
    
    NO_CMD --> RECEIVE{_receive_data có dữ liệu?}
    RECEIVE -->|Có| PARSE[parse_node_data]
    PARSE --> UPDATE[ScoreManager.update]
    UPDATE --> SEND_OUT[Gửi board + log qua out_queue]
    UPDATE --> SAVE_FILE[Ghi JSON + score.txt]
    
    RECEIVE -->|Không| SLEEP[sleep 50ms]
    SEND_OUT --> SLEEP
    SAVE_FILE --> SLEEP
    SLEEP --> LOOP
    
    EXIT --> STOP([Dừng Controller])
'''

---

## sơ đồ cập nhật giao diện trong GUI thread (với signal / slot).

```mermaid
flowchart TD
    subgraph READER["Luồng đọc queue"]
        R1[_read_out_queue] --> R2{out_queue.get}
        R2 -->|log, board| R3[comm.update_log.emit<br/>comm.update_board.emit]
    end

    subgraph MAIN_THREAD["Thread chính PyQt6"]
        S1[comm.update_log signal] --> S2[append_log slot]
        S2 --> T1[log_text.append]
        
        S3[comm.update_board signal] --> S4[set_board_text slot]
        S4 --> T2[board_text.setPlainText]
    end

    R3 --> S1
    R3 --> S3
```