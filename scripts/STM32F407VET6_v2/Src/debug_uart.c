#include "../Inc/debug_uart.h"
#include <stdio.h>
#include <stdarg.h>

UART_HandleTypeDef huart1;
static char debug_buffer[128];

void Debug_UART_Init(void) {
    __HAL_RCC_USART1_CLK_ENABLE();
    __HAL_RCC_GPIOA_CLK_ENABLE();

    GPIO_InitTypeDef gpio = {0};
    // PA9 -> USART1_TX, PA10 -> USART1_RX , chỉ dung PA9 TX làm debug
    gpio.Pin = GPIO_PIN_9;
    gpio.Mode = GPIO_MODE_AF_PP;
    gpio.Pull = GPIO_PULLUP;
    gpio.Speed = GPIO_SPEED_FREQ_HIGH;
    gpio.Alternate = GPIO_AF7_USART1;
    HAL_GPIO_Init(GPIOA, &gpio);

    huart1.Instance = USART1;
    huart1.Init.BaudRate = 115200;
    huart1.Init.WordLength = UART_WORDLENGTH_8B;
    huart1.Init.StopBits = UART_STOPBITS_1;
    huart1.Init.Parity = UART_PARITY_NONE;
    huart1.Init.Mode = UART_MODE_TX;
    huart1.Init.HwFlowCtl = UART_HWCONTROL_NONE;
    huart1.Init.OverSampling = UART_OVERSAMPLING_16;
    HAL_UART_Init(&huart1);
    if (HAL_UART_Init(&huart1) != HAL_OK) {
        // Handle initialization error
    }
}
// hàm printf cho debug qua UART
void Debug_UART_Printf(const char *format, ...) {
    va_list args;
    va_start(args, format);
    vsnprintf(debug_buffer, sizeof(debug_buffer), format, args);
    va_end(args);
    if (len > 0) {
        Debug_UART_Send(debug_buffer, len);
        HAL_UART_Transmit(&huart1, (uint8_t*)buffer, len, 100);
    }
}