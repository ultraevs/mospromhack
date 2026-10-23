from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from shared.schemas import RuleSettings, TelegramSettings

ROOT = Path(__file__).resolve().parent.parent


class CigaretteCrop(BaseModel):
    enabled: bool = True
    max_per_tick: int = 6
    top_fraction: float = 0.6
    margin: float = 0.2


class HeadCrop(BaseModel):
    enabled: bool = False
    max_per_frame: int = 4
    top_fraction: float = 0.5
    margin: float = 0.15
    max_person_height_px: int = 200


class CropPass(BaseModel):
    cigarette: CigaretteCrop = Field(default_factory=CigaretteCrop)
    head: HeadCrop = Field(default_factory=HeadCrop)


class DecodeSection(BaseModel):
    backend: Literal["ffmpeg", "opencv"] = "ffmpeg"
    width: int = 1280
    hwaccel: Literal["auto", "none"] = "auto"


class LinqSection(BaseModel):
    transport: Literal["cloud", "pytpu"] = "cloud"
    broker: str = "10.0.114.30"
    port: int = 5672
    program_dir: str = "/auto/cache/{login}"
    timeout_s: float = 20
    batch: int = 8


class WorkerSection(BaseModel):
    detector: Literal["onnx", "linq", "fake"] = "onnx"
    linq: LinqSection = Field(default_factory=LinqSection)
    precision: Literal["auto", "fp32", "fp16"] = "auto"
    ppe_fps: float = 6
    hazard_fps: float = 1
    crop_pass: CropPass = Field(default_factory=CropPass)
    decode: DecodeSection = Field(default_factory=DecodeSection)
    overlay_delay_ms: int = 0
    fake_scenario: str | None = None


class EvidenceSection(BaseModel):
    pre_seconds: float = 5
    post_seconds: float = 5
    clip_width: int = 1280


class AppConfig(BaseModel):
    worker: WorkerSection = Field(default_factory=WorkerSection)
    evidence: EvidenceSection = Field(default_factory=EvidenceSection)
    rules: RuleSettings = Field(default_factory=RuleSettings)
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    telegram_bot_token: str = ""
    ppe_fps: float | None = None
    telegram_enabled: bool = True
    internal_token: str = "change-me"
    train_runs_dir: str = ""
    merged_dir: str = ""
    linq_login: str = ""

    backend_host: str = "127.0.0.1"
    mediamtx_host: str = "127.0.0.1"

    backend_port: int = 8000
    frontend_port: int = 5173
    mediamtx_rtsp_port: int = 8554
    mediamtx_webrtc_port: int = 8889
    mediamtx_api_port: int = 9997
    mediamtx_hls_port: int = 8888
    video_mode: Literal["webrtc", "hls"] = "webrtc"
    # на сколько сек ролик в ui отстаёт от воркера; когда воркер далеко (стенд), рамки иначе не успевают
    sync_lag_s: float = 0.0
    stream_width: int = 0
    stream_maxrate: str = ""

    config_path: Path = ROOT / "config.yaml"
    models_dir: Path = ROOT / "models"
    data_dir: Path = ROOT / "data"


def load_config(path: Path | None = None) -> AppConfig:
    path = path or get_settings().config_path
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return AppConfig.model_validate(raw)


def merged_dir(settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    return Path(settings.merged_dir) if settings.merged_dir else Path(settings.train_runs_dir).parent / "merged"


@lru_cache
def get_settings() -> Settings:
    return Settings()
