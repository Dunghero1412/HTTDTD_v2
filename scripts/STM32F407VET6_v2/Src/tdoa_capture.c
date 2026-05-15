#include "../Inc/tdoa_capture.h"
#include "../Inc/debug_uart.h"

/*----------- biến toàn cục -----------*/
volatile uint8_t data_ready = 0; // Cờ báo dữ liệu đã sẵn sàng
volatile uint8_t capture_enable = 0; // Cờ cho phép bắt tín hiệu
volatile uint8_t capture_count = 0; // Biến đếm số lần bắt tín hiệu
volatile uint32_t timestamp[4] = {0}; // Mảng lưu trữ thời gian bắt tín hiệu
volatile uint8_t capture_flag[4] = {0}; // Mảng cờ báo đã bắt tín hiệu cho mỗi kênh

static volatile uint8_t tc_high = 0 ; // Biến tạm để lưu trạng thái cao của tín hiệu
static volatile uint8_t delay_done = 0; // Cờ báo đã hoàn thành độ trễ
static volatile uint8_t tx_packet[PACKET_SIZE]; // Mảng lưu trữ gói dữ liệu truyền đi

// timmer phụ dùng để delay 2s (TIM3)
TIM_HandleTypeDef htim3;

// SPI3 Handle
SPI_HandleTypeDef hspi3;

// các định nghĩa chân
#define DATA_READY_PIN                 GPIO_PIN_0   // Chân báo dữ liệu đã sẵn sàng
#define DATA_READY_GPIO_PORT           GPIOB        // Cổng GPIO cho chân báo dữ liệu đã sẵn sàng
#define TRIGGER_COMMAND_PIN            GPIO_PIN_2   // Chân để nhận lệnh bắt tín hiệu
#define TRIGGER_COMMAND_GPIO_PORT      GPIOB        // Cổng GPIO cho chân nhận lệnh bắt tín hiệu
#define READ_DATA_COMPLETE_PIN         GPIO_PIN_1   // Chân báo đã hoàn thành việc đọc dữ liệu
#define READ_DATA_COMPLETE_GPIO_PORT   GPIOB        // Cổng GPIO cho chân báo đã hoàn thành việc đọc dữ liệu
#define RESET_PIN                      GPIO_PIN_4   // Chân để nhận lệnh reset
#define RESET_GPIO_PORT                GPIOB        // Cổng GPIO cho chân nhận lệnh reset

// prototype nội bộ

static void Enable_Capture(void);
static void Disable_Capture(void);
static void Reset_Captures(void);

