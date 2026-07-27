import json
import queue
import socket
import struct
import threading
import time

import serial
from serial.tools import list_ports

# =========================
# Configuration
# =========================

ARTNET_PORT = 6454
OUTPUT_FILE = "recording.jsonl"

DMX_UNIVERSE_OUTPUT = 0

# Set this to your Enttec Open DMX USB device.
# Usually /dev/ttyUSB0 on Raspberry Pi.
DMX_DEVICE = "/dev/ttyUSB0"

DMX_BAUDRATE = 250000
DMX_CHANNELS = 512

# DMX512 timing
DMX_BREAK_TIME = 0.0001      # 100 us
DMX_MAB_TIME = 0.000012      # 12 us
DMX_REFRESH_RATE = 44

# =========================
# Globals
# =========================

dmx_buffer = bytearray(DMX_CHANNELS)
dmx_lock = threading.Lock()

record_queue = queue.Queue()

running = True

dmx_serial = None


# =========================
# DMX USB
# =========================

def find_enttec_device():
    """
    Find an FTDI-based Enttec Open DMX USB device.
    The Open DMX USB normally uses FTDI VID 0403 / PID 6001.
    """

    ports = list_ports.comports()

    for port in ports:
        if port.vid == 0x0403 and port.pid == 0x6001:
            print(f"Found Enttec Open DMX USB: {port.device}")
            return port.device

    return None


def open_dmx_device():
    global dmx_serial

    device = DMX_DEVICE

    # Automatically use the FTDI device if the configured
    # device does not exist.
    try:
        test = serial.Serial(device)
        test.close()
    except serial.SerialException:
        detected = find_enttec_device()

        if detected is None:
            raise RuntimeError(
                "Could not find an Enttec Open DMX USB device.\n"
                "Check that it is connected and run:\n"
                "lsusb\n"
                "You should normally see an FTDI device with ID 0403:6001."
            )

        device = detected

    print(f"Opening DMX device: {device}")

    dmx_serial = serial.Serial(
        port=device,
        baudrate=DMX_BAUDRATE,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_TWO,
        timeout=0,
        write_timeout=1
    )

    print("DMX USB device opened")


def send_dmx_frame(data):
    """
    Send one complete DMX512 frame directly through the FTDI interface.

    DMX frame:
        BREAK
        MARK AFTER BREAK
        START CODE
        CHANNEL 1
        CHANNEL 2
        ...
        CHANNEL 512
    """

    if dmx_serial is None:
        return

    # DMX BREAK
    dmx_serial.break_condition = True
    time.sleep(DMX_BREAK_TIME)
    dmx_serial.break_condition = False

    # Mark After Break
    time.sleep(DMX_MAB_TIME)

    # Start code 0x00 + 512 DMX channels
    packet = b"\x00" + bytes(data)

    dmx_serial.write(packet)


def dmx_output_loop():
    """
    Continuously output the current DMX buffer.
    """

    frame_interval = 1.0 / DMX_REFRESH_RATE

    while running:
        frame_start = time.perf_counter()

        with dmx_lock:
            frame = bytes(dmx_buffer)

        try:
            send_dmx_frame(frame)
        except (serial.SerialException, OSError) as e:
            print(f"DMX output error: {e}")
            break

        elapsed = time.perf_counter() - frame_start
        remaining = frame_interval - elapsed

        if remaining > 0:
            time.sleep(remaining)


# =========================
# Recorder
# =========================

def recorder_loop():
    with open(OUTPUT_FILE, "w") as f:
        while running or not record_queue.empty():
            try:
                record = record_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            f.write(json.dumps(record) + "\n")
            f.flush()


# =========================
# Art-Net Receiver
# =========================

def artnet_loop():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    sock.setsockopt(
        socket.SOL_SOCKET,
        socket.SO_REUSEADDR,
        1
    )

    sock.bind(("0.0.0.0", ARTNET_PORT))

    # Allows the thread to notice when running becomes False.
    sock.settimeout(1.0)

    print(f"Listening for Art-Net on UDP port {ARTNET_PORT}...")

    start = time.perf_counter_ns()
    last_frames = {}

    while running:
        try:
            packet, addr = sock.recvfrom(2048)
        except socket.timeout:
            continue
        except OSError:
            break

        # Minimum ArtDMX packet size
        if len(packet) < 18:
            continue

        # Art-Net identifier
        if packet[:8] != b"Art-Net\x00":
            continue

        # Art-Net OpCode
        opcode = struct.unpack("<H", packet[8:10])[0]

        # ArtDMX only
        if opcode != 0x5000:
            continue

        # Art-Net protocol version
        # Bytes 10-11 are protocol version.
        # We don't need to use it here.

        # Sequence / physical
        # Bytes 12-13 are sequence and physical.

        # Universe
        universe = struct.unpack("<H", packet[14:16])[0]

        # DMX data length
        length = struct.unpack(">H", packet[16:18])[0]

        # Protect against malformed packets
        if length > 512:
            length = 512

        if len(packet) < 18 + length:
            continue

        dmx = list(packet[18:18 + length])

        # Pad to 512 channels
        if len(dmx) < DMX_CHANNELS:
            dmx.extend([0] * (DMX_CHANNELS - len(dmx)))

        # Only record changes
        if last_frames.get(universe) == dmx:
            continue

        last_frames[universe] = dmx.copy()

        timestamp = time.perf_counter_ns() - start

        # Output live DMX
        if universe == DMX_UNIVERSE_OUTPUT:
            with dmx_lock:
                dmx_buffer[:] = bytes(dmx)

        # Save recording
        record_queue.put({
            "time_ns": timestamp,
            "universe": universe,
            "data": dmx
        })

        print(
            f"Universe {universe} received "
            f"from {addr[0]}:{addr[1]}"
        )

    sock.close()


# =========================
# Shutdown
# =========================

def shutdown():
    global running

    running = False

    print("Stopping...")

    if dmx_serial is not None:
        try:
            # Stop the DMX break condition if active.
            dmx_serial.break_condition = False
            dmx_serial.close()
        except Exception:
            pass


# =========================
# Main
# =========================

if __name__ == "__main__":
    try:
        open_dmx_device()

        threads = [
            threading.Thread(
                target=artnet_loop,
                daemon=True
            ),
            threading.Thread(
                target=recorder_loop,
                daemon=True
            ),
            threading.Thread(
                target=dmx_output_loop,
                daemon=True
            )
        ]

        for thread in threads:
            thread.start()

        print("Recorder running")
        print(f"Art-Net input: UDP {ARTNET_PORT}")
        print(f"DMX universe: {DMX_UNIVERSE_OUTPUT}")
        print(f"DMX output: {DMX_DEVICE}")
        print("Press Ctrl+C to stop.")

        while running:
            time.sleep(1)

    except KeyboardInterrupt:
        pass

    except Exception as e:
        print(f"Fatal error: {e}")

    finally:
        shutdown()

        for thread in threads if "threads" in locals() else []:
            thread.join(timeout=1)

        print("Stopped.")