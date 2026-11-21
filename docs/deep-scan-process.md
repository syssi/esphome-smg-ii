# Deep Register Scan Process

This document describes how to discover and validate Modbus register mappings
for ISolar/EASUN SMG II inverters using the TCP bridge + `deep_scan.py` tool.

Use this process when:
- Your device firmware differs from the standard decimal-201+ register map
- Sensors report implausible values (zero, constant, or wildly wrong)
- You want to validate scaling factors against known physics

---

## Overview

```
 Inverter RS232                ESP32-C3                  PC / Mac
 ─────────────    USB-A COM    ────────────   Wi-Fi TCP   ────────────────
 Modbus RTU   ←─────────────→  TCP bridge  ←───────────→  deep_scan.py
                               port 8888                  register_map.tsv
```

The ESP32-C3 running `tools/deep-scan-esp32-c3.yaml` acts as a transparent
TCP-to-serial bridge. `deep_scan.py` sends raw Modbus FC 0x03 frames through
the bridge and records every register that returns a non-zero value across
multiple scan passes.

---

## Step 1 — Flash the bridge firmware

```bash
esphome run tools/deep-scan-esp32-c3.yaml --device <ip-or-hostname>
```

Adjust `tx_pin` / `rx_pin` in the YAML if your wiring differs from the default
(GPIO6 / GPIO5 for the USB-A COM port variant).

The bridge sends the proprietary wakeup frame `01 AA 06 DE A2` at boot and
keeps the UART transparent on TCP port 8888.

---

## Step 2 — Run the scan

```bash
# recommended: 3 passes, 30 s apart (captures live variation)
python3 tools/deep_scan.py --host <ip-or-hostname> --output scans/mydevice/register_map.tsv

# more passes for noisy measurements:
python3 tools/deep_scan.py --host <ip-or-hostname> --scans 5 --interval 60 --output scans/mydevice/register_map.tsv
```

The script scans address ranges `0x0000–0x0200` (and some vendor-specific pages)
in blocks of 10 registers. It runs the full sweep N times with a configurable
pause between passes.

### TSV output columns

| Column   | Meaning |
|:---------|:--------|
| `reg`    | Register address (hex) |
| `scan1`–`scanN` | Raw U_WORD value from each pass |
| `min`    | Minimum value across all passes |
| `max`    | Maximum value across all passes |
| `delta`  | `max − min` |
| `varies` | `YES` if delta > 0 — **these are the live measurements** |

---

## Step 3 — Identify live measurements

Focus on registers marked `varies: YES`. Constant registers are either
configuration values, rated/nominal parameters, or unused.

**Quick heuristics from the terminal output:**

| Raw value range | Likely meaning |
|:----------------|:---------------|
| 2000–2600       | Voltage × 0.1 (200–260 V AC) |
| 4900–5100       | Frequency × 0.01 (49–51 Hz) |
| 450–600         | Battery voltage × 0.1 (45–60 V) |
| 10–90           | Temperature (°C) |
| 60000–65535     | Negative S_WORD (e.g. 65475 → −61 as signed int16) |

---

## Step 4 — Validate with the energy balance

Once you have candidates for battery current, battery voltage, and output power,
cross-check them using the inverter energy equation:

```
P_out ≈ I_bat × V_bat × η_inv
```

Typical inverter efficiency: **η ≈ 0.88–0.95** depending on load.

**Example (EASUN SMS-6.5KP-W-E, inv-54):**

```
0x0156 raw = 65475  →  S_WORD: 65475 − 65536 = −61  →  × 0.1 = −6.1 A discharge
0x0155 raw = 531    →  × 0.1 = 53.1 V
0x0085 raw = 300    →  300 W (no scaling needed)

Check:  6.1 A × 53.1 V × 0.92 ≈ 298 W  ≈  300 W  ✓
```

If the balance doesn't hold, adjust the scaling factor and re-check:

| Register reads | Applied scale | Result | Energy balance |
|:---------------|:-------------|:-------|:---------------|
| 300 (output)   | × 1          | 300 W  | 6.1 × 53.1 × 0.92 = 298 W ✓ |
| 300 (output)   | × 10         | 3000 W | 6.1 × 53.1 × 0.92 ≠ 3000 W ✗ |
| 300 (output)   | × 0.1        | 30 W   | 6.1 × 53.1 × 0.92 ≠ 30 W ✗  |

Use a real-time battery voltage from a BMS or trusted sensor as the ground
truth for V_bat — the inverter's own battery voltage register may use a
different scale or address than expected (see known mistakes below).

### Cross-check with varying load

To make power registers jump visibly between scan passes, switch a known
resistive load on and off between scans (e.g. a 500 W space heater or kettle).
The register that changes by the expected wattage is the output power register.

---

## Step 5 — Restore production firmware

```bash
esphome run <your-device>.yaml --device <ip-or-hostname>
```

---

## Known register mapping mistakes (EASUN SMS-6.5KP-W-E firmware)

| Wrong register | Problem | Correct register |
|:--------------:|:--------|:----------------:|
| `0x005B`       | Output power — zero below ~200 W load threshold | `0x0085` |
| `0x0159`       | Constant 480 = nominal system voltage (48.0 V), **not** measured | `0x0155` |
| `0x015D`       | Constant ~−15 = configuration value, **not** battery current | `0x0156` |
| `0x0091`       | Does not exist in this firmware — always 0 (PV power) | N/A |
| `0x0090`       | Does not exist in this firmware — always 0 (PV current) | N/A |

The standard community register map (decimal addresses 201+) does **not**
respond on this firmware variant. Use the addresses in `scans/easun-sms-65kp-w-e/`
as a reference.

---

## Scan result files

Scan TSV files live under `scans/<device-model>/`:

```
scans/
└── easun-sms-65kp-w-e/
    ├── README.md
    ├── register_map-54.tsv   # inv-54  (verty-mitte) — reference unit
    ├── register_map-130.tsv  # inv-130 (verty-links)
    └── register_map-135.tsv  # inv-135 (verty-rechts)
```

If you scan a different device, save results under `scans/<your-model>/` and
open a PR — additional data points help the community.
