from typing import Optional
from sqlmodel import Field, SQLModel
from datetime import datetime

class DomainMapping(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    realm: str = Field(index=True, unique=True)
    domain: str = Field(index=True)
    enabled: bool = Field(default=True)
    theme_name: str = Field(default="dynamic-standard")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
