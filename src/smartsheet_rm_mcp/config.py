"""Configuration management for Smartsheet Resource Management MCP server."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from smartsheet_rm_mcp.client import DEFAULT_BASE_URL


class Settings(BaseSettings):
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
        description="Tool subset profile: full, time, projects, admin, readonly",
    )
    READONLY: bool = Field(
        default=False,
        description="Restrict server strictly to tools marked readOnlyHint=True",
    )
    ALLOW_BULK_DESTRUCTIVE: bool = Field(
        default=False,
        description="Opt-in gate required to register bulk deletion operations",
    )
    ENABLE_TOOL_SEARCH: bool = Field(
        default=False,
        description="Enable dynamic tool search transform instead of flat tools/list",
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


settings = Settings()
