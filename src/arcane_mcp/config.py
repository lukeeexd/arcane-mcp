"""Environment-variable configuration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_TAGS: frozenset[str] = frozenset({
    "Containers", "Images", "Volumes", "Networks", "Projects", "Project Workspace",
    "Environments", "System", "Dashboard", "Activities", "Events", "Ports", "Updater",
    "Image Updates", "Vulnerabilities", "Health", "Version",
})

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


class ConfigError(ValueError):
    """Raised when required configuration is missing or malformed."""


@dataclass(frozen=True)
class Settings:
    arcane_base_url: str
    arcane_api_key: str
    mcp_auth_token: str
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8000
    tags: frozenset[str] = DEFAULT_TAGS
    exclude_tags: frozenset[str] = frozenset()
    read_only: bool = False
    confirm_destructive: bool = True
    destructive_patterns: tuple[str, ...] = ()
    spec_timeout: float = 60.0
    log_level: str = "INFO"


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ConfigError(f"Required environment variable {name} is not set")
    return value


def _csv(env: Mapping[str, str], name: str) -> tuple[str, ...]:
    raw = env.get(name, "")
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    lowered = raw.strip().lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise ConfigError(f"{name} must be a boolean (true/false), got {raw!r}")


def _number(env: Mapping[str, str], name: str, default: float, cast: type) -> float | int:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return cast(default)
    try:
        return cast(raw.strip())
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc


def _base_url(raw: str) -> str:
    url = raw.strip().rstrip("/")
    if url.endswith("/api"):
        url = url[: -len("/api")]
    return url


def load_settings(env: Mapping[str, str]) -> Settings:
    tags = _csv(env, "ARCANE_MCP_TAGS")
    return Settings(
        arcane_base_url=_base_url(_required(env, "ARCANE_BASE_URL")),
        arcane_api_key=_required(env, "ARCANE_API_KEY"),
        mcp_auth_token=_required(env, "MCP_AUTH_TOKEN"),
        mcp_host=env.get("MCP_HOST", "").strip() or "0.0.0.0",
        mcp_port=int(_number(env, "MCP_PORT", 8000, int)),
        tags=frozenset(tags) if tags else DEFAULT_TAGS,
        exclude_tags=frozenset(_csv(env, "ARCANE_MCP_EXCLUDE_TAGS")),
        read_only=_bool(env, "ARCANE_MCP_READ_ONLY", False),
        confirm_destructive=_bool(env, "ARCANE_MCP_CONFIRM_DESTRUCTIVE", True),
        destructive_patterns=_csv(env, "ARCANE_MCP_DESTRUCTIVE_PATTERNS"),
        spec_timeout=float(_number(env, "ARCANE_MCP_SPEC_TIMEOUT", 60.0, float)),
        log_level=(env.get("ARCANE_MCP_LOG_LEVEL", "").strip() or "INFO").upper(),
    )
