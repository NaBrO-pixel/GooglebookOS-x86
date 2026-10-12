# Googlebook x86

An experimental Googlebook OS port for UEFI x86_64 hardware, with a persistent
USB session and an offline NVMe installer. It is an independent project.

> **Experimental — not ready for daily use.** Physical hardware testing has
> been limited to **one device: an ASUS VivoBook S5602ZA**, with a Core
> i5-12500H, Intel Iris Xe graphics and Intel AX211 wireless. The cleaned release
> candidate still needs a follow-up test on that laptop. Other devices have
> not been tested; virtual-machine checks do not establish hardware support.
>
> **Occasional crashes and bugs are possible.** Apps or features may crash or
> behave unexpectedly, and stability and hardware compatibility are still
> being tested.
>
> **Installation carries risks.** It changes partitions and UEFI
> boot entries and can cause data loss or leave Googlebook or an existing OS
> unbootable, including when using the alongside option. The erase option
> deletes the selected drive's existing partitions. **Back up your files and
> recovery keys first, use a test device, and proceed at your own risk.**

The release USB has exactly two choices:

| Boot choice | What it does |
| --- | --- |
| **Live** | Runs Googlebook from the USB; keeps its apps/data on that USB. |
| **Install** | Reviews a selected internal disk, then installs a fresh system. |

## Installation choices

**Install alongside:** use existing contiguous unallocated GPT space. Choose a
free region and optionally the allocation size. Existing partition entries,
GUIDs, contents and the current firmware boot order are preserved; a new
Googlebook boot entry is appended. This does not shrink or move partitions.
These are the intended safeguards, not a guarantee against installation bugs
or power loss. Real Windows boot after installation remains unverified.

**Erase disk and install:** remove the selected disk's existing partitions and
use its available capacity for Googlebook. This deletes existing files from
normal use; it is not a forensic secure erase. The source USB cannot be erased
through the installer.

Both modes create an independent EFI partition, verify the copied OS, create
fresh apps/data, and shut down automatically after success. Errors remain
visible. There is no full-disk zeroing pass.

[See the installation walkthrough](docs/INSTALL.md) and
[supported hardware and restrictions](docs/COMPATIBILITY.md).

## Status

**0.1.0-rc1 — experimental source release.** The native baseline was tested on
an ASUS VivoBook S5602ZA with Core i5-12500H, Iris Xe and AX211. The new release
needs a native hardware test before a general binary release. Disk-planning
support does not imply that every laptop's GPU, audio or wireless is supported.

Curated validation results are in [VALIDATION.md](docs/VALIDATION.md).

## Repository contents

- `src/googlebook/`: disk validation, pure partition planner, installer and UI.
- `tools/`: file-only release builder, minimal speaker helper and VM tests.
- `profiles/`: the tested Intel Xe boot settings.
- `tests/`: planning, write-bound, confirmation and firmware-order regression tests.
- `docs/`: installation, build inputs, compatibility and release validation.

Personal disk snapshots, serial numbers, accounts, encryption keys, raw images,
captured logs and old experiments are excluded. The release removes automatic
lab dumps, audio test/inspection services and experimental boot alternatives.
Normal OS error logging and essential installation backups remain.

## Develop and build

Python 3.11+ is sufficient for the source tests:

```sh
python3 -m unittest discover -s tests -v
```

Building boot media requires an x86_64 Linux environment and a locally prepared
native port image. This repository does not contain Google's recovery image,
firmware or donor binaries. It currently is not a one-command build from an
untouched Google recovery download. See [BUILD.md](docs/BUILD.md) for explicit
inputs, upstream revisions and the release command.

## Security and licensing

This port uses software security replacements and modified verification paths;
it does not provide the original device's hardware-backed security guarantees.
Use test accounts while evaluating the release. Secure Boot must be disabled.

Our source is MIT licensed. Other components retain their original licenses;
see [THIRD_PARTY.md](THIRD_PARTY.md). OS binaries are excluded from source exports.
