# Compatibility boundaries

| Area | Current scope |
| --- | --- |
| Firmware | x86_64 UEFI; Secure Boot disabled. No legacy BIOS installer. |
| Native baseline | ASUS VivoBook S5602ZA, i5-12500H, Iris Xe, AX211. |
| Other laptops | Experimental; disk planning is portable, hardware drivers are not universally validated. |
| Installation target | Idle whole NVMe disk with 512-byte logical sectors. |
| Dual boot | Matching standard GPT copies, 128 entry slots, at least 30 unused slots, one contiguous free region of at least 32 GiB, no conflicting native partition names. |
| Capacity | At least 32 GiB usable allocation; at least 16 GiB must remain for apps/data. |
| Full disk install | Replaces the selected disk's layout with fresh GPT, EFI, OS and data partitions. |
| USB media | 64 GB or larger for the current approximately 36.5 GB raw image. |
| Boot registration | Append an independent Googlebook entry; retain all prior entries and prior order. Alongside requires writable UEFI variables. |
| Unsupported storage | 4Kn, legacy/hybrid MBR alongside, RAID/dynamic disks, mounted targets, automatic partition shrinking and fragmented gap aggregation. |
| CPU ISA | This release conservatively gates installation on GFNI, SHA-NI and AVX-VNNI, matching the tested CPU profile. This is a release policy, not proof that every recovery component requires each instruction set. |
| Graphics | Retains the known OpenGL/ANGLE and Xe stability settings. No promise of NVIDIA/AMD support. |
| Audio | Default HDA output with normalized hardware attenuation. Experimental SOF microphone boot option is removed. |
| Microphone | Not established on the physical baseline; removing the experimental option does not claim a mic fix. |
| Security | Software replacements, modified verification paths; not equivalent to the original hardware-backed security. |

If the installer cannot safely establish an alongside plan, that option is not
offered. Erase remains an explicit choice with a separate destructive review;
it is never selected automatically as a fallback.

The original ASUS desktop experience was user-tested. A VM storage/kernel test
does not establish a working desktop, GPU acceleration or Windows boot on every
physical machine. The cleaned release requires its next native hardware test.
