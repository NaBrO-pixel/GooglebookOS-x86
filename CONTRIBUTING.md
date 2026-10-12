# Contributing

Run the source tests before submitting a change. Storage changes also require
the full file-backed UEFI installer and installed-boot tests for both modes.
Never use a real Windows disk as an automated test fixture.

Keep image files, device captures, account information, keys and raw logs out of
Git. Prefer short synthetic reproductions and redacted hardware details. The
source export tool accepts only an explicit list of public source/doc folders.

When reporting hardware support, distinguish a kernel/storage boot from a
working desktop, graphics acceleration, sound, microphone and Bluetooth.
Do not remove corruption checks or expand writable ranges to work around a
failed installation. Unsupported layouts should be refused explicitly.
