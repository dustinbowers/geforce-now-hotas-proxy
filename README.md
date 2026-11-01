# GeForce NOW HOTAS Proxy

Make an unsupported flight stick work in GeForce NOW on macOS by putting a
Raspberry Pi between the stick and the computer.

**Status:** confirmed working in a live GFN session, controlling the game.
Verified against GFN client 2.0.90.120, macOS 26.6.2 (Apple silicon), and a
Raspberry Pi 4 Model B running Debian 13 (trixie), kernel 6.18.50.

---

## The problem

GeForce NOW does not forward arbitrary HID devices into a cloud session. Flight
sticks, wheels and HOTAS gear are gated behind a **hardcoded VID:PID allowlist**
compiled into `libGeronimo.dylib`. Gamepads take a different path entirely (SDL's
`gamecontrollerdb` plus a DS4/DS5 synthesizer); this allowlist is only about
non-gamepad HID peripherals.

If your stick's VID:PID is not on that list, GFN ignores it. There is no setting
to change this, and the list is not user-editable.

The Thrustmaster **HOTAS Warthog Base MKII** (`044f:0450`) is not on the list.
The original **Warthog Joystick** (`044f:0402`) is.

## The solution

A Raspberry Pi 4B runs in USB **peripheral** (gadget) mode. It:

1. Reads the real MKII stick on one of its USB-A host ports.
2. Presents itself to the Mac over its USB-C port as `044f:0402` — the
   allowlisted original Warthog joystick, with that device's genuine HID report
   descriptor.
3. Translates each 64-byte MKII report into the 12-byte report layout the
   original Warthog uses, and writes it to the gadget endpoint.

GFN sees an allowlisted joystick and forwards it into the session. It cannot
distinguish the gadget from real hardware.

```
  MKII stick ──USB-A──▶ Raspberry Pi 4B ──USB-C──▶ MacBook ──▶ GeForce NOW ──▶ game
   044f:0450            (translate report)         044f:0402      (allowlisted)
```

**This is an interoperability shim for hardware you own.** It does not modify
GFN's binary, bypass authentication, or alter anything server-side.

---

## Hardware

| Item | Notes |
| --- | --- |
| Raspberry Pi 4 Model B | Must be a 4B. See *Other Pi models* below. |
| USB-C cable (data-capable) | Pi's USB-C port → Mac. Carries power **and** data. |
| microSD card | 8 GB+ |
| Your flight stick | Plugs into one of the Pi's USB-A ports |
| Powered USB hub | *Optional.* Needed if your stick draws more current than the Mac's port can spare through the Pi. |

The Pi 4B's USB-C port is both its power input and its USB 2.0 OTG port. A single
cable from the Mac powers the Pi and carries the gadget data. If the Pi
brownouts under load, power it from GPIO or a powered hub instead and use the
USB-C port for data only.

### Other Pi models

The UDC (USB Device Controller) name in `tm.sh` is Pi 4B-specific:

| Model | UDC name | Works? |
| --- | --- | --- |
| Pi 4 Model B | `fe980000.usb` | Yes — this guide |
| Pi Zero / Zero 2 W | `20980000.usb` | Yes, change `UDC_NAME` in `tm.sh` |
| Pi 5 | — | **No.** No usable dwc2 peripheral mode. |
| CM4 | `fe980000.usb` | Yes, but `otg_mode=1` must be off |

Check yours with `ls /sys/class/udc/` after Step 2.

---

## Conventions

Commands below use three shell variables so you can paste them unchanged. Set
them once per terminal session, substituting your own values:

```bash
export PI_USER=pi                      # the Pi's login user
export PI_HOST=raspberrypi.local       # hostname or IP
export PI_KEY=~/.ssh/id_ed25519        # SSH private key
```

Then connecting is:

```bash
ssh -i "$PI_KEY" "$PI_USER@$PI_HOST"
```

If you use password auth rather than a key, drop `-i "$PI_KEY"` from every
command.

---

## Step 1 — Install Raspberry Pi OS

Flash Raspberry Pi OS (64-bit, Bookworm or later) with Raspberry Pi Imager.
In Imager's advanced options, set a hostname, enable SSH and add your public key
— you will not need a monitor.

Verify you can reach it:

```bash
ssh -i "$PI_KEY" "$PI_USER@$PI_HOST" 'uname -a; cat /proc/device-tree/model'
```

### Passwordless sudo (recommended)

