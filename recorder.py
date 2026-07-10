import json
import queue
import socket
import struct
import threading
import time

from ola.ClientWrapper import ClientWrapper

# =========================
# Configuration
# =========================

ARTNET_PORT = 6454
OUTPUT_FILE = "recording.jsonl"

DMX_UNIVERSE_OUTPUT = 0

# =========================
# Globals
# =========================

dmx_buffer = bytearray(512)
record_queue = queue.Queue()

running = True

# =========================
# OLA DMX Output
# =========================

wrapper = ClientWrapper()
client = wrapper.Client()


def send_dmx():
    """Send current DMX buffer through Open DMX USB."""
    client.SendDmx(DMX_UNIVERSE_OUTPUT, dmx_buffer, None)


def dmx_output_loop():
    while running:
        send_dmx()

        # DMX refresh rate (~44 Hz)
        time.sleep(1 / 44)


# =========================
# Recorder
# =========================

def recorder_loop():
    with open(OUTPUT_FILE, "w") as f:
        while running:
            record = record_queue.get()

            f.write(json.dumps(record) + "\n")
            f.flush()


# =========================
# Art-Net Receiver
# =========================

def artnet_loop():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", ARTNET_PORT))

    print("Listening for Art-Net...")

    start = time.perf_counter_ns()
    last_frames = {}

    while running:
        packet, addr = sock.recvfrom(1024)

        if packet[:8] != b"Art-Net\x00":
            continue

        opcode = struct.unpack("<H", packet[8:10])[0]

        # ArtDMX only
        if opcode != 0x5000:
            continue

        timestamp = time.perf_counter_ns() - start

        universe = struct.unpack("<H", packet[14:16])[0]
        length = struct.unpack(">H", packet[16:18])[0]

        dmx = list(packet[18:18 + length])

        # Pad to 512 channels
        dmx.extend([0] * (512 - len(dmx)))

        # Only record changes
        if last_frames.get(universe) == dmx:
            continue

        last_frames[universe] = dmx.copy()

        # Output live DMX
        if universe == DMX_UNIVERSE_OUTPUT:
            dmx_buffer[:] = bytes(dmx)

        # Save recording
        record_queue.put({
            "time_ns": timestamp,
            "universe": universe,
            "data": dmx
        })

        print(f"Universe {universe} received")


# =========================
# Main
# =========================

if __name__ == "__main__":
    threads = [
        threading.Thread(target=artnet_loop, daemon=True),
        threading.Thread(target=recorder_loop, daemon=True),
        threading.Thread(target=dmx_output_loop, daemon=True)
    ]

    for thread in threads:
        thread.start()

    print("Recorder running")

    try:
        wrapper.Run()
    except KeyboardInterrupt:
        running = False
        print("Stopping...")