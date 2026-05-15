#include "../Inc/stm32f4xx_hal.h"
#include "../Inc/tdoa_capture.h"
#include "../Inc/debug_uart.h"

// khai báo TIM Handle toàn cục
// extern TIM_HandleTypeDef htim1; - bỏ
extern TIM_HandleTypeDef htim2;
extern TIM_HandleTypeDef htim3;
extern SPI_HandleTypeDef hspi3;

void SystemClock_Config(void);
void Error_Handler(void);

int main(void)
{
  // Khởi tạo HAL Library
  HAL_Init();

  // Cấu hình hệ thống clock
  SystemClock_Config();

  // main không cần làm gì , việc xử lý ngắt đã do tdoa_capture.c đảm nhiệm
    while (1)
    {
        // Vòng lặp chính có thể để trống hoặc thực hiện các tác vụ khác nếu cần
        //thêm watchdog hoặc chế độ sleep
        HAL_Delay(1000); // Delay 1 giây để giảm tải CPU, có thể điều chỉnh hoặc loại bỏ nếu không cần thiết
        debug_printf("Main   loop is running...\n"); // In thông báo để kiểm tra main loop
    }
  
}
// cấu hình clock măc định 168MHz
void SystemClock_Config(void) {
    RCC_OscInitTypeDef RCC_OscInitStruct = {0};
    RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};
    RCC_PeriphCLKInitTypeDef PeriphClkInitStruct = {0};

    __HAL_RCC_PWR_CLK_ENABLE();
    __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE1);

    RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSE;
    RCC_OscInitStruct.HSEState = RCC_HSE_ON;
    RCC_OscInitStruct.PLL.PLLState = RCC_PLL_ON;
    RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_HSE;
    RCC_OscInitStruct.PLL.PLLM = 8;
    RCC_OscInitStruct.PLL.PLLN = 336;
    RCC_OscInitStruct.PLL.PLLP = RCC_PLLP_DIV2;
    RCC_OscInitStruct.PLL.PLLQ = 7;
    if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK) Error_Handler();

    RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK | RCC_CLOCKTYPE_SYSCLK |
                                  RCC_CLOCKTYPE_PCLK1 | RCC_CLOCKTYPE_PCLK2;
    RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
    RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
    RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV4;   // APB1 = 42 MHz (timer 84 MHz)
    RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV2;
    if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_5) != HAL_OK) Error_Handler();

    PeriphClkInitStruct.PeriphClockSelection = RCC_PERIPHCLK_USART1 | RCC_PERIPHCLK_SPI3;
    PeriphClkInitStruct.Usart1ClockSelection = RCC_USART1CLKSOURCE_PCLK2;
    PeriphClkInitStruct.Spi3ClockSelection = RCC_SPI3CLKSOURCE_PCLK1;
    if (HAL_RCCEx_PeriphCLKConfig(&PeriphClkInitStruct) != HAL_OK) Error_Handler();
}

void Error_Handler(void) {
    while(1) {}
}