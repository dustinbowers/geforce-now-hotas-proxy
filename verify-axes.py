#!/usr/bin/env python3
"""
Watch the physical stick and report what actually moved.

Runs until interrupted, so it cannot "catch idle" the way a fixed-length
capture does: actuate everything, then press Ctrl+C for the summary.

This matters because the MKII emits a frozen idle report at ~4 Hz and only
bursts to ~250 Hz while being moved -- timed captures reliably record
nothing but the idle frame.

Run as root on the Raspberry Pi. Safe to run while hotas_proxy.py is
running: both only read the source hidraw node.
"""

import os
import struct
import sys

SOURCE_REPORT_ID = 0x01
SOURCE_REPORT_SIZE = 64

AXIS_NAMES = ["X", "Y", "Z", "Rz", "Ry", "Rx", "Slider", "Dial"]
AXIS_BASE = 13

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hotas_proxy import find_source


def main():
    if os.geteuid() != 0:
        sys.exit("Run as root: sudo python3 verify-axes.py")

    source_path, source_name = find_source()
    print(f"Watching: {source_name} ({source_path})")
    print("Sweep both stick axes to their stops, work the hat, press buttons.")
    print("Press Ctrl+C when done.\n")

    mins = [0xFFFF] * 8
    maxs = [0] * 8
    buttons_seen = 0
    hats_seen = set()
    reports = 0

    fd = os.open(source_path, os.O_RDONLY)
    try:
        while True:
            report = os.read(fd, 4096)
            if len(report) < SOURCE_REPORT_SIZE or report[0] != SOURCE_REPORT_ID:
                continue

            reports += 1
            buttons_seen |= int.from_bytes(report[1:4], "little") & 0x7FFFF
            hats_seen.add(report[12] & 0x0F)

            for axis in range(8):
                value = struct.unpack_from("<H", report, AXIS_BASE + 2 * axis)[0]
                mins[axis] = min(mins[axis], value)
                maxs[axis] = max(maxs[axis], value)
    except KeyboardInterrupt:
        pass
    finally:
        os.close(fd)

    print(f"\n--- {reports} reports ---")
    for axis, name in enumerate(AXIS_NAMES):
        low, high = mins[axis], maxs[axis]
        span = high - low
        verdict = "MOVED" if span > 1000 else "static"
        print(f"  {name:7s} min={low:5d} max={high:5d} span={span:5d}  {verdict}")

    print(f"\n  buttons pressed (mask): 0x{buttons_seen:05x}")
    print(f"  distinct hat values:    {sorted(hats_seen)}")


if __name__ == "__main__":
    main()
