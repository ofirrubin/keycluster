from typing import Optional
from pydantic import BaseModel
from sqlmodel import Field, SQLModel
from datetime import datetime, timezone


class DomainMapping(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    realm: str = Field(index=True, unique=True)
    domain: str = Field(index=True)
    enabled: bool = Field(default=True)
    theme_name: str = Field(default="dynamic-standard")

    # Security Settings
    csp_allowed_origins: str = Field(default="")
    ssl_required: str = Field(default="external")
    hsts_max_age: int = Field(default=31536000)

    # TLS Settings
    tls_enabled: bool = Field(default=False)
    tls_secret_name: Optional[str] = Field(default=None)

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DomainMappingResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    realm: str
    domain: str
    enabled: bool
    theme_name: str
    csp_allowed_origins: str
    ssl_required: str
    hsts_max_age: int
    tls_enabled: bool
    tls_secret_name: Optional[str]
    created_at: datetime
    updated_at: datetime
