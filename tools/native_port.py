"""Native Intel adaptations applied locally to separately fetched upstream code.

No donor binaries or upstream implementation are bundled in this module.
"""
import hashlib
import json
from pathlib import Path
import stat
import subprocess
import sys

BRINGUP = '''# Experimental Intel laptop profile.
on early-init
    stop android.system.desktop.security.pinweaver_storaged
    stop android.system.desktop.security.udsattestation
    stop android.system.desktop.security.gscd
    stop timberslide

on boot
    stop android.system.desktop.security.pinweaver_storaged
    stop android.system.desktop.security.udsattestation
    stop android.system.desktop.security.gscd
    stop timberslide
    stop vendor.secretkeeper.trusty

on early-init && property:ro.boot.gbos_renderer=gl
    setprop debug.renderengine.backend skiaglthreaded
    setprop debug.renderengine.vulkan false
    setprop debug.renderengine.graphite_desktop_optin false
    setprop debug.hwui.renderer skiagl

on early-init && property:ro.boot.gbos_shader_cache=single
    setprop debug.egl.blobcache.multifile false

on early-init && property:ro.boot.gbos_angle=legacy
    setprop debug.angle.feature_overrides_disabled preferDynamicRendering
'''
AUDIO_RC = '''service gbos_audio_output_init /vendor/etc/googlebook-audio-init --init-speakers
    class hal
    user audioserver
    group audio
    seclabel u:r:hal_audio_default:s0
    disabled
    oneshot
    stdio_to_kmsg

on post-fs-data && property:ro.boot.gbos_audio=alsa
    start gbos_audio_output_init
'''
AUDIO_POLICY = b'''<?xml version="1.0" encoding="UTF-8"?>
<!-- Synchronous HDA PCM; microphone capability still needs native validation. -->
<module name="primary" halVersion="3.0">
 <attachedDevices><item>Speaker</item><item>Built-In Mic</item></attachedDevices>
 <defaultOutputDevice>Speaker</defaultOutputDevice>
 <mixPorts>
  <mixPort name="primary output" role="source" flags="AUDIO_OUTPUT_FLAG_PRIMARY">
   <profile name="" format="AUDIO_FORMAT_PCM_16_BIT" samplingRates="48000" channelMasks="AUDIO_CHANNEL_OUT_STEREO"/>
  </mixPort>
  <mixPort name="primary input" role="sink">
   <profile name="" format="AUDIO_FORMAT_PCM_16_BIT" samplingRates="48000" channelMasks="AUDIO_CHANNEL_IN_MONO AUDIO_CHANNEL_IN_STEREO"/>
  </mixPort>
 </mixPorts>
 <devicePorts>
  <devicePort tagName="Speaker" type="AUDIO_DEVICE_OUT_SPEAKER" role="sink" address="CARD_0_DEV_0"/>
  <devicePort tagName="Built-In Mic" type="AUDIO_DEVICE_IN_BUILTIN_MIC" role="source" address="CARD_0_DEV_0"/>
 </devicePorts>
 <routes>
  <route type="mix" sink="Speaker" sources="primary output"/>
  <route type="mix" sink="primary input" sources="Built-In Mic"/>
 </routes>
</module>
'''
AUDIO_CONFIGS = ('audio_effects.xml', 'audio_effects_config.xml',
    'audio_policy_configuration.xml', 'audio_policy_volumes.xml',
    'default_volume_tables.xml', 'bluetooth_with_le_audio_policy_configuration_7_0.xml',
    'primary_audio_policy_configuration.xml', 'r_submix_audio_policy_configuration.xml',
    'surround_sound_configuration_5_0.xml', 'usb_audio_policy_configuration.xml')
FIRMWARE = ('i915/adlp_guc_70.bin', 'i915/adlp_dmc.bin', 'i915/adlp_dmc_ver2_16.bin',
    'i915/tgl_huc.bin', 'iwlwifi-so-a0-gf-a0.pnvm', 'iwlwifi-so-a0-gf-a0-72.ucode',
    'iwlwifi-so-a0-gf-a0-89.ucode', 'intel/ibt-0040-0041.sfi', 'intel/ibt-0040-0041.ddc',
    'intel/ibt-1040-0041.sfi', 'intel/ibt-1040-0041.ddc')


