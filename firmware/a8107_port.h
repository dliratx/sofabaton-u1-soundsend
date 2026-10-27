#ifndef SS_A8107_PORT_H
#define SS_A8107_PORT_H
#include "ble_central.h"
struct a8107_port {
    uint32_t saved_irq, saved_adc;
    /* Acquisition seed is consumed by bc_start before the first TX. */
    union { uint32_t seed, tx_fifo; } scratch;
    uint32_t clock_before, clock_after;
    uint32_t last_rf_status, last_rf_header, last_rf_control;
    uint16_t tx_packets, rx_packets, crc_errors, errors;
    uint16_t last_launch_status, last_tx_header;
    /* radio_options: low2 bits RX-clear mode; upper6 bits TX lead in us
     * (multiples of4). This keeps the retained SRAM/stack layout unchanged. */
    uint8_t active, rx_armed, error, tx_mode, receive_locked, receive_primask, clock_control, radio_options;
};
static inline unsigned ap_rx_clear_mode(const struct a8107_port *p) {return p->radio_options&3u;}
static inline unsigned ap_tx_lead_us(const struct a8107_port *p) {return p->radio_options&0xfcu;}
unsigned ap_legacy_state(void);
unsigned ap_configuration_connected(void);
unsigned ap_ir_busy(void);
void ap_request_idle(void);
int ap_claim(struct a8107_port *p,unsigned tx_mode);
void ap_release(struct a8107_port *p);
uint32_t ap_now(void);
/* Controlled raw-mode delay masks, restored after each TX. Not calibrated. */
uint32_t ap_raw_delay(unsigned mode,uint32_t original);
int ap_action(struct a8107_port *p,struct ble_central *central,const struct radio_action *a);
void ap_receive(struct a8107_port *p,struct ble_central *central);
/* Nonconnectable lab beacon on one advertising channel; no SoundSend query. */
int ap_beacon(struct a8107_port *p,const uint8_t own_le[6],unsigned channel);
#endif
