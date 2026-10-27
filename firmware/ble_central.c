#include "ble_central.h"

#define US(x) ((uint32_t)(x)*16u)
#define ADV_AA 0x8e89bed6u
#define INTERVAL US(30000)
#define SUPERVISION US(2000000)
#define TX_PREPARE US(1000)
static int reached(uint32_t now,uint32_t when) {return (int32_t)(now-when)>=0;}
static uint16_t get16(const uint8_t *p) {return p[0]|((uint16_t)p[1]<<8);}
static void put16(uint8_t *p,unsigned v) {p[0]=(uint8_t)v;p[1]=(uint8_t)(v>>8);}
static void put32(uint8_t *p,uint32_t v) {unsigned i;for(i=0;i<4;i++) {p[i]=(uint8_t)v;v>>=8;}}
static int equal6(const uint8_t *a,const uint8_t *b) {unsigned i;for(i=0;i<6;i++) if(a[i]!=b[i]) return 0;return 1;}
unsigned bc_connect_payload(const struct ble_central *c,uint8_t out[34]) {
    unsigned i;
    for(i=0;i<6;i++) {out[i]=c->own[i];out[i+6]=c->peer[i];}
    put32(out+12,c->access_address);out[16]=(uint8_t)c->crc_init;
    out[17]=(uint8_t)(c->crc_init>>8);out[18]=(uint8_t)(c->crc_init>>16);
    out[19]=2;put16(out+20,0);put16(out+22,24);put16(out+24,0);put16(out+26,200);
    out[28]=out[29]=out[30]=out[31]=0xff;out[32]=0x1f;out[33]=c->hop;
    return 5|(c->own_type<<6)|(c->peer_type<<7);
}
static uint32_t random_step(uint32_t *state) {
    uint32_t v=*state;v^=v<<13;v^=v>>17;v^=v<<5;*state=v;return v;
}
unsigned bc_valid_access_address(uint32_t v) {
    uint32_t difference=v^ADV_AA;unsigned i,run=1,transitions=0,top=0,last=v&1;
    if(!difference || !(difference&(difference-1))) return 0;
    if((uint8_t)v==(uint8_t)(v>>8) && (uint8_t)v==(uint8_t)(v>>16) && (uint8_t)v==(uint8_t)(v>>24)) return 0;
    for(i=1;i<32;i++) {
        unsigned bit=(v>>i)&1;
        if(bit==last) {if(++run>6) return 0;}
        else {run=1;transitions++;if(i>=27) top++;}
        last=bit;
    }
    return transitions<=24 && top>=2;
}
static void action(struct ble_central *c,unsigned kind,unsigned channel,uint32_t at) {
    c->action.kind=(uint8_t)kind;c->action.channel=(uint8_t)channel;
    c->action.at=at;c->action.deadline=at+US(2);
    c->action.access_address=kind==RA_RECEIVE || kind==RA_CONNECT_TX ? ADV_AA : c->access_address;
    c->action.crc_init=kind==RA_RECEIVE || kind==RA_CONNECT_TX ? 0x555555 : c->crc_init;
    c->action.header=0;c->action.length=0;c->action_pending=1;
}
static void fail(struct ble_central *c,unsigned reason,uint32_t now) {
    c->state=BC_FAILED;c->reason=(uint8_t)reason;
    ss_disconnected(&c->client);action(c,RA_IDLE,c->channel,now);
}
static int control_reply(struct ble_central *c,const uint8_t *p,unsigned n,uint32_t now) {
    unsigned i,index=c->control_head+c->control_count;
    if(index>=4) index-=4;
    if(c->control_count==4 || n>9) {fail(c,0x07,now);return 0;}
    for(i=0;i<n;i++) c->control[index][i]=p[i];
    c->control_length[index]=(uint8_t)n;c->control_count++;return 1;
}
static void l2cap_reply(struct ble_central *c,unsigned cid,const uint8_t *p,unsigned n,uint32_t now) {
    unsigned i;
    if(c->queued_length || n>23) {fail(c,0x07,now);return;}
    put16(c->queued_l2cap,n);put16(c->queued_l2cap+2,cid);
    for(i=0;i<n;i++) c->queued_l2cap[4+i]=p[i];
    c->queued_length=(uint8_t)(n+4);
}
static void deliver_l2cap(struct ble_central *c,uint32_t now) {
    uint8_t *p=c->reassembly;unsigned n=c->reassembly_total;
    if(c->reassembly_cid==4) ss_receive_att(&c->client,p,n);
    else if(c->reassembly_cid==5 && n>=4 && get16(p+2)==n-4 && p[1]) {
        uint8_t reply[6]={0x01,p[1],0x02,0x00,0x00,0x00};
        if(p[0]==0x12 && n==12) {reply[0]=0x13;reply[4]=0x01;}
        else if(p[0]&1) return;
        l2cap_reply(c,5,reply,sizeof(reply),now);
    } else if(c->reassembly_cid==6 && n==2 && p[0]==0x0b) {
        /* Security request: report pairing unsupported; never fake encryption. */
        const uint8_t failed[2]={0x05,0x05};l2cap_reply(c,6,failed,2,now);
    }
}
static void data_received(struct ble_central *c,unsigned llid,const uint8_t *p,unsigned n,uint32_t now) {
    unsigned i;
    if(llid==3) {
        if(!n) {fail(c,0x1e,now);return;}
        if(p[0]==0x02 && n==2) {fail(c,p[1],now);return;}
        if(p[0]==0x08 && n==9) {
            const uint8_t features[9]={0x09,0,0,0,0,0,0,0,0};
            control_reply(c,features,sizeof(features),now);
        } else if(p[0]==0x0c && n==6) {
            if(!c->version_sent) {
                /* Same version tuple as the original V32 responder at0x58e4. */
                const uint8_t version[6]={0x0c,0x06,0xc0,0x00,0x01,0x00};
                c->version_sent=1;control_reply(c,version,sizeof(version),now);
            }
        } else if(p[0]!=0x07 && p[0]!=0x09 && p[0]!=0x0c) {
            uint8_t unknown[2]={0x07,p[0]};control_reply(c,unknown,sizeof(unknown),now);
        }
        return;
    }
    if(!n) return;
    if(llid==2) {
        if(n<4 || get16(p)>sizeof(c->reassembly)) {fail(c,0x1e,now);return;}
        c->reassembly_total=(uint8_t)get16(p);c->reassembly_cid=get16(p+2);
        c->reassembly_used=0;p+=4;n-=4;
    } else if(llid!=1 || !c->reassembly_total) {fail(c,0x1e,now);return;}
    if(c->reassembly_used+n>c->reassembly_total) {fail(c,0x1e,now);return;}
    for(i=0;i<n;i++) c->reassembly[c->reassembly_used++]=p[i];
    if(c->reassembly_used==c->reassembly_total) {
        deliver_l2cap(c,now);c->reassembly_total=0;c->reassembly_used=0;
    }
}
static void prepare_data(struct ble_central *c) {
    unsigned i,n;
    if(c->outstanding) {c->retransmissions++;return;}
    c->tx_llid=1;c->tx_length=0;
    if(c->closing) {
        c->tx_llid=3;c->tx_length=2;c->tx_payload[0]=0x02;c->tx_payload[1]=0x13;
        c->termination_inflight=1;
    } else if(c->control_count) {
        c->tx_llid=3;c->tx_length=c->control_length[c->control_head];
        for(i=0;i<c->tx_length;i++) c->tx_payload[i]=c->control[c->control_head][i];
        if(++c->control_head==4) c->control_head=0;
        c->control_count--;
    } else if(c->queued_length) {
        c->tx_llid=2;c->tx_length=c->queued_length;c->queued_length=0;
        for(i=0;i<c->tx_length;i++) c->tx_payload[i]=c->queued_l2cap[i];
    } else if((n=ss_take_tx(&c->client,c->tx_payload+4,23))!=0) {
        put16(c->tx_payload,n);put16(c->tx_payload+2,4);
        c->tx_llid=2;c->tx_length=(uint8_t)(n+4);
    }
    c->outstanding=1;
}