def patch_properties(data):
    changes = {'ro.vendor.apex.com.android.hardware.audio.desktop': 'none',
        'aaudio.mmap_policy': '1', 'audio.offload.disable': '1',
        'vendor.hwc.backend_override': 'client',
        'ro.vendor.hwc.drm.present_fence_not_reliable': 'true'}
    lines = [l for l in data.decode().splitlines() if l.split('=', 1)[0] not in changes]
    return ('\n'.join(lines) + '\n' + ''.join(k+'='+v+'\n' for k,v in changes.items())).encode()


def patch_power(data):
    power = json.loads(data)
    removed = {n['Name'] for n in power['Nodes'] if n['Name'].endswith('Cpuset')
        or n['Name'] in ('WorkQueueAffinity', 'XeIrqAffinity')}
    power['Nodes'] = [n for n in power['Nodes'] if n['Name'] not in removed]
    power['Actions'] = [a for a in power['Actions'] if a.get('Node') not in removed]
    for n in power['Nodes']:
        if n['Name'] == 'SchedLoadBalance': n.update(Values=['1'], DefaultIndex=0)
    for a in power['Actions']:
        if a.get('Node') == 'SchedLoadBalance': a['Value'] = '1'
    return (json.dumps(power, indent=2)+'\n').encode()


def audio_extract(vendor_file, temporary, out):
    import unpack_cuttlefish_donor as donor
    audio = out/'audio'; audio.mkdir()
    package = vendor_file('/apex/com.android.hardware.audio.apex')
    (audio/'com.android.hardware.audio.apex').write_bytes(package)
    # Keep all ten policy XMLs from the same donor build as its audio HAL.
    for name in AUDIO_CONFIGS:
        data = vendor_file('/etc/'+name)
        if not data.lstrip().startswith((b'<?xml', b'<!--')):
            raise ValueError('Expected donor audio XML: '+name)
        (audio/name).write_bytes(data)


def vendor_overlay(src_fs, donor, files, firmware_root, helper):
    """Return additions for the upstream's one-pass vendor overlay."""
    def cat(name):
        return subprocess.check_output(['dump.erofs', '--cat', '--path='+name, str(src_fs)])
    additions = {}
    def add(name, data, label='vendor_configs_file', mode=0o644, uid=0, gid=0):
        additions[name] = (data, label, mode, uid, gid)
    for name in AUDIO_CONFIGS: add('etc/'+name, (donor/'audio'/name).read_bytes())
    add('apex/com.android.hardware.audio.apex',
        (donor/'audio/com.android.hardware.audio.apex').read_bytes(), 'vendor_apex_file')
    for name in ('bluetooth_audio.xml', 'soundtrigger.qti.xml'):
        path = 'etc/vintf/manifest/'+name
        try: cat('/'+path)
        except subprocess.CalledProcessError: continue
        add(path, b'<manifest version="1.0" type="device"/>\n')
    add('etc/primary_audio_policy_configuration.xml', AUDIO_POLICY)
    add('etc/init/zz-googlebook-bringup.rc', BRINGUP.encode())
    add('etc/init/zz-googlebook-display-audio.rc', AUDIO_RC.encode())
    add('etc/init/vm-absent-hardware.rc', b'# Native profile uses explicit init stop actions.\n')
    add('etc/googlebook-bringup.sh', b'#!/system/bin/sh\n# No automatic recording.\nexit 0\n')
    add('etc/googlebook-audio-init', helper.read_bytes(), 'hal_audio_default_exec', 0o755, 0, 2000)
    add('etc/init/hal_desktop_charge_control_default.rc', b'# No Chrome EC charge-control hardware.\n')
    cfg = cat('/etc/init.insmod.cfg').decode().splitlines()
    for name in ('snd-hda-codec-alc269.ko', 'snd-hda-intel.ko', 'btintel.ko', 'btusb.ko'):
        request = 'modprobe|'+name
        if request not in cfg: cfg.append(request)
    add('etc/init.insmod.cfg', ('\n'.join(cfg)+'\n').encode())
    add('etc/powerhint.json', patch_power(cat('/etc/powerhint.json')))
    old_prop = files['build.prop']
    additions['build.prop'] = (patch_properties(old_prop[0]), *old_prop[1:])
    secret = 'etc/init/android.hardware.security.secretkeeper.trusty.desktop.rc'
    rc = cat('/'+secret).decode()
    if '    disabled\n' not in rc:
        rc = rc.replace('    class hal\n', '    class hal\n    disabled\n    oneshot\n')
    add(secret, rc.encode())
    for name in FIRMWARE:
        source = (firmware_root/name).resolve(strict=True)
        if not source.is_relative_to(firmware_root.resolve()):
            raise ValueError('Firmware symlink escapes extracted package')
        add('firmware/'+name, source.read_bytes(), 'vendor_firmware_file', 0o644, 0, 2000)
    return additions


