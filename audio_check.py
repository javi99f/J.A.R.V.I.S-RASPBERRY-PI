"""List microphones and measure input levels before launching JARVIS."""

import argparse
import math
from array import array

import sounddevice as sd


def main() -> None:
    parser = argparse.ArgumentParser(description="JARVIS microphone diagnostic")
    parser.add_argument("--device", type=int, help="Input device number")
    parser.add_argument("--seconds", type=float, default=8.0)
    args = parser.parse_args()

    print(sd.query_devices())
    print("\nSpeak normally and then say 'Hey Jarvis' from the intended distance.")
    peak_rms = 0.0

    def callback(indata, frames, time_info, status):
        nonlocal peak_rms
        samples = array("h")
        samples.frombytes(indata.tobytes())
        rms = math.sqrt(sum(int(v) * int(v) for v in samples) / max(1, len(samples)))
        peak_rms = max(peak_rms, rms)
        bars = min(50, int(rms / 200))
        print(f"\rRMS {rms:7.0f} |" + "#" * bars + " " * (50 - bars) + "|", end="", flush=True)

    with sd.InputStream(
        device=args.device,
        samplerate=16000,
        channels=1,
        dtype="int16",
        blocksize=1024,
        callback=callback,
    ):
        sd.sleep(int(args.seconds * 1000))

    print(f"\nPeak RMS: {peak_rms:.0f}")
    if peak_rms < 300:
        print("WARNING: input is very quiet; raise microphone gain or move it closer.")
    elif peak_rms > 25000:
        print("WARNING: input may be clipping; reduce microphone gain.")
    else:
        print("Input level looks usable. Fine-tune WAKE_THRESHOLD during room testing.")


if __name__ == "__main__":
    main()