unsigned bc_context_size(void) {return sizeof(struct ble_central);}
void bc_init(struct ble_central *c) {
    unsigned i;uint8_t *p=(uint8_t *)c;for(i=0;i<sizeof(*c);i++) p[i]=0;
}
unsigned bc_status(const struct ble_central *c) {return c->state;}
int bc_volume(const struct ble_central *c) {return ss_volume(&c->client);}
int bc_start(struct ble_central *c,const uint8_t own[6],unsigned own_type,
             const uint8_t peer[6],unsigned peer_type,uint32_t seed,uint32_t now) {
    unsigned i;bc_init(c);
    if(own_type>1 || peer_type>1 || !seed) {c->state=BC_FAILED;return 0;}
    for(i=0;i<6;i++) {c->own[i]=own[i];c->peer[i]=peer[i];}
    c->own_type=(uint8_t)own_type;c->peer_type=(uint8_t)peer_type;
    for(i=0;i<128;i++) if(bc_valid_access_address(c->access_address=random_step(&seed))) break;
    if(i==128) {c->state=BC_FAILED;return 0;}
    c->crc_init=random_step(&seed)&0xffffff;c->hop=5;c->channel=37;
    c->state=BC_SCANNING;c->last_poll=now;c->scan_deadline=now+US(3000000);
    c->scan_hop=now+US(20000);action(c,RA_RECEIVE,37,now);return 1;
}
void bc_stop(struct ble_central *c,uint32_t now) {
    ss_disconnected(&c->client);c->state=BC_OFF;c->early_delta=0;
    c->closing=0;c->termination_inflight=0;
    action(c,RA_IDLE,c->channel,now);
}
void bc_disconnect(struct ble_central *c,uint32_t now) {
    if(c->state==BC_OFF || c->state==BC_FAILED) return;
    if(!c->linked) {bc_stop(c,now);return;}
    if(!c->closing) {c->closing=1;c->close_deadline=now+US(120000);}
}
void bc_key_delta(struct ble_central *c,int delta) {
    if(delta!=1 && delta!=-1) return;
    if(c->state==BC_OFF || c->state==BC_FAILED || c->closing) return;
    if(c->state==BC_SCANNING || c->state==BC_CONNECT_TX) {
        int d=c->early_delta+delta;c->early_delta=(int16_t)(d < -100 ? -100 : d>100 ? 100 : d);
    } else ss_key_delta(&c->client,delta);
}
unsigned bc_take_action(struct ble_central *c,struct radio_action *out,unsigned capacity) {
    unsigned i;uint8_t *p=(uint8_t *)out,*source=(uint8_t *)&c->action;
    if(!c->action_pending || capacity<sizeof(*out)) return 0;
    for(i=0;i<sizeof(*out);i++) p[i]=source[i];
    c->action_pending=0;return sizeof(*out);
}
const struct radio_action *bc_take_action_ref(struct ble_central *c) {
    if(!c->action_pending) return 0;
    c->action_pending=0;return &c->action;
}
void bc_poll(struct ble_central *c,uint32_t now) {
    uint32_t elapsed=now-c->last_poll;unsigned ms=0,i;c->last_poll=now;
    if(c->state==BC_OFF || c->state==BC_FAILED) return;
    if(c->closing && reached(now,c->close_deadline)) {bc_stop(c,now);return;}
    if(elapsed>SUPERVISION) elapsed=SUPERVISION;
    c->tick_remainder+=elapsed;
    while(c->tick_remainder>=US(1000)) {c->tick_remainder-=US(1000);ms++;}
    ss_tick(&c->client,ms);
    if(c->client.state==SS_ERROR) {fail(c,0x40,now);return;}
    if(c->state==BC_SCANNING) {
        if(reached(now,c->scan_deadline)) {fail(c,0x08,now);return;}
        if(!c->action_pending && reached(now,c->scan_hop)) {
            c->channel=c->channel==39 ? 37 : c->channel+1;
            c->scan_hop=now+US(20000);action(c,RA_RECEIVE,c->channel,now);
        }
        return;
    }
    if(c->state==BC_CONNECT_TX) {
        if(reached(now,c->action.deadline+US(400))) fail(c,0x3e,now);
        return;
    }
    if(reached(now,c->last_rx+SUPERVISION)) {fail(c,0x08,now);return;}
    if(c->state==BC_DATA_RX && reached(now,c->rx_deadline)) {
        c->state=BC_BETWEEN;action(c,RA_IDLE,c->channel,now);
    }
    if(c->action_pending) return;
    if(c->state==BC_DATA_TX) {
        if(reached(now,c->action.deadline+US(400))) fail(c,0x3e,now);
        return;
    }
    if(c->state==BC_BETWEEN && reached(now,c->next_anchor-TX_PREPARE)) {
        /* Skip expired events without changing their anchor grid or hop count. */
        while(reached(now,c->next_anchor+US(2))) {
            c->unmapped+=c->hop;if(c->unmapped>=37) c->unmapped-=37;
            c->events++;c->next_anchor+=INTERVAL;
        }
        if(!reached(now,c->next_anchor-TX_PREPARE)) return;
        if(!c->linked && c->events>=6) {fail(c,0x3e,now);return;}
        c->unmapped+=c->hop;if(c->unmapped>=37) c->unmapped-=37;c->channel=c->unmapped;
        prepare_data(c);action(c,RA_DATA_TX,c->channel,c->next_anchor);
        c->action.header=c->tx_llid|(c->rx_next<<2)|(c->tx_sn<<3);
        c->action.length=c->tx_length;
        for(i=0;i<c->tx_length;i++) c->action.payload[i]=c->tx_payload[i];
        c->next_anchor+=INTERVAL;c->events++;c->state=BC_DATA_TX;
    }
}
void bc_tx_done(struct ble_central *c,uint32_t end) {
    if(c->state==BC_CONNECT_TX) {
        /* The radio port may use these otherwise-unused bytes as its encoded
         * CONNECT_IND cache during scanning. Release them before L2CAP RX. */
        c->reassembly_used=0;c->reassembly_total=0;c->reassembly_cid=0;
        c->next_anchor=end+US(1750);c->last_rx=end;c->last_poll=end;
        c->state=BC_BETWEEN;ss_connected(&c->client);
        while(c->early_delta>0) {ss_key_delta(&c->client,1);c->early_delta--;}
        while(c->early_delta<0) {ss_key_delta(&c->client,-1);c->early_delta++;}
    } else if(c->state==BC_DATA_TX) {c->rx_deadline=end+US(2000);c->state=BC_DATA_RX;}
}
void bc_rx_cached_advertisement(struct ble_central *c,unsigned header,const uint8_t *p,unsigned n,
                                uint32_t end,unsigned crc_valid) {
    unsigned type=header&15;
    if(c->state!=BC_SCANNING || !crc_valid) return;
    if((type!=0 && type!=1) || n<6 || n>37 || !equal6(p,c->peer) ||
           ((header>>6)&1)!=c->peer_type || (type==1 && (n!=12 || !equal6(p+6,c->own) || ((header>>7)&1)!=c->own_type))) return;
    action(c,RA_CONNECT_TX,c->channel,end+US(150));
    c->action.header=(uint8_t)(5|(c->own_type<<6)|(c->peer_type<<7));
    c->action.length=34;c->state=BC_CONNECT_TX;
}
void bc_rx(struct ble_central *c,unsigned header,const uint8_t *p,unsigned n,
           uint32_t end,unsigned crc_valid) {
    if(!crc_valid) return;
    if(c->state==BC_SCANNING) {
        bc_rx_cached_advertisement(c,header,p,n,end,crc_valid);
        if(c->state==BC_CONNECT_TX) bc_connect_payload(c,c->action.payload);
        return;
    }
    if(c->state!=BC_DATA_RX || n>27 || !(header&3)) return;
    c->last_rx=end;c->linked=1;
    if(c->outstanding && ((header>>2)&1)!=c->tx_sn) {
        c->outstanding=0;c->tx_sn^=1;
        if(c->termination_inflight) {bc_stop(c,end);return;}
    }
    if(((header>>3)&1)==c->rx_next) {
        c->rx_next^=1;data_received(c,header&3,p,n,end);
    } else c->duplicates++;
    if(c->state==BC_FAILED || c->state==BC_OFF) return;
    c->state=BC_BETWEEN;action(c,RA_IDLE,c->channel,end);
}
