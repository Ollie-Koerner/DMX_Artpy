import socket
import struct
import json
import time

ARTNET_PORT = 6454
OUTPUT_FILE = "recording.jsonl"

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("0.0.0.0", ARTNET_PORT))

print("Listening for Art-Net...")

start = time.perf_counter_ns()

with open(OUTPUT_FILE, "w") as f:
    while True:
        packet, addr = sock.recvfrom(1024)

        if packet[:8] != b"Art-Net\x00":
            continue

        timestamp_ns = time.perf_counter_ns() - start

        opcode = struct.unpack("<H", packet[8:10])[0]

        # ArtDMX
        if opcode != 0x5000:
            continue

        universe = struct.unpack("<H", packet[14:16])[0]
        length = struct.unpack(">H", packet[16:18])[0]
        dmx = list(packet[18:18 + length])

        record = {
            "time_ns": timestamp_ns,
            "universe": universe,
            "data": dmx
        }

        f.write(json.dumps(record) + "\n")
        f.flush()

        print(f"Recorded universe {universe}")