from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from audio import AUDIO_FORMATS
from mod import CENSOR_MODES


class Body(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, strict=True)


class VoiceSettings(Body):
    voice: str | None = None
    profile: str | None = None
    preset: str | None = None
    fx: str | None = None
    length_scale: float | None = None
    noise_scale: float | None = None
    noise_w: float | None = None
    speaker_id: int | None = None
    normalize: bool | None = None


class Part(VoiceSettings):
    text: str = ""
    sfx: str | None = None


class SynthReq(VoiceSettings):
    text: str = ""
    parts: list[Part] | None = Field(None, min_length=1)
    format: Literal[*AUDIO_FORMATS] | None = None
    bitrate: str | None = None


class VoiceAliasReq(Body):
    voice: str = Field(min_length=1)


class SfxAliasReq(Body):
    target_id: str = Field(min_length=1)


class ProfileReq(VoiceSettings):
    chain: list[dict] | dict | None = None


class ModReq(Body):
    reload: bool = False
    censoring: bool | None = None
    mode: Literal[*CENSOR_MODES] | None = None
    add: list[str] = []
    remove: list[str] = []
