# Release-candidate validation

Validated on October 12, 2026. This is an experimental source candidate, not a
claim of universal laptop support or a stable public binary release.

## Source and safety checks

All **19 regression tests passed on Linux**. The suite covers:

- Disk capacities from 128 GiB to 2 TiB, differently positioned free regions,
  sparse GPT slot numbering and user-selected allocation sizes.
- Preservation of original entries, GUIDs, attributes and the protective MBR.
- Refusal of fragmented small gaps, insufficient GPT slots, corrupt/different
  GPT copies, hybrid MBR, already installed layouts and colliding native names.
- Disk-specific confirmation, writes outside the reviewed allocation, CPU ISA
  preflight, source corruption, explicit replacement of old bytes under zero
  payload chunks and caught GPT commit rollback.
- Actual completion counts/heartbeat and firmware-order preservation.
- Source export exclusion of lab folders, keys/tokens and external symlinks.

The suite passes on Linux. Platform-specific disk-file and symlink tests are
skipped on Windows rather than accessing a physical disk.

## Full UEFI tests using disk files

| Check | Alongside / 256 GiB fixture | Erase / 64 GiB fixture |
| --- | --- | --- |
| Real offline installer copied and verified every OS payload | Passed | Passed |
| Thirty native partitions / both GPT copies valid | Passed | Passed |
| Original Windows entries, GUID, attributes, MBR and content markers preserved | Passed | Existing layout intentionally replaced |
| Removed old partitions / fresh disk GUID | Not applicable | Passed |
| Recorded installer writes within allocation or exact GPT regions | Passed | Passed |
| Independent EFI entry created / original firmware order retained | Passed | Passed |
| Installed kernel booted with USB absent | Passed | Passed |
| Metadata-encryption key created, userdata header encrypted, kernel data mount seen | Passed | Passed |
| Automatic shutdown after install | Passed | Passed |
| Lab capture services absent | Passed | Passed |

The alongside fixture used a 146 GiB gap at a different location from the
original ASUS layout, with existing partition slots 1, 7 and 120. These are
synthetic Windows markers, not a real Windows installation.

Serial instrumentation is added to the test loader. The data proof uses the
resulting metadata key files, encrypted raw userdata header and kernel trace of
the data mount, rather than depending on a diagnostic service's printed text.
The installer receipt is also checked for its firmware result and write bounds.

Final CPU/name preflight checks and the cleaned ASCII console were packaged
after these payload tests; the OS payloads and copy/commit/boot logic were
unchanged. The final UI is tested through review/cancel without target writes.

The captured walkthrough stops before confirmation and verifies the mock
target was not changed. It uses the actual two-entry menu and installer UI.

The final Live smoke test also passed: its native kernel booted with the fresh
release boot UUID, USB native partitions were detected and custom recording
services did not start. No internal disk was attached to that VM. This is a
kernel/storage test, not a validated virtual desktop.

## Limits and next acceptance test

A kernel/storage boot is not a working GPU desktop. The VM's emulated graphics
does not reproduce Iris Xe, and the image can report missing virtual graphics
services in these tests. The native baseline's desktop was user-tested on the
ASUS; the cleaned release has not yet had that native follow-up test.

Real Windows boot, installed app/data persistence through repeated clean
reboots, microphone capture and other manufacturers' hardware remain outside
this validation. The test boot is forcibly stopped after its observation period;
it does not validate recovery from an interrupted first boot.

Before a public binary release: test Live, fresh install, setup, repeated clean
shutdown/reboot, graphics, speakers, wireless and the existing Windows boot on
the ASUS. Keep the release experimental until those checks pass. Binary
redistribution notices/permissions also need checking separately.
