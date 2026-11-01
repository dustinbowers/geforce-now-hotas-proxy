#!/usr/bin/env bash
# Bring up a USB HID gadget that presents this Pi to the host as a
# Thrustmaster HOTAS Warthog joystick (044f:0402) -- a VID:PID that
# GeForce NOW allowlists.
#
# Run as root on the Raspberry Pi. Idempotent: safe to re-run.
set -Eeuo pipefail

G=/sys/kernel/config/usb_gadget/hotas
F="$G/functions/hid.usb0"
C="$G/configs/c.1"
UDC_NAME=fe980000.usb

die() { echo "ERROR: $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Run as root: sudo $0"

# libcomposite provides the configfs gadget interface. On a fresh install it
# is usually not loaded yet.
modprobe libcomposite

if ! mountpoint -q /sys/kernel/config; then
    mount -t configfs none /sys/kernel/config
fi

[[ -d "/sys/class/udc/$UDC_NAME" ]] || die "UDC $UDC_NAME not found (is dwc2 in peripheral mode?)"

mkdir -p "$G" "$G/strings/0x409" "$C/strings/0x409" "$F"

# Unbind if currently bound.
if [[ -n "$(cat "$G/UDC" 2>/dev/null)" ]]; then
    printf '\n' > "$G/UDC"
fi

# Remove config link before changing HID attributes.
if [[ -L "$C/hid.usb0" ]]; then
    rm "$C/hid.usb0"
elif [[ -e "$C/hid.usb0" ]]; then
    die "$C/hid.usb0 exists but is not a symlink"
fi

# Original Warthog joystick USB identity (GFN-compatible).
printf '0x0200' > "$G/bcdUSB"
printf '0x0100' > "$G/bcdDevice"
printf '0x044f' > "$G/idVendor"
printf '0x0402' > "$G/idProduct"
printf '%s' 'Thrustmaster' > "$G/strings/0x409/manufacturer"
printf '%s' 'Joystick - HOTAS Warthog' > "$G/strings/0x409/product"
printf '%s' 'HID' > "$C/strings/0x409/configuration"
printf '250' > "$C/MaxPower"
printf '0x80' > "$C/bmAttributes"

printf '0'  > "$F/subclass"
printf '0'  > "$F/protocol"
printf '12' > "$F/report_length"
printf '0'  > "$F/no_out_endpoint"

# 105-byte descriptor: identical to the genuine V1 stick EXCEPT the X axis
# logical/physical maximum uses the 4-byte form (27 FF FF 00 00 = 65535)
# instead of the ambiguous 2-byte 26 FF FF. HID logical min/max are signed,
# so 2-byte 0xFFFF parses as -1; with Logical Minimum 0 that is nonsense and
# parsers collapse the axes to binary. The 4-byte form is unambiguous.
# Report layout (12-byte report ID 1) is unchanged.
python3 - "$F/report_desc" <<'PY'
import pathlib, sys
descriptor = bytes.fromhex("""
05 01 09 04 A1 01 85 01 05 09 19 01 29 13 15 00
25 01 35 00 45 01 65 00 55 00 75 01 95 13 81 02
95 01 81 03 05 01 09 39 25 07 46 3B 01 65 14 75
04 81 42 09 30 27 FF FF 00 00 47 FF FF 00 00 65 00 75 10 81
02 09 31 81 02 05 FF 09 01 81 02 09 02 81 02 85
02 09 03 25 FF 45 FF 75 08 95 0B 81 02 85 01 09
BB 91 02 C1 00
""")
if len(descriptor) != 105:
    raise SystemExit(f"Descriptor is {len(descriptor)} bytes; expected 105")
pathlib.Path(sys.argv[1]).write_bytes(descriptor)
print(f"Wrote {len(descriptor)} descriptor bytes")
PY

actual=$(wc -c < "$F/report_desc")
[[ "$actual" -eq 105 ]] || die "Descriptor length is $actual; expected 105"

ln -s "$F" "$C/hid.usb0"
printf '%s' "$UDC_NAME" > "$G/UDC"

echo "Configured 044f:0402 stick (axis-range fix). Descriptor=$actual bytes, report_len=12, UDC=$(cat "$G/UDC")"