Adding your user to the `sudo` group still prompts for a password, which breaks
non-interactive scripting — every `ssh ... 'sudo ...'` below will fail with
`sudo: a terminal is required to read the password`. For a dedicated appliance
like this, a NOPASSWD rule is far more convenient:

```bash
ssh -i "$PI_KEY" "$PI_USER@$PI_HOST"
echo "$USER ALL=(ALL) NOPASSWD: ALL" | sudo tee "/etc/sudoers.d/010_$USER-nopasswd"
sudo chmod 440 "/etc/sudoers.d/010_$USER-nopasswd"
```

Decide for yourself whether that tradeoff is acceptable on your network. Without
it, run the `sudo` commands from an interactive SSH session instead of inline.

---

## Step 2 — Enable USB peripheral mode

By default the Pi's USB-C port is a power input only. The `dwc2` overlay in
peripheral mode turns it into a USB device port.

Edit `/boot/firmware/config.txt` (on Bookworm and later; it was `/boot/config.txt`
on older releases):

```bash
sudo nano /boot/firmware/config.txt
```

Add this line **in the `[all]` section**, or anywhere before the first
model-specific filter:

```ini
# FORCE USB PERIPHERAL MODE FOR GADGET HID
dtoverlay=dwc2,dr_mode=peripheral
```

> **Gotcha that will cost you an hour.** Stock Raspberry Pi OS ships a
> `config.txt` containing `dtoverlay=dwc2,dr_mode=host` under a `[cm5]` filter
> and a commented `otg_mode=1` under `[cm4]`. Those are *conditional* sections
> and do not apply to a Pi 4B — but if you paste your line *after* `[cm5]` it
> inherits that filter and silently does nothing. Put your line under `[all]`,
> or at the top of the file. Section filters stay in effect until the next
> `[...]` header.

Load `libcomposite` at boot:

```bash
echo libcomposite | sudo tee /etc/modules-load.d/libcomposite.conf
```

Reboot, then confirm the UDC exists:

```bash
sudo reboot
# wait, reconnect
ls /sys/class/udc/
# expected: fe980000.usb
lsmod | grep libcomposite
```

If `/sys/class/udc/` is empty, peripheral mode is not active. Re-check the
`config.txt` section placement before going further — nothing downstream can
work without this.

---

## Step 3 — Identify your stick

Plug the stick into a **USB-A** port on the Pi.

```bash
lsusb
# Bus 001 Device 003: ID 044f:0450 ThrustMaster, Inc. Base MKII

cat /proc/bus/input/devices     # confirms it bound and created js0/event0
ls -la /dev/hidraw*
```

Record its VID:PID. Then dump its HID report descriptor — this is what tells you
the report layout you must translate *from*:

```bash
sudo xxd -p /sys/class/hidraw/hidraw0/device/report_descriptor | tr -d '\n'; echo
```

For the Base MKII this yields 142 bytes:

```
05010904a1018501050919012913150025017501951381029545810305010939150025073500
463b0165147504950181426500750495018103050109300931093209350934093309360937150
027ffff00007510950881020950150026ff00750895238103094785f2150026ff007508953fb1
02094785f1150026ff007508953fb102094885f37508953fb102c0
```

Decoded, the input report (ID 1, 64 bytes total) is:

| Bytes | Contents |
| --- | --- |
| 0 | Report ID (`0x01`) |
| 1–11 | 19 button bits + 69 constant padding bits |
| 12 | Hat in the low nibble (0–7 = direction, >7 = null), 4 pad bits |
| 13–28 | Eight 16-bit little-endian axes: **X, Y, Z, Rz, Ry, Rx, Slider, Dial** |
| 29–63 | 35 bytes constant padding |

All eight axes declare `Logical Minimum 0` / `Logical Maximum 65535` via the
4-byte form `27 FF FF 00 00`. **This matters:** the target device uses the same
0–65535 range, so values pass through raw with no scaling. If your stick
declares a different range, you must scale — see *Adapting this to a different
stick*.

---

## Step 4 — Install the scripts

From a checkout of this repository:

```bash
scp -i "$PI_KEY" tm.sh disconnect.sh hotas_proxy.py verify-axes.py \
    "$PI_USER@$PI_HOST:/tmp/"

ssh -i "$PI_KEY" "$PI_USER@$PI_HOST" \
    'sudo install -m755 /tmp/tm.sh /tmp/disconnect.sh /tmp/hotas_proxy.py \
     /tmp/verify-axes.py /root/'
```

