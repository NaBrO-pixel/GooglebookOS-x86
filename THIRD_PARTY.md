# Third-party components

The MIT license covers this repository's original tooling, tests and docs, not
the operating-system image or dependencies used by the build environment.

| Component | Source / revision | Use |
| --- | --- | --- |
| Googlebook recovery / lapis | Obtained separately from Google | Kernel and OS input; not included in this repository. |
| gbos-vm-linux | [chaitan3/gbos-vm-linux](https://github.com/chaitan3/gbos-vm-linux), `a2942f96088f450f675c05d3702c1bcd4044aaa5` | External port helpers / prepared baseline. No upstream source is vendored here. |
| gbos-vm | [skylartaylor/gbos-vm](https://github.com/skylartaylor/gbos-vm), `3b08ed29a73858644c65f24e06ee2a7cfb7e3774` | Earlier research reference; not vendored. |
| TinyALSA | [tinyalsa/tinyalsa](https://github.com/tinyalsa/tinyalsa), `961babfe962e71d952ae734074cc89576b28a9e7` | BSD 3-Clause; linked into the speaker helper. Notice in `LICENSES/tinyalsa.txt`. |
| GNU GRUB | [GNU GRUB](https://www.gnu.org/software/grub/) | GPLv3+; bootloader and build tools installed separately. |
| Linux / Debian maintenance kernel | [Debian kernel source](https://sources.debian.org/src/linux/) | GPL-2.0; a separate console installer kernel. |
| BusyBox, Python, glibc, e2fsprogs, dosfstools, mtools, efibootmgr | Installed from the build distribution | Retain their respective upstream licenses. |
| Intel firmware, Android userspace and Cuttlefish donor components | Obtained separately for the prepared port | Not copied into Git or the source-only export. |

The source builder fetches upstream archives into the user's ignored build
directory. It runs external helpers with our own native overlay callbacks;
it does not vendor those helpers into this repository. Exact download URLs,
sizes and hashes are recorded in `profiles/sources.json`. EROFS tools are built
from upstream commit `f36cadb5c563995ab3aa8572a60ed6b721b9557d` (1.9.4).
The pinned Debian maintenance packages are fetched from Debian Snapshot because
their older versions are no longer available in the current package pool.

The local upstream port checkout did not contain a top-level license file when
this source release was prepared. This project does not relicense or vendor its
code. Check upstream permissions and the image components' notices before
redistributing binary boot media. A future downloadable image must include its
third-party notices and corresponding-source arrangements where required.