// ------------ khởi tạo ------------
void TDOA_Init(void) {
    //cấu hình GPIO ngoài
    GPIO_InitTypeDef gpio = {0}; // Cấu hình chung cho tất cả các chân

    __HAL_RCC_GPIOB_CLK_ENABLE(); // Bật clock cho GPIOB
    __HAL_RCC_GPIOA_CLK_ENABLE(); // Bật clock cho GPIOA
    __HAL_RCC_GPIOC_CLK_ENABLE(); // Bật clock cho GPIOC

    // PB0 - DATA_READY  - output
    gpio.Pin = DATA_READY_PIN;
    gpio.Mode = GPIO_MODE_OUTPUT_PP;
    gpio.Pull = GPIO_NOPULL;
    gpio.Speed = GPIO_SPEED_FREQ_LOW;
    HAL_GPIO_Init(DATA_READY_GPIO_PORT, &gpio);
    HAL_GPIO_WritePin(DATA_READY_GPIO_PORT, DATA_READY_PIN, GPIO_PIN_RESET); // Khởi tạo ở mức thấp

    //PB1 - READ_DATA_COMPLETE - input
    gpio.Pin = READ_DATA_COMPLETE_PIN;
    gpio.Mode = GPIO_MODE_IT_RISING; // Ngắt khi tín hiệu chuyển từ thấp lên cao
    gpio.Pull = GPIO_PULLDOWN; // Kéo xuống để đảm bảo tín hiệu ở mức thấp khi không có tín hiệu
    HAL_GPIO_Init(READ_DATA_COMPLETE_GPIO_PORT, &gpio);

    // PB2 - TRIGGER_COMMAND - input
    gpio.Pin = TRIGGER_COMMAND_PIN;
    gpio.Mode = GPIO_MODE_IT_RISING_FALLING; // Ngắt khi tín hiệu chuyển từ thấp lên cao
    gpio.Pull = GPIO_PULLDOWN; // Kéo xuống để đảm bảo tín hiệu ở mức thấp khi không có tín hiệu
    HAL_GPIO_Init(TRIGGER_COMMAND_GPIO_PORT, &gpio);

    //PB4 - RESET - input
    gpio.Pin = RESET_PIN;
    gpio.Mode = GPIO_MODE_IT_RISING; // Ngắt khi tín hiệu chuyển từ thấp lên cao
    gpio.Pull = GPIO_PULLDOWN; // Kéo xuống để đảm bảo tín hiệu ở mức thấp khi không có tín hiệu
    HAL_GPIO_Init(RESET_GPIO_PORT, &gpio);

    // 2. Cấu hình TIM2 input capture 4 kênh
    TIM_IC_InitTypeDef ic = {0}; // Cấu hình chung cho tất cả các kênh
    TIM_HandleTypeDef htim2; // Handle cho TIM2
    __HAL_RCC_TIM2_CLK_ENABLE(); // Bật clock cho TIM2
    htim2.Instance = TIM2;
    htim2.Init.Prescaler = 0; // Không chia tần số
    htim2.Init.CounterMode = TIM_COUNTERMODE_UP; // Đếm lên
    htim2.Init.Period = 0xFFFFFFFF; // Chu kỳ tối đa (32-bit)
    htim2.Init.ClockDivision = TIM_CLOCKDIVISION_DIV1; // Không chia tần số
    htim2.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_DISABLE; // Không sử dụng tính năng tự động nạp lại
    HAL_TIM_IC_Init(&htim2);

    ic.ICPolarity = TIM_INPUTCHANNELPOLARITY_RISING; // Bắt tín hiệu ở cạnh lên
    ic.ICSelection = TIM_ICSELECTION_DIRECTTI; // Sử dụng trực tiếp tín hiệu từ chân
    ic.ICPrescaler = TIM_ICPSC_DIV1; // Không chia tần số
    ic.ICFilter = 0; // Không sử dụng bộ lọc
    
    // CH1 - PA0
    HAL_TIM_IC_ConfigChannel(&htim2, &ic, TIM_CHANNEL_1);
    // CH2 - PA1
    HAL_TIM_IC_ConfigChannel(&htim2, &ic, TIM_CHANNEL_2);
    // CH3 - PA2
    HAL_TIM_IC_ConfigChannel(&htim2, &ic, TIM_CHANNEL_3);
    // CH4 - PA3
    HAL_TIM_IC_ConfigChannel(&htim2, &ic, TIM_CHANNEL_4);
    
    // bật ngắt capture toàn cục
    HAL_NVIC_SetPriority(TIM2_IRQn, 1, 0); // Thiết lập độ ưu tiên cho ngắt TIM2
    HAL_NVIC_EnableIRQ(TIM2_IRQn); // Kích hoạt ngắt TIM2
    HAL_TIM_IC_Start_IT(&htim2, TIM_CHANNEL_1); // Bắt đầu bắt tín hiệu trên kênh 1 với ngắt
    HAL_TIM_IC_Start_IT(&htim2, TIM_CHANNEL_2); // Bắt đầu bắt tín hiệu trên kênh 2 với ngắt
    HAL_TIM_IC_Start_IT(&htim2, TIM_CHANNEL_3); // Bắt đầu bắt tín hiệu trên kênh 3 với ngắt
    HAL_TIM_IC_Start_IT(&htim2, TIM_CHANNEL_4); // Bắt đầu bắt tín hiệu trên kênh 4 với ngắt

    // 3. Cấu hình TIM3 dùng cho delay 2s
    __HAL_RCC_TIM3_CLK_ENABLE(); // Bật clock cho TIM3
    htim3.Instance = TIM3;
    htim3.Init.Prescaler = 8399; // Chia tần số để có xung 10 kHz (84 MHz / (8399 + 1) = 10 kHz)
    htim3.Init.CounterMode = TIM_COUNTERMODE_UP; // Đếm lên
    htim3.Init.Period = 20000 - 1; // Chu kỳ 2s (10 kHz / 20000 = 0.5 Hz)
    htim3.Init.ClockDivision = TIM_CLOCKDIVISION_DIV1; // Không chia tần số
    htim3.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_DISABLE; // Không sử dụng tính năng tự động nạp lại
    HAL_TIM_Base_Init(&htim3); // Khởi tạo TIM3
    HAL_NVIC_SetPriority(TIM3_IRQn, 2, 0); // Thiết lập độ ưu tiên cho ngắt TIM3
    HAL_NVIC_EnableIRQ(TIM3_IRQn); // Kích hoạt ngắt TIM3

    // 4. Cấu hình SPI3 để truyền dữ liệu slave (DMA TX)
    __HAL_RCC_SPI3_CLK_ENABLE(); // Bật clock cho SPI3
    hspi3.Instance = SPI3;
    hspi3.Init.Mode = SPI_MODE_SLAVE; // Chế độ slave
    hspi3.Init.Direction = SPI_DIRECTION_2LINES; // Truyền nhận 2 dây
    hspi3.Init.DataSize = SPI_DATASIZE_8BIT; // Kích thước dữ liệu 8 bit
    hspi3.Init.CLKPolarity = SPI_POLARITY_LOW; // Cựctính clock thấp
    hspi3.Init.CLKPhase = SPI_PHASE_1EDGE; // Dữ liệu được lấy mẫu ở cạnh lên của clock
    hspi3.Init.NSS = SPI_NSS_HARD_INPUT; // Sử dụng chân NSS cứng để đồng bộ
    hspi3.Init.BaudRatePrescaler = SPI_BAUDRATEPRESCALER_256; // Chia tần số để có tốc độ truyền thấp (84 MHz / 256 = 328 kHz)
    hspi3.Init.FirstBit = SPI_FIRSTBIT_MSB; // Truyền bit cao trước
    hspi3.Init.TIMode = SPI_TIMODE_DISABLE; // Không sử dụng chế độ TI
    hspi3.Init.CRCCalculation = SPI_CRCCALCULATION_DISABLE; // Không sử dụng CRC
    hspi3.Init.CRCPolynomial = 7; // Giá trị đa thức CRC (không sử dụng nên đặt mặc định)
    HAL_SPI_Init(&hspi3); // Khởi tạo SPI3

    // Cấu hình DMA cho SPI3 TX (DMA2 Stream 5 Channel 0)
    // Sẽ khởi tạo sau khi cần gửi dữ liệu để tối ưu hóa bộ nhớ và tránh lãng phí tài nguyên khi không cần thiết
    __HAL_RCC_DMA2_CLK_ENABLE(); // Bật clock cho DMA2

    // 5. Cấu hình ngắt EXTI cho các chân TRIGGER_COMMAND, READ_DATA_COMPLETE và RESET
    HAL_NVIC_SetPriority(EXTI1_IRQn, 3, 0); // Thiết lập độ ưu tiên cho ngắt EXTI0 (READ_DATA_COMPLETE)
    HAL_NVIC_EnableIRQ(EXTI1_IRQn); // Kích hoạt ngắt EXTI1
    HAL_NVIC_SetPriority(EXTI2_IRQn, 3, 0); // Thiết lập độ ưu tiên cho ngắt EXTI2 (TRIGGER_COMMAND)
    HAL_NVIC_EnableIRQ(EXTI2_IRQn); // Kích hoạt ngắt EXTI2
    HAL_NVIC_SetPriority(EXTI4_IRQn, 3, 0); // Thiết lập độ ưu tiên cho ngắt EXTI4 (RESET)
    HAL_NVIC_EnableIRQ(EXTI4_IRQn); // Kích hoạt ngắt EXTI4

}

