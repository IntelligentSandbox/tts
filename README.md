# Piper TTS Service

Piper TTS Service is a FastAPI-based library for local, high-speed neural text-to-speech synthesis.

## Installation

```bash
# Install system requirements
sudo apt install ffmpeg

# Install python dependencies
python3 src/setup.sh
source src/venv/bin/activate
```

## Usage

```python
import requests

# Generate speech with an API key
response = requests.post(
    "http://localhost:8000/api/tts",
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
