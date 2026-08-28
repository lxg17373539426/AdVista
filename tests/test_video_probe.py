import unittest
from pathlib import Path

from ad_vista_agent.tools import ToolContext, VideoProbeTool


class VideoProbeTests(unittest.TestCase):
    def test_missing_video(self) -> None:
        tool = VideoProbeTool()
        context = ToolContext(run_id="test", run_dir=Path("/tmp"))
        with self.assertRaises(FileNotFoundError):
            tool.run(context, {"video_path": "/definitely/missing/video.mp4"})


if __name__ == "__main__":
    unittest.main()
