/* Behavioral checks for the C patch logic. ISA glue is audited separately. */
#include <assert.h>
#include <stdio.h>
#include <string.h>

volatile unsigned char h3s_test_qcy[5500];
static unsigned int send_count, restore_count;
static unsigned int last_bypass;
static int active;
static int simulated_gain;
extern void eq_record_preset_source(void);
extern void eq_record_custom_source(void);
extern void eq_open_config(unsigned char *, const unsigned char *);
extern int eq_toggle(void);

int h3s_test_send(int uuid, const char *name, const void *param, int size)
{
    const unsigned int *words = param;
    assert(uuid == 0x737b && size == 44);
    assert(!strcmp(name, (send_count % 2) ? "Eq0Media" : "MusicEqBt"));
    assert(words[0] == 2 && words[1] <= 1);
    for (int i = 2; i < 11; ++i) assert(words[i] == 0);
    last_bypass = words[1];
    ++send_count;
    /* Reopening may reset to factory coefficients; restoration must follow. */
    if (active && !words[1]) simulated_gain = -4;
    return active;
}

int h3s_test_restore(int unused, int event)
{
    assert(unused == 0 && event == 10 && !last_bypass);
    ++restore_count;
    simulated_gain = 7;  /* Current App curve, including edits made while off. */
    eq_record_custom_source();
    return 0;
}

int main(void)
{
    unsigned char saved_curve[143];
    memset((void *)&h3s_test_qcy[5356], 0x35, 143);
    h3s_test_qcy[5356] = 126;
    memcpy(saved_curve, (void *)&h3s_test_qcy[5356], 143);
    active = 1;
    simulated_gain = 7;
    eq_record_custom_source();
    assert(eq_toggle() == 1 && h3s_test_qcy[42] == 129);
    assert(send_count == 2 && last_bypass == 1 && restore_count == 0);
    eq_record_preset_source();
    assert(h3s_test_qcy[42] == 130); /* Mode change preserves OFF. */
    eq_record_custom_source();
    assert(h3s_test_qcy[42] == 129); /* App edit preserves OFF. */

    /* Recreated playback nodes respect OFF; call and unrelated EQs do not. */
    unsigned char context[80] = {0};
    unsigned char config[8] = {0};
    const char *names[] = {"MusicEqBt", "Eq0Media", "EscoDlEq", "EscoUlEq", "spk_eq"};
    for (unsigned int i = 0; i < sizeof(names) / sizeof(names[0]); ++i) {
        memset(context, 0x5a, sizeof(context));
        strcpy((char *)context, names[i]);
        eq_open_config(context, config);
        assert(context[35] == (i < 2));
        assert(context[34] == 0x5a && context[36] == 0x5a);
    }
    assert(eq_toggle() == 2 && h3s_test_qcy[42] == 1);
    assert(last_bypass == 0 && restore_count == 1 && simulated_gain == 7);
    assert(!memcmp(saved_curve, (void *)&h3s_test_qcy[5356], 143));

    /* Toggling with no active stream must still affect the next node opening. */
    active = 0;
    assert(eq_toggle() == 1);
    strcpy((char *)context, "MusicEqBt");
    eq_open_config(context, config);
    assert(context[35] == 1);
    assert(eq_toggle() == 2);
    eq_open_config(context, config);
    assert(context[35] == 0);
    /* Respect file-level bypass when the user switch is ON. */
    config[4] = 1;
    eq_open_config(context, config);
    assert(context[35] == 1);

    /* Spatial mode is intentionally excluded and must not mutate state. */
    unsigned int count_before = send_count;
    unsigned char source_before = h3s_test_qcy[42];
    h3s_test_qcy[5356] = 10;
    assert(eq_toggle() == 0 && send_count == count_before);
    assert(h3s_test_qcy[42] == source_before);
    puts("PASS: toggle, curve restore, edits/mode changes, node recreation, target isolation, inactive stream, spatial exclusion");
    return 0;
}
