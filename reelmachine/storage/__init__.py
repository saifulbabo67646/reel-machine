"""Storage: where job artifacts and assets live.

Local filesystem by default. The interface is deliberately small — key in, bytes or a
local path out — because rendering needs a real file, and a remote store has to
materialise one to be useful.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class Storage(Protocol):
    name: str

    def put_bytes(self, key: str, data: bytes) -> str: ...

    def put_file(self, key: str, source: Path, *, move: bool = False) -> str: ...

    def get_bytes(self, key: str) -> bytes: ...

    def local_path(self, key: str) -> Path | None:
        """A real path for `key`, or None when the store cannot expose one."""
        ...

    def exists(self, key: str) -> bool: ...

    def delete(self, key: str) -> None: ...

    def list(self, prefix: str = "") -> list[str]: ...


def normalise_key(key: str) -> str:
    """Reject absolute paths and traversal; stores must never escape their root."""
    if not isinstance(key, str) or not key.strip() or key.startswith("/"):
        raise ValueError(f"unsafe storage key {key!r}")
    parts = key.split("/")
    if ".." in parts or any(not part for part in parts):
        raise ValueError(f"unsafe storage key {key!r}")
    return key


class InMemoryStorage:
    """Deterministic fake storage for tests and for planning without a disk."""

    name = "memory"

    def __init__(self, root: Path | None = None) -> None:
        self.root = root
        self._files: dict[str, bytes] = {}

    def put_bytes(self, key: str, data: bytes) -> str:
        cleaned = normalise_key(key)
        self._files[cleaned] = data
        return cleaned

    def put_file(self, key: str, source: Path, *, move: bool = False) -> str:
        cleaned = self.put_bytes(key, Path(source).read_bytes())
        if move:
            Path(source).unlink(missing_ok=True)
        return cleaned

    def get_bytes(self, key: str) -> bytes:
        try:
            return self._files[normalise_key(key)]
        except KeyError as exc:
            raise FileNotFoundError(key) from exc

    def local_path(self, key: str) -> Path | None:
        return None

    def exists(self, key: str) -> bool:
        return normalise_key(key) in self._files

    def delete(self, key: str) -> None:
        self._files.pop(normalise_key(key), None)

    def list(self, prefix: str = "") -> list[str]:
        return sorted(key for key in self._files if key.startswith(prefix))


__all__ = ["InMemoryStorage", "Storage", "normalise_key"]
