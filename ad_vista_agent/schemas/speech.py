from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TranscriptWord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    text: str
    probability: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_interval(self) -> "TranscriptWord":
        if self.end_ms < self.start_ms:
            raise ValueError("Word end_ms must be greater than or equal to start_ms")
        return self


class TranscriptSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segment_id: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    text: str
    confidence: float | None = Field(default=None, ge=0, le=1)
    words: list[TranscriptWord] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_interval(self) -> "TranscriptSegment":
        if self.end_ms <= self.start_ms:
            raise ValueError("Segment end_ms must be greater than start_ms")
        for word in self.words:
            if word.start_ms < self.start_ms or word.end_ms > self.end_ms:
                raise ValueError("Words must be contained in their segment")
        return self


class SpeechTranscript(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "0.1"
    asset_id: str
    duration_ms: int = Field(gt=0)
    language: str | None = None
    language_probability: float | None = Field(default=None, ge=0, le=1)
    has_audio: bool
    segments: list[TranscriptSegment]
    tool: str
    model: str
    model_version: str
    generation: dict[str, object]

    @model_validator(mode="after")
    def validate_segments(self) -> "SpeechTranscript":
        previous_end = 0
        for segment in self.segments:
            if segment.end_ms > self.duration_ms:
                raise ValueError("Transcript segment exceeds asset duration")
            if segment.start_ms < previous_end:
                raise ValueError("Transcript segments must not overlap")
            previous_end = segment.end_ms
        if not self.has_audio and self.segments:
            raise ValueError("A video without an audio stream cannot have speech segments")
        return self
