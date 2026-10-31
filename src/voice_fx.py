"""Post-synth voice effects chain (pedalboard)."""

import contextlib
import os
import tempfile

from echo_common import logger
from pedalboard import (
    Bitcrush,
    Chorus,
    Compressor,
    Delay,
    Distortion,
    Gain,
    HighpassFilter,
    LowpassFilter,
    Pedalboard,
    PitchShift,
    Reverb,
)
from pedalboard.io import AudioFile


def _build(chain_cfg):
    kinds = {
        "pitch_shift": PitchShift,
        "bitcrush": Bitcrush,
        "chorus": Chorus,
        "reverb": Reverb,
        "gain": Gain,
        "highpass": HighpassFilter,
        "lowpass": LowpassFilter,
        "distortion": Distortion,
        "delay": Delay,
        "compressor": Compressor,
    }
    stages = []

    for s in chain_cfg:
        t = (s.get("type") or "").lower()
        cls = kinds.get(t)

        if not cls:
            logger.warning(f"[voice_fx] unknown stage type: {t}")
            continue

        params = {k: v for k, v in s.items() if k != "type"}

        try:
            stages.append(cls(**params))
        except Exception as e:
            logger.warning(f"[voice_fx] bad params for {t}: {e}")

    return Pedalboard(stages)


def _stages_of(chain_cfg):
    if isinstance(chain_cfg, dict):
        return chain_cfg.get("chain") or []

    return chain_cfg or []


class VoiceFx:
    def __init__(self, cfg=None):
        cfg = cfg or {}
        self.on = bool(cfg.get("enabled"))
        self.default_board = None
        self.voice_boards = {}
        self.named_boards = {}

        if not self.on:
            logger.info("[voice_fx] disabled")
            return

        self.default_board = _build(cfg.get("chain") or [])

        for vid, sub in (cfg.get("per_voice") or {}).items():
            self.voice_boards[vid] = _build(sub.get("chain") or [])

        for name, chain in (cfg.get("chains") or {}).items():
            self.set_chain(name, chain)

        logger.info(
            f"[voice_fx] enabled; default_stages={len(self.default_board)} "
            f"per_voice={list(self.voice_boards)} chains={list(self.named_boards)}"
        )

    def set_chain(self, name, chain_cfg):
        """Register a named chain. Overwrites one of the same name."""
        name = (name or "").strip().lower()

        if not name:
            return None

        self.named_boards[name] = _build(_stages_of(chain_cfg))

        return name

    def del_chain(self, name):
        name = (name or "").strip().lower()
        self.named_boards.pop(name, None)

    def process_wav(self, in_path, voice_id=None, fx=None):
        """Apply effects chain. Returns new wav path, or in_path on no-op/error."""
        if not self.on:
            return in_path

        fx = (fx or "").strip().lower()
        board = self.named_boards.get(fx) if fx else None

        if board is None:
            board = self.voice_boards.get(voice_id, self.default_board)

        if board is None or len(board) == 0:
            return in_path

        fd, out_path = tempfile.mkstemp(suffix=".fx.wav")
        os.close(fd)

        try:
            with AudioFile(in_path) as f:
                sr = f.samplerate
                audio = f.read(f.frames)

            processed = board(audio, sr)

            with AudioFile(out_path, "w", sr, processed.shape[0]) as fo:
                fo.write(processed)

            return out_path
        except Exception as e:
            logger.warning(f"[voice_fx] process failed: {e}")

            with contextlib.suppress(Exception):
                os.remove(out_path)

            return in_path
