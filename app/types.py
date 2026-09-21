"""Only the domain types used by the foundation."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProjectCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)


class Project(BaseModel):
    id: int
    name: str
    task: str
    additional_instructions: str
    output_format: str
    custom_output_format: str
    current_version_id: int | None
    created_at: datetime
    updated_at: datetime

