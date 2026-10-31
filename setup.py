import os
import shutil
import sys
from subprocess import run

no_venv = "--no-venv" in sys.argv

root = os.path.dirname(os.path.abspath(__file__))
venv_dir = os.path.join(root, "tts-venv")
private = os.path.join(root, "config", "private")
templates = os.path.join(root, "config", "templates")

if not no_venv and not os.path.isdir(venv_dir):
    uv_path = shutil.which("uv")
    assert uv_path, "uv not found in PATH"
    run(
        [uv_path, "sync", "--no-config", "--project", root],
        env={**os.environ, "UV_PROJECT_ENVIRONMENT": venv_dir},
        check=True,
    )

os.makedirs(private, exist_ok=True)

for name in ("config.yaml", "mod_blocklist.txt"):
    if not os.path.isfile(os.path.join(private, name)):
        shutil.copyfile(
            os.path.join(templates, f"{name}.example"), os.path.join(private, name)
        )
