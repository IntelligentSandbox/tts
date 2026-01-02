import os
import shutil
from venv import create
from subprocess import run

venv_dir = "./tts-venv"
pip_path = os.path.join(venv_dir, "bin", "pip")

if not os.path.isdir(venv_dir):
    create(venv_dir, with_pip=True)
    run([pip_path, "install", "-r", os.path.abspath("requirements.txt")], check=True)

os.makedirs("private", exist_ok=True)

if not os.path.isfile("private/config.yaml"):
    shutil.copyfile("templates/config.yaml.example", "private/config.yaml")

if not os.path.isfile("private/mod_blocklist.txt"):
    shutil.copyfile("templates/mod_blocklist.txt.example", "private/mod_blocklist.txt")
