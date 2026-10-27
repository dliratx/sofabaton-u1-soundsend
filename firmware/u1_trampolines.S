.syntax unified
.cpu cortex-m0
.thumb
.section .text.u1_trampolines,"ax",%progbits
.balign 4
.global u1_boot_gateway
.type u1_boot_gateway,%function
.thumb_func
u1_boot_gateway:
    /* After stock boot/scatter loading, use the larger retained main stack.
     * LCD SRAM20004000..20007fff powers off in PM1/PM2 and cannot hold state
     * or a sleeping call stack. Stock bootloader stack/heap stay unchanged. */
    ldr r0,=__ss_stack_top
    mov sp,r0
    bl u1_runtime_init
    ldr r0,=0xc1a9
    blx r0
4:
    b 4b
.size u1_boot_gateway,.-u1_boot_gateway
.ltorg
.balign 4
.global u1_original_key
.type u1_original_key,%function
.thumb_func
u1_original_key:
    /* Original instructions 0x10b58..0x10b5f. */
    push {r4,r5,r6,r7,lr}
    sub sp,#0x7c
    mov r5,r0
    mov r6,r1
    push {r3}
    push {r3}
    ldr r3,=0x10b61
    str r3,[sp,#4]
    pop {r3,pc}
.size u1_original_key,.-u1_original_key
.ltorg
.balign 4
.global u1_original_screen
.type u1_original_screen,%function
.thumb_func
u1_original_screen:
    /* Original11ec8..11ecf, before its first PC-relative literal. */
    push {r3,r4,r5,r6,r7,lr}
    movs r4,#0
    movs r0,#0
    str r0,[sp]
    push {r3}
    push {r3}
    ldr r3,=0x11ed1
    str r3,[sp,#4]
    pop {r3,pc}
.size u1_original_screen,.-u1_original_screen
.ltorg
.balign 4
.global ap_capture_packet_tail
.type ap_capture_packet_tail,%function
.thumb_func
ap_capture_packet_tail:
    /* Return status:r0 and timestamp:r1 as an AAPCS uint64_t. */
    push {r4,r5,r6,lr}
    ldr r2,=0x50001010
    ldr r3,=0x40001004
    ldr r4,=4096
    ldr r5,=0x1000
1:
    ldr r0,[r2]
    /* CRC-filtered packets can finish with TRER still set. EOPF is the
     * physical end flag; only the counter timestamp determines its time. */
    tst r0,r5
    bne 2f
    movs r1,#3
    ands r1,r0
    cmp r1,#1
    bne 2f
    subs r4,#1
    bne 1b
2:
    ldr r1,[r3]
    mvns r1,r1
    pop {r4,r5,r6,pc}
.size ap_capture_packet_tail,.-ap_capture_packet_tail
.ltorg
.balign 4
.global ap_launch_packet
.type ap_launch_packet,%function
.thumb_func
ap_launch_packet:
    /* r0=anchor, r1=deadline, r2=mode, r3=TX header. Timer1 VALUE is at+4.
     * Launch modes remain experimental until physical RF is verified. */
    push {r3,r4,r5,r6,r7,lr}
    mov r4,r0
    mov r5,r1
    mov r6,r2
    mov r7,r3
    /* Reserve40 timer ticks for the fixed final Cortex-M0 MMIO path. The
     * previous check bounded the timer READ, allowing the later strobe to
     * exceed the2us deadline. RF settling still needs physical validation. */
    subs r4,#40
    subs r5,#40
    ldr r2,=0x40001004
    ldr r3,=200000
5:
    ldr r0,[r2]
    mvns r0,r0
    subs r1,r0,r4
    bpl 6f
    subs r3,#1
    bne 5b
    b 8f
6:
    subs r1,r0,r5
    bgt 8f
    cmp r6,#6
    beq 11f
    cmp r6,#7
    beq 7f
    cmp r6,#1
    beq 7f
    ldr r2,=0x50001300
    ldr r3,[r2]
    movs r1,#2
    orrs r3,r1
    str r3,[r2]
    cmp r6,#4
    blo 10f
    /* Compare the original engine-before-header ordering. */
    adds r2,#4
    str r7,[r2]
    b 9f
10:
    cmp r6,#2
    beq 9f
7:
    ldr r2,=0x50001004
    movs r3,#0xd0
    str r3,[r2]
    b 9f
11:
    /* Mode6: stock engine/header/FIFO order; release the last byte at the
     * anchor. Empty payloads use the header write itself instead. */
    cmp r7,#0
    bpl 12f
    ldr r2,=0x50001400
    movs r1,#255
    ands r1,r7
    lsrs r3,r7,#8
    movs r0,#255
    ands r3,r0
    strb r1,[r2,r3]
    b 9f
12:
    ldr r2,=0x50001304
    str r7,[r2]
9:
    movs r0,#1
    pop {r3,r4,r5,r6,r7,pc}
8:
    movs r0,#0
    pop {r3,r4,r5,r6,r7,pc}
.size ap_launch_packet,.-ap_launch_packet
.ltorg
.balign 4
.global u1_poll_gateway
.type u1_poll_gateway,%function
.thumb_func
u1_poll_gateway:
    /* Restore the caller's complete stack before entering the stock poll.
     * In particular, its OTA loop can remain inside poll until reset. */
    push {r0,r1,r2,r3,r4,lr}
    bl u1_poll_prepare
    cmp r0,#0
    beq 3f
    ldr r4,[sp,#20]
    mov lr,r4
    pop {r0,r1,r2,r3,r4}
    add sp,#4
    b u1_original_poll
3:
    str r0,[sp]
    pop {r0,r1,r2,r3,r4,pc}
.size u1_poll_gateway,.-u1_poll_gateway
.balign 4
.global u1_original_poll
.type u1_original_poll,%function
.thumb_func
u1_original_poll:
    /* Original 0x41ac..0x41bb; PC-relative literals are relocated explicitly. */
    push {r4,r5,r6,r7,lr}
    ldr r0,=0x20000270
    sub sp,#0x14
    ldrb r1,[r0]
    movs r5,#0
    ldr r7,=0x20000330
    ldr r4,=0x20000369
    cmp r1,#3
    /* Preserve the CMP flags and r3 before the original conditional branch. */
    push {r3}
    push {r3}
    ldr r3,=0x41bd
    str r3,[sp,#4]
    pop {r3,pc}
.size u1_original_poll,.-u1_original_poll
.ltorg
