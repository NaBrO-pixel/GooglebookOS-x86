#!/usr/bin/env python3
"""Report instructions in extracted OS files that an older CPU cannot execute.

  isa_audit.py --cpu haswell DIRECTORY [DIRECTORY ...]

Reads files only. A hit is not proof of a crash: libraries such as BoringSSL
select SHA/VAES paths at run time. Hits spread across ordinary binaries do
indicate a compiler baseline the CPU cannot run.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import subprocess

# Instruction-set extensions beyond Haswell that compilers or hand-written
# kernels emit, keyed by /proc/cpuinfo flag. Matched against objdump mnemonics.
EXTENSIONS = {
    'sha_ni': r'sha1rnds4|sha1nexte|sha1msg[12]|sha256rnds2|sha256msg[12]',
    'gfni': r'v?gf2p8(affineqb|affineinvqb|mulb)',
    'avx_vnni': r'(\{vex\} )?vpdp(busd|busds|wssd|wssds)',
    'adx': r'adcx|adox',
    'rdseed': r'rdseed',
    'clflushopt': r'clflushopt',
    'clwb': r'clwb',
    'xsavec': r'xsavec(64)?',
    'xsaves': r'xsaves(64)?|xrstors(64)?',
    'movdiri': r'movdiri',
    'movdir64b': r'movdir64b',
    'serialize': r'serialize',
    'waitpkg': r'umonitor|umwait|tpause',
    'rdpid': r'rdpid',
    'avx512f': r'k(mov|and|or|xor|not|test|shift)[bwdq]\w*',
}
# Mnemonics whose 256-bit form needs VAES/VPCLMULQDQ rather than AES-NI/PCLMUL.
WIDE = {'vaes': r'vaes(enc|enclast|dec|declast)', 'vpclmulqdq': r'vpclmulqdq'}
CPUS = {
    'haswell': {'sse4_2', 'popcnt', 'avx', 'avx2', 'fma', 'bmi1', 'bmi2', 'movbe', 'f16c', 'aes', 'pclmulqdq'},
}
PATTERN = re.compile(r'^\s*[0-9a-f]+:\s+(?:\{vex\} )?(\S+)\s*(.*)$')


def elf_files(roots):
    for root in roots:
        for path in Path(root).rglob('*'):
            if path.is_symlink() or not path.is_file():continue
            with path.open('rb') as f:
                if f.read(4)==b'\x7fELF':yield path


def scan(path, unsupported):
    out = subprocess.run(['objdump', '-d', '--no-show-raw-insn', str(path)],
        capture_output=True, text=True, errors='replace').stdout
    counts = {}
    for line in out.splitlines():
        m = PATTERN.match(line)
        if not m:continue
        mnemonic, operands = m[1], m[2]
        if 'avx512f' in unsupported and '%zmm' in operands:
            counts['avx512f'] = counts.get('avx512f', 0)+1
        for flag, regex in EXTENSIONS.items():
            if flag in unsupported and re.fullmatch(regex, mnemonic):
                counts[flag] = counts.get(flag, 0)+1
        for flag, regex in WIDE.items():
            if flag in unsupported and '%ymm' in operands and re.fullmatch(regex, mnemonic):
                counts[flag] = counts.get(flag, 0)+1
    return counts


def host_flags():
    for line in Path('/proc/cpuinfo').read_text().splitlines():
        if line.startswith('flags'):return set(line.split(':', 1)[1].split())
    return set()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('roots', nargs='+', type=Path)
    p.add_argument('--cpu', default='host', help='host (this machine) or '+', '.join(CPUS))
    p.add_argument('--json', type=Path, help='Write the full per-file report here')
    a = p.parse_args()
    available = host_flags() if a.cpu=='host' else CPUS[a.cpu]
    unsupported = (set(EXTENSIONS)|set(WIDE))-available
    files = list(elf_files(a.roots))
    with ThreadPoolExecutor(os.cpu_count() or 2) as pool:
        results = dict(zip(files, pool.map(lambda f: scan(f, unsupported), files)))
    hits = {str(f): c for f, c in results.items() if c}
    by_flag = {}
    for f, c in hits.items():
        for flag in c:by_flag.setdefault(flag, []).append(f)
    summary = dict(cpu=a.cpu, elf_files=len(files), files_with_unsupported=len(hits),
        by_extension={flag: dict(files=len(v), examples=sorted(v)[:8]) for flag, v in sorted(by_flag.items())})
    if a.json:a.json.write_text(json.dumps(dict(summary, files=hits), indent=2)+'\n')
    print(json.dumps(summary, indent=2))


if __name__=='__main__':
    main()
