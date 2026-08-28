from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = Path(__file__).resolve().parent / "resources" / "default.yaml"


class ProjectConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    schema_version: str


class PathConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_root: Path
    video_data: Path
    output_root: Path


class HardwareConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cuda_visible_devices: str = "0"
    max_gpus: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def enforce_gpu_limit(self) -> "HardwareConfig":
        devices = [item.strip() for item in self.cuda_visible_devices.split(",") if item.strip()]
        if len(devices) > self.max_gpus:
            raise ValueError("cuda_visible_devices exceeds max_gpus")
        return self


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    qwen: str
    asr: str
    ocr: str


class ToolConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ffmpeg_executable: str = "ffmpeg"
    ffprobe_executable: str = "ffprobe"
    probe_timeout_seconds: int = Field(default=30, ge=1)
    frame_extract_timeout_seconds: int = Field(default=60, ge=1)


class TimelineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detector: str = "pyscenedetect_content"
    scene_threshold: float = Field(default=27.0, gt=0)
    min_shot_ms: int = Field(default=500, gt=0)
    frame_format: str = Field(default="jpg", pattern=r"^jpg$")
    jpeg_quality: int = Field(default=2, ge=1, le=31)


class AsrConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    python_executable: Path
    model: str
    device: str = Field(default="cuda", pattern=r"^(cuda|cpu)$")
    device_index: int = Field(default=0, ge=0)
    compute_type: str = "float16"
    beam_size: int = Field(default=5, ge=1)
    vad_filter: bool = True
    condition_on_previous_text: bool = False
    word_timestamps: bool = True
    merge_gap_ms: int = Field(default=500, ge=0)
    timeout_seconds: int = Field(default=600, ge=1)


class OcrConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deepseek_python: Path
    paddle_python: Path
    model: str
    primary: str = Field(default="deepseek_ocr", pattern=r"^deepseek_ocr$")
    fallback: str = Field(default="paddleocr", pattern=r"^paddleocr$")
    prompt: str
    base_size: int = Field(default=1024, gt=0)
    image_size: int = Field(default=640, gt=0)
    crop_mode: bool = True
    timeout_seconds: int = Field(default=1800, ge=1)
    paddle_score_threshold: float = Field(default=0.5, ge=0, le=1)


class LedgerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ocr_similarity_threshold: float = Field(default=0.92, ge=0, le=1)
    ocr_max_gap_ms: int = Field(default=2500, ge=0)
    ocr_min_region_iou: float = Field(default=0.5, ge=0, le=1)
    cross_modal_similarity_threshold: float = Field(default=0.8, ge=0, le=1)
    cross_modal_window_ms: int = Field(default=1500, ge=0)
    conflict_context_similarity_threshold: float = Field(default=0.8, ge=0, le=1)


class InsightConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    python_executable: Path
    model: str
    runtime: str = Field(default="openai", pattern=r"^(openai|subprocess)$")
    endpoint: str = "http://127.0.0.1:8000/v1"
    served_model: str = "AdInsight-RL"
    max_model_len: int = Field(default=8192, ge=1024)
    max_tokens: int = Field(default=4096, ge=256)
    gpu_memory_utilization: float = Field(default=0.8, gt=0, le=1)
    temperature: float = Field(default=0.0, ge=0, le=2)
    timeout_seconds: int = Field(default=900, ge=1)
    max_insights_per_dimension: int = Field(default=5, ge=1, le=10)


class ReportConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_evidence_per_insight: int = Field(default=4, ge=1, le=10)
    embed_images: bool = True
    max_embedded_image_bytes: int = Field(default=500_000, ge=10_000)


class WebConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = "127.0.0.1"
    port: int = Field(default=8080, ge=1, le=65535)
    max_upload_bytes: int = Field(default=1_073_741_824, ge=1_048_576)
    analysis_workers: int = Field(default=1, ge=1, le=4)


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backend: str = Field(default="langgraph", pattern=r"^(legacy|langgraph)$")
    recursion_limit: int = Field(default=32, ge=4, le=100)
    tool_choice: str = Field(default="required", pattern=r"^(auto|required)$")


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: ProjectConfig
    paths: PathConfig
    hardware: HardwareConfig
    models: ModelConfig
    tools: ToolConfig
    timeline: TimelineConfig
    asr: AsrConfig
    ocr: OcrConfig
    ledger: LedgerConfig
    insight: InsightConfig
    report: ReportConfig
    web: WebConfig = WebConfig()
    agent: AgentConfig = AgentConfig()

    def model_path(self, model_name: str) -> Path:
        return self.paths.model_root / model_name


def load_settings(config_path: Path = DEFAULT_CONFIG) -> Settings:
    with config_path.expanduser().resolve().open(encoding="utf-8") as handle:
        value = yaml.safe_load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Configuration must be a YAML object: {config_path}")
    return Settings.model_validate(value)
