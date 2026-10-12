#!/usr/bin/env python3
"""Fetch pinned public inputs and assemble fresh Intel Live/Install USB media.

Debian 13 x86_64 only. Writes regular files inside the chosen build directory;
never flashes a USB or opens a physical disk. OS downloads remain local.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import stat
import subprocess
import sys
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT/'profiles/sources.json'
TOOLS = ('python3', 'curl', 'unzip', 'cp', 'lz4', 'cpio', 'gzip', 'dpkg-deb',
    'depmod', 'ldd', 'grub-mkstandalone', 'grub-mkimage', 'mcopy', 'mmd',
    'mkfs.fat', 'fsck.fat', 'mkfs.ext4', 'e2fsck', 'blkid', 'fallocate',
    'efibootmgr', 'make', 'gcc', 'autoconf', 'automake', 'libtoolize', 'pkg-config')


def run(args, **kwargs):
    return subprocess.run(list(map(str,args)), check=True, **kwargs)


def digest(path):
    if not path.is_file() or not stat.S_ISREG(path.stat().st_mode):
        raise ValueError('Expected a regular input file: '+str(path))
    with path.open('rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()


def verify(path, spec):
    if path.is_symlink() or not path.is_file(): raise ValueError('Missing regular cached file: '+path.name)
    if 'bytes' in spec and path.stat().st_size!=spec['bytes']: raise ValueError('Wrong input size: '+path.name)
    if digest(path)!=spec['sha256']: raise ValueError('SHA-256 mismatch: '+path.name)


def resolve_ci(url):
    with urllib.request.urlopen(url, timeout=60) as f: text=f.read(2*1024**2).decode()
    match=re.search(r'var JSVariables = (\{.*?\});',text)
    if not match: raise ValueError('Android CI artifact page no longer resolves; pinned input unavailable')
    resolved=json.loads(match[1]).get('artifactUrl')
    if not resolved or not resolved.startswith('https://'): raise ValueError('Expected HTTPS CI artifact URL')
    return resolved


def fetch(spec, cache):
    path=cache/spec['filename']
    if path.exists():
        print('Checking cached input:',path.name,flush=True); verify(path,spec); return path
    partial=path.with_name(path.name+'.part')
    if partial.is_symlink() or (partial.exists() and not partial.is_file()):
        raise ValueError('Unsafe download partial file')
    if partial.exists() and partial.stat().st_size>=spec.get('bytes',float('inf')):
        try: verify(partial,spec)
        except ValueError: partial.unlink()
        else: partial.replace(path); return path
    url=resolve_ci(spec['url']) if spec.get('resolve_ci') else spec['url']
    start=partial.stat().st_size if partial.exists() else 0
    request=urllib.request.Request(url,headers={'User-Agent':'GooglebookOS-x86-source-builder',
        **({'Range':f'bytes={start}-'} if start else {})})
    print('Downloading:',path.name,flush=True)
    with urllib.request.urlopen(request,timeout=120) as f:
        if start and f.status==206:
            if not f.headers.get('Content-Range','').startswith(f'bytes {start}-'):
                raise ValueError('Server returned the wrong download range')
            mode='ab'
        else: start=0; mode='wb'
        total=spec.get('bytes',0); last=0; done=start
        with partial.open(mode) as out:
            while chunk:=f.read(1024**2):
                out.write(chunk); done+=len(chunk)
                if total and done>total: raise ValueError('Download exceeds pinned input size')
                if time.monotonic()-last>=5:
                    print(f'  {100*done/total:.1f}% ({done:,}/{total:,} bytes)' if total else f'  {done:,} bytes',flush=True)
                    last=time.monotonic()
    verify(partial,spec); partial.replace(path)
    print('SHA-256 verified:',path.name,flush=True); return path


def member_destination(root, name):
    parts=PurePosixPath(name)
    if parts.is_absolute() or '..' in parts.parts or '\\' in name or ':' in name:
        raise ValueError('Unsafe ZIP member')
    path=root.joinpath(*parts.parts)
    if not path.resolve().is_relative_to(root.resolve()): raise ValueError('ZIP path escapes output')
    return path


def unzip(archive, root):
    root.mkdir()
    with zipfile.ZipFile(archive) as z:
        for entry in z.infolist():
            target=member_destination(root,entry.filename)
            mode=entry.external_attr>>16
            if entry.is_dir(): target.mkdir(parents=True,exist_ok=True); continue
            target.parent.mkdir(parents=True,exist_ok=True)
            if stat.S_ISLNK(mode):
                link=z.read(entry).decode()
                if Path(link).is_absolute() or not (target.parent/link).resolve().is_relative_to(root.resolve()):
                    raise ValueError('ZIP symlink escapes output')
                target.symlink_to(link)
            else:
                with z.open(entry) as source,target.open('xb') as out: shutil.copyfileobj(source,out,1024**2)
                target.chmod(mode&0o777 or 0o644)
    children=list(root.iterdir())
    if len(children)!=1 or not children[0].is_dir(): raise ValueError('Expected one source archive root')
    return children[0]


def owned_cleanup(path, work):
    """Only failed, generated stage directories directly below our marked workspace."""
    if path.name not in ('native','release','donor','maintenance','packages','recovery'):
        raise ValueError('Not a generated stage directory')
    if path.is_symlink() or path.resolve().parent!=work.resolve(): raise ValueError('Unsafe stage cleanup')
    if path.exists(): shutil.rmtree(path)


def stage(name, number): print(f'\n[{number}/9] {name}',flush=True)


def run_logged(args, work, **kwargs):
    with (work/'build-tools.log').open('ab') as output:
        try: run(args,stdout=output,stderr=subprocess.STDOUT,**kwargs)
        except subprocess.CalledProcessError:
            print('Tool details:',work/'build-tools.log',flush=True); raise


def image_alias(result):
    """Provide the .img name accepted by graphical USB flashers, without a second copy."""
    alias=result.with_suffix('.img')
    if not alias.exists(): os.link(result,alias)
    if not os.path.samefile(result,alias): raise ValueError('Unexpected image alias')
    return alias


def extract_recovery(archive, root, spec):
    root.mkdir(); raw=root/'recovery.raw'
    with zipfile.ZipFile(archive) as z:
        entry=z.getinfo(spec['entry'])
        if entry.file_size!=spec['raw_bytes']: raise ValueError('Wrong recovery member size')
        with z.open(entry) as source,raw.open('xb') as out:
            done=0; last=0
            while chunk:=source.read(4*1024**2):
                out.write(chunk); done+=len(chunk)
                if time.monotonic()-last>=5:
                    print(f'  Extracting recovery: {100*done/entry.file_size:.1f}%',flush=True); last=time.monotonic()
    verify(raw,{'bytes':spec['raw_bytes'],'sha256':spec['raw_sha256']}); return raw


def verify_donor(path, expected):
    for name,sha in expected.items():
        source=path/name
        if not source.resolve().is_relative_to(path.resolve()): raise ValueError('Donor path escapes input')
        verify(source,{'sha256':sha})


def maintenance_root(extracted, work, version):
    root=work/'maintenance'; root.mkdir()
    for name in ('dev','proc','sys','tmp','run','stage','bin','sbin','lib64','usr/lib','usr/bin'):
        (root/name).mkdir(parents=True,exist_ok=True)
    def copy(source,name):
        target=root/name.lstrip('/'); target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,target)
    busybox=extracted/'usr/bin/busybox'; copy(busybox,'/bin/busybox')
    for name in subprocess.check_output([str(busybox),'--list'],text=True).splitlines():
        target=root/'bin'/name
        if not target.exists(): target.symlink_to('busybox')
    (root/'sbin/modprobe').symlink_to('../bin/modprobe')
    python=Path(sys.executable).resolve(); copy(python,'/usr/bin/python3.13')
    (root/'usr/bin/python3').symlink_to('python3.13')
    copy(shutil.which('blkid'),'/bin/blkid')
    stdlib=Path('/usr/lib/python3.13')
    shutil.copytree(stdlib,root/'usr/lib/python3.13',ignore=shutil.ignore_patterns('__pycache__','test','idlelib','tkinter','ensurepip'))
    shutil.copytree(extracted/'usr/lib/modules'/version,root/'lib/modules'/version)
    run(['depmod','-b',root,version])
    libs=set()
    for binary in (python,Path(shutil.which('blkid')),*stdlib.joinpath('lib-dynload').glob('*.so')):
        p=subprocess.run(['ldd',str(binary)],text=True,capture_output=True)
        for line in p.stdout.splitlines(): libs.update(re.findall(r'(/[^\s()]+)',line))
    for library in libs:
        if Path(library).is_file(): copy(library,library)
    return root,extracted/'boot'/('vmlinuz-'+version)


def preflight():
    if platform.system()!='Linux' or platform.machine()!='x86_64': raise ValueError('Use x86_64 Debian 13, native or WSL')
    release=Path('/etc/os-release').read_text()
    if 'ID=debian\n' not in release or 'VERSION_ID="13"' not in release:
        raise ValueError('This automated build supports Debian 13 only')
    if sys.version_info[:2]!=(3,13): raise ValueError('Run with Debian 13 system Python 3.13')
    missing=[name for name in TOOLS if not shutil.which(name)]
    if missing: raise ValueError('Install the packages in docs/BUILD.md; missing: '+', '.join(missing))


def build(args):
    lock=json.loads(LOCK.read_text()); work=args.work.resolve(); cache=(args.cache or work/'cache').resolve()
    build_files=[Path(__file__),ROOT/'tools/native_port.py',ROOT/'tools/build_release.py',
        ROOT/'tools/build_audio.py',ROOT/'tools/audio_init.c',ROOT/'profiles/intel-xe.json',
        *sorted((ROOT/'src').rglob('*.py'))]
    fingerprint=hashlib.sha256(LOCK.read_bytes()+b''.join(p.read_bytes() for p in build_files)).hexdigest()
    if args.plan:
        print(json.dumps({'host':'Debian 13 x86_64','work':str(work),'steps':9,
            'downloads':[s['url'] for s in [*lock['archives'].values(),*lock['packages'].values(),*lock['modules'].values()]],
            'output':str(work/'release/googlebook-x86.raw'),'physical_disk_access':False},indent=2)); return
    preflight()
    if work.is_symlink(): raise ValueError('Build directory cannot be a symlink')
    marker=work/'builder.json'
    if work.exists() and not marker.is_file(): raise ValueError('Choose an empty/new build directory')
    work.mkdir(parents=True,exist_ok=True); cache.mkdir(parents=True,exist_ok=True)
    if marker.exists() and json.loads(marker.read_text()).get('fingerprint')!=fingerprint:
        raise ValueError('Builder or source pins changed; choose a new --work directory')
    marker.write_text(json.dumps({'format':1,'fingerprint':fingerprint})+'\n')
    if (work/'complete.json').exists():
        receipt=json.loads((work/'complete.json').read_text())
        verify(work/'release/googlebook-x86.raw',receipt['image'])
        image_alias(work/'release/googlebook-x86.raw')
        print('Already built and SHA-256 verified:',work/'release/googlebook-x86.raw'); return
    stage('Fetch and verify pinned inputs (downloads resume)',1)
    inputs={name:fetch(spec,cache) for name,spec in lock['archives'].items() if name!='recovery' or not args.recovery_file}
    packages={name:fetch(spec,cache) for name,spec in lock['packages'].items()}
    sources=work/'sources'; sources.mkdir(exist_ok=True); checkouts={}
    for name in ('upstream','erofs','tinyalsa'):
        dest=sources/name
        if not dest.exists(): checkout=unzip(inputs[name],dest)
        else:
            dirs=[p for p in dest.iterdir() if p.is_dir()]
            if len(dirs)!=1: raise ValueError('Incomplete source extraction; choose a new build directory')
            checkout=dirs[0]
        checkouts[name]=checkout
    upstream=checkouts['upstream']; sys.path.insert(0,str(upstream))
    sys.path.insert(0,str(ROOT/'tools'))
    stage('Build local EROFS tools and speaker helper',2)
    erofs=checkouts['erofs']; prefix=work/'toolchain'
    if not (prefix/'bin/mkfs.erofs').exists():
        run_logged(['sh','autogen.sh'],work,cwd=erofs)
        run_logged(['sh','configure','--prefix='+str(prefix)],work,cwd=erofs)
        run_logged(['make','-j',str(min(os.cpu_count() or 2,8))],work,cwd=erofs)
        run_logged(['make','install'],work,cwd=erofs)
    os.environ['PATH']=str(prefix/'bin')+':'+os.environ['PATH']
    ndk=args.ndk_dir
    if ndk:
        # Reuse is permitted only after comparing the SDK's compiler against its verified ZIP.
        with zipfile.ZipFile(inputs['ndk']) as z:
            name='android-ndk-r30/toolchains/llvm/prebuilt/linux-x86_64/bin/clang-21'
            candidates=[n for n in z.namelist() if re.fullmatch(r'android-ndk-r30/toolchains/llvm/prebuilt/linux-x86_64/bin/clang-\d+',n)]
            if len(candidates)!=1: raise ValueError('Expected one NDK compiler executable')
            name=candidates[0]; actual=ndk/str(PurePosixPath(name).relative_to('android-ndk-r30'))
            with z.open(name) as compiler: expected=hashlib.file_digest(compiler,'sha256').hexdigest()
            verify(actual,{'sha256':expected})
    else:
        dest=sources/'ndk'
        ndk=dest/'android-ndk-r30' if dest.exists() else unzip(inputs['ndk'],dest)
    helper=work/'audio-init'
    run([sys.executable,ROOT/'tools/build_audio.py','--compiler',ndk/'toolchains/llvm/prebuilt/linux-x86_64/bin/x86_64-linux-android35-clang','--tinyalsa',checkouts['tinyalsa'],'--out',helper])
    stage('Extract stock recovery and matching native boot files',3)
    for name in ('native','release','packages','maintenance'): owned_cleanup(work/name,work)
    if args.recovery_file:
        raw=args.recovery_file.resolve(); verify(raw,{'bytes':lock['archives']['recovery']['raw_bytes'],'sha256':lock['archives']['recovery']['raw_sha256']})
    else:
        if (work/'recovery/recovery.raw').exists():
            raw=work/'recovery/recovery.raw'; verify(raw,{'bytes':lock['archives']['recovery']['raw_bytes'],'sha256':lock['archives']['recovery']['raw_sha256']})
        else:
            owned_cleanup(work/'recovery',work); raw=extract_recovery(inputs['recovery'],work/'recovery',lock['archives']['recovery'])
    for name,spec in lock['modules'].items():
        path=fetch(spec,cache); target=upstream/'modules'/name; target.parent.mkdir(exist_ok=True)
        shutil.copy2(path,target)
    run([sys.executable,upstream/'extract_lapis.py',raw,upstream/'firmware'])
    run([sys.executable,upstream/'make_initrd_x86.py',raw,upstream/'firmware',upstream/'modules',upstream/'initrd'])
    stage('Extract and authenticate Cuttlefish donor HALs',4)
    from native_port import execute_upstream, audio_extract, vendor_overlay, native_initrd, disable_trusty_declaration
    if args.donor_dir:
        donor=args.donor_dir.resolve(); verify_donor(donor,lock['donor_files'])
    else:
        donor=work/'donor'
        if donor.exists():
            try: verify_donor(donor,lock['donor_files'])
            except ValueError: owned_cleanup(donor,work)
        if not donor.exists():
            execute_upstream(upstream/'unpack_cuttlefish_donor.py',[inputs['donor'],donor],
                [('        # --- KeyMint payload ---\n','        _gbos_audio_extract(vendor_file, td, out)\n')],
                {'_gbos_audio_extract':audio_extract})
            verify_donor(donor,lock['donor_files'])
    stage('Extract Intel firmware and maintenance kernel',5)
    extracted=work/'packages'; extracted.mkdir()
    for package in packages.values(): run(['dpkg-deb','-x',package,extracted])
    firmware=extracted/'usr/lib/firmware'
    stage('Prepare a fresh native image; apply graphics/audio fixes',6)
    native=work/'native'; native.mkdir()
    def overlay(src_fs,donor,files): return vendor_overlay(src_fs,donor,files,firmware,helper)
    execute_upstream(upstream/'make_lapis_security_port.py',
        ['--raw',raw,'--donor',donor,'--out',native,'--locksettings','--quiet-absent-hardware','--composer','--vmcompat','--no-initrd'],
        [('    tar = tmp / \'overlay.tar\'\n','    files.update(_gbos_native_overlay(src_fs, donor, files))\n')],
        {'_gbos_native_overlay':overlay})
    image=native/'googlebook-security.bin'
    run([sys.executable,upstream/'expand_lapis_userdata.py',image,'16'])
    disable_trusty_declaration(image,upstream,native)
    initrd=native/'initrd-native'; native_initrd(upstream,initrd)
    (native/'native-build.json').write_text(json.dumps({'format':1,'fresh_from_pinned_recovery':True,'image_name':image.name})+'\n')
    stage('Create maintenance environment',7)
    maintenance,kernel=maintenance_root(extracted,work,lock['maintenance_kernel'])
    stage('Assemble two-entry release; create fresh data and verify files',8)
    from build_release import build as release_build
    release_build(argparse.Namespace(base_image=image,upstream=upstream,kernel=upstream/'firmware/kernel',
        native_initrd=initrd,maintenance_root=maintenance,maintenance_kernel=kernel,audio_helper=helper,
        profile=ROOT/'profiles/intel-xe.json',out=work/'release',consume_generated_base=True))
    stage('Write image checksum and completion receipt',9)
    result=work/'release/googlebook-x86.raw'; sha=digest(result)
    alias=image_alias(result)
    (result.parent/'SHA256SUMS').write_text(sha+'  '+result.name+'\n'+sha+'  '+alias.name+'\n')
    (work/'complete.json').write_text(json.dumps({'format':1,'fingerprint':fingerprint,
        'image':{'bytes':result.stat().st_size,'sha256':sha},'physical_disk_access':False},indent=2)+'\n')
    print('\nREADY:',alias,'\nSHA-256:',sha,'\nNo USB or internal disk was written.',flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--work',type=Path,default=ROOT/'.build')
    p.add_argument('--cache',type=Path,help='Optional directory containing the exact pinned download filenames')
    p.add_argument('--plan',action='store_true',help='Print inputs/steps without downloading or writing')
    p.add_argument('--recovery-file',type=Path,help='Reuse an untouched official recovery RAW; full pinned hash required')
    p.add_argument('--donor-dir',type=Path,help='Reuse extracted donor only if every pinned donor-file hash matches')
    p.add_argument('--ndk-dir',type=Path,help='Reuse an NDK extracted from the pinned SDK ZIP')
    args=p.parse_args()
    try: build(args)
    except (ValueError,OSError,subprocess.CalledProcessError) as error:
        p.exit(1,'\nBUILD STOPPED: '+str(error)+'\nDownloads are retained. Rerun the same command to restart generated stages.\n')
