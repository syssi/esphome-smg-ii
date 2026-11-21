#!/usr/bin/env python3
"""
Deep register scan for ISolar/EASUN SMG II 6KW inverter.

Runs N scans with a configurable interval between them, then highlights
registers whose values changed across scans – those are live measurements.
Constant registers are config values or unused.

Connection modes
----------------
TCP (ESP running esp32-c3-usb-a-tcp-bridge.yaml)  ← recommended:
    python3 deep_scan.py --host verty-links.local

Serial (direct USB/RS232 adapter):
    python3 deep_scan.py --port /dev/ttyUSB0

Options:
    --scans N          number of full scans to run (default: 3)
    --interval SECS    seconds between scans (default: 30)
    --tcp-port PORT    TCP port of bridge (default: 8888)
    --output FILE      output TSV file (default: register_map.tsv)

Requirements:
    pip install pyserial
"""
import argparse
import struct
import sys
import time
from pathlib import Path

BAUD   = 9600
WAKEUP = bytes.fromhex('01aa06dea2')
BLOCK  = 10


# ── Modbus helpers ─────────────────────────────────────────────────────────────

def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def build_read(reg: int, count: int) -> bytes:
    frame = struct.pack('>BBHH', 0x01, 0x03, reg, count)
    return frame + struct.pack('<H', crc16(frame))


def drain(ser) -> None:
    try:
        ser.reset_input_buffer()
    except AttributeError:
        old_timeout, ser.timeout = ser.timeout, 0.05
        while ser.read(256):
            pass
        ser.timeout = old_timeout


def read_block(ser, start: int, count: int) -> list[int] | None:
    ser.write(WAKEUP)
    time.sleep(0.25)
    drain(ser)
    ser.write(build_read(start, count))
    time.sleep(0.4)
    resp = ser.read(256)
    if (len(resp) >= 5
            and resp[0] == 0x01
            and resp[1] == 0x03
            and resp[2] == count * 2):
        return [
            struct.unpack('>H', resp[3 + i * 2: 5 + i * 2])[0]
            for i in range(count)
        ]
    return None


def s16(v: int) -> int:
    return v if v < 32768 else v - 65536


# ── One full scan pass ─────────────────────────────────────────────────────────

RANGES = [
    (0x0000, 0x0050),   # device info / serial number
    (0x0050, 0x00A0),   # confirmed data block (grid, output, PV)
    (0x00A0, 0x0100),   # temperature candidates + gap
    (0x0100, 0x0160),   # gap / unknown
    (0x0150, 0x01A0),   # battery + charging (re-scan fine)
    (0x01A0, 0x0200),   # extended settings
    (0x0200, 0x0300),   # more settings / config
    (0x0300, 0x0400),   # unknown
    (0x0400, 0x0500),   # syssi turn-on mode area
    (0x1000, 0x1020),   # extended / vendor-specific
    (0x2000, 0x2020),   # extended / vendor-specific
]

