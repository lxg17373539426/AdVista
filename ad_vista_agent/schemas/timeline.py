from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Shot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shot_id: str
    asset_id: str
    index: int = Field(ge=0)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    duration_ms: int = Field(gt=0)
    start_frame: int = Field(ge=0)
    end_frame: int = Field(ge=0)
    detector: str
    detector_score: float | None = Field(default=None, ge=0)
    normalization_actions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_interval(self) -> "Shot":
        if self.end_ms <= self.start_ms:
            raise ValueError("Shot end_ms must be greater than start_ms")
        if self.duration_ms != self.end_ms - self.start_ms:
            raise ValueError("Shot duration_ms must equal end_ms - start_ms")
        if self.end_frame < self.start_frame:
            raise ValueError("Shot end_frame must be greater than or equal to start_frame")
        return self


class Keyframe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keyframe_id: str
    asset_id: str
    shot_id: str
    timestamp_ms: int = Field(ge=0)
    frame_number: int = Field(ge=0)
    role: str = "primary"
    selection_reason: str
    artifact_path: Path
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class TimelineCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    gap_ms: int = Field(ge=0)
    overlap_ms: int = Field(ge=0)


class Timeline(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "0.1"
    asset_id: str
    duration_ms: int = Field(gt=0)
    shots: list[Shot] = Field(min_length=1)
    coverage: TimelineCoverage

    @model_validator(mode="after")
    def validate_timeline(self) -> "Timeline":
        if self.coverage.start_ms != 0 or self.coverage.end_ms != self.duration_ms:
            raise ValueError("Timeline coverage must span the complete asset")
        if self.coverage.gap_ms != 0 or self.coverage.overlap_ms != 0:
            raise ValueError("Timeline must not contain gaps or overlaps")
        if self.shots[0].start_ms != 0 or self.shots[-1].end_ms != self.duration_ms:
            raise ValueError("Shots must span the complete asset")

        expected_indices = list(range(len(self.shots)))
        if [shot.index for shot in self.shots] != expected_indices:
            raise ValueError("Shot indices must be continuous from zero")
        if len({shot.shot_id for shot in self.shots}) != len(self.shots):
            raise ValueError("Shot IDs must be unique")
        for previous, current in zip(self.shots, self.shots[1:]):
            if previous.end_ms != current.start_ms:
                raise ValueError("Adjacent shots must be continuous")
        if any(shot.asset_id != self.asset_id for shot in self.shots):
            raise ValueError("All shots must belong to the timeline asset")
        return self
