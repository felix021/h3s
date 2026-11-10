/* Append one CRC-covered JLFS resource in the existing flash padding.
 * Runtime XIP base 0x06000000 corresponds to flash+0x2020.
 * Original final entry ends at flash+0xe5073. New header is 32 bytes;
 * one padding byte makes the payload four-byte aligned at flash+0xe5094.
 */
original_key_handled = 0x0602530a;
original_key_context = 0x06017958;
original_key_resume = 0x0602536a;
original_source_read_resume = 0x060059d4;
original_open_resume = 0x0607884c;
SECTIONS
{
    .site.source_read 0x060059ce : { *(.site.source_read) }
    .site.preset 0x06025030 : { *(.site.preset) }
    .site.custom 0x0602510e : { *(.site.custom) }
    .site.key 0x06025364 : { *(.site.key) }
    .site.open 0x06078846 : { *(.site.open) }
    .payload 0x060e3074 : {
        *(.text.hooks)
        *(.text*)
        *(.rodata*)
    }
    .data : { *(.data*) }
    .bss : { *(.bss*) *(COMMON) }
    /DISCARD/ : { *(.comment*) *(.note*) *(.llvm*) *(.debug*) }
}
ASSERT(SIZEOF(.site.source_read) == 6, "source-read hook must be six bytes")
ASSERT(SIZEOF(.site.preset) == 6, "preset hook must be six bytes")
ASSERT(SIZEOF(.site.custom) == 6, "custom hook must be six bytes")
ASSERT(SIZEOF(.site.key) == 6, "key hook must be six bytes")
ASSERT(SIZEOF(.site.open) == 6, "open hook must be six bytes")
ASSERT(SIZEOF(.data) == 0, "no new initialized RAM")
ASSERT(SIZEOF(.bss) == 0, "no new BSS allocation")
ASSERT(SIZEOF(.payload) <= 0xf6c, "payload exceeds flash padding")
