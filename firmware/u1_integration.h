#ifndef SS_U1_INTEGRATION_H
#define SS_U1_INTEGRATION_H
#include <stdint.h>
#define SS_LAB_REVISION 17
#define SS_LAB_BUILD_ID 0x544d5248u
void u1_runtime_init(void);
unsigned u1_key_gateway(unsigned profile,unsigned button);
unsigned u1_poll_gateway(unsigned a,unsigned b,unsigned c,unsigned d);
unsigned u1_poll_prepare(void);
void u1_screen_gateway(void);
void u1_boot_gateway(void) __attribute__((noreturn));
unsigned u1_debug_command(const uint8_t request[20],uint8_t response[20]);
unsigned u1_echo_gateway(uint8_t *data,const void *attribute,unsigned length);
#endif
