"""Configuration management for Smartsheet Resource Management MCP server."""

import os
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from smartsheet_rm_mcp.client import DEFAULT_BASE_URL


class SmartsheetRMSettings(BaseSettings):
    """Application settings with environment variable bindings."""

    model_config = SettingsConfigDict(
        env_prefix="SMARTSHEET_RM_",
        env_file=".env",
        extra="ignore",
    )

    API_TOKEN: str = Field(
        default="",
        description="Smartsheet Resource Management (10,000ft) API token",
    )
    BASE_URL: str = Field(
        default=DEFAULT_BASE_URL,
        description="Target Smartsheet RM API base URL",
    )
    PROFILE: str = Field(
        default="full",
        description=(
            "Server profile: domain-mount profiles 'full', 'time', 'projects', 'admin', 'readonly', "
            "or a job-shaped allowlist profile 'timesheets', 'staffing', 'org_setup', 'portfolio' "
            "(see profiles.PROFILES)"
        ),
    )
    READONLY: bool = Field(
        default=False,
        description="Restrict server strictly to tools marked readOnlyHint=True",
    )
    ALLOW_BULK_DESTRUCTIVE: bool = Field(
        default=False,
        description="Opt-in gate required to execute bulk deletion operations (listed in full, refused without it)",
    )
    ENABLE_TOOL_SEARCH: bool = Field(
        default=False,
        description=("Opt-in Tool Search transform (search_tools + call_tool). Attached only when profile is 'full'."),
    )
    TOOL_SEARCH_BACKEND: Literal["regex", "bm25"] = Field(
        default="regex",
        description="Tool Search backend: 'regex' (default) or 'bm25'",
    )
    ENABLE_CODE_MODE: bool = Field(
        default=False,
        description=(
            "Opt-in experimental Code Mode transform (search + execute). "
            "Attached only when profile is 'full'; mutually exclusive with Tool Search."
        ),
    )
    STATELESS_HTTP: bool = Field(
        default=False,
        description="Run Streamable HTTP in stateless mode (fresh session per request)",
    )
    JSON_RESPONSE: bool = Field(
        default=False,
        description="Return direct JSON responses over Streamable HTTP instead of SSE stream",
    )
    CATALOG_CACHE_TTL_MS: int = Field(
        default=3600000,
        gt=0,
        description="Cache TTL in milliseconds for catalog discovery (tools/list, etc.)",
    )
    LOG_FORMAT: str = Field(
        default="",
        description="Logging format: 'json' for structured JSON logs, empty for standard",
    )


Settings = SmartsheetRMSettings
settings = SmartsheetRMSettings()


def _env_flag(name: str) -> bool:
    """Return True when environment variable ``name`` is set to ``1`` at call time."""
    return os.environ.get(name, "").strip() == "1"


def readonly_enabled() -> bool:
    """Return True when read-only mode is on (settings or live ``SMARTSHEET_RM_READONLY=1``)."""
    return settings.READONLY or _env_flag("SMARTSHEET_RM_READONLY")


def bulk_destructive_allowed() -> bool:
    """Return True when bulk deletes may execute (``SMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE=1``)."""
    return settings.ALLOW_BULK_DESTRUCTIVE or _env_flag("SMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE")


__all__ = [
    "SmartsheetRMSettings",
    "Settings",
    "bulk_destructive_allowed",
    "readonly_enabled",
    "settings",
]
