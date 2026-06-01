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
        "text": "Hello! [SFX: airhorn] Welcome.",
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
| POST | `/api/tts` | Synthesize speech from a JSON body |
| GET | `/api/tts` | Synthesize speech from query params |
| POST | `/api/tts_batch` | Synthesize and concatenate multiple text and SFX parts |
| GET | `/api/voices` | List available voices |
| POST | `/api/reload` | Reload voices from disk (admin) |
| GET | `/api/healthz` | Health check |
| GET | `/api/metrics` | Synthesis metrics |

### Voice aliases

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/aliases` | List voice aliases (admin) |
| POST | `/api/aliases` | Create or update a voice alias (admin) |
| DELETE | `/api/aliases/{name}` | Delete a voice alias (admin) |

### Sounds

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/sounds` | List sound effects and aliases |
| POST | `/api/sfx_aliases` | Create or update an SFX alias (admin) |
| DELETE | `/api/sfx_aliases/{name}` | Delete an SFX alias (admin) |

### Queue

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/push` | Queue a TTS request |
| GET | `/api/pull` | Pull and remove the next queued item |
| GET | `/api/peek` | View the next queued item without removing it (mod) |
| DELETE | `/api/queue/{qid}` | Remove a queued item by id (mod) |

### Auth & session

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/panel/login` | Log in with a role key |
| POST | `/api/panel/logout` | Clear the session |
| GET | `/api/panel/status` | Get current session roles |
| GET | `/api/auth/login` | Start OAuth login with a provider |
| GET | `/api/auth/callback` | OAuth provider redirect callback |
| GET | `/api/auth/me` | Get the OAuth identity in the session |
| GET | `/api/auth/mappings` | List OAuth-to-role mappings (admin) |
| POST | `/api/auth/mapping` | Map an OAuth account to a role (admin) |
| DELETE | `/api/auth/mapping/{provider}/{remote}` | Delete an OAuth mapping (admin) |

### Overlay

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/overlay` | Serve the overlay page, optionally for an embed |
| POST | `/api/overlay/token` | Mint an overlay access token (admin) |
| GET | `/api/overlay/tokens` | List overlay tokens (admin) |
| DELETE | `/api/overlay/token/{jti}` | Revoke an overlay token (admin) |
| POST | `/api/overlay/embed` | Create an overlay embed (admin) |
| GET | `/api/overlay/embeds` | List overlay embeds (admin) |
| DELETE | `/api/overlay/embed/{embed_id}` | Delete an overlay embed (admin) |

### Moderation

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/mod/mask` | Mask URLs, emojis, and slurs in text |
| GET | `/api/mod/list` | List moderation terms (mod) |
| POST | `/api/mod/add` | Add a moderation term (mod) |
| POST | `/api/mod/remove` | Remove a moderation term (mod) |
| POST | `/api/mod/reload` | Reload moderation terms (mod) |
| GET | `/api/mod/mode` | Get the current censor mode (mod) |
| POST | `/api/mod/mode` | Set the censor mode (mod) |
| GET | `/api/mod/test` | Test moderation on a string (mod) |
