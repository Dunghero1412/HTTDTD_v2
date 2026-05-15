#ifndef __TDOA_CAPTURE_H
#define __TDOA_CAPTURE_H

#include "stm32f4xx_hal.h"

// số lần capture thành công tối đa trước khi RESET
#define MAX_CAPTURE_ROUNDS 3

// kích thước gói dữ liệu : 4 kênh mỗi kênh 1 byte ID + 4 byte timestamp = 5 byte --> 20 byte
#define PACKET_SIZE 20

// các trạng thái ngoài

extern volatile uint8_t data_ready; // cờ báo dữ liệu đã sẵn sàng để gửi.
extern volatile uint8_t capture_enabled; // cờ báo đã bật chế độ capture.
extern volatile uint8_t cpture_count; // biến đếm số lần capture thành công
extern volatile uint32_t timestamps[4]; // mảng lưu trữ timestamp của 4 kênh
extern volatile uint8_t capture_flag; // cờ đã capture của từng kênh, mỗi bit tương ứng với một kênh

// hàm khởi tạo toàn bộ hệ thống capture
void TDOA_Init(void);

// callback cho timmer input capture (gọi trong HAL_TIM_IC_CaptureCallback)
void TDOA_CaptureCallback(TIM_HandleTypeDef *htim, uint32_t channel);

// hàm đóng gói và kích hoạt gửi qua SPI
void TDOA_PrepareDataAndSend(void);

// hàm xử lý khi nhận được READ_DATA_COMPLETE (RDC) từ RPI
void TDOA_DataComplete(void);

// hàm xử lý khi nhận được TRIGGER_COMMAND (TC) cạnh lên từ RPI
void TDOA_TriggerHigh(void);

// hàm xử lý khi nhận được TRIGGER_COMMAND (TC) cạnh xuống từ RPI
void TDOA_TriggerLow(void);

// hàm xử lý RESET
void TDOA_Reset(void);

#endif /* __TDOA_CAPTURE_H */

