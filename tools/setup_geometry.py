"""Install meshing wheels only into the project's ignored virtual environment."""
import json
from pathlib import Path
import subprocess

from find_blender import find_blender

ROOT = Path(__file__).resolve().parents[1]


def main():
    expression = "import sys,json; print('SHOE_PYTHON '+json.dumps({'prefix':sys.prefix,'version':list(sys.version_info[:2])}))"
    completed = subprocess.run([str(find_blender()), "-b", "--factory-startup", "--disable-autoexec", "--python-expr", expression],
                               check=True, text=True, capture_output=True)
    config = json.loads(next(line.removeprefix("SHOE_PYTHON ") for line in completed.stdout.splitlines() if line.startswith("SHOE_PYTHON ")))
    version = ".".join(map(str, config["version"]))
    binary = Path(config["prefix"]) / "bin" / ("python" + version)
    if not binary.is_file():
        raise RuntimeError("未找到 Blender 同版本 Python，请检查安装路径。")
    subprocess.run([str(binary), "-m", "venv", str(ROOT / ".venv")], check=True)
    subprocess.run([str(ROOT / ".venv/bin/python"), "-m", "pip", "install", "-r", str(ROOT / "requirements-geometry.txt")], check=True)


if __name__ == "__main__":
    main()