// ------------ Callback TIM2 Input Capture 4 kênh ------------
void TDOA_CaptureCallback(TIM_HandleTypeDef *htim, uint32_t channel) {
    if (htim->Instance != TIM2) return; // Chỉ xử lý ngắt từ TIM2
    if (!capture_enable) return; // Nếu chưa cho phép bắt tín hiệu thì bỏ qua

    uint8_t idx;
    switch (channel) {
        case TIM_CHANNEL_1: idx = 0; break;
        case TIM_CHANNEL_2: idx = 1; break;
        case TIM_CHANNEL_3: idx = 2; break;
        case TIM_CHANNEL_4: idx = 3; break;
        default: return; // Kênh không hợp lệ, bỏ qua
    }

    if (!capture_flag[idx]) { // Nếu chưa bắt tín hiệu cho kênh này
        timestamp[idx] = HAL_TIM_ReadCapturedValue(htim, channel); // Đọc giá trị thời gian bắt được
        capture_flag[idx] = 1; // Đánh dấu đã bắt tín hiệu cho kênh này
        debug_printf("Captured on channel %d: %lu\n", idx, timestamp[idx]); // In ra thời gian bắt được

        // kiểm tra đủ 4 kênh
        if (capture_flags[0] && capture_flag[1] && capture_flag[2] && capture_flag[3]) {
           Disable_Capture(); // Tắt bắt tín hiệu để tránh ghi đè dữ liệu
           TDOA_PrepareDataAndSend(); // Chuẩn bị dữ liệu và gửi đi
        }
    }
}
// ------------ Đóng gói dữ liệu và gửi đi qua SPI ------------
void TDOA_PrepareDataAndSend(void) {
    // Đóng gói: ID 'A' , 'B', 'C', 'D' + 4 timestamp (little-endian)
    for (int i = 0; i < 4; i++) {
        tx_packet[i * 5] = 'A' + i; // ID
        tx_packet[i * 5 + 1] = timestamp[i] & 0xFF; // Byte thấp
        tx_packet[i * 5 + 2] = (timestamp[i] >> 8) & 0xFF;
        tx_packet[i * 5 + 3] = (timestamp[i] >> 16) & 0xFF;
        tx_packet[i * 5 + 4] = (timestamp[i] >> 24) & 0xFF; // Byte cao
    }

    // kéo DATA_READY lên cao để báo dữ liệu đã sẵn sàng
    HAL_GPIO_WritePin(DATA_READY_GPIO_PORT, DATA_READY_PIN, GPIO_PIN_SET);
    data_ready = 1; // Đặt cờ báo dữ liệu đã sẵn sàng

    debug_printf("Data ready, waiting for master read ... \n");

    // cấu hình DMA TX cho SPI3
    HAL_SPI_Transmit_DMA(&hspi3, (uint8_t*)tx_packet, PACKET_SIZE); // Bắt đầu truyền dữ liệu qua SPI3 bằng DMA 
    // chú ý: DMA sẽ tự động gửi khi master bắt đầu bật clock
    // không cần callback hoàn tất vì RDC sẽ kích hoạt chân READ_DATA_COMPLETE khi master đọc xong, lúc đó sẽ reset lại cờ data_ready và kéo DATA_READY xuống thấp

}

