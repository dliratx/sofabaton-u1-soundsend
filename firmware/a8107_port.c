/* Experimental V32 HW3 radio port. Addresses come from the downloaded firmware
 * and A8107M0 datasheet. Physical RF behavior/timing has NOT been validated.
 * Lab firmware starts disabled; no OTA or flash writes are performed here.
 */
#include "a8107_port.h"
#include "ble_packet.h"
#define R32(a) (*(volatile uint32_t *)(uintptr_t)(a))
#define R8(a) (*(volatile uint8_t *)(uintptr_t)(a))
#define US(x) ((uint32_t)(x)*16u)
#define IRQ_MASK ((1u<<2)|(1u<<3)|(1u<<9)|(1u<<24))
#define STROBE R32(0x50001004)
#define STATUS R32(0x50001010)
#define RF_CTRL R32(0x50001300)
#define RF_HEADER R32(0x50001304)
#define MODE_CTRL R32(0x50001008)
#define FIFO_CTRL R32(0x50001020)
extern uint64_t ap_capture_packet_tail(void);
extern unsigned ap_launch_packet(uint32_t at,uint32_t deadline,unsigned mode,unsigned header);
static int reached(uint32_t a,uint32_t b) {return (int32_t)(a-b)>=0;}
static uint32_t irq_lock(void) {
    uint32_t old;__asm__ volatile("mrs %0, primask\ncpsid i":"=r"(old)::"memory");return old;
}
static void irq_restore(uint32_t old) {__asm__ volatile("msr primask, %0"::"r"(old):"memory");}
static void receive_unlock(struct a8107_port *p) {
    if(p->receive_locked) {p->receive_locked=0;irq_restore(p->receive_primask);}
}
static void capture_rf(struct a8107_port *p) {
    p->last_rf_status=STATUS;p->last_rf_header=RF_HEADER;p->last_rf_control=RF_CTRL;
}
static int failed(struct a8107_port *p,unsigned code) {p->error=(uint8_t)code;p->errors++;STROBE=0xa0;p->rx_armed=0;receive_unlock(p);return 0;}
static int error(struct a8107_port *p,unsigned code) {capture_rf(p);return failed(p,code);}
/* A8107M0 Timer1: +4 is the decrementing VALUE, +8 is static RELOAD.
 * See vendor header TIMER_Type and datasheet13.4/13.5. */
