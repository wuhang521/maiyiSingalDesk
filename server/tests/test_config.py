import sys
from pathlib import Path


SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

from app.config import Settings


def test_settings_accept_official_ark_and_tts_names(monkeypatch) -> None:
    monkeypatch.setenv("ARK_API_KEY", "ark-secret")
    monkeypatch.setenv("ARK_BASE_URL", "https://ark.example/api/v3")
    monkeypatch.setenv("SEEDANCE_MODEL_ID", "seedance-model")
    monkeypatch.setenv("TTS_APP_ID", "tts-app")
    monkeypatch.setenv("TTS_ACCESS_TOKEN", "tts-secret")
    monkeypatch.setenv("TTS_VOICE_TYPE", "tts-voice")

    settings = Settings(_env_file=None)

    assert settings.seedance_api_key == "ark-secret"
    assert settings.seedance_base_url == "https://ark.example/api/v3"
    assert settings.seedance_model == "seedance-model"
    assert settings.tts_app_id == "tts-app"
    assert settings.tts_access_token == "tts-secret"
    assert settings.tts_voice_type == "tts-voice"


def test_task_seedance_names_keep_priority(monkeypatch) -> None:
    monkeypatch.setenv("TASK_SEEDANCE_API_KEY", "task-secret")
    monkeypatch.setenv("ARK_API_KEY", "ark-secret")

    settings = Settings(_env_file=None)

    assert settings.seedance_api_key == "task-secret"