// ------------ Nhận RDC (READ_DATA_COMPLETE) ------------
void TDOA_DataComplete(void) {
    if (!data_ready) return; // Nếu dữ liệu chưa sẵn sàng thì bỏ qua

    // xóa cờ data_ready
    data_ready = 0;  // kéo DATA_READY xuống thấp để báo đã hoàn thành việc đọc dữ liệu
    HAL_GPIO_WritePin(DATA_READY_GPIO_PORT, DATA_READY_PIN, GPIO_PIN_RESET);
    
    // tăng số lần capture thành công
    capture_count++;
    debug_printf("Data read complete, total captures: %d\n", capture_count);

    if (capture_count >= MAX_CAPTURE_ROUNDS) {
        // Đủ 3 lần , dừng capture cho đến khi nhận lệnh RESET
        capture_enable = 0; // Tắt bắt tín hiệu
        delay_done = 0; // Reset cờ báo đã hoàn thành độ trễ
        debug_printf("All 3 round complete, waiting for reset ... \n");
    } else {
        // chưa đủ 3 lần , xóa capture và sẵn sàng capture tiếp nếu TC vânc đang ở mức cao
        Clear_Captures(); // Xóa dữ liệu capture cũ
        // nếu điều kiện TC vẫn đang ở mức cao và delay đã xong thì cho phép bắt tín hiệu tiếp
        if (tc_high && delay_done) {
            Enable_Capture(); // Cho phép bắt tín hiệu tiếp
            debug_printf("Restart for next capture round ... \n");
        }
    }

}

