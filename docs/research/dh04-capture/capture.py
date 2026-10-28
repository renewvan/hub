"""Read-only capture from a JBD/DH04 BMS: registers 0x03 (general), 0x04 (cell
voltages) and 0x05 (hardware name), a few samples apart. Sends read commands
only. The live driver must be stopped first: the BMS accepts one BLE client.

usage: python dh04_capture.py <mac> [samples=3] [interval_s=2]
Prints one JSON object per line.
"""

import asyncio
import json
import struct
import sys
import time

from bleak import BleakClient, BleakScanner

TX_RX = [
    ("dh04", "0000fff2-0000-1000-8000-00805f9b34fb", "0000fff1-0000-1000-8000-00805f9b34fb"),
    ("jbd", "0000ff02-0000-1000-8000-00805f9b34fb", "0000ff01-0000-1000-8000-00805f9b34fb"),
]
KELVIN = 273.15


def read_command(reg: int) -> bytes:
    body = bytes([reg, 0x00])
    return b"\xdd\xa5" + body + struct.pack(">H", (0x10000 - sum(body)) % 0x10000) + b"\x77"


def first_frame(data: bytes) -> bytes | None:
    start = data.find(b"\xdd")
    if start == -1 or len(data) - start < 7:
        return None
    length = struct.unpack_from(">H", data, start + 2)[0]
    end = start + length + 7
    return data[start:end] if len(data) >= end else None


def decode_general(payload: bytes) -> dict:
    f = struct.unpack_from(">HhHHHHhHHBBBBB", payload)
    names = (
        "voltage",
        "current",
        "remaining",
        "capacity",
        "cycles",
        "production",
        "balance_lo",
        "balance_hi",
        "protection",
        "version",
        "soc",
        "fet",
        "cell_count",
        "temp_count",
    )
    out = dict(zip(names, f))
    out["balance_lo_bits"] = format(out["balance_lo"], "016b")
    out["balance_hi_bits"] = format(out["balance_hi"], "016b")
    out["protection_bits"] = format(out["protection"], "016b")
    out["fet_bits"] = format(out["fet"], "08b")
    out["temps_c"] = [
        round(struct.unpack_from(">H", payload, 23 + 2 * t)[0] / 10 - KELVIN, 2)
        for t in range(out["temp_count"])
        if 23 + 2 * t + 2 <= len(payload)
    ]
    out["payload_len"] = len(payload)
    return out


async def main(mac: str, samples: int, interval: float) -> None:
    loop = asyncio.get_running_loop()
    client = BleakClient(mac, timeout=15)
    try:
        await client.connect()
    except Exception as exc:
        print(json.dumps({"note": f"direct connect failed: {exc}; scanning"}), flush=True)
        device = await BleakScanner.find_device_by_address(mac, timeout=15)
        if device is None:
            raise SystemExit(f"{mac} not found")
        client = BleakClient(device, timeout=15)
        await client.connect()
    chars = {str(c.uuid) for s in client.services for c in s.characteristics}
    variant, tx, rx = next(v for v in TX_RX if v[1] in chars and v[2] in chars)
    print(json.dumps({"variant": variant, "characteristics": sorted(chars)}), flush=True)

    buf = bytearray()
    waiter: asyncio.Future | None = None

    def on_notify(_sender, data: bytearray) -> None:
        buf.extend(data)
        frame = first_frame(bytes(buf))
        if frame is not None and waiter is not None and not waiter.done():
            waiter.set_result(frame)

    await client.start_notify(rx, on_notify)
    await asyncio.sleep(0.3)

    async def request(reg: int) -> dict:
        nonlocal waiter
        buf.clear()
        waiter = loop.create_future()
        await client.write_gatt_char(tx, read_command(reg), response=False)
        try:
            frame = await asyncio.wait_for(waiter, 5)
        except TimeoutError:
            return {"reg": reg, "error": "timeout", "raw": bytes(buf).hex()}
        length = struct.unpack_from(">H", frame, 2)[0]
        payload = frame[4 : 4 + length]
        rec = {"reg": reg, "frame": frame.hex(), "status_byte": frame[2], "payload": payload.hex()}
        if frame[1] & 0x80 or frame[2] != 0x00:
            rec["note"] = "possible error reply"
        elif reg == 0x03 and len(payload) >= 23:
            rec["general"] = decode_general(payload)
        elif reg == 0x04:
            count = len(payload) // 2
            rec["cells_mv"] = list(struct.unpack(f">{count}H", payload[: count * 2]))
        elif reg == 0x05:
            rec["text"] = payload.decode("ascii", "replace")
        return rec

    try:
        for i in range(samples):
            for reg in (0x03, 0x04, 0x05):
                rec = await request(reg)
                rec.update(sample=i, t=round(time.time(), 1))
                print(json.dumps(rec), flush=True)
                await asyncio.sleep(0.4)
            if i + 1 < samples:
                await asyncio.sleep(interval)
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(
        main(
            sys.argv[1],
            int(sys.argv[2]) if len(sys.argv) > 2 else 3,
            float(sys.argv[3]) if len(sys.argv) > 3 else 2.0,
        )
    )
