import glob
import json
import os
import threading
import wave
from pathlib import Path

from echo_common import logger

import audio
from resolve import SynthParams

DEFAULT_VOICES = os.path.join(os.path.dirname(__file__), "..", "voices")

KOKORO_REPO = "hexgrad/Kokoro-82M"
KOKORO_LANGS = {
    "a": "en-us",
    "b": "en-gb",
    "e": "es",
    "f": "fr-fr",
    "h": "hi",
    "i": "it",
    "j": "ja",
    "p": "pt-br",
    "z": "zh",
}
KOKORO_SR = 24000
KOKORO_WEIGHTS = "kokoro-v1_0.pth"


class Piper:
    def __init__(self):
        self._voices = {}
        self._lock = threading.Lock()

    def loaded(self):
        return sorted(self._voices)

    def _voice(self, info):
        vid = info["id"]

        with self._lock:
            v = self._voices.get(vid)

            if v is None:
                from piper import PiperVoice

                v = PiperVoice.load(info["model_path"], info["config_path"])
                self._voices[vid] = v

            return v

    def synth(self, info, txt, params, out_path):
        from piper import SynthesisConfig

        voice = self._voice(info)
        sc = SynthesisConfig(
            speaker_id=params.speaker_id,
            length_scale=params.length_scale,
            noise_scale=params.noise_scale,
            noise_w_scale=params.noise_w,
        )

        with wave.open(out_path, "wb") as wf:
            wf.setframerate(voice.config.sample_rate)
            wf.setsampwidth(2)
            wf.setnchannels(1)
            voice.synthesize_wav(txt, wf, sc, set_wav_format=False)

    @staticmethod
    def scan(base):
        v = {}
        p = (
            os.path.join(base, "piper")
            if os.path.isdir(os.path.join(base, "piper"))
            else base
        )

        for j in glob.glob(os.path.join(p, "**", "*.onnx.json"), recursive=True):
            m = j[:-5]
            if not os.path.exists(m):
                continue

            i = os.path.splitext(os.path.basename(m))[0]
            try:
                meta = json.loads(Path(j).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                meta = {}

            v[i] = {
                "id": i,
                "backend": "piper",
                "model_path": m,
                "config_path": j,
                "sample_rate": meta.get(
                    "sample_rate", meta.get("audio", {}).get("sample_rate", 22050)
                ),
                "speakers": len(meta.get("speakers", [0])),
                "language": meta.get(
                    "language", meta.get("espeak", {}).get("voice", "")
                ),
            }

        return v


class Kokoro:
    def __init__(self, base):
        self.dir = os.path.join(base, "kokoro")
        self._model = None
        self._pipelines = {}
        self._lock = threading.Lock()

    def scan(self):
        v = {}

        for f in glob.glob(os.path.join(self.dir, "voices", "*.pt")):
            name = os.path.basename(f)[:-3]

            if name[0] not in KOKORO_LANGS:
                continue

            v[name] = {
                "id": name,
                "backend": "kokoro",
                "model_path": f,
                "config_path": None,
                "sample_rate": KOKORO_SR,
                "speakers": 1,
                "language": KOKORO_LANGS[name[0]],
            }

        return v

    def _pipeline(self, lang):
        with self._lock:
            p = self._pipelines.get(lang)

            if p is None:
                import torch
                from kokoro import KModel, KPipeline

                # every language shares one model so it only sits in memory once
                if self._model is None:
                    device = "cuda" if torch.cuda.is_available() else "cpu"
                    self._model = (
                        KModel(
                            repo_id=KOKORO_REPO,
                            config=os.path.join(self.dir, "config.json"),
                            model=os.path.join(self.dir, KOKORO_WEIGHTS),
                        )
                        .to(device)
                        .eval()
                    )

                p = KPipeline(lang_code=lang, repo_id=KOKORO_REPO, model=self._model)
                self._pipelines[lang] = p

            return p

    def synth(self, info, txt, params, out_path):
        import numpy as np
        import soundfile as sf

        pipe = self._pipeline(info["id"][0])
        ls = params.length_scale
        speed = 1.0 / float(ls) if ls else 1.0
        chunks = []

        for _, _, a in pipe(txt, voice=info["model_path"], speed=speed):
            if a is None:
                continue

            chunks.append(
                a.detach().cpu().numpy() if hasattr(a, "detach") else np.asarray(a)
            )

        # Kokoro yields nothing for text it can't say so that's a silent clip
        full = np.concatenate(chunks or [np.zeros(0)]).astype(np.float32)
        sf.write(out_path, full, KOKORO_SR, subtype="PCM_16")


class Voices:
    """Every voice both backends can speak, with a cap on how many synth at once."""

    def __init__(self, voices_dir=None, max_concurrency=2):
        self.dir = voices_dir or DEFAULT_VOICES
        self.max_concurrency = max_concurrency
        self.piper = Piper()
        self.kokoro = Kokoro(self.dir)
        self._sem = threading.Semaphore(max_concurrency)
        self._by_id = None

    def _scanned(self):
        if self._by_id is None:
            self._by_id = {**self.kokoro.scan(), **Piper.scan(self.dir)}

        return self._by_id

    def all(self):
        v = self._scanned()
        return [v[k] for k in sorted(v)]

    def get(self, vid):
        return self._scanned().get(vid)

    def default_id(self):
        return self.all()[0]["id"]

    def reload(self):
        self._by_id = None
        self.all()

    def synth(self, text, params, out_path):
        info = self._scanned()[params.voice_id]
        backend = self.kokoro if info["backend"] == "kokoro" else self.piper

        with self._sem:
            backend.synth(info, text, params, out_path)

    def warmup(self):
        """Preload the first piper voice so the first request is hot."""
        piper = sorted(
            (v for v in self._scanned().values() if v["backend"] == "piper"),
            key=lambda x: x["id"],
        )

        if not piper:
            return

        info = piper[0]
        of_path = audio.temp_path(".wav")

        try:
            self.piper.synth(info, "warming up", SynthParams(info["id"]), of_path)
            logger.info(f"[warmup] piper voice ready: {info['id']}")
        except Exception as e:
            logger.warning(f"[warmup] failed: {e}")
        finally:
            audio.rm(of_path)
