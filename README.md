# TTS

A FastAPI-based service for local, high-speed neural text-to-speech synthesis. Supports Piper and Kokoro backends.

https://github.com/user-attachments/assets/d545fa6f-5a4f-4dc7-8460-3a5d0ab15ff1

## Installation

```bash
# Install system requirements
sudo apt install ffmpeg

# Install python dependencies
python3 src/setup.py
source src/tts-venv/bin/activate
```

## Usage

**Start the service:**

```bash
cd src/
python app.py
```

**Python Example:**

```python
import requests

# Generate speech with an API key
response = requests.post(
    "http://localhost:47100/api/tts",
    headers={"X-API-Key": "secret-key"},
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
| GET | `/api/voices` | List available voices |
| POST | `/api/warmup` | Preload one voice |
| GET | `/api/sounds` | List sound effects and aliases |
| GET | `/sounds/{file}` | Serve a sound file, mounted outside the `/api` prefix |

### Ops

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Health check |
| GET | `/api/metrics` | Synthesis metrics |

### Catalog

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/catalog` | Voices, aliases, profiles and sounds in one payload |
| PUT | `/api/catalog/{kind}/{name}` | Create or update a voice_alias, profile or sfx_alias (admin) |
| DELETE | `/api/catalog/{kind}/{name}` | Delete a voice_alias, profile or sfx_alias (admin) |

### Queue

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/queue` | Queue a TTS request (service key) |
| GET | `/api/queue` | List the queue (mod) |
| GET | `/api/queue/next` | Pull and remove the next item (service key) |
| DELETE | `/api/queue/{id}` | Remove a queued item by id (mod) |

### Auth

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/auth/logout` | Clear the session |
| GET | `/api/auth/me` | Current role and OAuth identity |
| GET | `/api/auth/oauth/{provider}` | Start an OAuth login |
| GET | `/api/auth/oauth/{provider}/callback` | OAuth redirect callback |

### Admin

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/admin/reload` | Reload voices, sounds and moderation terms |
| GET | `/api/admin/mod` | Moderation terms, mode and censoring (mod) |
| POST | `/api/admin/mod` | Set mode or censoring, add or remove terms, reload (mod) |
| GET | `/api/admin/mappings` | List OAuth to role mappings |
| PUT | `/api/admin/mappings/{provider}/{remote}` | Map an OAuth account to a role |
| DELETE | `/api/admin/mappings/{provider}/{remote}` | Delete a mapping |
| GET | `/api/admin/embeds` | List overlay embeds |
| POST | `/api/admin/embeds` | Create an overlay embed and its token |
| DELETE | `/api/admin/embeds/{id}` | Delete an embed and revoke its token |

### Overlay

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/overlay` | Serve the overlay page, optionally for an embed |
