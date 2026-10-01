"""Per-caller identity and policy for the MCP server.

Raw tokens are never stored: a tenant record carries the sha256 of its token, and a
request's bearer token is hashed and compared. One server serving many callers is the
normal case, not a special one.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from ..core.errors import InvalidInput
from ..core.job import CallerPolicy


class Tenant(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str
    token_sha256: str = Field(default="", alias="tokenSha256")
    allowed_recipes: list[str] | None = Field(default=None, alias="allowedRecipes")
    max_concurrent_jobs: int = Field(default=2, alias="maxConcurrentJobs")
    max_disk_mb: int = Field(default=4096, alias="maxDiskMb")
    quotas: dict[str, int] = Field(default_factory=dict)

    def policy(self) -> CallerPolicy:
        return CallerPolicy(
            allowed_recipes=self.allowed_recipes,
            max_concurrent_jobs=self.max_concurrent_jobs,
            max_disk_mb=self.max_disk_mb,
            quotas=dict(self.quotas),
        )


class TenantRegistry:
    """Callers this server accepts, and the policy each one runs under."""

    def __init__(
        self,
        tenants: list[Tenant] | None = None,
        *,
        default_caller: str = "local",
        single_token: str = "",
        single_token_caller: str = "",
    ) -> None:
        self._tenants = {tenant.id: tenant for tenant in tenants or []}
        self.default_caller = default_caller or "local"
        self._single_token = single_token
        self._single_token_caller = single_token_caller or self.default_caller

    @classmethod
    def from_env(cls) -> "TenantRegistry":
        path = os.environ.get("REEL_MCP_TENANTS", "").strip()
        tenants: list[Tenant] = []
        if path:
            tenants = load_tenants(Path(path))
        return cls(
            tenants,
            default_caller=os.environ.get("REEL_MCP_CALLER", "local"),
            single_token=os.environ.get("REEL_MCP_TOKEN", ""),
            single_token_caller=os.environ.get("REEL_MCP_CALLER", "local"),
        )

    @staticmethod
    def hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @property
    def requires_token(self) -> bool:
        return bool(self._tenants) or bool(self._single_token)

    def by_token(self, token: str) -> Tenant | None:
        digest = self.hash_token(token)
        for tenant in self._tenants.values():
            if tenant.token_sha256 and hmac.compare_digest(tenant.token_sha256, digest):
                return tenant
        if self._single_token and hmac.compare_digest(
            self.hash_token(self._single_token), digest
        ):
            return Tenant(id=self._single_token_caller)
        return None

    def get(self, caller: str) -> Tenant | None:
        return self._tenants.get(caller)

    def policy(self, caller: str) -> CallerPolicy:
        tenant = self._tenants.get(caller)
        return tenant.policy() if tenant is not None else CallerPolicy()

    def ids(self) -> list[str]:
        ids = sorted(self._tenants)
        if self._single_token and self._single_token_caller not in ids:
            ids.append(self._single_token_caller)
        return ids


def load_tenants(path: Path) -> list[Tenant]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InvalidInput(
            f"could not read the tenants file: {exc}",
            hint="REEL_MCP_TENANTS must point at JSON like {\"callers\": [{\"id\": ..., \"tokenSha256\": ...}]}",
            details={"path": str(path)},
        ) from exc
    raw = payload.get("callers") if isinstance(payload, dict) else payload
    if not isinstance(raw, list):
        raise InvalidInput(
            "the tenants file has no callers list",
            hint='expected {"callers": [{...}]}',
            details={"path": str(path)},
        )
    return [Tenant.model_validate(entry) for entry in raw]
