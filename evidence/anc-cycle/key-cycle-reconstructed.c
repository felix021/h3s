/* Manually reconstructed from QCY H3S 0.1.1.8 machine code.
 * This is explanatory pseudocode, NOT recovered original source or a linked patch.
 * app.bin SHA256 18f3d573a5b1a15f488a6803ce4f828634e602015c85e92d205bbe62d7060041
 * Actual code: 0x06018b60..0x06018be4; named anc_next key handler: 0x06018cc0.
 * Omitted training guard, IRQ lock and fade flag; all next-mode selection branches retained.
 * Internal mode IDs: OFF=1, ANC_ON=2, TRANSPARENT=3.
 * QCY app wire IDs DIFFER: OFF=2, ANC_ON=1, TRANSPARENT=3.
 */
static const unsigned char original_cycle[3] = {1, 3, 2}; /* 0x0609e510 */
static const unsigned char candidate_cycle[3] = {2, 3, 2};

unsigned char reconstructed_next(const unsigned char table[3],
                                 unsigned char current, unsigned char enabled)
{
    unsigned char next = 0;
    for (unsigned char i = 0; i < 3; ++i) {
        if (table[i] != current) continue;
        next = i + 1;
        if (next >= 3) next = 0;
        if (!(enabled & (1u << table[next]))) {
            ++next;
            if (next >= 3) next = 0;
        }
        break; /* FIRST match, relevant to the duplicated ANC_ON entry. */
    }
    return table[next];
}

/* QCY command 0x17 registry record at app.bin+0xa9ae8:
 * [0x0600dddc, 0x06025c84, 0x06025ca4, 0, 0, 0x06025d32, 0x17]
 * Getter 0x06025c84 returns the 3 bytes at 0x0012666b.
 * Setter 0x06025ca4 checks length >= 3 and device state guards, copies the
 * 3 bytes there, then schedules 0x06025d6a on task "app_core".
 * Callback 0x06025d6a calls 0x06012be2 (3-byte saved-setting write, key16)
 * then 0x0600dcee(mode, scene, strength, 1).
 * At 0x0600dcee the wire mode is mapped to ANC core:
 *   QCY1 -> internal2 (ANC), QCY2 -> internal1 (OFF), QCY3 -> internal3 (TRANS).
 * This direct-selection path never indexes original_cycle.
 */
