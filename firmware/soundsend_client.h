#ifndef SOUNDSEND_CLIENT_H
#define SOUNDSEND_CLIENT_H
#include <stdint.h>

/* Protocol component for an eventual U1 firmware. No radio driver is included.
 * Call from one application-loop context, never concurrently from an IRQ.
 * The integration must connect to the verified SoundSend before ss_connected().
 */
enum ss_state {
    SS_DISCONNECTED, SS_SERVICE, SS_CHARACTERISTIC, SS_DESCRIPTOR,
    SS_SUBSCRIBE, SS_INITIAL_VOLUME, SS_READY, SS_VOLUME_ACK, SS_ERROR
};

struct ss_client {
    uint16_t service_start, service_end, value_handle, cccd_handle, cursor;
    uint16_t elapsed_ms;
    int16_t deferred_delta;
    uint8_t state, known, volume, desired, sent, tx_len;
    uint8_t tx[23];
    uint8_t header[3], header_used, payload_used, volume_byte;
};

unsigned ss_context_size(void);
void ss_init(struct ss_client *client);
void ss_connected(struct ss_client *client);
void ss_disconnected(struct ss_client *client);
unsigned ss_take_tx(struct ss_client *client, uint8_t *output, unsigned capacity);
void ss_receive_att(struct ss_client *client, const uint8_t *packet, unsigned length);
void ss_key_delta(struct ss_client *client, int delta);
/* Absolute volume only after an actual value was read from the peer. */
void ss_set_volume(struct ss_client *client, unsigned volume);
void ss_tick(struct ss_client *client, unsigned elapsed_ms);
unsigned ss_status(const struct ss_client *client);
int ss_volume(const struct ss_client *client);
#endif
