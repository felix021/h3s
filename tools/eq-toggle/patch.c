/* Experimental H3S 0.1.1.8 patch. No SDK headers or dynamic/static RAM allocation.
 * Only bit 7 of QCY's existing EQ source byte is added; hooks preserve bits 0..6.
 * Source byte readers must mask that bit, and BOTH original writers are hooked.
 * State is volatile: power-on defaults to EQ enabled. App curve bytes are intact.
 */
typedef unsigned char u8;
typedef unsigned int u32;

#ifdef H3S_EQ_HOST_TEST
extern volatile u8 h3s_test_qcy[];
#define QCY h3s_test_qcy
extern int h3s_test_send(int uuid, const char *name, const void *param, int size);
extern int h3s_test_restore(int unused, int event);
#define SEND h3s_test_send
#define RESTORE h3s_test_restore
#else
#define QCY ((volatile u8 *)0x001265c0)
#define SEND ((int (*)(int, const char *, const void *, int))0x0607538a)
#define RESTORE ((int (*)(int, int))0x0602619a)
#endif

#define SOURCE_BYTE 42
#define OFF_FLAG 128

/* Exactly the two names used by the original custom-EQ application loop.
 * Do not bypass EscoDlEq, EscoUlEq, speaker correction, ANC, or other DSP nodes.
 */
static const char music_name[] = "MusicEqBt";
static const char media_name[] = "Eq0Media";

static int same_name(const u8 *name, const char *wanted)
{
    unsigned int i;
    for (i = 0; i < 16; ++i) {
        if (name[i] != (u8)wanted[i]) return 0;
        if (!wanted[i]) return 1;
    }
    return 0;
}

void eq_record_preset_source(void)
{
    QCY[SOURCE_BYTE] = (QCY[SOURCE_BYTE] & OFF_FLAG) | 2;
}

void eq_record_custom_source(void)
{
    QCY[SOURCE_BYTE] = (QCY[SOURCE_BYTE] & OFF_FLAG) | 1;
}

/* Called at the original file-config -> runtime-bypass assignment.
 * The rest of the original EQ-open routine still executes unchanged.
 */
void eq_open_config(u8 *context, const u8 *file_config)
{
    u8 bypass = file_config[4];
    if ((QCY[SOURCE_BYTE] & OFF_FLAG) &&
        (same_name(context, music_name) || same_name(context, media_name))) {
        bypass = 1;
    }
    context[35] = bypass;
}

int eq_toggle(void)
{
    /* App type 10 activates the separate spatial-effect path. Do not pretend
     * to switch that effect using the ordinary parameter-EQ bypass command.
     */
    if (QCY[5356] == 10) return 0;

    u8 off = (QCY[SOURCE_BYTE] ^ OFF_FLAG) & OFF_FLAG;
    QCY[SOURCE_BYTE] = (QCY[SOURCE_BYTE] & 127) | off;
    /* Original EQ control ABI: 44 bytes, u32 type at +0, union at +4.
     * Volatile stores avoid compiler-generated memset/runtime dependencies.
     */
    volatile u32 command[11];
    unsigned int i;
    for (i = 0; i < 11; ++i) command[i] = 0;
    command[0] = 2;
    command[1] = off ? 1 : 0;
    SEND(0x737b, music_name, (const void *)command, sizeof(command));
    SEND(0x737b, media_name, (const void *)command, sizeof(command));
    if (!off) {
        /* The original stream callback restores the CURRENT audio mode:
         * music -> App curve; game/movie -> their existing preset path.
         * Reopening a filter alone can load factory file parameters.
         */
        RESTORE(0, 10);
    }
    return off ? 1 : 2;
}
