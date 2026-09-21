import os

from app.config import load_settings


def test_defaults_need_no_api_key_and_create_no_data(tmp_path):
    settings = load_settings(root=tmp_path, environ={})
    assert settings.database_path == tmp_path / "data" / "workspace.db"
    assert settings.openrouter_api_key is None
    assert not settings.database_path.parent.exists()


def test_environment_overrides_dotenv_and_secrets_stay_out_of_serialization(tmp_path):
    (tmp_path / ".env").write_text(
        "OPENROUTER_API_KEY=dotenv-secret\nWORKSPACE_DB_PATH=other.db\n", encoding="utf-8"
    )
    before = dict(os.environ)
    settings = load_settings(
        root=tmp_path,
        environ={"OPENROUTER_API_KEY": "environment-secret", "WORKSPACE_DB_PATH": "local/test.db"},
    )
    assert settings.database_path == tmp_path / "local" / "test.db"
    assert settings.openrouter_api_key.get_secret_value() == "environment-secret"
    assert "environment-secret" not in repr(settings)
    assert "openrouter_api_key" not in settings.model_dump()
    assert "environment-secret" not in settings.model_dump_json()
    assert dict(os.environ) == before


def test_dotenv_is_supported_without_interpolation(tmp_path):
    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=${OTHER_SECRET}\n", encoding="utf-8")
    settings = load_settings(root=tmp_path, environ={})
    assert settings.openrouter_api_key.get_secret_value() == "${OTHER_SECRET}"


def test_blank_environment_key_overrides_dotenv(tmp_path):
    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=secret\n", encoding="utf-8")
    assert load_settings(root=tmp_path, environ={"OPENROUTER_API_KEY": " "}).openrouter_api_key is None

