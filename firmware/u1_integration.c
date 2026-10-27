/* V32-specific hook integration for the dedicated SoundSend profile. */
#include "u1_integration.h"
#include "a8107_port.h"
#ifndef SS_DEFAULT_ENABLED
#define SS_DEFAULT_ENABLED 1
#endif
#define R8(a) (*(volatile uint8_t *)(uintptr_t)(a))
#define R16(a) (*(volatile uint16_t *)(uintptr_t)(a))
#define R32(a) (*(volatile uint32_t *)(uintptr_t)(a))
#define US(x) ((uint32_t)(x)*16u)
extern unsigned u1_original_key(unsigned profile,unsigned button);
extern unsigned u1_original_poll(unsigned a,unsigned b,unsigned c,unsigned d);
extern void u1_original_screen(void);
extern uint8_t __ss_ram_start[],__ss_ram_end[];
enum runtime_phase { RT_IDLE, RT_ACQUIRE, RT_ACTIVE };
enum link_view { LINK_IDLE, LINK_CONNECTING, LINK_READY, LINK_ERROR };
struct u1_runtime {
    struct ble_central central;
    struct a8107_port port;
    uint32_t last_key;
    int16_t queued_delta;
    uint16_t attempts;
    uint8_t phase:2,enabled:1,request:1,probe:2,stop_requested:1,mute_pending:1;
    uint8_t tx_mode,last_volume,last_reason,last_state;
    uint8_t saved_volume,shown_volume,battery;
    uint8_t muted:1,shown_muted:1,screen_valid:1,repeating:1,shown_link:2;
};
static struct u1_runtime runtime;
static const uint8_t own_le[6]={0xab,0x2e,0x00,0x03,0x21,0x20};
static const uint8_t peer_le[6]={0x0a,0x93,0x18,0x93,0x90,0xcc};
static unsigned soundsend_profile(unsigned profile) {
    static const uint8_t name[]="SoundSend";unsigned i,j,n=R8(0x20000105);
    if(profile!=2 || n>15) return 0;
    for(i=0;i<n;i++) if(R8(0x20001435+32*i)==profile) {
        for(j=0;j<sizeof(name)-1;j++) if(R8(0x20001443+32*i+j)!=name[j]) return 0;
        return R8(0x20001443+32*i+j)==0 || R8(0x20001443+32*i+j)==' ';
    }
    return 0;
}
static unsigned volume_screen_selected(void) {
    return !ap_configuration_connected() && R8(0x20000163)!=3 &&
        R8(0x20000162)!=1 && R8(0x20000162)!=2 && !R8(0x200000d6) &&
        soundsend_profile(R8(0x20000103));
}
static void digits(char *s,unsigned v) {
    s[0]=v>=100 ? '1' : ' ';s[1]=v>=10 ? (char)('0'+v/10%10) : ' ';s[2]=(char)('0'+v%10);
}
static void draw(unsigned x,unsigned page,const char *s) {
    ((void (*)(unsigned,unsigned,const char *))(uintptr_t)0x11dc9)(x,page,s);
}
static unsigned screen_muted(void) {return runtime.muted && runtime.last_volume==0;}
static unsigned screen_link(void) {
    if(runtime.phase!=RT_IDLE || runtime.request) {
        if(runtime.phase==RT_ACTIVE && runtime.central.closing) return LINK_IDLE;
        if(runtime.phase==RT_ACTIVE && runtime.central.linked && bc_volume(&runtime.central)>=0)
            return LINK_READY;
        return LINK_CONNECTING;
    }
    return runtime.last_reason ? LINK_ERROR : LINK_IDLE;
}
static const char *link_text(unsigned link) {
    return link==LINK_CONNECTING ? "..." : link==LINK_READY ? " BT" : link==LINK_ERROR ? " ! " : "   ";
}
static void show_volume(void) {
    char title[17]="SoundSend       ",line[17]="Volumen: --- %  ",battery[17]="Bat: --- %      ";
    unsigned v=runtime.last_volume,link=screen_link(),i;const char *indicator=link_text(link);
    if(runtime.phase!=RT_IDLE || runtime.port.active || !volume_screen_selected()) return;
    if(v<=100) digits(line+9,v);
    if(runtime.battery<=100) digits(battery+5,runtime.battery);
    if(screen_muted()) {battery[12]='M';battery[13]='U';battery[14]='T';battery[15]='E';}
    for(i=0;i<3;i++) title[13+i]=indicator[i];
    draw(0,0,title);draw(0,3,line);draw(0,6,battery);
    runtime.shown_volume=(uint8_t)v;runtime.shown_muted=screen_muted();runtime.screen_valid=1;
    runtime.shown_link=link;
}
static void show_confirmed_change(void) {
    char value[4]="---";unsigned muted=screen_muted(),link=screen_link();
    /* Render at most4 characters per poll between30ms connection events. No ADC,
     * full screen, radio RX/TX or pending launch is allowed on this path.
     * Reserve12ms before the next anchor, including its1ms preparation. */
    if(!runtime.screen_valid || !volume_screen_selected() || bc_status(&runtime.central)!=BC_BETWEEN ||
       runtime.central.action_pending || runtime.port.rx_armed || runtime.port.receive_locked ||
       (int32_t)(runtime.central.next_anchor-ap_now())< (int32_t)US(12000)) return;
    if(runtime.shown_link!=link) {draw(104,0,link_text(link));runtime.shown_link=link;return;}
    if(runtime.last_volume<=100 && runtime.shown_volume!=runtime.last_volume) {
        digits(value,runtime.last_volume);draw(72,3,value);runtime.shown_volume=runtime.last_volume;return;
    }
    if(runtime.shown_muted!=muted) {draw(96,6,muted ? "MUTE" : "    ");runtime.shown_muted=muted;}
}
void u1_screen_gateway(void) {
    unsigned selected=R8(0x20000102);
    /* The stock wake/release handlers both call this entry. Drawing its
     * device list before the volume view visibly flashes that list. Bypass
     * only its renderer when the native selection is already valid. */
    if(volume_screen_selected() && selected && selected<=R8(0x20000105) &&
       ((unsigned (*)(unsigned))(uintptr_t)0x11cb9)(selected)==R8(0x20000103)) {
        /* Retain the existing OLED contents during a live radio operation;
         * finish() draws the confirmed result after releasing the port. */
        if(runtime.phase!=RT_IDLE || runtime.port.active) return;
        /* Preserve the original two-read low-battery decision, display GPIO
         * restore and screen-mode bookkeeping, without painting the menu. */
        runtime.battery=(uint8_t)((unsigned (*)(void))(uintptr_t)0x136a9)();
        if(runtime.battery<70) runtime.battery=(uint8_t)((unsigned (*)(void))(uintptr_t)0x136a9)();
        if(runtime.battery>=70) {
            show_volume();
            ((void (*)(unsigned))(uintptr_t)0x124d5)(255);
            ((void (*)(void))(uintptr_t)0x93f9)();
            R8(0x2000016f)=R8(0x20000162);
            return;
        }
        runtime.screen_valid=0;u1_original_screen();return;
    }
    /* Other devices, configuration and invalid/hidden selections retain
     * the stock selection repair and native screen behavior. */
    runtime.screen_valid=0;u1_original_screen();show_volume();
}

