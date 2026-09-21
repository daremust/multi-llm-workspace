"""Read environment configuration without changing the process environment."""

import os
from collections.abc import Mapping
from pathlib import Path

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, SecretStr

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    database_path: Path
    openrouter_api_key: SecretStr | None = Field(default=None, exclude=True, repr=False)


def load_settings(
    *, root: Path = ROOT, environ: Mapping[str, str] | None = None
) -> Settings:
    # Disable interpolation so values cannot accidentally expand other secrets.
    values = dict(dotenv_values(root / ".env", interpolate=False))
    values.update(os.environ if environ is None else environ)
    database_value = (values.get("WORKSPACE_DB_PATH") or "").strip()
    database_path = Path(database_value or "data/workspace.db").expanduser()
    if not database_path.is_absolute():
        database_path = root / database_path
    api_key = (values.get("OPENROUTER_API_KEY") or "").strip()
    return Settings(
        database_path=database_path.resolve(),
        openrouter_api_key=SecretStr(api_key) if api_key else None,
    )

