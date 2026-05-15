#ifndef __DEBUG_UART_H
#define __DEBUG_UART_H

#include "stm32f4xx_hal.h"

void Debug_UART_Init(void);
void debug_printf(const char *fmt, ...);

#endif /* __DEBUG_UART_H */
