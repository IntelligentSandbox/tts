import os
import shutil
from venv import create
from subprocess import run

venv_dir = "./tts-venv"
uv_path = shutil.which("uv")
assert uv_path, "uv not found in PATH"

if not os.path.isdir(venv_dir):
    create(venv_dir, with_pip=True)
    run(
        [uv_path, "sync", "--no-config", "--project", ".", "--python", venv_dir],
        check=True,
    )

os.makedirs("private", exist_ok=True)

if not os.path.isfile("private/config.yaml"):
    shutil.copyfile("templates/config.yaml.example", "private/config.yaml")

if not os.path.isfile("private/mod_blocklist.txt"):
    shutil.copyfile("templates/mod_blocklist.txt.example", "private/mod_blocklist.txt")

if not os.path.isfile("private/secrets.yaml"):
    shutil.copyfile("templates/secrets.yaml.example", "private/secrets.yaml")
