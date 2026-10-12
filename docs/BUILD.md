# Build inputs and release assembly

Use x86_64 Linux (including a dedicated WSL build distribution). Install Python
3.11+, GRUB EFI build tools, mtools, dosfstools, e2fsprogs, EROFS tools, lz4,
cpio, gzip, QEMU/KVM and OVMF from the build distribution.

Required external inputs:

- A **prepared native Googlebook port image** with its 30 GPT partitions,
  working Intel graphics/audio fixes, firmware and donor security replacements.
- Its matching kernel and native platform ramdisk.
- An external `gbos-vm-linux` checkout for LP/EROFS helpers (revision in
  `THIRD_PARTY.md`). EROFS tools 1.9.4 were used for the baseline.
- A console maintenance initramfs root containing Python, BusyBox, lib/module
  dependencies, blkid and its matching Debian kernel. The tested kernel is
  `6.12.111+deb13-amd64`; its modules must match exactly.
- Android NDK r30 and the pinned TinyALSA checkout for the minimal output helper.

This repository is the release/installer layer. Recreating the prepared native
baseline from an untouched official recovery image is a separate upstream port
step; it is not automated end-to-end here. Do not substitute a signed recovery
image directly for `prepared-native.raw`. Never use a personal live USB capture
as the prepared input: it can contain accounts and encrypted data.

Build the production output helper (no test tones or microphone probes):

```sh
python3 tools/build_audio.py \
  --compiler /path/to/ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/x86_64-linux-android35-clang \
  --tinyalsa /path/to/tinyalsa \
  --out /path/to/build/audio-init
```

Build into a new output directory:

```sh
python3 tools/build_release.py \
  --base-image /path/to/prepared-native.raw \
  --upstream /path/to/gbos-vm-linux \
  --kernel /path/to/native/kernel \
  --native-initrd /path/to/native/initrd \
  --maintenance-root /path/to/maintenance/root \
  --maintenance-kernel /path/to/maintenance/kernel \
  --audio-helper /path/to/build/audio-init \
  --out /path/to/build/release
```

The builder modifies only a newly created regular image file. It strips the
custom capture/test services, keeps hardware workarounds, uses detected CPU
topology rather than a fixed 16-thread mask, resets live userdata/metadata,
packages factory installation assets, assigns fresh partition identities and
creates the two-entry menu. It does not open physical disks or copy a user's
live data. Each installed system receives fresh partition and EFI UUIDs.

Outputs include `googlebook-x86.raw`, `grub.cfg`, the maintenance partition,
checksums and a build result. Raw/binary artifacts are ignored by Git. The
approximately 36.5 GB image is sparse on a capable filesystem.

Before releasing an image, run both file-backed install/boot tests:

```sh
python3 tools/test_release_vm.py --build /path/to/build/release \
  --out /path/to/tests/alongside --mode alongside
python3 tools/test_release_vm.py --build /path/to/build/release \
  --out /path/to/tests/erase --mode erase
python3 tools/test_live_vm.py --build /path/to/build/release \
  --out /path/to/tests/live
```

These fixtures contain simulated Windows partitions and marker bytes, not a
Windows installation. Serial output is added to the installed test loader for
kernel validation. Also perform a native hardware test and verify the existing
OS still boots before declaring a binary release stable.

Audit or export source without uploading it:

```sh
python3 tools/export_source.py --check
python3 tools/export_source.py --out /path/to/googlebook-x86-source.zip
```

The source ZIP accepts only curated source/docs, rejects symlinks, oversized
artifacts, private-key/token patterns and known personal identifiers. It never
includes boot images, full logs or machine-specific installation receipts.
