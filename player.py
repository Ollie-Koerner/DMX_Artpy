import json
import time
import threading

import serial
from serial.tools import list_ports

# =========================
# Configuration
# =========================

PORT = "/dev/ttyUSB0"

BAUDRATE = 250000
DMX_CHANNELS = 512

DMX_BREAK_TIME = 0.0001
DMX_MAB_TIME = 0.000012
DMX_REFRESH_RATE = 44

RECORDING_FILE = "recording.jsonl"

# =========================
# Globals
# =========================

ser = None

dmx_buffer = bytearray(DMX_CHANNELS)
dmx_lock = threading.Lock()

running = True


# =========================
# DMX Device
# =========================

def find_enttec_device():
    """
    Find an FTDI-based Enttec Open DMX USB device.
    """

    ports = list_ports.comports()

    for port in ports:
        if port.vid == 0x0403 and port.pid == 0x6001:
            print(f"Found Enttec Open DMX USB: {port.device}")
            return port.device

    return None


def open_dmx_device():
    global ser

    device = PORT

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

    ser = serial.Serial(
        port=device,
        baudrate=BAUDRATE,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_TWO,
        timeout=0,
        write_timeout=1
    )

    print("DMX USB device opened")


# =========================
# DMX Output
# =========================

def send_dmx(data):
    """
    Send one complete DMX512 frame.
    """

    if len(data) < DMX_CHANNELS:
        data = data + bytes(DMX_CHANNELS - len(data))
    elif len(data) > DMX_CHANNELS:
        data = data[:DMX_CHANNELS]

    # DMX BREAK
    ser.break_condition = True
    time.sleep(DMX_BREAK_TIME)
    ser.break_condition = False

    # Mark After Break
    time.sleep(DMX_MAB_TIME)

    # Start code + 512 channels
    packet = b"\x00" + bytes(data)

    ser.write(packet)


def dmx_output_loop():
    """
    Continuously transmit the current DMX buffer.

    This is important for Open DMX USB because it does not
    have its own DMX processor. The last frame must therefore
    be retransmitted continuously.
    """

    frame_interval = 1.0 / DMX_REFRESH_RATE

    while running:
        frame_start = time.perf_counter()

        with dmx_lock:
            frame = bytes(dmx_buffer)

        try:
            send_dmx(frame)
        except (serial.SerialException, OSError) as e:
            print(f"\nDMX output error: {e}")
            break

        elapsed = time.perf_counter() - frame_start
        remaining = frame_interval - elapsed

        if remaining > 0:
            time.sleep(remaining)


# =========================
# Load Recording
# =========================

def load_recording():
    records = []

    print(f"Loading {RECORDING_FILE}...")

    with open(RECORDING_FILE, "r") as f:
        for line in f:
            line = line.strip()

            if not line:
                continue

            record = json.loads(line)

            # Only use the configured output universe.
            if record.get("universe", 0) != 0:
                continue

            records.append(record)

    print(f"Loaded {len(records)} DMX frames")

    return records


# =========================
# Playback
# =========================

def playback(records):
    global running

    if not records:
        print("Recording contains no DMX frames.")
        return

    # Start with the first recorded DMX state.
    first_data = records[0]["data"]

    with dmx_lock:
        dmx_buffer[:] = bytes(
            first_data[:DMX_CHANNELS]
            + [0] * max(0, DMX_CHANNELS - len(first_data))
        )

    playback_start = time.perf_counter_ns()

    for index, record in enumerate(records):
        if not running:
            break

        target = playback_start + record["time_ns"]

        # Wait until this frame's recorded timestamp.
        while running:
            now = time.perf_counter_ns()
            remaining = target - now

            if remaining <= 0:
                break

            if remaining > 2_000_000:
                time.sleep((remaining - 1_000_000) / 1e9)

        if not running:
            break

        data = record["data"]

        # Update the DMX buffer.
        # The output thread will immediately continue
        # retransmitting this value until the next change.
        if len(data) < DMX_CHANNELS:
            data = data + [0] * (DMX_CHANNELS - len(data))
        else:
            data = data[:DMX_CHANNELS]

        with dmx_lock:
            dmx_buffer[:] = bytes(data)

        if index % 100 == 0:
            print(
                f"Playing frame {index + 1}/{len(records)}",
                end="\r",
                flush=True
            )

    print()
    print("Playback finished.")

    # Keep the final DMX state active.
    # Do NOT clear dmx_buffer.
    print("Continuing to output final DMX state.")


# =========================
# Main
# =========================

if __name__ == "__main__":
    try:
        open_dmx_device()

        records = load_recording()

        output_thread = threading.Thread(
            target=dmx_output_loop,
            daemon=True
        )

        output_thread.start()

        print("Starting playback...")
        print(f"DMX refresh rate: {DMX_REFRESH_RATE} Hz")

        playback(records)

        # Keep outputting the final DMX state indefinitely.
        # This prevents the Open DMX USB from stopping transmission
        # after the recording ends.
        while running:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\nStopping...")
        running = False

    except Exception as e:
        print(f"Error: {e}")
        running = False

    finally:
        running = False

        if ser is not None:
            try:
                ser.break_condition = False
                ser.close()
            except Exception:
                pass

        print("Stopped.")