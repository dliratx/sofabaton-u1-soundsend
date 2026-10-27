#include "soundsend_client.h"

/* ATT encodes 128-bit UUIDs least-significant byte first. */
static const uint8_t service_uuid[16] = {
    0xdd,0x0e,0xaa,0x2d,0x1d,0x7e,0x96,0xba,0xab,0x48,0x64,0xb5,0x4c,0xd9,0xde,0x97
};
static const uint8_t control_uuid[16] = {
    0x5e,0xcf,0x5a,0x11,0x20,0xf1,0xfb,0xa0,0x4c,0x40,0xd0,0xb8,0x42,0xd4,0xa8,0xa4
};

static uint16_t le16(const uint8_t *p) { return p[0] | ((uint16_t)p[1] << 8); }
static void put16(uint8_t *p, uint16_t v) { p[0]=(uint8_t)v; p[1]=(uint8_t)(v>>8); }
static uint8_t clamp_volume(int v) { return v<0 ? 0 : v>100 ? 100 : (uint8_t)v; }
static void fail(struct ss_client *c) { c->state=SS_ERROR; c->known=0; c->tx_len=0; }
static int uuid_equal(const uint8_t *a,const uint8_t *b) {
    unsigned i; for(i=0;i<16;i++) if(a[i]!=b[i]) return 0; return 1;
}
static int queue(struct ss_client *c, unsigned state, const uint8_t *p, unsigned n) {
    unsigned i;
    if(c->tx_len || n>sizeof(c->tx)) { fail(c); return 0; }
    for(i=0;i<n;i++) c->tx[i]=p[i];
    c->tx_len=(uint8_t)n; c->state=(uint8_t)state; c->elapsed_ms=0; return 1;
}
static void find_characteristic(struct ss_client *c) {
    uint8_t p[7]={0x08,0,0,0,0,0x03,0x28};
    put16(p+1,c->cursor); put16(p+3,c->service_end);
    queue(c,SS_CHARACTERISTIC,p,sizeof(p));
}
static void find_descriptor(struct ss_client *c) {
    uint8_t p[5]={0x04,0,0,0,0};
    put16(p+1,c->cursor); put16(p+3,c->service_end);
    queue(c,SS_DESCRIPTOR,p,sizeof(p));
}
static void send_volume(struct ss_client *c) {
    uint8_t p[7]={0x52,0,0,0x01,0x04,0x01,0};
    c->sent=c->desired; p[6]=c->sent; put16(p+1,c->value_handle);
    queue(c,SS_VOLUME_ACK,p,sizeof(p));
}
static void volume_received(struct ss_client *c, uint8_t v) {
    if(v>100) return;
    if(c->state==SS_INITIAL_VOLUME) {
        c->volume=v; c->known=1;
        c->desired=clamp_volume((int)v+c->deferred_delta); c->deferred_delta=0;
        c->state=SS_READY;
        if(c->desired!=v) send_volume(c);
    } else if(c->state==SS_VOLUME_ACK) {
        /* Delayed notifications must not undo queued button presses. */
        if(v!=c->sent) return;
        c->volume=v; c->state=SS_READY;
        if(c->desired!=v) send_volume(c);
    } else if(c->state==SS_READY) {
        c->volume=v; c->desired=v;
    }
}
static void notification_byte(struct ss_client *c, uint8_t b) {
    if(c->header_used<3) {
        c->header[c->header_used++]=b;
        if(c->header_used==3 && c->header[2]==0) c->header_used=0;
        return;
    }
    if(c->payload_used==0) c->volume_byte=b;
    c->payload_used++;
    if(c->payload_used==c->header[2]) {
        uint8_t volume_message=c->header[0]==2 && c->header[1]==4 && c->header[2]==1;
        c->header_used=0; c->payload_used=0;
        if(volume_message) volume_received(c,c->volume_byte);
    }
}

