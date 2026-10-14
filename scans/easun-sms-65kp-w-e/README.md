# Register Scans – EASUN SMS-6.5KP-W-E

Three unit scan files from empirical Modbus register scanning (FC 0x03, all addresses 0x0000–0x01FF).

| File | Inverter ID | ESPHome name | Notes |
|:-----|:-----------:|:-------------|:------|
| register_map-54.tsv  | inv-54  | verty-mitte  | reference unit |
| register_map-130.tsv | inv-130 | verty-links  | |
| register_map-135.tsv | inv-135 | verty-rechts | same HW/firmware as inv-54 |

## Column format

```
reg  scan1  scan2  scan3  min  max  delta  varies
```

- `reg`: register address (hex)
- `scan1`–`scan3`: three consecutive readings (U_WORD / raw)
- `delta`: max − min across scans
- `varies`: YES if the register changes between scans (live measurement), `no` if constant

## Key registers identified from these scans

See the [USB-A register map section](../../README.md#register-map-6kw-firmware) in the main README
for the validated register map and common mistakes to avoid.