def execute_upstream(path, argv, injections, callbacks):
    """Run a pinned external helper with our local callbacks; leave its source untouched."""
    text = path.read_text()
    for anchor, addition in injections:
        if text.count(anchor) != 1:
            raise ValueError('Pinned upstream source anchor changed: '+anchor.strip())
        text = text.replace(anchor, addition+anchor, 1)
    saved = sys.argv
    try:
        sys.argv = [str(path), *map(str, argv)]
        exec(compile(text, str(path), 'exec'),
            {'__name__': '__main__', '__file__': str(path), **callbacks})
    finally: sys.argv = saved


def native_initrd(upstream, out):
    import cpio_tools
    firmware = upstream/'firmware'
    entries = {}
    for name in ('vendor_0_platform.cpio', 'vendor_2_dlkm.cpio', 'init_ramdisk.cpio'):
        entries.update(cpio_tools.read((firmware/name).read_bytes()))
    vm = cpio_tools.read(subprocess.check_output(['lz4', '-d', '-c', str(upstream/'initrd')]))
    native_load = entries['lib/modules/modules.load'][1].decode().splitlines()
    vm_load = vm['lib/modules/modules.load'][1].decode().splitlines()
    for name,value in vm.items():
        if name.endswith('.ko') and name not in entries: entries[name] = value
    for name in ('modules.dep', 'modules.alias', 'modules.softdep', 'modules.options'):
        key = 'lib/modules/'+name
        original = entries.get(key, (0,b''))[1]
        extra = vm.get(key, (0,b''))[1]
        entries[key] = (stat.S_IFREG|0o644, original.rstrip(b'\n')+b'\n'+extra)
    entries['lib/modules/modules.load'] = (stat.S_IFREG|0o644,
        ('\n'.join(dict.fromkeys(native_load+vm_load))+'\n').encode())
    key = 'first_stage_ramdisk/fstab.android-desktop'
    mode,data = entries[key]
    lines = [l.replace(',avb=vbmeta','') if l.startswith(('vendor ', 'system_ext ')) else l
        for l in data.decode().splitlines()]
    entries[key] = (mode, ('\n'.join(lines)+'\n').encode())
    out.write_bytes(subprocess.check_output(['lz4', '-l', '-c'], input=cpio_tools.write(entries)))


def disable_trusty_declaration(image, upstream, out):
    from build_release import append_erofs_file
    import lp_tools
    from make_lapis_security_port import update_sb_checksum
    boundaries = json.loads((upstream/'firmware/gpt.json').read_text())['super']
    sup = lp_tools.Super(image, boundaries[0]); layout = sup.layout('system_ext_a')
    if len(layout)!=1: raise ValueError('Expected one system_ext extent')
    offset,size = layout[0]; fs = out/'system-ext.erofs'
    with image.open('rb') as f:
        f.seek(offset); fs.write_bytes(f.read(size))
    path = '/etc/init/desktop-trusty_security_vm_launcher.rc'
    replacement = b'# Software KeyMint/Gatekeeper are used; omit unsupported Trusty VM declarations.\n'
    append_erofs_file(fs, path, replacement, update_sb_checksum)
    length = fs.stat().st_size
    if offset+length>boundaries[1] or any(offset<b and a<offset+length for a,b in sup.used_ranges() if a!=offset):
        raise ValueError('Expanded system_ext overlaps another logical partition')
    subprocess.run(['fsck.erofs', str(fs)], check=True)
    with image.open('r+b') as f: f.seek(offset); f.write(fs.read_bytes())
    sup.set_single_extent('system_ext_a', length//512, (offset-boundaries[0])//512)
    fs.unlink()
