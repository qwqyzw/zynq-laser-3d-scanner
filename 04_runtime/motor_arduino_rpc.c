/* Own bounded read-query transport for the PYNQ Arduino MicroBlaze.
 * Uses the board BSP's nonblocking UARTLite send/receive calls.
 * There are no motor enable, movement, homing or configuration-write commands.
 */
#include <uart.h>
#include <timer.h>
#include <xuartlite.h>
#include <xuartlite_l.h>

extern XUartLite *xuart_ptr;
static int motor_device = -1;
static unsigned int last_received = 0;
static unsigned int last_sent = 0;
static unsigned int last_status = 0;

int motor_uart_init(void) {
    if (uart_get_num_devices() != 1) return -1;
    motor_device = uart_open(1, 0); /* Arduino D1 TX, D0 RX */
    if (motor_device < 0) return -1;
    XUartLite_DisableInterrupt(&xuart_ptr[motor_device]);
    XUartLite_ResetFifos(&xuart_ptr[motor_device]);
    return motor_device;
}

unsigned int motor_last_rx_length(void) { return last_received; }
unsigned int motor_last_tx_length(void) { return last_sent; }
unsigned int motor_last_status(void) { return last_status; }

/* Passive bounded capture for diagnosing idle-line noise; sends no bytes. */
int motor_passive_receive(unsigned char *response, unsigned int capacity,
                          unsigned int timeout_ms) {
    unsigned int ticks;
    last_received = last_sent = last_status = 0;
    if (motor_device < 0) return -1;
    if (capacity < 1 || capacity > 64 || timeout_ms < 10 || timeout_ms > 2000)
        return -2;
    ticks = timeout_ms * 20;
    while (ticks-- > 0) {
        last_status = XUartLite_GetStatusReg(xuart_ptr[motor_device].RegBaseAddress);
        if (last_status & (XUL_SR_OVERRUN_ERROR | XUL_SR_FRAMING_ERROR | XUL_SR_PARITY_ERROR))
            return -8;
        if (last_received < capacity)
            last_received += XUartLite_Recv(&xuart_ptr[motor_device],
                             response + last_received, capacity - last_received);
        if (last_received == capacity) return (int)last_received;
        delay_us(50);
    }
    return (int)last_received;
}

int motor_read_query(unsigned int address, unsigned int function,
                     unsigned char *response, unsigned int capacity,
                     unsigned int timeout_ms) {
    unsigned char command[4];
    unsigned int command_length = 3;
    unsigned int expected = 0;
    unsigned int ticks;
    last_received = last_sent = last_status = 0;
    if (motor_device < 0) return -1;
    if (address < 1 || address > 255 || capacity < 37 || capacity > 64 ||
        timeout_ms < 10 || timeout_ms > 2000) return -2;
    switch (function) {
        case 0x1F: expected = 7; break;
        case 0x24: case 0x31: expected = 5; break;
        case 0x36: expected = 8; break;
        case 0x3A: expected = 4; break;
        case 0x42: expected = 3; command_length = 4; break;
        default: return -2;
    }
    command[0] = (unsigned char)address;
    command[1] = (unsigned char)function;
    command[2] = command_length == 4 ? 0x6C : 0x6B;
    command[3] = 0x6B;
    XUartLite_ResetFifos(&xuart_ptr[motor_device]);
    ticks = timeout_ms * 20;
    while (ticks-- > 0) {
        if (last_sent < command_length)
            last_sent += XUartLite_Send(&xuart_ptr[motor_device],
                         command + last_sent, command_length - last_sent);
        last_status = XUartLite_GetStatusReg(xuart_ptr[motor_device].RegBaseAddress);
        if (last_status & (XUL_SR_OVERRUN_ERROR | XUL_SR_FRAMING_ERROR | XUL_SR_PARITY_ERROR))
            return -8;
        if (last_sent == command_length && last_received < expected)
            last_received += XUartLite_Recv(&xuart_ptr[motor_device],
                             response + last_received, expected - last_received);
        if (last_received > 0 && response[0] != address) return -4;
        if (last_received > 1 && response[1] != function) return -5;
        if (last_received >= 3) {
            if (response[2] == 0xE2 || response[2] == 0xEE) expected = 4;
            else if (function == 0x42) {
                if (response[2] != 33 && response[2] != 37) return -6;
                expected = response[2];
            }
        }
        if (last_received == expected) {
            if (response[expected - 1] != 0x6B) return -7;
            return (int)expected;
        }
        delay_us(50);
    }
    return -3;
}
