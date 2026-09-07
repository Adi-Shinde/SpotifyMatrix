# Boot Recovery — when the Pi will not start

**You almost never need to reflash the SD card.** The boot partition is a
plain FAT volume that Windows, macOS and Linux can all read. If the Pi stops
booting after a config change, you fix it by editing one text file on that
partition from any PC.

---

## What a solid green LED means

| LED behaviour | Meaning |
|---|---|
| Green flickering irregularly | Normal — that is SD card activity |
| **Green solid / on and never blinking** | The kernel started but could not continue — most often it could not find its root filesystem. **This is almost always a bad `cmdline.txt`.** |
| Green blinking a repeating count | Firmware error code (see the Raspberry Pi docs); usually a genuinely unreadable card |

A solid green LED after editing boot files points at `cmdline.txt`, not at a
dead card. Recover it with the steps below.

---

## Recovery, step by step

1. Power off the Pi and move the SD card to your PC.
2. A small FAT partition mounts automatically — on Windows it usually appears
   as a drive named **`bootfs`** (a few hundred MB). Ignore any prompt to
   format the *other* partition; Windows cannot read the Linux root and asking
   to format it is normal. **Never accept that prompt.**
3. Look for a backup this project made:
   - `cmdline.txt.matrixbak`
   - `config.txt.matrixbak`

   If they exist, copy each over the live file (`cmdline.txt.matrixbak` →
   `cmdline.txt`). That is the whole fix — eject and boot.
4. If there is no backup, open `cmdline.txt` in a real text editor
   (**Notepad++ or VS Code — not Windows Notepad**) and check that it is:
   - **exactly one line**, no blank line after it
   - contains a `root=` parameter

   A healthy file looks like this, all on one line:

   ```
   console=serial0,115200 console=tty1 root=PARTUUID=a1b2c3d4-02 rootfstype=ext4 fsck.repair=yes rootwait quiet splash
   ```

   Delete any `isolcpus=3` you find at the end, make sure nothing was split
   onto a second line, save, eject, and boot.

### Saving the file correctly

This matters more than it looks:

- Save with **LF (Unix) line endings**, not CRLF. In Notepad++:
  *Edit → EOL Conversion → Unix (LF)*. In VS Code, click **CRLF** in the
  bottom-right status bar and switch it to **LF**.
- No trailing blank line.
- Plain UTF-8 or ASCII, no BOM.

A stray carriage return in the middle of the kernel arguments produces exactly
the same solid-green symptom as a missing `root=`.

---

## Why this happened

The anti-flicker option edits `cmdline.txt`, which the kernel needs in order
to find its root filesystem. The earlier version of that code had three
problems, all now fixed:

1. **It used `sed -i`.** That does not edit in place despite the name — it
   writes a new file and swaps it in, replacing the directory entry. On the
   FAT boot partition, immediately followed by a reboot, that is a good way to
   end up with a truncated or orphaned `cmdline.txt`.
2. **It appended to every line.** `sed 's/$/ isolcpus=3/'` adds text to each
   line in the file. If `cmdline.txt` ended with a blank line you got a second
   line of kernel arguments; if it had Windows line endings you got a carriage
   return embedded in the middle of them.
3. **It validated nothing and backed up nothing**, then offered to reboot
   straight away.

The current version detects the real boot partition (Bookworm moved it from
`/boot` to `/boot/firmware`), backs both files up as `*.matrixbak`, collapses
the file to exactly one line, refuses to install a `cmdline.txt` that has lost
its `root=`, uses `cp` so the FAT directory entry is preserved, and syncs
before rebooting.

**Menu option 9 → Restore boot config** puts the backups back over SSH, for
when the Pi still boots but something is misbehaving.

---

## Is `isolcpus=3` worth it at all?

Honestly: probably not, on its own. It removes core 3 from the Linux
scheduler, and unless something is explicitly pinned to that core, you have
taken a quarter of the CPU away and given it to nobody. That is why the
service now also gets `CPUAffinity=3` — the two only make sense as a pair.

If you would rather not touch boot files again, skip the anti-flicker option
entirely. Disabling onboard audio is the part that genuinely helps PWM timing,
and the flicker difference from core isolation is small. The script now skips
core isolation automatically on any Pi reporting fewer than four cores.
