/* Append to motor_arduino_rpc.c. Finite CURRENT-relative position only.
 * op0 stop, op1 enable, op2 position. No velocity or configuration writes.
 */
int motor_position_exchange(unsigned int address, unsigned int operation,
                            unsigned int direction, unsigned int pulses,
                            unsigned int rpm, unsigned char *response,
                            unsigned int capacity, unsigned int timeout_ms) {
    unsigned char command[13] = {0};
    unsigned int length, ticks;
    last_received=last_sent=last_status=0;
    if (motor_device<0) return -1;
    if (address<1 || address>255 || operation>2 || direction>1 || pulses>1024 ||
        rpm<1 || rpm>3 || capacity!=64 || timeout_ms<50 || timeout_ms>1000) return -2;
    command[0]=address;
    if (operation==0) {
        command[1]=0xFE;command[2]=0x98;command[4]=0x6B;length=5;
    } else if (operation==1) {
        command[1]=0xF3;command[2]=0xAB;command[3]=1;command[5]=0x6B;length=6;
    } else {
        if (pulses==0) return -2;
        command[1]=0xFD;command[2]=direction;command[4]=rpm;command[5]=10;
        command[6]=(pulses>>24)&255;command[7]=(pulses>>16)&255;
        command[8]=(pulses>>8)&255;command[9]=pulses&255;
        command[10]=2; /* relative current actual position */
        command[12]=0x6B;length=13;
    }
    XUartLite_ResetFifos(&xuart_ptr[motor_device]);
    ticks=timeout_ms*20;
    while(ticks-- > 0) {
        if(last_sent<length)
            last_sent+=XUartLite_Send(&xuart_ptr[motor_device],command+last_sent,length-last_sent);
        last_status=XUartLite_GetStatusReg(xuart_ptr[motor_device].RegBaseAddress);
        if(last_status & (XUL_SR_OVERRUN_ERROR|XUL_SR_FRAMING_ERROR|XUL_SR_PARITY_ERROR))return -8;
        if(last_received<capacity)
            last_received+=XUartLite_Recv(&xuart_ptr[motor_device],response+last_received,capacity-last_received);
        delay_us(50);
    }
    if(last_sent!=length || !(last_status & XUL_SR_TX_FIFO_EMPTY))return -9;
    return last_received;
}
