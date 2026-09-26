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

_cfg = {}
_default_board = None
_voice_boards = {}
_named_boards = {}
_named_chains = {}


def init(cfg):
    global _cfg, _default_board, _voice_boards, _named_boards, _named_chains

    _cfg = cfg.get("voice_fx") or {}
    _default_board = None
    _voice_boards = {}
    _named_boards = {}
    _named_chains = {}

    if not _cfg.get("enabled"):
        logger.info("[voice_fx] disabled")
        return

    _default_board = _build(_cfg.get("chain") or [])

    for vid, sub in (_cfg.get("per_voice") or {}).items():
        _voice_boards[vid] = _build(sub.get("chain") or [])

    for name, chain in (_cfg.get("chains") or {}).items():
        set_chain(name, chain)

    logger.info(
        f"[voice_fx] enabled; default_stages={len(_default_board or [])} "
        f"per_voice={list(_voice_boards.keys())} chains={list(_named_boards.keys())}"
    )


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


def set_chain(name, chain_cfg):
    """Register a named chain. Overwrites one of the same name."""
    name = (name or "").strip().lower()

    if not name:
        return None

    stages = _stages_of(chain_cfg)
    _named_chains[name] = stages
    _named_boards[name] = _build(stages)

    return name


def del_chain(name):
    name = (name or "").strip().lower()
    _named_chains.pop(name, None)
    _named_boards.pop(name, None)


def chains():
    return dict(_named_chains)


def enabled():
    return bool(_cfg.get("enabled")) and _default_board is not None


def process_wav(in_path, voice_id=None, fx=None):
    """Apply effects chain. Returns new wav path, or in_path on no-op/error."""
    if not enabled():
        return in_path

    fx = (fx or "").strip().lower()
    board = _named_boards.get(fx) if fx else None

    if board is None:
        board = _voice_boards.get(voice_id, _default_board)

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
