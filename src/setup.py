from venv import create
from os.path import abspath
from subprocess import run
dir = "./tts-venv"
create(dir, with_pip=True)
run(["bin/pip", "install", "-r", abspath("requirements.txt")], cwd=dir)