#!/usr/bin/env bash
# Tear down the HID gadget created by tm.sh and return the UDC to idle.
#
# Run as root on the Raspberry Pi. Idempotent: safe to run when nothing is up.
set -Eeuo pipefail

G=/sys/kernel/config/usb_gadget/hotas
F="$G/functions/hid.usb0"
C="$G/configs/c.1"

[[ $EUID -eq 0 ]] || { echo "ERROR: run as root: sudo $0" >&2; exit 1; }

if [[ ! -d "$G" ]]; then
    echo "No gadget at $G; nothing to do."
    exit 0
fi

# Unbind from the UDC first -- nothing else can be removed while bound.
if [[ -n "$(cat "$G/UDC" 2>/dev/null)" ]]; then
    printf '\n' > "$G/UDC"
fi

# Drop the function out of the config.
[[ -L "$C/hid.usb0" ]] && rm "$C/hid.usb0"

# configfs requires removal in strict order: strings, then config, then
# function, then the gadget's own strings, then the gadget.
[[ -d "$C/strings/0x409" ]] && rmdir "$C/strings/0x409"
[[ -d "$C" ]] && rmdir "$C"
[[ -d "$F" ]] && rmdir "$F"
[[ -d "$G/strings/0x409" ]] && rmdir "$G/strings/0x409"
rmdir "$G"

echo "Gadget torn down."
