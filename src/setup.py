import os
import shutil
import sys
from subprocess import run

no_venv = "--no-venv" in sys.argv

venv_dir = os.path.abspath("./tts-venv")

if not no_venv and not os.path.isdir(venv_dir):
    uv_path = shutil.which("uv")
    assert uv_path, "uv not found in PATH"
    run(
        [uv_path, "sync", "--no-config", "--project", "."],
        env={**os.environ, "UV_PROJECT_ENVIRONMENT": venv_dir},
        check=True,
    )

os.makedirs("private", exist_ok=True)

if not os.path.isfile("private/config.yaml"):
    shutil.copyfile("templates/config.yaml.example", "private/config.yaml")

if not os.path.isfile("private/mod_blocklist.txt"):
    shutil.copyfile("templates/mod_blocklist.txt.example", "private/mod_blocklist.txt")

if not os.path.isfile("private/secrets.yaml"):
    shutil.copyfile("templates/secrets.yaml.example", "private/secrets.yaml")
