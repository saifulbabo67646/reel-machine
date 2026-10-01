"""S3-compatible storage — optional, behind the `[s3]` extra.

`boto3` is imported lazily so the default install never carries it. A client may be
injected (which is how the unit tests run without a network).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import normalise_key


class S3Storage:
    name = "s3"

    def __init__(
        self,
        bucket: str = "",
        *,
        prefix: str = "",
        client: Any = None,
        endpoint_url: str | None = None,
        region: str | None = None,
        cache_dir: Path | None = None,
    ) -> None:
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self._client = client
        self.endpoint_url = endpoint_url
        self.region = region
        self.cache_dir = Path(cache_dir) if cache_dir else None

    def missing(self) -> list[str]:
        if not self.bucket:
            return ["no bucket configured (REEL_S3_BUCKET)"]
        return []

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import boto3  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - depends on the extra
            raise RuntimeError(
                "S3 storage needs the optional dependency: pip install 'reel-machine[s3]'"
            ) from exc
        self._client = boto3.client(
            "s3",
            endpoint_url=self.endpoint_url,
            region_name=self.region,
        )
        return self._client

    def _key(self, key: str) -> str:
        cleaned = normalise_key(key)
        return f"{self.prefix}/{cleaned}" if self.prefix else cleaned

    def put_bytes(self, key: str, data: bytes) -> str:
        self._ensure_client().put_object(Bucket=self.bucket, Key=self._key(key), Body=data)
        return normalise_key(key)

    def put_file(self, key: str, source: Path, *, move: bool = False) -> str:
        data = Path(source).read_bytes()
        stored = self.put_bytes(key, data)
        if move:
            Path(source).unlink(missing_ok=True)
        return stored

    def get_bytes(self, key: str) -> bytes:
        response = self._ensure_client().get_object(Bucket=self.bucket, Key=self._key(key))
        return response["Body"].read()

    def local_path(self, key: str) -> Path | None:
        if self.cache_dir is None:
            return None
        path = self.cache_dir / normalise_key(key)
        if not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(self.get_bytes(key))
        return path

    def exists(self, key: str) -> bool:
        try:
            self._ensure_client().head_object(Bucket=self.bucket, Key=self._key(key))
        except Exception:  # noqa: BLE001 - any client error means "not usable here"
            return False
        return True

    def delete(self, key: str) -> None:
        self._ensure_client().delete_object(Bucket=self.bucket, Key=self._key(key))

    def list(self, prefix: str = "") -> list[str]:
        client = self._ensure_client()
        full_prefix = self._key(prefix) if prefix else (f"{self.prefix}/" if self.prefix else "")
        keys: list[str] = []
        token: str | None = None
        while True:
            kwargs: dict[str, Any] = {"Bucket": self.bucket, "Prefix": full_prefix}
            if token:
                kwargs["ContinuationToken"] = token
            response = client.list_objects_v2(**kwargs)
            for item in response.get("Contents") or []:
                name = item["Key"]
                if self.prefix and name.startswith(f"{self.prefix}/"):
                    name = name[len(self.prefix) + 1 :]
                keys.append(name)
            if not response.get("IsTruncated"):
                break
            token = response.get("NextContinuationToken")
        return sorted(keys)
