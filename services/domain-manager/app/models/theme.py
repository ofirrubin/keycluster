from typing import Optional
from sqlmodel import Field, SQLModel, Column
from sqlalchemy.dialects.postgresql import JSONB
from datetime import datetime, timezone


class RealmTheme(SQLModel, table=True):
    __tablename__ = "realm_themes"

    id: Optional[int] = Field(default=None, primary_key=True)
    realm: str = Field(index=True, unique=True, max_length=255)
    theme_name: str = Field(default="dynamic-standard", max_length=255)
    config: dict = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False, server_default="{}"))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
