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


class WorkerSection(BaseModel):
    detector: Literal["onnx", "linq", "fake"] = "onnx"
    ppe_fps: float = 6
    hazard_fps: float = 1
    crop_pass: CropPass = Field(default_factory=CropPass)
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
    internal_token: str = "change-me"
    train_runs_dir: str = ""

    backend_port: int = 8000
    frontend_port: int = 5173
    mediamtx_rtsp_port: int = 8554
    mediamtx_webrtc_port: int = 8889
    mediamtx_api_port: int = 9997

    config_path: Path = ROOT / "config.yaml"
    models_dir: Path = ROOT / "models"
    data_dir: Path = ROOT / "data"


def load_config(path: Path | None = None) -> AppConfig:
    path = path or get_settings().config_path
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return AppConfig.model_validate(raw)


@lru_cache
def get_settings() -> Settings:
    return Settings()