uint32_t ap_now(void) {return ~R32(0x40001004);}
static int wait_until(uint32_t target) {
    unsigned guard=200000;
    while(!reached(ap_now(),target)) if(!--guard) return 0;
    return 1;
}
static int clock_wait(struct a8107_port *p) {
    int ok;
    p->clock_before=ap_now();
    ok=wait_until(p->clock_before+US(17));
    p->clock_after=ap_now();p->clock_control=R32(0x40001000);
    return ok;
}
static int clock_start(struct a8107_port *p,unsigned control) {
    R32(0x40001000)=0;R32(0x40001008)=0xffffffffu;
    R32(0x4000100c)=1;R32(0x40001000)=control;
    return clock_wait(p);
}
static uint32_t reverse32(uint32_t a) {
    return (a>>24)|((a>>8)&0x0000ff00u)|((a<<8)&0x00ff0000u)|(a<<24);
}
static unsigned frequency(unsigned channel) {
    if(channel<11) return 8+4*channel;
    if(channel<37) return 12+4*channel;
    return channel==37 ? 4 : channel==38 ? 52 : 160;
}
static void configure(unsigned channel,uint32_t aa,uint32_t crc,unsigned advertising) {
    STROBE=0xa0;RF_CTRL&=0x3d;
    R32(0x50001180)=advertising ? 0x01280015 : 0x01280005;
    R32(0x50001018)=reverse32(aa);
    R32(0x50001310)=crc;R32(0x50001314)=crc;
    R32(0x50001308)=channel;R32(0x500010c0)=frequency(channel);
}
unsigned ap_legacy_state(void) {return R8(0x20000270);}
unsigned ap_configuration_connected(void) {return R8(0x20000163)==0 && ap_legacy_state()==3;}
unsigned ap_ir_busy(void) {return R8(0x20000134)!=0;}
void ap_prepare_handoff(void) {
    /* The native post-poll loop at CFee/D000 restarts the previous HID
     * advertiser whenever C8 is82 or514. LL disconnect alone cannot release
     * the radio: the next foreground iteration races that reconnect forever.
     * End the old HID retry policy before requesting its asynchronous stop.
     * Keep the mode until disconnect completes, otherwise an existing HID
     * link would be misclassified as a phone configuration connection. */
    if(R8(0x20000163)!=0) {
        R32(0x200000c8)=R8(0x200000ba);R32(0x200000c4)=0;
        R8(0x200000c1)=0xff;R8(0x200000c2)=0xff;
    }
}
void ap_request_idle(void) {
    if(ap_legacy_state()==1) {
        uint8_t parameters[10];unsigned i,timer;
        for(i=0;i<10;i++) parameters[i]=R8(0x20002cec+i);
        /* The stock API stores a transformed timer, not its input interval.
         * Invert that transform before calling it again to stop advertising. */
        timer=parameters[2]|((unsigned)parameters[3]<<8);
        timer=parameters[1]==1 ? 0 : timer>=0x80 ? (timer-0x80)/4 : 0;
        parameters[2]=(uint8_t)timer;parameters[3]=(uint8_t)(timer>>8);
        parameters[8]=0;
        ((unsigned (*)(const uint8_t *))(uintptr_t)0x10a5)(parameters);
    } else if(ap_legacy_state()==2 || ap_legacy_state()==3)
        ((void (*)(void))(uintptr_t)0x1611)();
}
int ap_claim(struct a8107_port *p,unsigned tx_mode) {
    unsigned samples=0,bits=0;uint32_t seed=0,lock;
    if(ap_legacy_state()!=0 || ap_ir_busy() || tx_mode<1 || tx_mode>10) return 0;
    p->error=0;p->rx_armed=0;p->tx_mode=(uint8_t)tx_mode;
    p->last_launch_status=0;p->last_tx_header=0;
    lock=irq_lock();p->saved_irq=R32(0xe000e100)&IRQ_MASK;
    R32(0xe000e180)=IRQ_MASK;
    R8(0x50006004)=0;
    /* LPTIMER1 at50006010 drives the native inactivity counter (C4).
     * Keep it running: IRQ24 is masked only during radio ownership. Stopping
     * this unrelated timer here permanently disabled screen-off and sleep. */
    R32(0x40001000)=0;
    p->saved_adc=R32(0x50001240);p->active=1;irq_restore(lock);
    /* Vendor V32 consistently uses CTRL9. Try documented polling mode first,
     * then that stock configuration; NVIC Timer1 IRQ9 remains masked here.
     * Actual register snapshots distinguish a stopped counter from reset. */
    if(!clock_start(p,1) && !clock_start(p,9)) return error(p,0x81);
    configure(37,0x8e89bed6,0x555555,1);
    /* Continuous RSSI, no averaging, 4 MHz ADC. Von Neumann extraction avoids
     * assuming each ADC bit is unbiased. Entropy quality still needs hardware
     * assessment; a constant input fails instead of producing a fixed seed.
     */
    R32(0x50001240)=0x2f;STROBE=0xc0;
    while(bits<32 && samples++<4096) {
        unsigned a,b;
        if(!clock_wait(p)) return error(p,0x81);
        a=R32(0x50001244)&1;
        if(!clock_wait(p)) return error(p,0x81);
        b=R32(0x50001244)&1;
        if(a!=b) {seed=(seed<<1)|a;bits++;}
    }
    STROBE=0xa0;R32(0x50001240)=p->saved_adc;
    if(bits!=32 || !seed) return error(p,0x82);
    p->scratch.seed=seed;return 1;
}
void ap_release(struct a8107_port *p) {
    uint32_t lock;
    if(!p->active) return;
    lock=irq_lock();STROBE=0xa0;RF_CTRL&=0x3d;p->rx_armed=0;
    R32(0x50001240)=p->saved_adc;
    R32(0x40001000)=0;R32(0x4000100c)=1;
    /* The old BLE link was disconnected. Its expired timer must remain off;
     * the stock advertising stack initializes the next link's timer itself. */
    /* Preserve a pending inactivity tick; its stock ISR rearms LPTIMER1.
     * Only discarded BLE/Timer1 events may be cleared on this handoff. */
    R32(0xe000e280)=IRQ_MASK&~(1u<<24);R32(0xe000e100)=p->saved_irq;
    p->active=0;irq_restore(lock);receive_unlock(p);
    /* Stock routine rebuilds normal SB programming advertisements and resets
     * the original link state. IR/profile/key records are not rewritten.
     */
    ((void (*)(void))(uintptr_t)0x8721)();
}
static int receive_start(struct a8107_port *p) {
    unsigned guard=100000,clear_mode=ap_rx_clear_mode(p);uint32_t deadline=ap_now()+US(300);
    /* Public EOP wording is ambiguous. Physical LAB12 clear0 left a stale
     * latch while clear1400 accepted the exact peer and seven TX operations.
     * LAB13 defaults to2=1400;0=zero and1=no-write remain diagnostic only.
     * The model now reflects that physical clearing comparison. */
    if(clear_mode!=1) STATUS=clear_mode==2 ? 0x1400 : 0;
    RF_CTRL&=0x3d;
    /* Controlled lab comparison: stock006cc sets BLECTRL bit1 before its RX
     * strobe. Modes2/3 test that sequence; mode1 retains the prior baseline. */
    if(p->tx_mode>=2) RF_CTRL|=2;
    STROBE=0xc0;
    while((STATUS&3)!=1) if(!--guard || reached(ap_now(),deadline)) return error(p,0x83);
    p->rx_armed=1;return 1;
}
static void discard_scan_packet(struct a8107_port *p,unsigned status,unsigned completed) {
    capture_rf(p);STROBE=0xa0;p->rx_armed=0;receive_unlock(p);
    if(completed) {
        if(status&0x20) p->crc_errors++;else p->rx_packets++;
    } else p->errors++;
    receive_start(p);
}
static int prepare_raw_connect(struct ble_central *c,unsigned channel) {
    uint8_t payload[34];unsigned header=bc_connect_payload(c,payload);
    unsigned i;uint32_t ble;
    /* Reassembly is unused until a link exists. Cache only the current scan
     * channel, before arming RX; no extra retained SRAM or late CRC work. */
    if(ble_encode_packet(header,payload,34,0x555555,channel,c->reassembly,sizeof(c->reassembly))!=39) return 0;
    /* Fill TX RAM before RX, with native framing stopped. At the150us reply
     * only the fixed raw controls and TX strobe remain; no39-byte MMIO copy.
     * Reset the FIFO pointer BEFORE filling it. Preserving RAM alone is not
     * enough if a later FWPRN reset discards the written-byte position. */
    STROBE=0xa0;ble=RF_CTRL;RF_CTRL=0;
    R32(0x50001000)=0x40;
    for(i=0;i<39;i++) R8(0x50001400+i)=c->reassembly[i];
    RF_CTRL=ble&0x3d;
    c->reassembly_used=39;c->reassembly_cid=(uint16_t)channel;return 1;
}
uint32_t ap_raw_delay(unsigned mode,uint32_t original) {
    /* The vendor stock code reads TDL from bits4:3; the related AMICCOM FIFO
     * datasheet identifies low3 bits as PDL. Exact A8107 timing is unproven.
     * Mode7 baseline;8 lower PDL;9 lower TDL;10 lower both. No analog, carrier,
     * power or data-rate controls are modified. Always restore the original. */
    unsigned mask=mode==8 ? 7 : mode==9 ? 0x18 : mode==10 ? 0x1f : 0;
    return original&~mask;
}
static unsigned raw_transmit(struct a8107_port *p,struct ble_central *c,const struct radio_action *a,uint32_t *end) {
    uint8_t buffer[39];const uint8_t *packet=buffer;unsigned n,i,guard,code=0,complete=0;uint32_t start;
    uint32_t mode=MODE_CTRL,fifo=FIFO_CTRL,ble=RF_CTRL;
    uint32_t delay=R32(0x500011c0);
    if(a->kind==RA_CONNECT_TX) {
        if(c->reassembly_used!=39 || c->reassembly_cid!=a->channel) return 0x84;
        packet=c->reassembly;n=39;
    } else n=ble_encode_packet(a->header,a->payload,a->length,a->crc_init,a->channel,buffer,sizeof(buffer));
    if(!n) return 0x84;
    /* Public datasheet9.2.3/9.2.5/9.2.11/10.5.4: generic FIFO, LSB-first
     * data/ID, one-byte preamble, four-byte ID, explicit payload end pointer.
     * CRC and whitening are already in packet; native BLE framing is off.
     * Native controls are restored before RX, stock code, or any error exit. */
    RF_CTRL=0;STROBE=0xa0;
    /* LAB11 mode7 was decoded by an independent UGREEN watcher. Keep that
     * verified bit order for all raw timing variants in LAB13. */
    MODE_CTRL=(mode&~0x704u)|0x502u;
    R32(0x500011c0)=ap_raw_delay(p->tx_mode,delay);
    FIFO_CTRL=(fifo&~255u)|(n-1);
    R32(0x50001180)=0x01280000;
    if(a->kind!=RA_CONNECT_TX) {
        R32(0x50001000)=0x40; /* Reset BEFORE writing, not after preloading. */
        for(i=0;i<n;i++) R8(0x50001400+i)=packet[i];
    }
    p->scratch.tx_fifo=FIFO_CTRL;STATUS=0;STROBE=0xb0;
    p->clock_control&=0x7f;
    if(!ap_launch_packet(a->at-US(ap_tx_lead_us(p)),a->deadline-US(ap_tx_lead_us(p)),7,0)) {
        /* A failed launch needs its own measurement, not an entropy sample.
         * This is lateness after the intended strobe anchor; no TX occurred. */
        p->clock_before=a->at-US(ap_tx_lead_us(p));p->clock_after=ap_now();
        p->clock_control|=0x80;complete=1;code=0x85;goto restore;
    }
    p->last_launch_status=(uint16_t)STATUS;start=ap_now();guard=10000;
    /* Reuse the entropy-wait snapshot after acquisition. Bit7 identifies a
     * measured raw-TX polling interval, beginning AFTER the launch returns.
     * This is not calibrated on-air duration and adds no retained RAM. */
    p->clock_before=start;p->clock_control|=0x80;
    while((STATUS&0x83)!=0x83)
        if(!--guard || reached(ap_now(),start+US(100))) {code=0x86;goto restore;}
    guard=100000;
    /* TRER is the documented transceiver-enable state. Physical LAB11 showed
     * TRER=0 while WTR remained1. Measure the first TX-state release instead
     * of assigning that later WTR transition as the packet's end. Real peer
     * response must validate whether this is a suitable BLE anchor. */
    while(STATUS&1)
        if(!--guard || reached(ap_now(),start+US(800))) {code=0x87;goto restore;}
    *end=ap_now();p->clock_after=*end;complete=1;
    /* Record TX state release, then allow the PA/WTR tail to finish before
     * changing framing or issuing standby. LAB11 measured about18us of tail.
     * Interrupting it in LAB12 may have disturbed later RX; not yet proven. */
    guard=20000;
    while(STATUS&0x80)
        if(!--guard || reached(ap_now(),*end+US(100))) {code=0x8a;goto restore;}
restore:
    if((p->clock_control&0x80) && !complete) p->clock_after=ap_now();
    capture_rf(p);STROBE=0xa0;MODE_CTRL=mode;FIFO_CTRL=fifo;
    R32(0x500011c0)=delay;
    R32(0x50001180)=a->kind==RA_CONNECT_TX ? 0x01280015 : 0x01280005;
    RF_CTRL=ble;return code;
}
int ap_beacon(struct a8107_port *p,const uint8_t own[6],unsigned channel) {
    static const uint8_t ad[]={2,1,6,13,9,'S','S','L','A','B','-','R','F','T','E','S','T'};
    struct radio_action a;unsigned i,code;uint32_t lock,end;
    if(!p->active || p->tx_mode<7 || channel<37 || channel>39) return error(p,0x84);
    a.kind=RA_DATA_TX;a.channel=(uint8_t)channel;a.header=2;a.length=6+sizeof(ad);
    a.access_address=0x8e89bed6;a.crc_init=0x555555;
    for(i=0;i<6;i++) a.payload[i]=own[i];
    for(i=0;i<sizeof(ad);i++) a.payload[i+6]=ad[i];
    configure(channel,a.access_address,a.crc_init,1);
    lock=irq_lock();a.at=ap_now()+US(1000);a.deadline=a.at+US(50);
    p->last_tx_header=(uint16_t)(a.header|((unsigned)a.length<<8));
    code=raw_transmit(p,0,&a,&end);
    R32(0x50001180)=0x01280015;irq_restore(lock);
    if(code) return failed(p,code);
    p->tx_packets++;return 1;
}
int ap_action(struct a8107_port *p,struct ble_central *c,const struct radio_action *a) {
    unsigned i,guard,n;uint32_t lock,start,end,status,launch_word;
    if(!p->active) return 0;
    if(a->kind==RA_IDLE) {STROBE=0xa0;p->rx_armed=0;return 1;}
    if(a->channel>39 || a->length>34) return error(p,0x84);
    if(a->kind==RA_RECEIVE && p->tx_mode>=7 && !prepare_raw_connect(c,a->channel)) return error(p,0x84);
    configure(a->channel,a->access_address,a->crc_init,a->kind==RA_RECEIVE || a->kind==RA_CONNECT_TX);
    if(a->kind==RA_RECEIVE) return receive_start(p);
    if(a->kind!=RA_CONNECT_TX && a->kind!=RA_DATA_TX) return error(p,0x84);
    lock=irq_lock();
    p->last_tx_header=(uint16_t)((uint32_t)a->header|((uint32_t)a->length<<8));
    if(p->tx_mode>=7) {
        unsigned code=raw_transmit(p,c,a,&end);
        if(code) {irq_restore(lock);return failed(p,code);}
        goto transmitted;
    }
    /* Prepare with the PLL running, then launch at the requested anchor. */
    /* Stock00970 calls006cc (BLECTRL bit1) before7784 (BLEHEADER),
     * without a TX strobe. Modes4/5 compare this ordering at the anchor;
     * mode5 also retains stock standby instead of forcing PLL beforehand.
     * The public register header does not document the launch side effect,
     * so these remain controlled physical experiments, not proven semantics. */
    if(p->tx_mode!=5 && p->tx_mode!=6) STROBE=0xb0;
    launch_word=p->last_tx_header;n=a->length;
    if(p->tx_mode==6) {
        /* Original advertising fragment0af2..0b9e: engine, HEADER, then
         * FIFO bytes. Test the inference that the last FIFO write releases
         * TX. Keep that final byte (or the empty PDU header) for the anchor. */
        RF_CTRL|=2;
        if(n) {
            RF_HEADER=p->last_tx_header;n--;
            launch_word=0x80000000u|(n<<8)|a->payload[n];
        }
    } else if(p->tx_mode<=3) RF_HEADER=p->last_tx_header;
    for(i=0;i<n;i++) R8(0x50001400+i)=a->payload[i];
    p->scratch.tx_fifo=R32(0x50001020);
    /* A bounded assembly loop keeps compiler-generated branches and structure
     * loads out of the last timestamp-to-strobe interval. */
    if(!ap_launch_packet(a->at,a->deadline,p->tx_mode,launch_word)) {
        irq_restore(lock);return error(p,0x85);
    }
    p->last_launch_status=(uint16_t)STATUS;start=ap_now();
    guard=10000;
    while(((status=STATUS)&0x83)!=0x83) {
        if(!--guard || reached(ap_now(),start+US(100))) {
            irq_restore(lock);return error(p,0x86);
        }
    }
    guard=100000;
    while((STATUS&0x80)!=0) {
        if(!--guard || reached(ap_now(),start+US(500))) {
            irq_restore(lock);return error(p,0x87);
        }
    }
    end=ap_now();
transmitted:
    p->tx_packets++;RF_CTRL&=0x3d;
    n=a->kind; /* End the borrowed-action lifetime before bc_tx_done. */
    bc_tx_done(c,end);
    if(n==RA_DATA_TX) {
        if(!receive_start(p)) {irq_restore(lock);return 0;}
    } else {STROBE=0xa0;p->rx_armed=0;}
    irq_restore(lock);receive_unlock(p);return 1;
}
void ap_receive(struct a8107_port *p,struct ble_central *c) {
    uint8_t payload[37];uint32_t status,header,end,deadline;unsigned n,i,guard,copy_n;
    if(!p->active || !p->rx_armed) return;
    if(bc_status(c)==BC_SCANNING) {
        /* An application-loop timestamp is too late for the150us initiating
         * response. Observe SYNC while the packet is still arriving, then
         * protect its tail and CONNECT_IND from input-ISR latency. Otherwise
         * discard this advertisement rather than transmit at a false time.
         */
        deadline=ap_now()+US(2000);guard=200000;
        while(((status=STATUS)&3)==1 && !(status&0x100) && !(status&0x1000))
            if(!--guard || reached(ap_now(),deadline)) return;
        if((status&3)!=1 || !(status&0x100)) {
            /* The main loop missed SYNC. A late timestamp cannot initiate a
             * BLE link, but that packet must not strand the receiver. */
            discard_scan_packet(p,status,(status&0x1000)!=0);return;
        }
        p->receive_primask=irq_lock();p->receive_locked=1;
        if((STATUS&3)!=1) {p->rx_armed=0;receive_unlock(p);return;}
        deadline=ap_now()+US(400);
        {
            uint64_t captured=ap_capture_packet_tail();
            status=(uint32_t)captured;end=(uint32_t)(captured>>32);
        }
        if((!(status&0x1000) && (status&3)==1) || reached(end,deadline)) {
            /* Noise/CRC filtering can keep RX enabled. Recover this scan
             * window and retain the bounded central scan deadline. */
            discard_scan_packet(p,status,(status&0x1000)!=0);return;
        }
    } else {status=STATUS;if((status&3)==1 && !(status&0x1000)) return;end=~R32(0x40001004);}
    capture_rf(p);p->rx_armed=0;RF_CTRL&=0x3d;STROBE=0xa0;
    header=RF_HEADER;n=(header>>8)&0x3f;
    if(n>sizeof(payload)) {error(p,0x88);return;}
    /* ADV_IND needs only AdvA6. Directed ADV also needs TargetA6. */
    copy_n=n;
    if(bc_status(c)==BC_SCANNING) {
        copy_n=(header&15)==1 ? 12 : 6;
        if(copy_n>n) copy_n=n;
    }
    for(i=0;i<copy_n;i++) payload[i]=R8(0x50001500+i);
    if(status&0x20) p->crc_errors++;else p->rx_packets++;
    if(bc_status(c)==BC_SCANNING && p->tx_mode>=7)
        bc_rx_cached_advertisement(c,header&0xff,payload,n,end,(status&0x20)==0);
    else bc_rx(c,header&0xff,payload,n,end,(status&0x20)==0);
    if(bc_status(c)!=BC_CONNECT_TX) receive_unlock(p);
    if(bc_status(c)==BC_SCANNING) receive_start(p);
}
