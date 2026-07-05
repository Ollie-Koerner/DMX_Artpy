import json
import time
import serial

PORT = "/dev/ttyUSB0"
BAUD = 57600

ser = serial.Serial(PORT, BAUD)

START = 0x7E
END = 0xE7
LABEL_DMX = 6


def send_dmx(data):
    if len(data) < 512:
        data += [0] * (512 - len(data))

    payload = bytes([0]) + bytes(data)
    length = len(payload)

    packet = bytes([
        START,
        LABEL_DMX,
        length & 0xff,
        length >> 8
    ]) + payload + bytes([END])

    ser.write(packet)


records = []
with open("recording.jsonl") as f:
    for line in f:
        records.append(json.loads(line))


start = time.perf_counter_ns()
for record in records:
    target = start + record["time_ns"]

    while True:
        now = time.perf_counter_ns()
        remaining = target - now

        if remaining <= 0:
            break

        # Sleep until close
        if remaining > 2_000_000:      # >2 ms
            time.sleep((remaining - 1_000_000) / 1e9)

print("Finished")