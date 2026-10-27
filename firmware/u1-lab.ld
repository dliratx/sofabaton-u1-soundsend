/* Link placement for a V32-only experimental module, not an OTA image. */
ENTRY(u1_boot_gateway)
MEMORY {
    PATCH (rx) : ORIGIN = 0x18000, LENGTH = 0x6f80
    /* Only the lower16KB SRAM is retained in PM1/PM2. Upper LCD SRAM is not. */
    RAM (rw) : ORIGIN = 0x200039d8, LENGTH = 0x1a8
}
SECTIONS {
    __ss_stack_limit = 0x20003b80;
    __ss_stack_top = 0x20003ff0;
    .text : { *(.text*) *(.rodata*) } > PATCH
    .ARM.exidx : { *(.ARM.exidx*) } > PATCH
    .data : { *(.data*) } > RAM AT>PATCH
    .bss (NOLOAD) : {
        . = ALIGN(4);__ss_ram_start = .;
        *(.bss*) *(COMMON)
        . = ALIGN(4);__ss_ram_end = .;
    } > RAM
    ASSERT(SIZEOF(.data)==0,"Initialized patch RAM requires an explicit startup copy")
    ASSERT(__ss_ram_end<=__ss_stack_limit,"Patch RAM overlaps the new main stack")
    /DISCARD/ : { *(.comment*) *(.note*) }
}