unsigned ss_context_size(void) { return sizeof(struct ss_client); }
unsigned ss_status(const struct ss_client *c) { return c->state; }
int ss_volume(const struct ss_client *c) { return c->known ? c->volume : -1; }
void ss_init(struct ss_client *c) {
    unsigned i; uint8_t *p=(uint8_t *)c;
    for(i=0;i<sizeof(*c);i++) p[i]=0;
}
void ss_disconnected(struct ss_client *c) { ss_init(c); }
void ss_connected(struct ss_client *c) {
    uint8_t p[23]={0x06,0x01,0x00,0xff,0xff,0x00,0x28}; unsigned i;
    ss_init(c);
    for(i=0;i<16;i++) p[7+i]=service_uuid[i];
    queue(c,SS_SERVICE,p,sizeof(p));
}
unsigned ss_take_tx(struct ss_client *c,uint8_t *out,unsigned capacity) {
    unsigned i,n=c->tx_len;
    if(capacity<n) return 0;
    for(i=0;i<n;i++) out[i]=c->tx[i];
    c->tx_len=0; return n;
}
void ss_key_delta(struct ss_client *c,int delta) {
    if(delta!=1 && delta!=-1) return;
    if(c->state==SS_DISCONNECTED || c->state==SS_ERROR) return;
    if(!c->known) {
        int n=c->deferred_delta+delta;
        c->deferred_delta=(int16_t)(n < -100 ? -100 : n>100 ? 100 : n);
        return;
    }
    c->desired=clamp_volume((int)c->desired+delta);
    if(c->state==SS_READY && c->desired!=c->volume) send_volume(c);
}
void ss_set_volume(struct ss_client *c,unsigned volume) {
    if(volume>100 || !c->known || (c->state!=SS_READY && c->state!=SS_VOLUME_ACK)) return;
    c->desired=(uint8_t)volume;
    if(c->state==SS_READY && c->desired!=c->volume) send_volume(c);
}
void ss_tick(struct ss_client *c,unsigned elapsed_ms) {
    if(c->state==SS_DISCONNECTED || c->state==SS_READY || c->state==SS_ERROR) return;
    if(elapsed_ms>=3000u-c->elapsed_ms) { fail(c); return; }
    c->elapsed_ms=(uint16_t)(c->elapsed_ms+elapsed_ms);
}
void ss_receive_att(struct ss_client *c,const uint8_t *p,unsigned n) {
    unsigned i,entry,end; uint16_t last=0;
    if(n==0 || c->state==SS_DISCONNECTED || c->state==SS_ERROR) return;
    /* No MTU exchange is issued: the default ATT MTU is 23. */
    if(n>23) {fail(c);return;}
    if(p[0]==0x1b) {
        if(n>=3 && (c->known || c->state==SS_INITIAL_VOLUME) && le16(p+1)==c->value_handle)
            for(i=3;i<n;i++) notification_byte(c,p[i]);
        return;
    }
    if(p[0]==0x01) { fail(c); return; }
    if(c->state==SS_SERVICE && p[0]==0x07) {
        /* This unit was verified to expose one matching control service. */
        if(n!=5 || !(c->service_start=le16(p+1)) ||
           (c->service_end=le16(p+3))<c->service_start) {fail(c);return;}
        c->cursor=c->service_start; find_characteristic(c); return;
    }
    if(c->state==SS_CHARACTERISTIC && p[0]==0x09) {
        if(n<2 || (p[1]!=7 && p[1]!=21)) {fail(c);return;}
        entry=p[1]; end=2;
        if((entry==21 && n!=23) || (entry==7 && n!=9 && n!=16 && n!=23)) {fail(c);return;}
        for(i=2;i+entry<=n;i+=entry) {
            uint16_t declaration=le16(p+i),value=le16(p+i+3);
            if(declaration<c->cursor || declaration<=last || value<=declaration ||
               value>c->service_end) {fail(c);return;}
            last=declaration; end=i+entry;
            if(entry==21 && uuid_equal(p+i+5,control_uuid)) {
                if((p[i+2]&0x14)!=0x14 || value==c->service_end) {fail(c);return;}
                c->value_handle=value; c->cursor=value+1; find_descriptor(c); return;
            }
        }
        if(end!=n || last<c->cursor || last>=c->service_end) {fail(c);return;}
        c->cursor=last+1; find_characteristic(c); return;
    }
    if(c->state==SS_DESCRIPTOR && p[0]==0x05) {
        if(n<2 || (p[1]!=1 && p[1]!=2)) {fail(c);return;}
        entry=p[1]==1 ? 4 : 18; end=2;
        if((entry==4 && (n<6 || ((n-2)&3)!=0)) || (entry==18 && n!=20)) {fail(c);return;}
        for(i=2;i+entry<=n;i+=entry) {
            uint16_t handle=le16(p+i);
            if(handle<c->cursor || handle<=last || handle>c->service_end) {fail(c);return;}
            last=handle; end=i+entry;
            if(entry==4 && le16(p+i+2)==0x2902) {
                uint8_t request[5]={0x12,0,0,0x01,0x00};
                c->cccd_handle=handle; put16(request+1,handle);
                queue(c,SS_SUBSCRIBE,request,sizeof(request)); return;
            }
        }
        if(end!=n || last<c->cursor || last>=c->service_end) {fail(c);return;}
        c->cursor=last+1; find_descriptor(c); return;
    }
    if(c->state==SS_SUBSCRIBE && p[0]==0x13) {
        uint8_t request[6]={0x52,0,0,0x00,0x04,0x00};
        if(n!=1) {fail(c);return;}
        put16(request+1,c->value_handle);
        queue(c,SS_INITIAL_VOLUME,request,sizeof(request));
    }
}
