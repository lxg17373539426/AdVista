from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .evidence import BoundingBox


class OcrRegion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    region: BoundingBox
    confidence: float | None = Field(default=None, ge=0, le=1)
    label: str = "text"


class OcrFrameResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keyframe_id: str
    shot_id: str
    timestamp_ms: int = Field(ge=0)
    frame_path: str
    backend: str
    raw_text: str
    regions: list[OcrRegion]

    @model_validator(mode="after")
    def validate_unique_regions(self) -> "OcrFrameResult":
        keys = {
            (
                item.text.casefold(),
                item.region.x1,
                item.region.y1,
                item.region.x2,
                item.region.y2,
            )
            for item in self.regions
        }
        if len(keys) != len(self.regions):
            raise ValueError("OCR regions must be unique")
        return self
