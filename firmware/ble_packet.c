/* Bluetooth Core Vol6 PartB3.1.1/3.2. Pure packet construction, no MMIO.
 * Primary CRC/whitening vectors: Core Vol6 PartC2.1,4.1,4.2,4.3.1.
 */
#include "ble_packet.h"
#include "ble_packet_tables.h"
uint32_t ble_crc24(const uint8_t *p,unsigned n,uint32_t init) {
    uint32_t s=((uint32_t)reverse_table[init&255]<<16)|
               ((uint32_t)reverse_table[(init>>8)&255]<<8)|reverse_table[(init>>16)&255];
    unsigned i;
    for(i=0;i<n;i++) s=(s>>8)^crc_table[(s^p[i])&255];
    return s;
}
unsigned ble_whiten(uint8_t *p,unsigned n,unsigned channel) {
    unsigned i,s=channel|64;
    if(channel>39) return 0;
    for(i=0;i<n;i++) {unsigned v=white_table[s];p[i]^=(uint8_t)v;s=v>>8;}
    return 1;
}
unsigned ble_encode_packet(unsigned header,const uint8_t *payload,unsigned n,
                           uint32_t init,unsigned channel,uint8_t *out,unsigned capacity) {
    unsigned i;uint32_t crc;
    if(n>34 || channel>39 || capacity<n+5 || !out || (n && !payload)) return 0;
    out[0]=(uint8_t)header;out[1]=(uint8_t)n;
    for(i=0;i<n;i++) out[i+2]=payload[i];
    crc=ble_crc24(out,n+2,init);
    for(i=0;i<3;i++) {out[n+2+i]=(uint8_t)crc;crc>>=8;}
    ble_whiten(out,n+5,channel);return n+5;
}
