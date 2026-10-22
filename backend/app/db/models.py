from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class CameraRow(Base):
    __tablename__ = "cameras"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    source_type: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    required_ppe: Mapped[list] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    zones: Mapped[list["ZoneRow"]] = relationship(back_populates="camera", cascade="all, delete-orphan")


class ZoneRow(Base):
    __tablename__ = "zones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    polygon: Mapped[list] = mapped_column(JSON, default=list)
    required_ppe: Mapped[list] = mapped_column(JSON, default=list)

    camera: Mapped[CameraRow] = relationship(back_populates="zones")


class EventRow(Base):
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    type: Mapped[str] = mapped_column(String, index=True)
    severity: Mapped[str] = mapped_column(String, index=True)
    status: Mapped[str] = mapped_column(String, index=True, default="open")
    camera_id: Mapped[int] = mapped_column(Integer, index=True)
    # копируем имена чтоб журнал не ломался после удаления камеры
    camera_name: Mapped[str] = mapped_column(String, default="")
    zone_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    zone_name: Mapped[str | None] = mapped_column(String, nullable=True)
    track_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    bbox: Mapped[list | None] = mapped_column(JSON, nullable=True)
    snapshot_url: Mapped[str | None] = mapped_column(String, nullable=True)
    snapshot_clean_url: Mapped[str | None] = mapped_column(String, nullable=True)
    clip_url: Mapped[str | None] = mapped_column(String, nullable=True)
    review: Mapped[str] = mapped_column(String, index=True, default="none")
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)


class SettingRow(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[dict | list | int | str | None] = mapped_column(JSON)
