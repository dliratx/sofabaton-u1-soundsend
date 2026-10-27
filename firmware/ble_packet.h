#ifndef SS_BLE_PACKET_H
#define SS_BLE_PACKET_H
#include <stdint.h>
/* Return CRC in wire-byte order, suitable for little-endian byte stores. */
uint32_t ble_crc24(const uint8_t *p,unsigned n,uint32_t init);
unsigned ble_whiten(uint8_t *p,unsigned n,unsigned channel);
/* Legacy PDU header/length/payload/CRC, whitened; excludes preamble/address. */
unsigned ble_encode_packet(unsigned header,const uint8_t *payload,unsigned n,
                           uint32_t crc_init,unsigned channel,uint8_t *out,unsigned capacity);
#endif
