#!/usr/bin/env python3
"""Build the production output helper against explicit external NDK/TinyALSA."""
import argparse
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--compiler',type=Path,required=True)
    p.add_argument('--tinyalsa',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();a.out.parent.mkdir(parents=True,exist_ok=True)
    sources=['limits.c','mixer.c','pcm.c','pcm_plugin.c','pcm_hw.c','snd_card_plugin.c','mixer_plugin.c','mixer_hw.c']
    subprocess.run([str(a.compiler),'-O2','-Wall','-Wextra','-Werror','-ffunction-sections','-fdata-sections','-Wl,--gc-sections',
                    '-I'+str(a.tinyalsa/'include'),str(ROOT/'tools/audio_init.c'),
                    *[str(a.tinyalsa/'src'/s) for s in sources],'-ldl','-o',str(a.out)],check=True)
