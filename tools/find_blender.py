#!/usr/bin/env python3
"""Find a Blender executable without installing anything or changing preferences."""
import os
from pathlib import Path
import shutil
import sys


def _expand(candidate):
    path = Path(candidate).expanduser()
    if path.suffix.lower() == ".app":
        path = path / "Contents/MacOS/Blender"
    return path


def find_blender():
    override = os.environ.get("BLENDER_PATH")
    if override:
        path = _expand(override)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path.resolve())
        raise FileNotFoundError("BLENDER_PATH 已设置，但不是可执行的 Blender 路径：" + str(path))
    candidates = [shutil.which("blender"), "/Applications/Blender.app/Contents/MacOS/Blender",
                  str(Path.home() / "Applications/Blender.app/Contents/MacOS/Blender"),
                  "/opt/homebrew/bin/blender", "/usr/local/bin/blender", "/usr/bin/blender"]
    for folder in (Path("/Applications"), Path.home() / "Applications"):
        if folder.is_dir():
            candidates.extend(str(p) for p in sorted(folder.glob("Blender*.app")))
    for candidate in candidates:
        if candidate:
            path = _expand(candidate)
            if path.is_file() and os.access(path, os.X_OK):
                return str(path.resolve())
    raise FileNotFoundError("未找到 Blender。请从 https://www.blender.org/download/ 安装，或设置 BLENDER_PATH 为程序路径。")


if __name__ == "__main__":
    try:
        print(find_blender())
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
