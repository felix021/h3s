/* Six-byte branch/call sites, linked at the original addresses. */
    .section .site.key,"ax",@progbits
    .global eq_site_key
eq_site_key:
    goto eq_key_hook
    nop

    .section .site.source_read,"ax",@progbits
    .global eq_site_source_read
eq_site_source_read:
    goto eq_source_read_hook
    nop

    .section .site.preset,"ax",@progbits
    .global eq_site_preset
eq_site_preset:
    call eq_record_preset_source

    .section .site.custom,"ax",@progbits
    .global eq_site_custom
eq_site_custom:
    call eq_record_custom_source

    .section .site.open,"ax",@progbits
    .global eq_site_open
eq_site_open:
    goto eq_open_hook
    nop

    .section .text.hooks,"ax",@progbits
    .global eq_key_hook
eq_key_hook:
    /* At this match point: r2=key, r3=gesture, r0=&mapped action. */
    r5 = b[r0+0] (u)
    if (r2 != 7) goto key_passthrough
    if (r3 != 2) goto key_passthrough
    call eq_toggle
    /* Existing 'handled' epilogue. Other keys retain their original mapping. */
    goto original_key_handled
key_passthrough:
    call original_key_context
    goto original_key_resume

    .global eq_source_read_hook
eq_source_read_hook:
    /* Replay the overwritten load and r0=0, masking our new source flag. */
    r1 = b[r6+42] (u)
    r1 &= 127
    r0 = 0
    goto original_source_read_resume

    .global eq_open_hook
eq_open_hook:
    /* Original r4=context, r6=file configuration, r7=context+32.
     * r0/r1/r2/r3 are overwritten before their next original use.
     */
    r0 = r4
    r1 = r6
    call eq_open_config
    r0 = [r6+4]
    goto original_open_resume
