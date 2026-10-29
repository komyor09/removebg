"""Settings from environment variables (and an optional .env file). See .env.example."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    log_level: str = "INFO"
    max_upload_mb: int = 10  # bigger files get 413
    max_side: int = 2000  # bigger photos are downscaled first to save RAM and time
    max_batch_files: int = 100  # images per /remove-background/batch request (~1 s each on 2 vCPU)
    max_batch_mb: int = 100  # total upload size of one batch request

    rembg_model: str = "u2net_human_seg"  # file <U2NET_HOME>/<rembg_model>.onnx must exist
    u2net_home: str = "models/rembg"  # folder with .onnx models; /models/rembg in Docker


@lru_cache
def get_settings() -> Settings:
    return Settings()
