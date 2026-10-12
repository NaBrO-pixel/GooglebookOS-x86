# Build fresh boot media from public inputs

The source builder downloads the pinned stock recovery, Cuttlefish donor, NDK,
Intel firmware and maintenance packages directly from their upstream hosts.
It applies the native port adaptations locally and creates a fresh **Live /
Install** image. You do not need our private lab or a capture of someone's USB.
No OS image or donor binary is redistributed in this repository.

The pinned OS input is Google's **ASUS Googlebook 14 (`lapis`) recovery image,
build `16452207`**. It is the only recovery image used and tested; substituting
another model's image is outside the validated build path.

This is an experimental build path for developers and careful hardware testers.
Physical testing is limited to the ASUS VivoBook S5602ZA. See
[validation](VALIDATION.md) for what has actually been tested.

## 1. Use the supported build environment

- **Debian 13, x86_64, system Python 3.13**, native Linux or WSL 2.
- An internet connection; approximately 10 GB of pinned downloads.
- **100 GB of real free disk space** recommended for extraction, compilation
  and the final image. Additional VM install tests need more space.
- A spare **64 GB or larger USB**, preferably an external SSD for performance.

On Windows, install Debian through [Microsoft's WSL instructions](https://learn.microsoft.com/windows/wsl/install),
then check `cat /etc/os-release` inside Debian. It must report Debian version 13.
Keep the build inside Linux's home directory, not a OneDrive or `/mnt/c` folder.
Check Windows free space too: WSL's virtual filesystem capacity can be much
larger than the real free space on its host drive.

For a new Windows WSL setup, the PowerShell commands are:

```powershell
wsl --install -d Debian
wsl -d Debian
```

Follow Microsoft's restart/account prompts if requested. If an existing Debian
installation is older than version 13, use a fresh Debian 13 environment for
this build instead of assuming its libraries match.

Install build dependencies in Debian:

```sh
sudo apt-get update
sudo apt-get install -y git python3 python3-lz4 curl unzip lz4 cpio gzip \
  e2fsprogs dosfstools mtools grub-efi-amd64-bin efibootmgr kmod \
  build-essential autoconf automake libtool pkg-config uuid-dev \
  liblz4-dev liblzma-dev libzstd-dev libdeflate-dev libssl-dev \
  qemu-system-x86 qemu-utils ovmf
```

The builder compiles its own pinned EROFS tools into the build directory. It
extracts the maintenance kernel package without installing or replacing your
host kernel. Run the build itself as your normal user, without `sudo`.

## 2. Get this project's source

```sh
cd ~
git clone https://github.com/ReUnknown/GooglebookOS-x86.git
cd GooglebookOS-x86
python3 -m unittest discover -s tests -v
```

Review the [component notices](../THIRD_PARTY.md) and the
[NDK download terms](https://developer.android.com/ndk/downloads) before building.

## 3. Build the image

To inspect the input URLs and output location first, without downloading:

```sh
python3 tools/build_from_sources.py --plan
```

Then run:

```sh
python3 tools/build_from_sources.py
```

The terminal shows nine stages and download/extraction byte progress. Pinned
sizes and SHA-256 digests are in `profiles/sources.json`. A wrong or unavailable
input stops the build rather than silently substituting another version.

The stages fetch sources, compile tools and the speaker helper, extract the
stock boot files and donor HALs, extract Intel firmware, prepare the native
image, build the maintenance environment, assemble the two-entry menu and
produce a full image checksum. All changes are to regular build files; this
command never writes your USB or internal disk.

On success the output is:

```text
.build/release/googlebook-x86.img
.build/release/googlebook-x86.raw
.build/release/SHA256SUMS
```

The `.img` and `.raw` names refer to the same bytes through a Linux hard link,
not two independent disk copies. The image is approximately 36.5 GB logically;
unused regions are sparse on a suitable filesystem. Copying it to another
filesystem can require the full logical size.

Verify it:

```sh
cd .build/release
sha256sum -c SHA256SUMS
cd ../..
```

Then continue with [flashing and installation](INSTALL.md).

## Interrupted builds and existing downloads

Rerun the same build command. Downloads are resumed when the server supports
ranges; complete cached inputs are rechecked. Failed native/release stages are
rebuilt from the stock input, never from a partially booted or personal image.
Compiler details are retained locally in `.build/build-tools.log`.

If an interruption leaves an incomplete ZIP extraction or the rerun reports a
bad generated input, use a new work directory with the same verified download
cache, as shown below. Do not bypass a checksum failure.

If the source code or pins changed after an update, use a new build directory
while reusing the old download cache:

```sh
python3 tools/build_from_sources.py --work .build-next --cache .build/cache
```

The output is then in `.build-next/release`. Build directories and their private
receipts are ignored by Git and excluded from source exports. Do not use an
image copied from a personal USB as a stock recovery input.

## File-backed VM validation

Before sharing a rebuilt image, test it without touching physical disks:

```sh
python3 tools/test_release_vm.py --build .build/release \
  --out .build/tests-alongside --mode alongside
python3 tools/test_release_vm.py --build .build/release \
  --out .build/tests-erase --mode erase
python3 tools/test_live_vm.py --build .build/release \
  --out .build/tests-live
```

The install tests contain synthetic Windows markers, not a real Windows
installation. VM kernel/storage checks do not validate Iris Xe desktop
rendering, real Windows boot or repeated clean reboot/data persistence on a
laptop. Test Live on the target hardware before considering SSD installation.

## Advanced: an already prepared native image

The lower-level `tools/build_release.py` remains available for developers with
an independently prepared native image and matching boot/maintenance files.
Use `python3 tools/build_release.py --help` for its explicit inputs. The normal
source-build command above supplies those inputs automatically.
