/* Initialize ordinary HDA output controls. No test tone, recording or dumps. */
#include <tinyalsa/asoundlib.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

static void unmute(struct mixer *m, const char *name) {
    struct mixer_ctl *c=mixer_get_ctl_by_name(m,name);
    if (!c || mixer_ctl_get_type(c)!=MIXER_CTL_TYPE_BOOL) return;
    for (unsigned i=0;i<mixer_ctl_get_num_values(c);i++)
        if (mixer_ctl_set_value(c,i,1)<0) fprintf(stderr,"Cannot unmute %s\n",name);
}

static void volume(struct mixer *m, const char *name) {
    struct mixer_ctl *c=mixer_get_ctl_by_name(m,name);
    if (!c || mixer_ctl_get_type(c)!=MIXER_CTL_TYPE_INT) return;
    /* Android supplies listening-volume attenuation. Use the driver's normal
     * range only, without amplifier boost or microphone control changes. */
    for (unsigned i=0;i<mixer_ctl_get_num_values(c);i++)
        if (mixer_ctl_set_percent(c,i,100)<0) fprintf(stderr,"Cannot initialize %s\n",name);
}

int main(int argc, char **argv) {
    if (argc!=2 || strcmp(argv[1],"--init-speakers")) return 64;
    struct mixer *m=NULL;
    for (unsigned i=0;i<80 && !m;i++) {
        m=mixer_open(0);
        if (!m) usleep(250000);
    }
    if (!m) { fputs("No HDA output card after 20 seconds\n",stderr); return 2; }
    unmute(m,"Master Playback Switch");
    unmute(m,"Speaker Playback Switch");
    unmute(m,"Headphone Playback Switch");
    volume(m,"Master Playback Volume");
    volume(m,"Speaker Playback Volume");
    volume(m,"PCM Playback Volume");
    mixer_close(m);
    return 0;
}
