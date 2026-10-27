/* Offline ARM test layout. This ELF is NOT a U1 OTA image. */
ENTRY(ss_init)
MEMORY { FLASH (rx) : ORIGIN = 0x18000, LENGTH = 0x6000 }
SECTIONS {
    .text : { *(.text*) *(.rodata*) } > FLASH
    .ARM.exidx : { *(.ARM.exidx*) } > FLASH
    /DISCARD/ : { *(.comment*) *(.note*) }
}
