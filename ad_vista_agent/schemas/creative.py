from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CreativeHook(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=500)
    angle: str = Field(min_length=1, max_length=200)
    evidence_refs: list[str] = Field(min_length=1, max_length=8)


class ScriptScene(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order: int = Field(ge=1, le=30)
    duration_seconds: float = Field(gt=0, le=60)
    narration: str = Field(min_length=1, max_length=2000)
    visual_direction: str = Field(min_length=1, max_length=2000)
    on_screen_text: str = Field(default="", max_length=1000)
    evidence_refs: list[str] = Field(min_length=1, max_length=8)


class CreativeScript(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    target_audience: str = Field(min_length=1, max_length=500)
    scenes: list[ScriptScene] = Field(min_length=1, max_length=30)
    call_to_action: str = Field(min_length=1, max_length=500)


class StoryboardFrame(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order: int = Field(ge=1, le=30)
    shot_description: str = Field(min_length=1, max_length=1500)
    camera_direction: str = Field(min_length=1, max_length=1000)
    text_overlay: str = Field(default="", max_length=1000)
    evidence_refs: list[str] = Field(min_length=1, max_length=8)


class CreativeStoryboard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frames: list[StoryboardFrame] = Field(min_length=1, max_length=30)


class ABVariant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    hypothesis: str = Field(min_length=1, max_length=500)
    hook_text: str = Field(min_length=1, max_length=500)
    change: str = Field(min_length=1, max_length=1000)
    success_metric: str = Field(min_length=1, max_length=300)
    evidence_refs: list[str] = Field(min_length=1, max_length=8)


class CreativePackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "0.1"
    hooks: list[CreativeHook] = Field(min_length=1, max_length=5)
    script: CreativeScript
    storyboard: CreativeStoryboard
    ab_variants: list[ABVariant] = Field(min_length=2, max_length=4)
    unsupported_points: list[str] = Field(default_factory=list, max_length=12)
