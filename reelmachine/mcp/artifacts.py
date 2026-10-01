"""Signed, expiring artifact URLs.

`get_artifact` never returns bytes. Over HTTP it returns a short-lived signed URL served
by the same app; over stdio it returns a `file://` URL, because no listener exists on that
transport.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from ..core.errors import NotFound, ReelError


def _payload(caller: str, job_id: str, name: str, expires: int) -> bytes:
    return f"{caller}\n{job_id}\n{name}\n{expires}".encode("utf-8")


@dataclass(frozen=True)
class ArtifactSigner:
    key: str
    ttl_s: int = 900
    route: str = "/artifacts"

    @classmethod
    def from_env(cls) -> "ArtifactSigner":
        # A generated key is safe but invalidates URLs on restart; a deployment that
        # serves them across restarts sets REEL_MCP_SIGNING_KEY.
        return cls(key=os.environ.get("REEL_MCP_SIGNING_KEY") or secrets.token_hex(32))

    def signature(self, caller: str, job_id: str, name: str, expires: int) -> str:
        return hmac.new(self.key.encode("utf-8"), _payload(caller, job_id, name, expires), hashlib.sha256).hexdigest()

    def url(self, base_url: str, caller: str, job_id: str, name: str, *, now: int | None = None) -> tuple[str, int]:
        expires = int(now if now is not None else time.time()) + self.ttl_s
        if not base_url:
            return "", 0
        signature = self.signature(caller, job_id, name, expires)
        url = (
            f"{base_url.rstrip('/')}{self.route}/{quote(caller)}/{quote(job_id)}/{quote(name)}"
            f"?expires={expires}&sig={signature}"
        )
        return url, expires

    def verify(self, caller: str, job_id: str, name: str, expires: int, signature: str) -> bool:
        if expires < int(time.time()):
            return False
        expected = self.signature(caller, job_id, name, expires)
        return hmac.compare_digest(expected, signature)


def artifact_file(engine, job_id: str, name: str, *, caller: str) -> Path:
    """Resolve an artifact's path — ownership is enforced by the engine lookup."""
    reference = engine.artifact(job_id, name, caller=caller)
    path = Path(reference.path)
    if not path.is_file():
        raise NotFound(
            "the artifact record exists but its file is gone",
            hint="the job's work directory may have been cleaned up",
            details={"jobId": job_id, "artifact": name},
        )
    return path


def file_url(path: Path) -> str:
    return path.resolve().as_uri()


def is_reel_error(exc: BaseException) -> bool:
    return isinstance(exc, ReelError)
