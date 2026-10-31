import contextlib
import os
import subprocess
import tempfile
from pathlib import Path

WAV_HEADER_BYTES = 44
AUDIO_FORMATS = ("mp3", "wav")

# trim leading and trailing silence so segments sit flush
SILENCE_TRIM = (
    "silenceremove=start_periods=1:start_threshold=-50dB,"
    "areverse,"
    "silenceremove=start_periods=1:start_threshold=-50dB,"
    "areverse"
)


def temp_path(suffix):
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    return path


def rm(p):
    with contextlib.suppress(Exception):
        os.remove(p)


class Ffmpeg:
    """The ffmpeg steps around a synth, each one a no op when ffmpeg is missing."""

    def __init__(self, path):
        self.path = path

    def _run(self, *args):
        cmd = [self.path, "-y", "-loglevel", "error", *args]
        return subprocess.run(cmd, capture_output=True).returncode == 0

    def normalize(self, w):
        if not self.path:
            return w

        n = w + ".norm.wav"
        ok = self._run("-i", w, "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", n)
        return n if ok and os.path.exists(n) else w

    def mp3(self, w, br):
        if not self.path:
            return b""

        m = w + ".mp3"

        if not self._run("-i", w, "-codec:a", "libmp3lame", "-b:a", br, m):
            return b""

        if not os.path.exists(m):
            return b""

        b = Path(m).read_bytes()
        rm(m)
        return b

    def encode(self, wav, fmt, bitrate):
        """Turn a rendered wav into response bytes and fall back to wav if mp3 fails."""
        if fmt == "mp3":
            b = self.mp3(wav, bitrate)

            if b:
                return b, "audio/mpeg"

        elif fmt != "wav":
            raise RuntimeError("bad format")

        return Path(wav).read_bytes(), "audio/wav"

    def to_mono_wav(self, inp, sample_rate=48000, trim=False, gain_db=0):
        if not self.path:
            return inp

        out_path = temp_path(".wav")
        af = []

        if trim:
            af.append(SILENCE_TRIM)

        if gain_db:
            af.append(f"volume={gain_db}dB")

        args = ["-i", inp]

        if af:
            args += ["-af", ",".join(af)]

        args += ["-ac", "1", "-ar", str(sample_rate), "-c:a", "pcm_s16le", out_path]

        return out_path if self._run(*args) and os.path.exists(out_path) else inp

    def concat(self, paths, fmt, bitrate):
        if not self.path:
            raise RuntimeError("ffmpeg not found")

        lst_path = temp_path(".txt")
        merged_wav_path = temp_path(".wav")

        try:
            Path(lst_path).write_text("".join(f"file '{p}'\n" for p in paths))
            args = ["-f", "concat", "-safe", "0", "-i", lst_path, "-c", "copy"]

            if not self._run(*args, merged_wav_path):
                raise RuntimeError("concat failed")

            return self.encode(merged_wav_path, fmt, bitrate)
        finally:
            rm(lst_path)
            rm(merged_wav_path)
