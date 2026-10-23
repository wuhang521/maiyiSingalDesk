from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "SignalDesk Task API"
    database_url: str = "sqlite:///./signaldesk-tasks.db"
    api_key: str = "change-me"
    cors_origins: str = "*"
    hermes_webhook_url: str = ""
    hermes_webhook_secret: str = ""
    prompt_api_key: str = ""
    prompt_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    prompt_model: str = ""
    video_storage_dir: str = "./video-project-files"
    video_planner_source: str = "local"
    seedance_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("TASK_SEEDANCE_API_KEY", "ARK_API_KEY"),
    )
    seedance_base_url: str = Field(
        default="https://ark.cn-beijing.volces.com/api/v3",
        validation_alias=AliasChoices("TASK_SEEDANCE_BASE_URL", "ARK_BASE_URL"),
    )
    seedance_model: str = Field(
        default="doubao-seedance-2-0-mini-260615",
        validation_alias=AliasChoices("TASK_SEEDANCE_MODEL", "SEEDANCE_MODEL_ID"),
    )
    tts_app_id: str = Field(
        default="",
        validation_alias=AliasChoices("TASK_TTS_APP_ID", "TTS_APP_ID"),
    )
    tts_access_token: str = Field(
        default="",
        validation_alias=AliasChoices("TASK_TTS_ACCESS_TOKEN", "TTS_ACCESS_TOKEN"),
    )
    tts_voice_type: str = Field(
        default="zh_female_cancan_mars_bigtts",
        validation_alias=AliasChoices("TASK_TTS_VOICE_TYPE", "TTS_VOICE_TYPE"),
    )
    manager_copy_staff: str = '振华'
    knowledge_storage_dir: str = "./knowledge-files"
    knowledge_max_upload_mb: int = Field(default=500, ge=1, le=500)
    knowledge_user_quota_mb: int = Field(default=5120, ge=20)
    knowledge_queue_limit: int = Field(default=200, ge=1, le=10000)
    knowledge_asr_enabled: bool = False
    knowledge_asr_command: str = ""
    knowledge_cloud_asr_enabled: bool = False
    knowledge_cloud_asr_api_key: str = ""
    knowledge_cloud_asr_app_id: str = ""
    knowledge_cloud_asr_access_key: str = ""
    knowledge_cloud_asr_max_minutes: int = Field(default=10, ge=1, le=10)
    knowledge_silk_enabled: bool = False
    knowledge_paid_daily_limit: int = Field(default=20, ge=0, le=10000)
    knowledge_paid_monthly_limit: int = Field(default=200, ge=0, le=100000)
    knowledge_money_budget_enabled: bool = False
    knowledge_budget_daily_cny: str = "0"
    knowledge_budget_monthly_cny: str = "0"
    knowledge_request_ceiling_cny: str = "{}"
    knowledge_ffmpeg_binary: str = "ffmpeg"
    knowledge_ffprobe_binary: str = "ffprobe"
    knowledge_video_ocr_enabled: bool = False
    knowledge_video_ocr_max_frames: int = Field(default=6, ge=1, le=6)
    knowledge_gateway_secret: str = ""
    knowledge_trash_retention_days: int = Field(default=0, ge=0, le=3650)
    knowledge_cloud_qa_enabled: bool = False
    knowledge_organization_enabled: bool = False
    knowledge_semantic_enabled: bool = False
    knowledge_semantic_max_chunks: int = Field(default=2000, ge=500, le=10000)
    knowledge_embedding_api_key: str = ""
    knowledge_embedding_model: str = ""
    knowledge_dispatch_enabled: bool = False
    knowledge_notification_webhook_base: str = ""
    knowledge_notification_webhook_secret: str = ""
    knowledge_ocr_enabled: bool = False
    knowledge_ocr_api_key: str = ""
    knowledge_ocr_model: str = "doubao-seed-2-0-lite-260428"
    knowledge_ocr_max_pdf_pages: int = Field(default=10, ge=1, le=10)
    reminder_poll_seconds: int = 60
    reminder_start_hour: int = 9
    reminder_end_hour: int = 20

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="TASK_",
        extra="ignore",
        populate_by_name=True,
    )

    @property
    def cors_origin_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
