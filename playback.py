import json
import os
import time
import threading

import serial
from serial.tools import list_ports
from mpv import MPV


# =========================
# Configuration
# =========================

DMX_PORT = "/dev/ttyUSB0"

DMX_BAUDRATE = 250000
DMX_CHANNELS = 512

DMX_REFRESH_RATE = 44

DMX_BREAK_TIME = 0.0001
DMX_MAB_TIME = 0.000012


RECORDING_DIRECTORY = "dmx_recordings"
MUSIC_DIRECTORY = "music"
SETTINGS_FILE = "settings.json"


# =========================
# Playback Controller
# =========================

class PlaybackController:
    def __init__(self):
        self.ser = None

        self.running = True

        self.dmx_buffer = bytearray(DMX_CHANNELS)
        self.dmx_lock = threading.Lock()

        self.playback_thread = None
        self.output_thread = None

        self.stop_event = threading.Event()

        self.current_show = None
        self.current_position = 0
        self.playing = False

        self.loaded_records = []

        self.audio_player = MPV(
            video=False,
            audio_display=False,
            cache=False,
            keep_open=True,
            gapless_audio="yes",
            ao="alsa",
            audio_device="alsa/plughw:2,0"
        )


    # =========================
    # DMX Device
    # =========================

    def find_enttec_device(self):
        """
        Find Enttec Open DMX USB device.
        """

        ports = list_ports.comports()
        for port in ports:
            if port.vid == 0x0403 and port.pid == 0x6001:
                print(
                    f"Found Enttec Open DMX USB: {port.device}"
                )
                return port.device

        return None


    def open_dmx_device(self):
        device = DMX_PORT

        try:
            test = serial.Serial(device)
            test.close()

        except serial.SerialException:
            detected = self.find_enttec_device()
            if detected is None:
                raise RuntimeError(
                    "No Enttec Open DMX USB device found."
                )
            device = detected

        print(f"Opening DMX device: {device}")

        self.ser = serial.Serial(
            port=device,
            baudrate=DMX_BAUDRATE,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_TWO,
            timeout=0,
            write_timeout=1
        )

        print("DMX device opened")


    # =========================
    # DMX Output
    # =========================

    def send_dmx(self, data):
        """
        Send one complete DMX frame.
        """

        if len(data) < DMX_CHANNELS:
            data = data + bytes(
                DMX_CHANNELS - len(data)
            )

        elif len(data) > DMX_CHANNELS:
            data = data[:DMX_CHANNELS]

        self.ser.break_condition = True
        time.sleep(DMX_BREAK_TIME)
        self.ser.break_condition = False
        time.sleep(DMX_MAB_TIME)

        packet = b"\x00" + bytes(data)
        self.ser.write(packet)


    def dmx_output_loop(self):
        """
        Continuously retransmit current DMX frame.
        """

        interval = 1 / DMX_REFRESH_RATE

        while self.running:
            start = time.perf_counter()

            with self.dmx_lock:
                frame = bytes(self.dmx_buffer)

            try:
                self.send_dmx(frame)

            except Exception as e:
                print(
                    f"DMX output error: {e}"
                )
                break

            elapsed = time.perf_counter() - start
            remaining = interval - elapsed

            if remaining > 0:
                time.sleep(remaining)


    def start_output(self):
        if self.output_thread is None:
            self.output_thread = threading.Thread(
                target=self.dmx_output_loop,
                daemon=True
            )
            self.output_thread.start()

            print(
                "DMX output thread started"
            )


    # =========================
    # Settings / Files
    # =========================

    def load_settings(self):
        """
        Load music synchronization settings.
        """

        if not os.path.exists(SETTINGS_FILE):
            return {}

        with open(SETTINGS_FILE, "r") as f:
            return json.load(f)


    def load_recording(self, recording_name):
        """
        Load a DMX jsonl recording.
        """

        path = os.path.join(
            RECORDING_DIRECTORY,
            recording_name
        )

        if not os.path.exists(path):
            raise FileNotFoundError(
                f"Recording not found: {path}"
            )

        records = []

        print(
            f"Loading recording: {recording_name}"
        )


        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                record = json.loads(line)

                # Only use universe 0
                if record.get("universe", 0) != 0:
                    continue

                records.append(record)

        print(
            f"Loaded {len(records)} DMX frames"
        )

        return records



    def find_music_file(self, recording_name):
        """
        Find matching music file.

        Uses settings.json first.
        Falls back to matching filename.
        """

        settings = self.load_settings()

        name = os.path.splitext(
            recording_name
        )[0]

        if name in settings:
            music = settings[name].get(
                "music"
            )
            if music:
                path = os.path.join(
                    MUSIC_DIRECTORY,
                    music
                )
                if os.path.exists(path):
                    return path

        possible_extensions = [
            ".mp3",
            ".wav",
            ".ogg",
            ".flac"
        ]


        for ext in possible_extensions:
            path = os.path.join(
                MUSIC_DIRECTORY,
                name + ext
            )

            if os.path.exists(path):
                return path

        return None



    def get_sync_settings(self, recording_name):
        """
        Return synchronization settings
        for a recording.
        """

        settings = self.load_settings()
        name = os.path.splitext(
            recording_name
        )[0]

        return settings.get(
            name,
            {}
        )


    # =========================
    # Audio
    # =========================

    def start_music(self, music_file):
        if music_file is None:
            return
        print("Preparing audio...")
        self.audio_player.play(os.path.abspath(music_file))
        self.audio_player.pause = True
        print("Audio preloaded")

    def resume_music(self):
        if self.audio_player:
            self.audio_player.pause = False
            print("Audio started")

    def stop_music(self):
        if self.audio_player:
            try:
                self.audio_player.stop()
            except Exception:
                pass



    # =========================
    # Playback Preparation
    # =========================

    def prepare_dmx(self, data):
        """
        Normalize DMX data to 512 channels.
        """

        if len(data) < DMX_CHANNELS:
            data = data + [
                0
            ] * (
                DMX_CHANNELS - len(data)
            )

        else:
            data = data[:DMX_CHANNELS]

        return bytes(data)



    def set_dmx_frame(self, data):
        """
        Update current DMX output buffer.
        """

        frame = self.prepare_dmx(data)

        with self.dmx_lock:
            self.dmx_buffer[:] = frame



    def blackout(self):
        """
        Set all DMX channels to 0.
        """

        with self.dmx_lock:
            self.dmx_buffer[:] = bytes(DMX_CHANNELS)

        print("DMX blackout sent")



    def clear_stop(self):
        self.stop_event.clear()


    def request_stop(self):
        self.stop_event.set()


    # =========================
    # DMX Playback
    # =========================

    def playback_loop(
        self,
        recording_name,
        records,
        music_file,
        sync_settings
    ):
        try:
            self.playing = True
            self.current_show = recording_name
            self.current_position = 0

            dmx_offset = sync_settings.get(
                "dmx_offset_ms",
                0
            )

            audio_offset = sync_settings.get(
                "audio_offset_ms",
                0
            )
            

            playback_start = time.perf_counter_ns()

            dmx_start = (
                playback_start
                +
                int(dmx_offset * 1_000_000)
            )

            music_start = (
                playback_start
                +
                int(audio_offset * 1_000_000)
            )


            def wait_until(target):
                while not self.stop_event.is_set():

                    remaining = target - time.perf_counter_ns()

                    if remaining <= 0:
                        break

                    if remaining > 5_000_000:
                        time.sleep(
                            remaining / 1_000_000_000 / 2
                        )

                    else:
                        time.sleep(0.0005)


            if music_file:
                print("Loading music before show")

                self.start_music(
                    music_file
                )

                def music_worker():
                    wait_until(music_start)
                    if not self.stop_event.is_set():
                        self.resume_music()

                threading.Thread(
                    target=music_worker,
                    daemon=True
                ).start()


            for index, record in enumerate(records):

                if self.stop_event.is_set():
                    break


                self.current_position = index


                target = (
                    dmx_start
                    +
                    record["time_ns"]
                )


                wait_until(target)


                if self.stop_event.is_set():
                    break


                self.set_dmx_frame(
                    record["data"]
                )


            print(
                "DMX playback finished"
            )


        except Exception as e:
            print(
                f"Playback error: {e}"
            )


        finally:
            self.playing = False
            self.stop_music()
            self.current_show = None
            self.current_position = 0



    # =========================
    # Public Playback Control
    # =========================

    def play(self, recording_name):
        self.stop()

        recording_path = recording_name
        records = self.load_recording(
            recording_path
        )
        if recording_path != "none":
            music_file = self.find_music_file(
                recording_path
            )
        else:
            music_file = None
        sync_settings = self.get_sync_settings(
            recording_path
        )

        self.clear_stop()

        self.playback_thread = threading.Thread(
            target=self.playback_loop,
            args=(
                recording_path,
                records,
                music_file,
                sync_settings
            ),
            daemon=True
        )

        self.playback_thread.start()
        print(
            f"Started playback: {recording_name}"
        )



    def stop(self):
        self.request_stop()

        if self.playback_thread:
            if (
                self.playback_thread.is_alive()
            ):
                self.playback_thread.join(
                    timeout=2
                )

        self.stop_music()
        self.playback_thread = None
        self.playing = False

        print(
            "Playback stopped"
        )



    # =========================
    # Status
    # =========================

    def get_status(self):
        total_frames = len(
            self.loaded_records
        )

        return {
            "playing": self.playing,
            "show": self.current_show,
            "frame": self.current_position,
            "total_frames": total_frames
        }



    # =========================
    # Shutdown
    # =========================

    def shutdown(self):
        print(
            "Shutting down playback controller"
        )

        self.running = False
        self.stop()

        if self.ser:
            try:
                self.ser.break_condition = False
                self.ser.close()

            except Exception:
                pass


    # =========================
    # Recording Discovery
    # =========================

    def list_recordings(self):
        """
        Return available DMX recordings.
        """

        recordings = []

        if not os.path.exists(RECORDING_DIRECTORY):
            return recordings

        for filename in os.listdir(
            RECORDING_DIRECTORY
        ):

            if not filename.endswith(
                ".jsonl"
            ):
                continue

            music = self.find_music_file(
                filename
            )

            settings = self.get_sync_settings(
                filename
            )

            recordings.append(
                {
                    "name": filename,
                    "titel": settings.get("titel", os.path.splitext(filename)[0]),
                    "description": settings.get("description", ""),
                    "music": (
                        os.path.basename(music)
                        if music
                        else None
                    ),
                    "settings": settings
                }
            )

        recordings.sort(
            key=lambda recording: recording["titel"].lower()
        )

        return recordings



    # =========================
    # Initialization
    # =========================

    def start(self):
        self.open_dmx_device()
        self.start_output()

        print(
            "Playback controller ready"
        )



# =========================
# Standalone Test
# =========================

if __name__ == "__main__":
    controller = PlaybackController()

    try:
        controller.start()
        recordings = controller.list_recordings()
        print(
            "\nAvailable recordings:"
        )

        for index, recording in enumerate(
            recordings
        ):
            print(
                f"{index + 1}. "
                f"{recording['name']}"
            )

        if not recordings:
            print(
                "No recordings found."
            )
            while True:
                time.sleep(1)

        selection = input(
            "\nSelect recording number: "
        )


        try:
            index = int(selection) - 1
            selected = recordings[index]


        except Exception:
            print(
                "Invalid selection"
            )
            controller.shutdown()
            exit()

        controller.play(
            selected["name"]
        )

        while True:
            time.sleep(1)


    except KeyboardInterrupt:
        print(
            "\nStopping..."
        )

    finally:
        controller.shutdown()
        print(
            "Stopped"
        )