TOTAL_BLOCKS = sum((e - s + BLOCK - 1) // BLOCK for s, e in RANGES)


def run_scan(ser, scan_num: int, total_scans: int) -> tuple[dict[int, int], list[int]]:
    """Run one full scan pass. Returns (found, no_resp)."""
    print(f'\n── Scan {scan_num}/{total_scans} ──────────────────────────────────────────')
    found: dict[int, int] = {}
    no_resp: list[int] = []
    done = 0

    for range_start, range_end in RANGES:
        reg = range_start
        while reg < range_end:
            count = min(BLOCK, range_end - reg)
            vals = read_block(ser, reg, count)
            if vals is not None:
                for i, v in enumerate(vals):
                    if v != 0:
                        found[reg + i] = v
            else:
                no_resp.append(reg)
            reg += count
            done += 1
            if done % 30 == 0:
                pct = done * 100 // TOTAL_BLOCKS
                print(f'  {pct}%  ({len(found)} non-zero)', flush=True)

    print(f'  done – {len(found)} non-zero registers, {len(no_resp)} no-response blocks')
    return found, no_resp


# ── ASCII string detector ──────────────────────────────────────────────────────

def find_strings(found: dict[int, int], min_len: int = 3) -> list[tuple[int, str]]:
    results = []
    run_start = None
    run_chars: list[str] = []

    for reg in sorted(found):
        v = found[reg]
        hi, lo = v >> 8, v & 0xFF
        if (32 <= hi <= 126) and (32 <= lo <= 126) and hi != 0:
            if run_start is None:
                run_start = reg
            run_chars += [chr(hi), chr(lo)]
        else:
            if len(run_chars) >= min_len:
                results.append((run_start, ''.join(run_chars).strip()))
            run_start = None
            run_chars = []

    if len(run_chars) >= min_len:
        results.append((run_start, ''.join(run_chars).strip()))
    return results


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description='SMG II 6KW deep Modbus register scan with variance analysis',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument('--host', metavar='HOST',
                     help='ESP hostname/IP for TCP bridge mode')
    grp.add_argument('--port', metavar='PORT',
                     help='Serial port (e.g. /dev/ttyUSB0 or COM3)')
    parser.add_argument('--tcp-port', type=int, default=8888)
    parser.add_argument('--scans',    type=int, default=3,
                        help='Number of full scans to run (default: 3)')
    parser.add_argument('--interval', type=int, default=30,
                        help='Seconds between scans (default: 30)')
    parser.add_argument('--output',   default='register_map.tsv',
                        help='Output TSV file (default: register_map.tsv)')
    args = parser.parse_args()

    import serial  # noqa: PLC0415

    if args.host:
        url = f'socket://{args.host}:{args.tcp_port}'
        print(f'Connecting via TCP → {url}')
        ser = serial.serial_for_url(url, timeout=2)
        time.sleep(1.0)
    else:
        print(f'Opening serial port {args.port}')
        ser = serial.Serial(args.port, BAUD, timeout=2,
                            bytesize=8, parity='N', stopbits=1)

    # Initial wakeup
    ser.write(WAKEUP)
    time.sleep(0.5)
    drain(ser)

    print(f'\nRunning {args.scans} scans, {args.interval}s apart.')
    print(f'Registers that change between scans = live measurements.\n')

    # ── Collect all scan results ───────────────────────────────────────────────
    all_scans: list[dict[int, int]] = []
    all_no_resp: list[list[int]] = []

    for i in range(1, args.scans + 1):
        found, no_resp = run_scan(ser, i, args.scans)
        all_scans.append(found)
        all_no_resp.append(no_resp)
        if i < args.scans:
            print(f'\nWaiting {args.interval}s …', flush=True)
            time.sleep(args.interval)

    ser.close()

    # ── Merge: all registers seen in any scan ──────────────────────────────────
    all_regs: set[int] = set()
    for scan in all_scans:
        all_regs.update(scan.keys())

    # Build per-register value list (None if not seen in that scan)
    reg_values: dict[int, list[int | None]] = {}
    for reg in sorted(all_regs):
        reg_values[reg] = [scan.get(reg) for scan in all_scans]

    # Classify
    varying:  dict[int, list[int | None]] = {}
    constant: dict[int, list[int | None]] = {}
    for reg, vals in reg_values.items():
        seen = [v for v in vals if v is not None]
        if len(set(seen)) > 1:
            varying[reg] = vals
        else:
            constant[reg] = vals

    # ── Print results ──────────────────────────────────────────────────────────
    sep = '═' * 68
    print(f'\n{sep}')
    print(f'  SCAN COMPLETE  –  {len(all_regs)} unique non-zero registers')
    print(f'  {len(varying)} VARYING (live measurements)  ·  {len(constant)} constant')
    print(sep)

    def fmt_val(v: int | None) -> str:
        return f'{v:6d}' if v is not None else '     –'

    def unit_guess(reg: int, vals: list[int | None]) -> str:
        seen = [v for v in vals if v is not None]
        if not seen:
            return ''
        v, sv = seen[0], s16(seen[0])
        hints: list[str] = []
        if any(2000 <= x <= 2600 for x in seen):  hints.append('→ V×0.1?')
        if any(4900 <= x <= 5100 for x in seen):  hints.append('→ Hz×0.01?')
        if any(450  <= x <= 600  for x in seen) and reg not in (0x0050, 0x0058):
                                                   hints.append('→ V (batt)×0.1?')
        if any(10   <= x <= 90   for x in seen):  hints.append('→ temp?')
        if sv != v:                                hints.append(f'signed={sv}')
        return '  ' + ', '.join(hints) if hints else ''

    # ── VARYING first ──────────────────────────────────────────────────────────
    if varying:
        print(f'\n  ▶▶ VARYING registers (most interesting for sensor mapping) ◀◀\n')
        scan_hdr = '  '.join(f'scan{i+1:>2}' for i in range(args.scans))
        print(f'    {"reg":6}  {scan_hdr}   delta')
        print(f'    {"─"*6}  {"─"*6}  ' + '  '.join(['──────'] * (args.scans - 1)) + '  ──────')

        prev_page = -1
        for reg in sorted(varying):
            page = reg & ~0x0F
            if page != prev_page:
                print(f'\n  ── page {page:#06x} ──')
                prev_page = page
            vals = varying[reg]
            seen = [v for v in vals if v is not None]
            delta = max(seen) - min(seen)
            val_str = '  '.join(fmt_val(v) for v in vals)
            print(f'    {reg:#06x}  {val_str}  Δ{delta:5d}{unit_guess(reg, vals)}')

    # ── CONSTANT ──────────────────────────────────────────────────────────────
    if constant:
        print(f'\n\n  Constant registers ({len(constant)}) – config / info / unused:\n')
        prev_page = -1
        for reg in sorted(constant):
            page = reg & ~0x0F
            if page != prev_page:
                print(f'\n  ── page {page:#06x} ──')
                prev_page = page
            vals = constant[reg]
            seen = [v for v in vals if v is not None]
            v = seen[0]
            sv = s16(v)
            tags: list[str] = []
            if sv != v:             tags.append(f'signed={sv}')
            if 32 <= v <= 126:      tags.append(f"char='{chr(v)}'")
            tag_str = '  ' + ', '.join(tags) if tags else ''
            print(f'    {reg:#06x} = {v:6d}  0x{v:04x}{tag_str}')

    # ── ASCII strings ──────────────────────────────────────────────────────────
    first_scan_found = all_scans[0]
    strings = find_strings(first_scan_found)
    if strings:
        print(f'\n\n  ── ASCII strings (possible serial number / model) ──')
        for start, s in strings:
            print(f'    {start:#06x}  "{s}"')

    # ── No-response summary ────────────────────────────────────────────────────
    all_no = sorted(set(r for nr in all_no_resp for r in nr))
    if all_no:
        nr_str = ', '.join(f'{r:#06x}' for r in all_no[:30])
        suffix = '…' if len(all_no) > 30 else ''
        print(f'\n  No-response blocks: {nr_str}{suffix}')

    # ── Save TSV ───────────────────────────────────────────────────────────────
    out = Path(args.output)
    with out.open('w') as f:
        headers = ['reg'] + [f'scan{i+1}' for i in range(args.scans)] + ['min', 'max', 'delta', 'varies']
        f.write('\t'.join(headers) + '\n')
        for reg in sorted(all_regs):
            vals = reg_values[reg]
            seen = [v for v in vals if v is not None]
            mn, mx = min(seen), max(seen)
            varies = 'YES' if len(set(seen)) > 1 else 'no'
            val_cols = [str(v) if v is not None else '' for v in vals]
            f.write('\t'.join([f'{reg:#06x}'] + val_cols + [str(mn), str(mx), str(mx - mn), varies]) + '\n')

    print(f'\n  Saved → {out.resolve()}')
    print(f'\nTip: focus on VARYING registers – connect a known load (e.g. 500 W bulb)')
    print(f'     between scan runs to make power registers jump more visibly.\n')


if __name__ == '__main__':
    main()