| File | Purpose |
| --- | --- |
| `tm.sh` | Creates and binds the `044f:0402` gadget via configfs |
| `disconnect.sh` | Tears the gadget down |
| `hotas_proxy.py` | Translates MKII reports → Warthog reports |
| `verify-axes.py` | Interactive check that axes/buttons actually move |

---

## Step 5 — Bring it up

Connect the Pi's USB-C port to the Mac, then:

```bash
ssh -i "$PI_KEY" "$PI_USER@$PI_HOST"
sudo /root/tm.sh
```

Expected:

```
Wrote 105 descriptor bytes
Configured 044f:0402 stick (axis-range fix). Descriptor=105 bytes, report_len=12, UDC=fe980000.usb
```

Confirm the host enumerated it:

```bash
cat /sys/class/udc/fe980000.usb/state
# configured      <- the Mac has set a configuration. "not attached" means it hasn't.
ls -la /dev/hidg0
```

Start the proxy:

```bash
sudo python3 -u /root/hotas_proxy.py
```

Expected:

```
Physical device: Thrustmaster Base MKII
Reading from:    /dev/hidraw0
Writing to:      /dev/hidg0
Target identity: 044f:0402 (original Warthog joystick)
Proxy running. Move the stick or press a button.
First report relayed: source=64 bytes, virtual=12 bytes
```

### Why the descriptor is 105 bytes, not 101

The genuine Warthog descriptor is 101 bytes and declares the X axis maximum as
`26 FF FF` — Logical Maximum, 2-byte form, `0xFFFF`.

HID logical min/max are **signed**. Two-byte `0xFFFF` is −1. Combined with
`Logical Minimum 0`, that is nonsense, and parsers respond by collapsing the
axes to binary on/off. Real Windows drivers paper over this for known hardware;
a generic parser does not.

`tm.sh` substitutes the unambiguous 4-byte form `27 FF FF 00 00` (= +65535) for
both the logical and physical maximum, adding 4 bytes. The report layout is
unchanged. GFN accepted this without complaint — the allowlist checks VID:PID
only and does not fingerprint descriptors.

If you ever suspect the descriptor is the problem, the genuine 101-byte version
is the first thing to revert to.

---

## Step 6 — Verify on the Mac

```bash
# Is it on the USB bus?
ioreg -p IOUSB -w 0 | grep -iE 'warthog|thrustmaster'

# Did macOS bind it as a joystick?
hidutil list | grep -i warthog
```

Expect `PrimaryUsagePage 1` / `PrimaryUsage 4` (Generic Desktop / Joystick) and
`AppleUserHIDEventDriver`.

Read live reports (`hidapitester`, from `brew install hidapitester`):

```bash
hidapitester --vidpid 044F:0402 --nonexclusive --open --length 12 \
  --timeout 300 --read-input
# 01 00 00 80 00 80 00 80 FF FF 00 00
```

Decode: byte 0 = report ID; bytes 1–3 = buttons + hat (`0x800000` → hat 8 =
null); bytes 4–5 = X; 6–7 = Y; 8–9 and 10–11 = the two vendor fields.

### Confirming the axes actually move

The MKII emits a **frozen idle report at ~4 Hz** and only bursts to ~250 Hz
while being actuated. Fixed-length captures therefore record nothing but the
idle frame and look like failure. Use the interactive checker instead:

```bash
sudo python3 /root/verify-axes.py
# sweep both axes to their stops, work the hat, press buttons, then Ctrl+C
```

It prints per-axis min/max with a MOVED/static verdict.

> Note for macOS: there is no `timeout` binary unless you install GNU coreutils.
> Scripted captures using it will fail silently with
> `command not found: timeout`.

### Steam is not a valid test

The stick will **not** appear in Steam's controller settings — and neither does
a genuine Warthog. Both enumerate as HID usage **4 (Joystick)**, not 5
(Gamepad), and Steam Input's UI only manages gamepads. Steam *does* see them;
check `~/Library/Application Support/Steam/logs/controller.txt` for
`Local Device Found ... type: 044f 0402`.

Absence from Steam's UI tells you nothing. Only GFN's own logs do.

---

## Step 7 — Verify in GeForce NOW

Start a session, then inspect
`~/Library/Application Support/NVIDIA/GeForceNOW/geronimo.log`:

