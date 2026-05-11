"""Post-synth voice effects chain (pedalboard)."""

import os
import tempfile
from log import logger

_cfg = {}
_default_board = None
_voice_boards = {}
_available = None


def init(cfg):
    global _cfg, _default_board, _voice_boards, _available
    _cfg = cfg.get("voice_fx") or {}
    _default_board = None
    _voice_boards = {}

    if not _cfg.get("enabled"):
        logger.info("[voice_fx] disabled")
        return

    try:
        import pedalboard  # noqa: F401

        _available = True
    except ImportError:
        _available = False
        logger.warning("[voice_fx] pedalboard not installed; effects disabled")
        return

    _default_board = _build(_cfg.get("chain") or [])
    for vid, sub in (_cfg.get("per_voice") or {}).items():
        _voice_boards[vid] = _build(sub.get("chain") or [])
    logger.info(
        f"[voice_fx] enabled; default_stages={len(_default_board or [])} "
        f"per_voice={list(_voice_boards.keys())}"
    )


def _build(chain_cfg):
    from pedalboard import (
        Pedalboard,
        PitchShift,
        Bitcrush,
        Chorus,
        Reverb,
        Gain,
        HighpassFilter,
        LowpassFilter,
        Distortion,
        Delay,
        Compressor,
    )

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


def enabled():
    return bool(_cfg.get("enabled")) and _available and _default_board is not None


def process_wav(in_path, voice_id=None):
    """Apply effects chain. Returns new wav path, or in_path on no-op/error."""
    if not enabled():
        return in_path

    board = _voice_boards.get(voice_id, _default_board)
    if board is None or len(board) == 0:
        return in_path

    out = tempfile.NamedTemporaryFile(suffix=".fx.wav", delete=False)
    out.close()
    try:
        from pedalboard.io import AudioFile

        with AudioFile(in_path) as f:
            sr = f.samplerate
            audio = f.read(f.frames)
        processed = board(audio, sr)
        with AudioFile(out.name, "w", sr, processed.shape[0]) as fo:
            fo.write(processed)
        return out.name
    except Exception as e:
        logger.warning(f"[voice_fx] process failed: {e}")
        try:
            os.remove(out.name)
        except Exception:
            pass
        return in_path
