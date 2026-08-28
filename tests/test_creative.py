import unittest

from ad_vista_agent.creative.builder import _validate_grounding
from ad_vista_agent.schemas import (
    ABVariant,
    CreativeHook,
    CreativePackage,
    CreativeScript,
    CreativeStoryboard,
    ScriptScene,
    StoryboardFrame,
)


def package(ref: str = "ocr_cluster_1") -> CreativePackage:
    return CreativePackage(
        hooks=[CreativeHook(text="先看见变化", angle="结果导向", evidence_refs=[ref])],
        script=CreativeScript(
            title="测试脚本",
            target_audience="广告明确面向的人群",
            scenes=[
                ScriptScene(
                    order=1,
                    duration_seconds=5,
                    narration="广告声称产品可以改善体验。",
                    visual_direction="展示广告中已有的产品画面。",
                    evidence_refs=[ref],
                )
            ],
            call_to_action="了解更多",
        ),
        storyboard=CreativeStoryboard(
            frames=[
                StoryboardFrame(
                    order=1,
                    shot_description="产品近景",
                    camera_direction="固定镜头",
                    evidence_refs=[ref],
                )
            ]
        ),
        ab_variants=[
            ABVariant(
                name="A",
                hypothesis="直接利益点更易理解",
                hook_text="先看见变化",
                change="使用结果导向开场",
                success_metric="前三秒留存",
                evidence_refs=[ref],
            ),
            ABVariant(
                name="B",
                hypothesis="问题导向更易引发共鸣",
                hook_text="你是否遇到这个问题？",
                change="使用问题导向开场",
                success_metric="点击率",
                evidence_refs=[ref],
            ),
        ],
    )


class CreativeGroundingTests(unittest.TestCase):
    def test_accepts_existing_evidence(self) -> None:
        _validate_grounding(package(), {"ocr_cluster_1"})

    def test_rejects_unknown_evidence(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown evidence"):
            _validate_grounding(package("invented"), {"ocr_cluster_1"})

    def test_rejects_duplicate_scene_order(self) -> None:
        value = package()
        value.script.scenes.append(value.script.scenes[0].model_copy())
        with self.assertRaisesRegex(ValueError, "scene orders"):
            _validate_grounding(value, {"ocr_cluster_1"})

    def test_rejects_truncated_text_fragment(self) -> None:
        value = package()
        value.hooks[0].text = "未完成："
        with self.assertRaisesRegex(ValueError, "incomplete"):
            _validate_grounding(value, {"ocr_cluster_1"})


if __name__ == "__main__":
    unittest.main()