```bash
D="$HOME/Library/Application Support/NVIDIA/GeForceNOW"

# 1. The runtime allowlist, as loaded:
grep -a 'GSHID: Supporting' "$D/geronimo.log"

# 2. Did GFN push the device to the cloud VM?
grep -a 'Plugging' "$D/geronimo.log"

# 3. Was anything rejected?
grep -a 'Invalid device for synthesizing' "$D/geronimo.log"
```

A working session shows:

```
DEBUG [HIDDevicesController] - GSHID: Supporting {044F:0402}
 INFO [HIDDevicesController] - Plugging 044F:0404:0100
 INFO [HIDDevicesController] - Plugging 044F:0402:0100
DEBUG [HIDDevicesController] - Handling generic device response event 10 25
```

`Plugging` is the line that matters — the device reached the cloud VM. The
rejection string must be **absent**.

GFN also emits a `HIDInfo` telemetry event per device (in `geronimo.log`, as
JSON) giving `vendorID`/`productID` in **decimal**: `1103` = `0x44f`,
`1026` = `0x402`, `1028` = `0x404`.

---

## Step 8 — Make it survive reboot

Neither the gadget nor the proxy persists across a reboot on its own. Install
the two units:

```bash
scp -i "$PI_KEY" hotas-gadget.service hotas-proxy.service \
    "$PI_USER@$PI_HOST:/tmp/"

ssh -i "$PI_KEY" "$PI_USER@$PI_HOST" \
  'sudo pkill -f hotas_proxy.py; \
   sudo install -m644 /tmp/hotas-gadget.service /tmp/hotas-proxy.service \
       /etc/systemd/system/ && \
   sudo systemctl daemon-reload && \
   sudo systemctl enable --now hotas-gadget.service hotas-proxy.service && \
   systemctl status --no-pager hotas-gadget hotas-proxy'
```

`hotas-gadget` is `oneshot` + `RemainAfterExit` with `disconnect.sh` as
`ExecStop`. `hotas-proxy` `Requires=` it and uses `Restart=always`, so if the
stick is unplugged or enumerates late at boot the proxy retries rather than
dying.

To undo:

```bash
sudo systemctl disable --now hotas-proxy hotas-gadget
```

> These units are **untested** — written but never installed during the original
> build. Watch the first boot and check
> `journalctl -u hotas-gadget -u hotas-proxy`.

---

## Adapting this to a different stick

Most of the work is in `hotas_proxy.py`. To retarget it:

**1. Change the source identity.**

```python
SOURCE_VID = 0x044F
SOURCE_PID = 0x0450          # ← your stick
SOURCE_REPORT_SIZE = 64      # ← your stick's input report size
```

**2. Re-derive the source layout** from your stick's report descriptor
(Step 3). You need the byte offsets of the buttons, hat and axes, and each
axis's declared logical range. Update the constants in `translate_report()`.

**3. Pick a target from the allowlist** (below). The target's descriptor
determines what you can express. Extract a genuine descriptor for that device —
Linux kernel sources, SDL, and the Linux USB ID repository are reasonable
places to look. Put its bytes in `tm.sh` and set `report_length` to match.

**4. Scale if the ranges differ.** The MKII→Warthog case needs none because both
are 0–65535. If yours differ, rescale each axis from the source's declared
logical range to the target's. Getting this wrong produces axes that work but
with a dead zone, a hard stop partway, or inverted travel.

**5. Verify at each layer** before moving to the next: `verify-axes.py` on the
Pi → `hidapitester` on the Mac → `geronimo.log` in GFN. Debugging all three at
once is miserable.

### The allowlist

As of GFN 2.0.90.120, `libGeronimo.dylib` contains exactly 20 entries. Re-extract
them yourself — NVIDIA updates this binary:

```bash
strings -a /Applications/GeForceNOW.app/Contents/Frameworks/libGeronimo.dylib \
  | grep -oE '\b[0-9a-f]{4}:[0-9a-f]{4}\b' | sort -u
```

```
044f: 0402 0404 2804 b10a b67c b687 b68d b68f b697   (Thrustmaster)
046d: c24f c262 c266 c268 c26e c272 c276             (Logitech wheels)
06a3: 075c 0762                                      (Saitek)
0738: 2221 a221                                      (Mad Catz / Saitek)
```

Useful identifications:

