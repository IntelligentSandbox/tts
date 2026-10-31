# TTS

A FastAPI-based service for local, high-speed neural text-to-speech synthesis. Supports Piper and Kokoro backends.

https://github.com/user-attachments/assets/d545fa6f-5a4f-4dc7-8460-3a5d0ab15ff1

## Installation

```bash
# Install system requirements
sudo apt install ffmpeg

# Install python dependencies
python3 setup.py
source tts-venv/bin/activate
```

## Usage

**Start the service:**

```bash
python src/app.py
```

**Python Example:**

```python
import requests

# Generate speech
response = requests.post(
    "http://localhost:47100/api/tts",
    json={
        "text": "Hello! {airhorn} Welcome.",
        "voice": "en_US-ryan-high"
    }
)

# Save the audio output
with open("speech.mp3", "wb") as f:
    f.write(response.content)
```

## Endpoints

All routes are served under the `/api` prefix.

### Synthesis

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/tts` | Synthesize speech from `text`, or from `parts` to concatenate text and SFX |
| GET | `/api/sounds` | List sound effects and aliases |
| GET | `/sounds/{file}` | Serve a sound file, mounted outside the `/api` prefix |

### Ops

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Backends, loaded voices, ffmpeg and cache stats |

### Catalog

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/catalog` | Voices, aliases, profiles and sounds in one payload |
| POST | `/api/catalog/reload` | Rescan voices and sounds and reread the blocklist |
| PUT | `/api/catalog/{kind}/{name}` | Create or update a voice_alias, profile or sfx_alias |
| DELETE | `/api/catalog/{kind}/{name}` | Delete a voice_alias, profile or sfx_alias |

### Moderation

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/mod` | Moderation terms, mode and censoring |
| POST | `/api/mod` | Set mode or censoring, add or remove terms, reload |

