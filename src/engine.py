import shutil
import uuid

from cachetools import TTLCache
from echo_common import resolve_path

import audio
import mod
import sfx
from resolve import BadRequest, Resolver
from voice_fx import VoiceFx
from voices import Voices

# what a batch part may set for itself, falling back to the top level body
PART_KEYS = (
    "voice",
    "profile",
    "preset",
    "fx",
    "length_scale",
    "noise_scale",
    "noise_w",
    "speaker_id",
    "normalize",
)


def _headers(mime, name):
    ext = "mp3" if mime == "audio/mpeg" else "wav"
    return {
        "Content-Disposition": (
            f'inline; filename="{name}-{uuid.uuid4().hex[:8]}.{ext}"'
        ),
        "Cache-Control": "no-store",
    }


class Engine:
    """Everything a request needs to become audio, built once from the config."""

    def __init__(self, cfg, base_dir=None):
        cfg = dict(cfg)

        for k in ("voices_dir", "sounds_dir"):
            if cfg.get(k) and base_dir:
                cfg[k] = resolve_path(cfg[k], base_dir)

        self.cfg = cfg
        self.ffmpeg = audio.Ffmpeg(shutil.which(cfg.get("ffmpeg_bin", "ffmpeg")))
        self.fx = VoiceFx(cfg.get("voice_fx"))
        self.moderator = mod.build_moderator(cfg, base_dir=base_dir)
        self.sounds = sfx.Sounds(cfg.get("sounds_dir"), cfg.get("sfx_aliases"))
        self.voices = Voices(cfg.get("voices_dir"), int(cfg.get("max_concurrency", 2)))
        self.resolver = Resolver(cfg, self.voices, self.fx, self.moderator)
        self.cache = TTLCache(
            maxsize=int(cfg.get("cache_size", 64)), ttl=int(cfg.get("cache_ttl_s", 300))
        )

    def reload(self):
        self.voices.reload()
        # cfg is the startup copy so reseeding aliases from it would drop panel edits
        self.sounds.rescan()

        if self.moderator:
            self.moderator.censor.reload()

    def health(self):
        all_voices = self.voices.all()
        return {
            "ok": True,
            "backends": sorted({v["backend"] for v in all_voices}),
            "piper_loaded": self.voices.piper.loaded(),
            "ffmpeg": self.ffmpeg.path,
            "voices": len(all_voices),
            "max_concurrency": self.voices.max_concurrency,
            "cache": {
                "items": len(self.cache),
                "capacity": self.cache.maxsize,
                "ttl_sec": self.cache.ttl,
            },
        }

    def _render_wav(self, text, params):
        """Synth then fx then normalize, handing back the wav and every temp file."""
        rm = [audio.temp_path(".wav")]

        try:
            self.voices.synth(text, params, rm[0])
            fx = self.fx.process_wav(rm[0], voice_id=params.voice_id, fx=params.fx)
            src = self.ffmpeg.normalize(fx) if params.normalize else fx
        except Exception:
            audio.rm(rm[0])
            raise

        rm += {fx, src} - {rm[0]}
        return src, rm

    def tts(self, d):
        clean, params = self.resolver.resolve(d)

        if sfx.has_sfx_tags(clean):
            return self._tts_with_sfx(clean, params)

        key = (params, clean)
        hit = self.cache.get(key)

        if not hit:
            src, rm = self._render_wav(clean, params)

            try:
                hit = self.ffmpeg.encode(src, params.fmt, params.bitrate)
            finally:
                for p in rm:
                    audio.rm(p)

            if len(hit[0]) <= audio.WAV_HEADER_BYTES:
                raise RuntimeError("empty audio")

            self.cache[key] = hit

        b, m = hit
        return b, m, _headers(m, params.voice_id)

    def render_parts(self, parts, params, rm, sfx_count=0):
        """Turn parsed sfx and text parts into mono wav segments.

        The caller owns rm so a partial failure still cleans up temp files.
        """
        max_sfx = int(self.cfg.get("max_sfx_per_request", 10))
        segs = []

        for p in parts:
            if "sfx" in p:
                if sfx_count >= max_sfx:
                    continue

                ap = self.sounds.resolve(p["sfx"])

                if not ap:
                    continue

                # quiet the censor beeps relative to speech
                gain = 0

                if p["sfx"].startswith("censor-beep"):
                    gain = self.cfg.get("moderation", {}).get("censor_gain_db", 0)

                wav48 = self.ffmpeg.to_mono_wav(ap, gain_db=gain)
                sfx_count += 1

            else:
                txt = (p.get("text") or "").strip()

                if not txt:
                    continue

                ap, tmp = self._render_wav(txt, params)
                rm += tmp
                wav48 = self.ffmpeg.to_mono_wav(ap, trim=True)

            segs.append(wav48)

            if wav48 != ap:
                rm.append(wav48)

        return segs, sfx_count

    def _tts_with_sfx(self, clean, params):
        rm = []

        try:
            segs, _ = self.render_parts(sfx.parse_sfx_tags(clean), params, rm)

            if not segs:
                raise RuntimeError("empty audio")

            b, m = self.ffmpeg.concat(segs, params.fmt, params.bitrate)
            return b, m, _headers(m, params.voice_id)

        finally:
            for pth in rm:
                audio.rm(pth)

    def _resolve_part(self, p, defaults):
        """Turn one batch part into render parts and its params."""
        if "sfx" in p:
            return [{"sfx": p.get("sfx")}], None

        d = dict(defaults)
        d.update({k: p[k] for k in PART_KEYS if p.get(k) is not None})
        d["text"] = p.get("text") or ""
        txt, params = self.resolver.resolve(d)
        return sfx.parse_sfx_tags(txt), params

    def batch(self, j):
        # the top level body picks the encoding the same way a single request does
        top = self.resolver.params_for(j)

        # top level settings are the fallback for a part that names none
        defaults = {k: j.get(k) for k in PART_KEYS}

        segs, rm, sfx_count = [], [], 0

        try:
            for p in j["parts"]:
                try:
                    sub, params = self._resolve_part(p, defaults)
                except BadRequest:
                    continue

                got, sfx_count = self.render_parts(sub, params, rm, sfx_count)
                segs += got

            if not segs:
                raise BadRequest("empty parts")

            b, m = self.ffmpeg.concat(segs, top.fmt, top.bitrate)
            return b, m, _headers(m, "batch")
        finally:
            for pth in rm:
                audio.rm(pth)