| VID:PID | Device |
| --- | --- |
| `044f:0402` | Thrustmaster HOTAS Warthog **Joystick** — 2 axes, 19 buttons, hat |
| `044f:0404` | Thrustmaster HOTAS Warthog **Throttle** |
| `0738:2221` | Saitek / Mad Catz Pro Flight **X-56 Rhino Stick** |
| `0738:a221` | X-56 Rhino **Throttle** |
| `06a3:0762` | Saitek **X52 Pro** |

If you need more than two axes, the **X-56 Rhino** pair is the richest target,
and both halves are allowlisted.

---

## Known limitations

**One gadget identity at a time.** One Pi has one UDC, so it can present exactly
one VID:PID. You cannot emulate both a stick and a throttle from a single Pi.
The working arrangement here is: Pi presents the stick, the real throttle plugs
into the Mac directly. Two emulated devices needs two Pis.

**`044f:0402` carries only X, Y, 19 buttons and one hat.** The original Warthog
joystick descriptor has exactly two real axes. The proxy forwards the MKII's Z
and Rz into the descriptor's two vendor-defined 16-bit fields, where **no game
will read them**, and Ry, Rx, Slider and Dial are not forwarded at all. This is
a ceiling imposed by the target descriptor, not by the code. Switch targets if
it matters.

**Poll rate differs from genuine hardware.** GFN logs
`reportIntervalMicroSeconds: 1000` for the gadget versus `8000` for a real
Warthog throttle — 1 ms versus 8 ms. That is lower latency, not a defect, but it
is the first thing to match if input ever behaves erratically.

**No force feedback.** The proxy is input-only. The gadget declares the output
report the genuine descriptor defines, but nothing consumes it.

**Reboot wipes everything** until Step 8 is done.

---

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `/sys/class/udc/` empty | `dwc2` not in peripheral mode. Check `config.txt` section placement — a line after `[cm5]` is ignored on a 4B. |
| `UDC fe980000.usb not found` | Same as above, or wrong UDC name for your Pi model. |
| UDC state `not attached` | Host hasn't enumerated. Bad/charge-only USB-C cable, or the Mac end isn't connected. |
| `/dev/hidg0` missing | `tm.sh` didn't finish. Re-run and read its output. |
| `sudo: a terminal is required to read the password` | No NOPASSWD rule. See Step 1, or run from an interactive SSH session. |
| Proxy: "Physical MKII stick not found" | Stick not on a USB-A port, or `SOURCE_PID` doesn't match `lsusb`. |
| Axes read as on/off only | Descriptor axis-range bug. Use the 4-byte `27 FF FF 00 00` form (`tm.sh` already does). |
| Device visible on Mac, ignored by GFN | VID:PID not allowlisted. Re-extract the list — NVIDIA may have changed it. |
| `Invalid device for synthesizing HID report` in log | Explicit allowlist rejection. The VID:PID is wrong. |
| Stick appears but nothing moves | Confirm the source is actually moving (`verify-axes.py`). Idle MKII reports are frozen at ~4 Hz. |
| Not in Steam controller settings | Expected. Not a fault — see Step 6. |
| configfs `rmdir: Device or resource busy` | Unbind the UDC before removing anything. Use `disconnect.sh`. |

---

## File reference

| File | Installed to | Run as |
| --- | --- | --- |
| `tm.sh` | `/root/tm.sh` | root |
| `disconnect.sh` | `/root/disconnect.sh` | root |
| `hotas_proxy.py` | `/root/hotas_proxy.py` | root |
| `verify-axes.py` | `/root/verify-axes.py` | root |
| `hotas-gadget.service` | `/etc/systemd/system/` | systemd |
| `hotas-proxy.service` | `/etc/systemd/system/` | systemd |

---

## What was and wasn't tested

The running system described here was verified end to end: gadget bring-up,
macOS enumeration, byte-exact report translation against live hardware, GFN
acceptance (`Plugging 044F:0402:0100`), and confirmed in-game control.

Two things are **reconstructed rather than re-tested**: the from-scratch
Step 1–2 setup (derived from the working machine's final state, not re-run on a
blank SD card), and the systemd units in Step 8 (written but never installed).
Expect to debug those two sections. Everything else was observed working.

---

## Trademarks

Thrustmaster, Saitek, Mad Catz, Logitech and NVIDIA names and product
identifiers appear here solely to describe hardware compatibility. This project
is not affiliated with or endorsed by any of them.
