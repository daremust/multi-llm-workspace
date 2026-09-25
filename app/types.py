"""Domain types for projects and persisted workspace inputs."""

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


OUTPUT_FORMATS = (
    'LinkedIn post', 'X post/thread', 'Reddit post', 'Slack/Discord message',
    'Newsletter', 'Blog/article', 'Email', 'Executive summary', 'Research summary',
    'Strategy memo', 'Technical documentation', 'Plain answer', 'Custom',
)


class Source(BaseModel):
    id: int
    project_id: int
    type: str
    title: str
    content: str
    original_url: str | None
    final_url: str | None
    created_at: datetime
    updated_at: datetime


class Response(BaseModel):
    id: int
    project_id: int
    text: str
    origin: str
    kind: str
    run_request_id: int | None
    verified_provider: str | None
    verified_model: str | None
    claimed_provider: str | None
    claimed_model: str | None
    finish_reason: str | None = None
    generation_metadata_json: str = "{}"
    created_at: datetime


class Run(BaseModel):
    id: int
    project_id: int
    operation: str
    status: str
    prompt_version: str
    input_snapshot_json: str
    parameters_json: str
    estimated_cost_usd: float | None
    created_at: datetime
    completed_at: datetime | None


class RunRequest(BaseModel):
    id: int
    project_id: int
    run_id: int
    requested_model: str
    status: str
    error_message: str | None
    provider_request_id: str | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    latency_ms: int | None
    created_at: datetime
    completed_at: datetime | None
    response: Response | None = None
