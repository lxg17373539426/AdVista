from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class VideoStream(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int
    codec: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: float = Field(gt=0)
    duration_ms: int | None = Field(default=None, ge=0)
    frame_count: int | None = Field(default=None, ge=0)


class AudioStream(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int
    codec: str
    sample_rate: int | None = Field(default=None, gt=0)
    channels: int | None = Field(default=None, gt=0)
    duration_ms: int | None = Field(default=None, ge=0)


class MediaMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format_name: str
    duration_ms: int = Field(ge=0)
    bit_rate: int | None = Field(default=None, ge=0)
    video_streams: list[VideoStream]
    audio_streams: list[AudioStream]


class AdAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "0.1"
    asset_id: str
    source_path: Path
    filename: str
    media_type: str
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    metadata: MediaMetadata
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
