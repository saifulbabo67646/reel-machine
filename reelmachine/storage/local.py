"""Local filesystem storage — the default."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from . import normalise_key


class LocalStorage:
    name = "local"

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        return self.root / normalise_key(key)

    def put_bytes(self, key: str, data: bytes) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return normalise_key(key)

    def put_file(self, key: str, source: Path, *, move: bool = False) -> str:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        if move:
            os.replace(source, target)
        else:
            shutil.copy2(source, target)
        return normalise_key(key)

    def get_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def local_path(self, key: str) -> Path | None:
        path = self._path(key)
        return path if path.is_file() else None

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def list(self, prefix: str = "") -> list[str]:
        base = self.root
        if not base.is_dir():
            return []
        keys: list[str] = []
        for path in sorted(base.rglob("*")):
            if path.is_file():
                key = str(path.relative_to(base))
                if key.startswith(prefix):
                    keys.append(key)
        return keys
