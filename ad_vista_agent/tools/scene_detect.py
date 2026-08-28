from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Any

from scenedetect import SceneManager, open_video
from scenedetect.detectors import ContentDetector

from ad_vista_agent.timeline import RawShot

from .base import Tool, ToolContext


@dataclass(frozen=True)
class SceneDetectResult:
    shots: list[RawShot]
    detector: str
    tool_version: str
    processed_frames: int


class SceneDetectTool(Tool):
    name = "scene_detect"
    description = "Detect raw shot boundaries with PySceneDetect ContentDetector."

    def run(self, context: ToolContext, arguments: dict[str, Any]) -> SceneDetectResult:
        del context
        video_path = Path(str(arguments["video_path"])).expanduser().resolve()
        if not video_path.is_file():
            raise FileNotFoundError(video_path)

        threshold = float(arguments["threshold"])
        min_scene_len = int(arguments["min_scene_len_frames"])
        if threshold <= 0:
            raise ValueError("threshold must be positive")
        if min_scene_len <= 0:
            raise ValueError("min_scene_len_frames must be positive")

        video = open_video(str(video_path))
        manager = SceneManager()
        manager.add_detector(ContentDetector(threshold=threshold, min_scene_len=min_scene_len))
        manager.detect_scenes(video=video, show_progress=False)
        scene_list = manager.get_scene_list(start_in_scene=True)
        shots = [
            RawShot(
                start_ms=round(start.get_seconds() * 1000),
                end_ms=round(end.get_seconds() * 1000),
            )
            for start, end in scene_list
        ]
        return SceneDetectResult(
            shots=shots,
            detector="pyscenedetect_content",
            tool_version=version("scenedetect"),
            processed_frames=int(video.frame_number),
        )