// ------------ TRIGGER COMMAND Callback ------------
void TDOA_triggerHigh(void) {
    tc_high = 1; // cập nhật trạng thái cao của tín hiệu TRIGGER_COMMAND
    debug_printf("Trigger command HIGH received \n");
    if (capture_count < MAX_CAPTURE_ROUNDS && !delay_done) {
        // bắt đầu đếm ngược 2s để cho phép bắt tín hiệu tiếp sau khi nhận lệnh TRIGGER_COMMAND
        __HAL_TIM_SET_COUNTER(&htim3, 0); // Reset bộ đếm của TIM3
        HAL_TIM_Base_Start_IT(&htim3); // Bắt đầu đếm ngược với ngắt
        debug_printf("Starting 2s delay for next capture round ... \n");
    }
}

// ------------ TRIGGER COMMAND Callback khi nhận tín hiệu thấp ------------
void TDOA_triggerLow(void) {
    tc_high = 0; // cập nhật trạng thái thấp của tín hiệu TRIGGER_COMMAND
    debug_printf("Trigger command LOW received \n");
    if (capture_enabled) {
        // Nếu đang cho phép bắt tín hiệu thì tạm thời tắt để tránh ghi đè dữ liệu khi nhận lệnh TRIGGER_COMMAND thấp
        Disable_Capture(); // Tắt bắt tín hiệu
        Clear_Captures(); // Xóa dữ liệu capture cũ
        data_ready = 0; // Reset cờ dữ liệu đã sẵn sàng
        HAL_GPIO_WritePin(DATA_READY_GPIO_PORT, DATA_READY_PIN, GPIO_PIN_RESET); // Kéo DATA_READY xuống thấp
        debug_printf("Capture aborted!! ... \n");
    }
    // nếu đang delay mà nhận lệnh TRIGGER_COMMAND thấp thì hủy delay và reset cờ đã hoàn thành độ trễ để chuẩn bị cho lần bắt tín hiệu tiếp theo
    if (HAL_TIM_Base_GetState(&htim3) == HAL_TIM_STATE_BUSY) {
        HAL_TIM_Base_Stop_IT(&htim3); // Dừng đếm ngược nếu đang trong quá trình delay
        delay_done = 0; // Reset cờ đã hoàn thành độ trễ
        debug_printf("Delay aborted!! ... \n");
    }
}
// ------------ RESET Callback ------------
void TDOA_Reset(void) {
    // reset tất cả về trạng thái ban đầu chuẩn bị cho chuôic bắt tín hiệu tiếp theo
    debug_printf("RESET triggered");
    capture_count = 0; // Reset số lần bắt tín hiệu thành công
    delay_done = 0; // Reset cờ đã hoàn thành độ trễ
    Disable_Capture(); // Tắt bắt tín hiệu
    Clear_Captures(); // Xóa dữ liệu capture cũ
    data_ready = 0; // Reset cờ dữ liệu đã sẵn sàng
    HAL_GPIO_WritePin(DATA_READY_GPIO_PORT, DATA_READY_PIN, GPIO_PIN_RESET); // Kéo DATA_READY xuống thấp
    // nếu TC đang ở mức cao thì tự động bắt đầu chuỗi bắt tín hiệu sau khi delay 2s
    if (tc_high) {
        TDOA_triggerHigh(); // Gọi lại hàm xử lý khi nhận lệnh TRIGGER_COMMAND cao để bắt đầu chuỗi bắt tín hiệu tiếp theo
    }
}
// ------------ Các hàm hỗ trợ nội bộ ------------
static void Enable_Capture(void) {
    capture_enable = 1; // Cho phép bắt tín hiệu
    debug_printf("Capture enabled \n");
}
static void Disable_Capture(void) {
    capture_enable = 0; // Tắt bắt tín hiệu
    debug_printf("Capture disabled \n");
}
static void Clear_Captures(void) {
    for (int i = 0; i < 4; i++) {
        capture_flag[i] = 0; // Reset cờ báo đã bắt tín hiệu cho mỗi kênh
        timestamp[i] = 0; // Reset thời gian bắt được cho mỗi kênh
    }
    debug_printf("Capture data cleared \n");
}
// ------------ Interrupt handler cho TIM3 (delay 2s) ------------
void TIM3_IRQHandler(void) {
    HAL_TIM_IRQHandler(&htim3); // Xử lý ngắt của TIM3
}
void HAL_TIM_PeriodElapsedCallback(TIM_HandleTypeDef *htim) {
    if (htim->Instance == TIM3) { // Kiểm tra nếu ngắt từ TIM3
        HAL_TIM_Base_Stop_IT(&htim3); // Dừng đếm ngược sau khi hoàn thành
        delay_done = 1; // Đặt cờ báo đã hoàn thành độ trễ
        debug_printf("2s delay completed \n");
        // nếu TC vẫn đang ở mức cao thì cho phép bắt tín hiệu tiếp
        if (tc_high && capture_count < MAX_CAPTURE_ROUNDS) {
            Enable_Capture(); // Cho phép bắt tín hiệu tiếp
            debug_printf("Restart for next capture round ... \n");
        }
    }
}
// ------------ Interrupt handler cho EXTI (TRIGGER_COMMAND, READ_DATA_COMPLETE, RESET) ------------
void EXTI1_IRQHandler(void) {
    HAL_GPIO_EXTI_IRQHandler(READ_DATA_COMPLETE_PIN); // Xử lý ngắt từ chân READ_DATA_COMPLETE
}
void EXTI2_IRQHandler(void) {
    HAL_GPIO_EXTI_IRQHandler(TRIGGER_COMMAND_PIN); // Xử lý ngắt từ chân TRIGGER_COMMAND
}
void EXTI4_IRQHandler(void) {
    HAL_GPIO_EXTI_IRQHandler(RESET_PIN); // Xử lý ngắt từ chân RESET
}
// ------------ Callback xử lý ngắt EXTI ------------
void HAL_GPIO_EXTI_Callback(uint16_t GPIO_Pin) {
    switch (GPIO_Pin) {
        case TRIGGER_COMMAND_PIN:
            if (HAL_GPIO_ReadPin(TRIGGER_COMMAND_GPIO_PORT, TRIGGER_COMMAND_PIN) == GPIO_PIN_SET) {
                TDOA_triggerHigh(); // Xử lý khi nhận lệnh TRIGGER_COMMAND cao
            } else {
                TDOA_triggerLow(); // Xử lý khi nhận lệnh TRIGGER_COMMAND thấp
            }
            break;
        case READ_DATA_COMPLETE_PIN:
            TDOA_DataComplete(); // Xử lý khi nhận tín hiệu đã hoàn thành việc đọc dữ liệu
            break;
        case RESET_PIN:
            TDOA_Reset(); // Xử lý khi nhận lệnh reset
            break;
        default:
            break; // Chân không hợp lệ, bỏ qua
    }
}
// ------------ Override weak TIM2 IRQ ------------
void TIM2_IRQHandler(void) {
    HAL_TIM_IRQHandler(&htim2); // Xử lý ngắt của TIM2
}
void HAL_TIM_IC_CaptureCallback(TIM_HandleTypeDef *htim) {
    if (htim->Instance == TIM2) {
        // Xác định kênh active và gọi TDOA_CaptureCallback
        uint32_t ch = 0;
        if (__HAL_TIM_GET_FLAG(htim, TIM_FLAG_CC1) != RESET) {
            __HAL_TIM_CLEAR_FLAG(htim, TIM_FLAG_CC1);
            ch = TIM_CHANNEL_1;
        } else if (__HAL_TIM_GET_FLAG(htim, TIM_FLAG_CC2) != RESET) {
            __HAL_TIM_CLEAR_FLAG(htim, TIM_FLAG_CC2);
            ch = TIM_CHANNEL_2;
        } else if (__HAL_TIM_GET_FLAG(htim, TIM_FLAG_CC3) != RESET) {
            __HAL_TIM_CLEAR_FLAG(htim, TIM_FLAG_CC3);
            ch = TIM_CHANNEL_3;
        } else if (__HAL_TIM_GET_FLAG(htim, TIM_FLAG_CC4) != RESET) {
            __HAL_TIM_CLEAR_FLAG(htim, TIM_FLAG_CC4);
            ch = TIM_CHANNEL_4;
        }
        if (ch != 0) {
            TDOA_CaptureCallback(htim, ch);
        }
    }
}
