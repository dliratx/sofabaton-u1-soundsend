#ifndef SS_BLE_CENTRAL_H
#define SS_BLE_CENTRAL_H
#include <stdint.h>
#include "soundsend_client.h"

/* Minimal legacy LE 1M / CSA#1 / unencrypted central for a single known peer.
 * Clock unit: 16 MHz timer ticks, wrapping uint32_t. No HCI/OS is required.
 * The radio backend supplies CRC-checked PDUs and precise end-of-packet times.
 */
enum central_state { BC_OFF, BC_SCANNING, BC_CONNECT_TX, BC_BETWEEN, BC_DATA_TX, BC_DATA_RX, BC_FAILED };
enum radio_action_kind { RA_NONE, RA_RECEIVE, RA_CONNECT_TX, RA_DATA_TX, RA_IDLE };
struct radio_action {
    uint32_t at, deadline, access_address, crc_init;
    uint8_t kind, channel, header, length;
    uint8_t payload[34];
};
struct ble_central {
    struct ss_client client;
    struct radio_action action;
    uint32_t access_address, crc_init, next_anchor, last_rx, last_poll;
    uint32_t scan_deadline, scan_hop, rx_deadline, tick_remainder, close_deadline;
    uint16_t events, retransmissions, duplicates;
    int16_t early_delta;
    uint8_t state, reason, own_type, peer_type, own[6], peer[6];
    uint8_t channel, unmapped, hop, tx_sn, rx_next, outstanding, linked;
    uint8_t tx_llid, tx_length, tx_payload[27];
    uint8_t control[4][9], control_length[4], control_head, control_count;
    uint8_t queued_l2cap[27], queued_length;
    uint8_t reassembly[64], reassembly_used, reassembly_total;
    uint16_t reassembly_cid;
    uint8_t version_sent, action_pending, closing, termination_inflight;
};

unsigned bc_context_size(void);
void bc_init(struct ble_central *c);
int bc_start(struct ble_central *c,const uint8_t own_le[6],unsigned own_type,
             const uint8_t peer_le[6],unsigned peer_type,uint32_t physical_seed,uint32_t now);
void bc_stop(struct ble_central *c,uint32_t now);
void bc_disconnect(struct ble_central *c,uint32_t now);
void bc_poll(struct ble_central *c,uint32_t now);
void bc_rx(struct ble_central *c,unsigned header,const uint8_t *payload,unsigned length,
           uint32_t end_ticks,unsigned crc_valid);
/* Raw radio has already encoded CONNECT before RX. Leaves action.payload
 * untouched; only use with that validated cached packet, not native TX. */
void bc_rx_cached_advertisement(struct ble_central *c,unsigned header,const uint8_t *payload,
                                unsigned length,uint32_t end_ticks,unsigned crc_valid);
void bc_tx_done(struct ble_central *c,uint32_t end_ticks);
unsigned bc_take_action(struct ble_central *c,struct radio_action *out,unsigned capacity);
/* Foreground-only borrowed action, consumed once. Valid until next mutation. */
const struct radio_action *bc_take_action_ref(struct ble_central *c);
void bc_key_delta(struct ble_central *c,int delta);
unsigned bc_status(const struct ble_central *c);
int bc_volume(const struct ble_central *c);
unsigned bc_valid_access_address(uint32_t value);
/* Connection payload is deterministic after bc_start; does not initiate TX. */
unsigned bc_connect_payload(const struct ble_central *c,uint8_t payload[34]);
#endif