void u1_runtime_init(void) {
    uint8_t *p;
    for(p=__ss_ram_start;p<__ss_ram_end;p++) *p=0;
    runtime.enabled=SS_DEFAULT_ENABLED;runtime.tx_mode=10;runtime.last_volume=0xff;
    runtime.shown_volume=0xff;runtime.battery=0xff;
    /* Physical LAB12: clear1400 accepted fresh peer and seven TX operations;
     * clear0 produced55406 stale-latch iterations and no TX. */
    runtime.port.radio_options=2|72;
}
static void finish(unsigned reason) {
    int volume=bc_volume(&runtime.central);
    runtime.last_state=(uint8_t)bc_status(&runtime.central);
    runtime.last_reason=(uint8_t)reason;
    if(volume>=0) runtime.last_volume=(uint8_t)volume;
    if(runtime.port.active) bc_stop(&runtime.central,ap_now());
    runtime.phase=RT_IDLE;runtime.request=0;runtime.probe=0;runtime.queued_delta=0;runtime.mute_pending=0;
    ap_release(&runtime.port);
    show_volume();
}
static void request_volume(int delta) {
    /* Volume keys leave mute from the actual zero level, never jumping to a
     * remembered louder setting. A pending mute that has not run is canceled. */
    runtime.mute_pending=0;runtime.muted=0;
    if(runtime.phase==RT_ACTIVE && !runtime.central.closing) {
        bc_key_delta(&runtime.central,delta);runtime.last_key=ap_now();
    } else {
        int n=runtime.queued_delta+delta;
        runtime.queued_delta=(int16_t)(n < -100 ? -100 : n>100 ? 100 : n);
        runtime.request=1;
    }
}
static void request_mute(void) {
    runtime.mute_pending^=1;
    if(runtime.phase==RT_ACTIVE && !runtime.central.closing) runtime.last_key=ap_now();
    else runtime.request=1;
}
static void apply_mute(void) {
    struct ss_client *c=&runtime.central.client;
    if(c->state!=SS_READY || runtime.central.closing) return;
    if(!runtime.mute_pending) {if(c->volume) runtime.muted=0;return;}
    runtime.mute_pending=0;
    if(runtime.muted && c->volume==0) {
        ss_set_volume(c,runtime.saved_volume);
    } else {
        /* Always use the freshly read/confirmed value. If another controller
         * raised volume while disconnected, silence that new level instead
         * of restoring an obsolete value. Zero has no invented restore level. */
        runtime.saved_volume=c->volume;runtime.muted=c->volume!=0;ss_set_volume(c,0);
    }
}
unsigned u1_key_gateway(unsigned profile,unsigned button) {
    if(runtime.enabled && !ap_configuration_connected() && R8(0x20000163)!=3 &&
       R8(0x20000162)!=1 && R8(0x20000162)!=2 &&
       soundsend_profile(profile) && (button==4 || button==10 || button==13)) {
        /* Stock9c54 calls every loop while held. A physical edge gives one
         * immediate step. Held volume repeats after400ms, then every120ms,
         * only after confirmation. Never queue a catch-up burst while the
         * peer is absent or a write is unconfirmed. Mute remains edge-only. */
        if(R8(0x20000136)==button && R8(0x20000135)!=button) {
            runtime.repeating=0;
            if(button==13) request_mute();else request_volume(button==4 ? 1 : -1);
        } else if(button!=13 && R8(0x20000136)==button && runtime.phase==RT_ACTIVE &&
                  !runtime.central.closing && runtime.central.client.state==SS_READY &&
                  (uint32_t)(ap_now()-runtime.last_key)>=US(runtime.repeating ? 120000 : 400000)) {
            runtime.repeating=1;request_volume(button==4 ? 1 : -1);
        }
        return 0;
    }
    /* Other keys relinquish the radio before executing their original path. */
    if(runtime.phase!=RT_IDLE || runtime.request) finish(0);
    return u1_original_key(profile,button);
}
unsigned u1_debug_command(const uint8_t request[20],uint8_t response[20]) {
    unsigned i,sum=0;uint8_t command=request[3];
    if(request[0]!=0x53 || request[1]!=0x53 || request[2]!=1 || command<1 || command>14) return 0;
    for(i=0;i<19;i++) sum+=request[i];
    if((uint8_t)sum!=request[19]) return 0;
    if(command==2) {
        if(runtime.phase==RT_IDLE && !runtime.request) {runtime.probe=1;runtime.request=1;runtime.queued_delta=0;}
        else runtime.last_reason=0x90;
    } else if(command==12) {
        if(runtime.phase==RT_IDLE && !runtime.request && !runtime.enabled && runtime.tx_mode>=7) {
            runtime.probe=2;runtime.request=1;runtime.queued_delta=0;
        } else runtime.last_reason=0x90;
    } else if(command==3) {
        if(request[4]<=1) runtime.enabled=request[4];
        else runtime.last_reason=0x91;
    } else if(command==4) {
        if(request[4]>=1 && request[4]<=10 && runtime.phase==RT_IDLE && !runtime.request) runtime.tx_mode=request[4];
        else runtime.last_reason=0x91;
    } else if(command==8) {
        if(request[4]<=2 && runtime.phase==RT_IDLE && !runtime.request)
            runtime.port.radio_options=(uint8_t)((runtime.port.radio_options&0xfc)|request[4]);
        else runtime.last_reason=0x91;
    } else if(command==11) {
        if(request[4]<=140 && !(request[4]&3) && runtime.phase==RT_IDLE && !runtime.request && !runtime.enabled)
            runtime.port.radio_options=(uint8_t)((runtime.port.radio_options&3)|request[4]);
        else runtime.last_reason=0x91;
    }
    for(i=0;i<20;i++) response[i]=0;
    response[0]=0x53;response[1]=0x53;response[2]=1;response[3]=(uint8_t)(command|0x80);
    response[4]=runtime.enabled;response[5]=runtime.phase;response[6]=runtime.last_state;
    response[7]=runtime.last_reason;response[8]=runtime.last_volume;response[9]=runtime.tx_mode;
    response[10]=(uint8_t)runtime.port.tx_packets;response[11]=(uint8_t)(runtime.port.tx_packets>>8);
    response[12]=(uint8_t)runtime.port.rx_packets;response[13]=(uint8_t)(runtime.port.rx_packets>>8);
    response[14]=(uint8_t)runtime.port.crc_errors;response[15]=(uint8_t)(runtime.port.crc_errors>>8);
    response[16]=(uint8_t)runtime.port.errors;response[17]=(uint8_t)(runtime.port.errors>>8);
    response[18]=SS_LAB_REVISION;
    if(command==5) {
        for(i=0;i<4;i++) {
            response[4+i]=(uint8_t)(SS_LAB_BUILD_ID>>(8*i));
            response[8+i]=(uint8_t)(runtime.port.clock_before>>(8*i));
            response[12+i]=(uint8_t)(runtime.port.clock_after>>(8*i));
        }
        response[16]=(uint8_t)runtime.port.clock_control;response[17]=runtime.port.error;
    } else if(command==6) {
        for(i=0;i<4;i++) {
            response[4+i]=(uint8_t)(runtime.port.last_rf_status>>(8*i));
            response[8+i]=(uint8_t)(runtime.port.last_rf_header>>(8*i));
            response[12+i]=(uint8_t)(runtime.port.last_rf_control>>(8*i));
        }
        response[16]=runtime.tx_mode;response[17]=runtime.port.error;
    } else if(command==7) {
        response[4]=(uint8_t)runtime.port.last_launch_status;
        response[5]=(uint8_t)(runtime.port.last_launch_status>>8);
        response[6]=(uint8_t)runtime.port.last_tx_header;
        response[7]=(uint8_t)(runtime.port.last_tx_header>>8);
        response[8]=(uint8_t)ap_rx_clear_mode(&runtime.port);
        if(runtime.port.clock_control&0x80) {
            uint32_t elapsed=runtime.port.clock_after-runtime.port.clock_before;
            for(i=0;i<4;i++) response[9+i]=(uint8_t)(elapsed>>(8*i));
            response[13]=1;
        }
        response[14]=(uint8_t)ap_tx_lead_us(&runtime.port);
        response[16]=runtime.tx_mode;response[17]=runtime.port.error;
    } else if(command==8) {
        response[4]=(uint8_t)ap_rx_clear_mode(&runtime.port);response[5]=runtime.phase;
        response[16]=runtime.tx_mode;response[17]=runtime.port.error;
    } else if(command==11) {
        response[4]=(uint8_t)ap_tx_lead_us(&runtime.port);response[5]=runtime.phase;
        response[16]=runtime.tx_mode;response[17]=runtime.port.error;
    } else if(command==14) {
        uint32_t values[3]={R32(0x200000c4),R32(0x200000c8),R32(0x50006014)};
        for(i=0;i<12;i++) response[4+i]=(uint8_t)(values[i/4]>>(8*(i%4)));
        response[16]=R8(0x20000162);response[17]=(uint8_t)ap_legacy_state();
    } else if(command==9 || command==10 || command==13) {
        uint32_t values[3];
        if(command==9) {
            values[0]=runtime.port.last_tx_header ? runtime.port.scratch.tx_fifo : 0;
            values[1]=R32(0x5000130c);values[2]=R32(0x50001020);
        } else if(command==10) {
            values[0]=R32(0x50001008);values[1]=R32(0x50001028);values[2]=R32(0x50001024);
        } else {
            values[0]=R32(0x500011c0);values[1]=R32(0x500011c4);
            values[2]=ap_raw_delay(runtime.tx_mode,values[0]);
        }
        for(i=0;i<12;i++) response[4+i]=(uint8_t)(values[i/4]>>(8*(i%4)));
        response[16]=runtime.tx_mode;response[17]=runtime.port.error;
    }
    sum=0;for(i=0;i<19;i++) sum+=response[i];response[19]=(uint8_t)sum;
    return 1;
}
unsigned u1_echo_gateway(uint8_t *data,const void *attribute,unsigned length) {
    uint8_t response[20];unsigned i;
    /* Intercept the actual FFF4 echo dispatch at stock1084e. A poll-entry
     * observer races the original main loop, which consumes the write flag
     * itself when an ATT write arrives after our foreground poll. */
    if(data==(uint8_t *)(uintptr_t)0x2000140a &&
       attribute==(const void *)(uintptr_t)0x146ea && length==20 &&
       u1_debug_command(data,response))
        for(i=0;i<20;i++) data[i]=response[i];
    return ((unsigned (*)(const void *,const void *,unsigned))(uintptr_t)0x1339)
        (data,attribute,length);
}
static void active_poll(void) {
    const struct radio_action *a;uint32_t now=ap_now();int volume;
    unsigned pass,client_before=runtime.central.client.state;
    if(runtime.probe==2) {
        if((int32_t)(now-runtime.last_key)<0) return;
        if(!ap_beacon(&runtime.port,own_le,37+runtime.attempts%3)) {finish(runtime.port.error);return;}
        if(++runtime.attempts==9) {finish(0);return;}
        runtime.last_key=ap_now()+US(100000);return;
    }
    for(pass=0;pass<3;pass++) {
        ap_receive(&runtime.port,&runtime.central);
        if(runtime.port.error) {finish(runtime.port.error);return;}
        now=ap_now();bc_poll(&runtime.central,now);
        a=bc_take_action_ref(&runtime.central);
        if(!a) break;
        if(!ap_action(&runtime.port,&runtime.central,a)) {finish(runtime.port.error);return;}
        /* Immediately handle a packet observed during scanning and its timed
         * initiating reply before returning to unrelated application work. */
        /* ap_action may consume/update central state; use the borrowed action
         * only during that call. No full56-byte copy on the150us path. */
        if(bc_status(&runtime.central)!=BC_SCANNING) break;
    }
    volume=bc_volume(&runtime.central);
    if(volume>=0) runtime.last_volume=(uint8_t)volume;
    if(bc_status(&runtime.central)==BC_FAILED) {finish(runtime.central.reason);return;}
    if(bc_status(&runtime.central)==BC_OFF) {
        /* A key arriving while LL_TERMINATE is in flight starts a fresh
         * connection after release instead of being silently discarded. */
        int16_t queued=runtime.queued_delta;uint8_t pending=runtime.request,mute=runtime.mute_pending;
        finish(0);runtime.queued_delta=queued;runtime.request=pending;runtime.mute_pending=mute;return;
    }
    /* Discovery or a write confirmation can take longer than the warm
     * interval. Start the ready window when the operation actually completes,
     * before considering idle close. Otherwise READY may last only this poll
     * and the next native held-key callback never gets a chance to repeat. */
    if(client_before!=SS_READY && runtime.central.client.state==SS_READY)
        runtime.last_key=now;
    apply_mute();show_confirmed_change();
    if(runtime.central.client.state==SS_READY && (uint32_t)(now-runtime.last_key)>=US(800000))
        bc_disconnect(&runtime.central,now);
}
unsigned u1_poll_prepare(void) {
    /* The assembly gateway tail-calls stock poll after this function returns.
     * Its dedicated OTA loop must not retain an extra C frame on the small
     * original stack. Return0 only while the new central owns this iteration. */
    if(runtime.phase==RT_ACTIVE) {active_poll();return 0;}
    if(!runtime.request) return 1;
    if(!runtime.probe && ap_configuration_connected()) {finish(0x92);return 1;}
    if(runtime.phase==RT_IDLE) {
        /* Show an attempt before acquisition; a cached percentage is not a
         * live-link indication. No display/ADC calls run in scanning windows. */
        if(runtime.screen_valid && volume_screen_selected()) {
            runtime.shown_link=LINK_CONNECTING;draw(104,0,link_text(LINK_CONNECTING));
        }
        ap_prepare_handoff();runtime.phase=RT_ACQUIRE;runtime.attempts=0;runtime.stop_requested=0;
    }
    /* A stock disconnect completes on a later connection event. Allow a
     * bounded poll budget instead of expiring after64 fast foreground loops. */
    if(++runtime.attempts>4096) {finish(0x93);return 1;}
    if(ap_ir_busy()) return 1;
    if(ap_legacy_state()!=0) {
        if(!runtime.stop_requested || ap_legacy_state()==1) {ap_request_idle();runtime.stop_requested=1;}
        return 1;
    }
    if(!ap_claim(&runtime.port,runtime.tx_mode)) {finish(runtime.port.error ? runtime.port.error : 0x94);return 1;}
    /* Acquisition fulfilled the original request. Only a NEW key received
     * during closing may survive BC_OFF and start another connection. */
    runtime.request=0;
    /* Keep the original main loop out of its idle/advertising/connected paths
     * while our poll hook owns the radio. The original stack uses states0..3.
     * Hardware validation must include all remaining main-loop/IRQ paths.
     */
    R8(0x20000270)=4;
    if(runtime.probe==2) {
        bc_init(&runtime.central);runtime.phase=RT_ACTIVE;runtime.attempts=0;
        runtime.last_key=ap_now()+US(1000000);return 0;
    }
    if(!bc_start(&runtime.central,own_le,0,peer_le,0,runtime.port.scratch.seed,ap_now())) {finish(0x95);return 1;}
    runtime.phase=RT_ACTIVE;runtime.last_key=ap_now();
    while(runtime.queued_delta>0) {bc_key_delta(&runtime.central,1);runtime.queued_delta--;}
    while(runtime.queued_delta<0) {bc_key_delta(&runtime.central,-1);runtime.queued_delta++;}
    active_poll();return runtime.phase==RT_ACTIVE ? 0 : 1;
}
