from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path


def home() -> Path:
    default = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".config")) / "CanvasStudy"
    return Path(os.environ.get("CANVAS_STUDY_HOME", default)).expanduser().resolve()


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_write(path: Path, data: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".canvas-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data.encode("utf-8") if isinstance(data, str) else data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def write_json(path: Path, value) -> None:
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def settings() -> dict:
    return read_json(home() / "config.json", {"base_url": "https://oc.sjtu.edu.cn", "courses": {}})


def save_settings(value: dict) -> None:
    write_json(home() / "config.json", value)


def safe_name(value: str, limit: int = 100) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    if not value or value in {".", ".."}:
        value = "untitled"
    if re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", value, re.I):
        value = "_" + value
    if len(value) > limit:
        suffix = Path(value).suffix[:12]
        value = (
            value[: limit - len(suffix) - 9] + "-" + hashlib.sha256(value.encode()).hexdigest()[:8] + suffix
        )
    return value


def under(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise ValueError("文件路径超出课程目录")
    return candidate


def sha256(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


class CourseLock:
    """OS lock is released on process death; the lock file itself is harmless."""

    def __init__(self, root: Path):
        self.path = root / ".canvas" / "sync.lock"

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a+b")
        try:
            self.file.seek(0)
            if not self.file.read(1):
                self.file.write(b"0")
                self.file.flush()
            self.file.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise ValueError("该课程正在同步，请等待另一个进程完成") from None
        return self

    def __exit__(self, *args):
        self.file.close()
