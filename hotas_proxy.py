#!/usr/bin/env python3
"""
Thrustmaster Warthog MKII -> virtual original Warthog MKI joystick proxy.

Physical source:  VID:PID 044F:0450
Virtual gadget:   VID:PID 044F:0402
Gadget endpoint:  /dev/hidg0

Requires tm.sh to have configured and bound the original-stick gadget.
Run as root on the Raspberry Pi.
"""

import glob
import os
import struct
import sys
from pathlib import Path

SOURCE_VID = 0x044F
SOURCE_PID = 0x0450
SOURCE_REPORT_ID = 0x01
SOURCE_REPORT_SIZE = 64

GADGET_DEVICE = "/dev/hidg0"
TARGET_REPORT_ID = 0x01
TARGET_REPORT_SIZE = 12

# MKII source report, including report ID:
#   byte 0:       report ID
#   bytes 1..11:  19 buttons + constant padding
#   byte 12:      hat
#   bytes 13..28: eight little-endian 16-bit axes
#
# Original MKI target report ID 1, including report ID:
#   byte 0:       report ID
#   bytes 1..3:   19 button bits, constant bit, 4-bit hat
#   bytes 4..5:   X axis (16-bit)
#   bytes 6..7:   Y axis (16-bit)
#   bytes 8..9:   vendor-defined 16-bit field 1
#   bytes 10..11: vendor-defined 16-bit field 2
#
# The original descriptor also defines input report ID 2 (11 payload
# bytes). This proxy currently forwards report ID 1 only.


def find_source():
    """Find the physical MKII stick's hidraw node by USB VID/PID."""
    for entry in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        uevent = Path(entry, "device", "uevent")

        try:
            lines = uevent.read_text(errors="replace").splitlines()
        except OSError:
            continue

        fields = {}
        for line in lines:
            if "=" in line:
                key, value = line.split("=", 1)
                fields[key] = value

        parts = fields.get("HID_ID", "").split(":")
        if len(parts) != 3:
            continue

        try:
            vid = int(parts[1], 16)
            pid = int(parts[2], 16)
        except ValueError:
            continue

        if vid == SOURCE_VID and pid == SOURCE_PID:
            node = "/dev/" + Path(entry).name
            if os.path.exists(node):
                return node, fields.get("HID_NAME", "Unknown HID device")

    raise RuntimeError(
        "Physical MKII stick not found. Check its USB-A connection to the Pi."
    )


def read_u16_le(report, offset):
    return struct.unpack_from("<H", report, offset)[0]


def translate_report(source):
    """Translate one 64-byte MKII report into a 12-byte MKI report."""
    if len(source) < SOURCE_REPORT_SIZE:
        raise ValueError(
            f"Short source report: {len(source)} bytes; "
            f"expected at least {SOURCE_REPORT_SIZE}"
        )

    if source[0] != SOURCE_REPORT_ID:
        return None

    out = bytearray(TARGET_REPORT_SIZE)
    out[0] = TARGET_REPORT_ID

    # Extract the first 19 buttons from the MKII report.
    buttons = int.from_bytes(source[1:4], "little") & 0x7FFFF

    # The MKI descriptor packs the 19 buttons, one constant bit,
    # and the 4-bit hat into exactly 3 bytes.
    hat = source[12] & 0x0F
    if hat > 7:
        hat = 8  # Null hat position; the descriptor allows null state.

    packed_controls = buttons | (hat << 20)
    out[1:4] = packed_controls.to_bytes(3, "little")

    # Preserve the source's raw 16-bit axis values.
    # MKII axes 0 and 1 -> MKI X and Y.
    # MKII axes 2 and 3 -> the two vendor-defined 16-bit fields.
    source_axis_offsets = [13 + 2 * i for i in range(8)]
    target_axis_offsets = [4, 6, 8, 10]

    for source_offset, target_offset in zip(
        source_axis_offsets[:4], target_axis_offsets
    ):
        value = read_u16_le(source, source_offset)
        struct.pack_into("<H", out, target_offset, value)

    return bytes(out)


def make_neutral_report():
    """Button-free, centered report with a null hat position."""
    out = bytearray(TARGET_REPORT_SIZE)
    out[0] = TARGET_REPORT_ID

    # Buttons and constant bit remain zero.
    # Hat value 8 is the null position.
    packed_controls = 8 << 20
    out[1:4] = packed_controls.to_bytes(3, "little")

    # Center the two standard axes and both vendor-defined fields.
    for offset in (4, 6, 8, 10):
        struct.pack_into("<H", out, offset, 32768)

    return bytes(out)


def write_report(fd, report):
    written = os.write(fd, report)
    if written != len(report):
        raise OSError(
            f"Short gadget write: {written}/{len(report)} bytes"
        )


def main():
    if os.geteuid() != 0:
        sys.exit("Run as root: sudo python3 /root/hotas_proxy.py")

    source_path, source_name = find_source()

    if not os.path.exists(GADGET_DEVICE):
        sys.exit(
            f"{GADGET_DEVICE} not found. Configure and bind the gadget "
            "with /root/tm.sh first."
        )

    print(f"Physical device: {source_name}")
    print(f"Reading from:    {source_path}")
    print(f"Writing to:      {GADGET_DEVICE}")
    print("Target identity: 044f:0402 (original Warthog joystick)")
    print(f"Target report:   ID {TARGET_REPORT_ID}, {TARGET_REPORT_SIZE} bytes")

    source_fd = os.open(source_path, os.O_RDONLY)
    gadget_fd = os.open(GADGET_DEVICE, os.O_WRONLY)

    reports_read = 0
    reports_written = 0

    try:
        write_report(gadget_fd, make_neutral_report())

        print("Proxy running. Move the stick or press a button.")
        print("Press Ctrl+C to stop.")

        while True:
            source = os.read(source_fd, 4096)
            if not source:
                continue

            reports_read += 1

            translated = translate_report(source)
            if translated is None:
                continue

            write_report(gadget_fd, translated)
            reports_written += 1

            if reports_written == 1:
                print(
                    f"First report relayed: source={len(source)} bytes, "
                    f"virtual={len(translated)} bytes"
                )

            if reports_written % 500 == 0:
                print(
                    f"Relayed {reports_written} reports "
                    f"(read {reports_read})"
                )

    except KeyboardInterrupt:
        print(
            f"\nStopped. Read {reports_read} reports; "
            f"relayed {reports_written}."
        )
    finally:
        os.close(source_fd)
        os.close(gadget_fd)


if __name__ == "__main__":
    main()
