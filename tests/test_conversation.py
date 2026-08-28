import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ad_vista_agent.agent.chat import (
    _is_greeting,
    _is_creative_request,
    _needs_visual_context,
    _render_creative_answer,
    _workspace_answer,
    ask_agent,
    normalize_conversation_answer,
    repair_grounded_references,
    validate_conversation_answer,
)
from ad_vista_agent.config import DEFAULT_CONFIG, load_settings
from ad_vista_agent.memory import ConversationStore
from ad_vista_agent.schemas import (
    ABVariant,
    ConversationAnswer,
    CreativeHook,
    CreativePackage,
    CreativeScript,
    CreativeStoryboard,
    ScriptScene,
    StoryboardFrame,
)


class ConversationTests(unittest.TestCase):
    def test_workspace_questions_use_deterministic_artifact_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / "report" / "report.html"
            report.parent.mkdir(parents=True)
            report.write_text("<html></html>", encoding="utf-8")
            result = _workspace_answer(
                "分析结果在哪里，怎么下载？",
                root,
                "waiting_confirmation",
                ["report_review"],
            )
            if result is None:
                self.fail("Expected deterministic workspace answer")
            answer, has_html = result
            self.assertIn("等待人工确认", answer.answer)
            self.assertIn("HTML 报告", answer.answer)
            self.assertTrue(has_html)

    def test_ask_agent_persists_history_and_answer_versions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = load_settings(DEFAULT_CONFIG)
            settings = settings.model_copy(
                update={"paths": settings.paths.model_copy(update={"output_root": root})}
            )
            state = SimpleNamespace(
                session_id="session_1",
                run_id="run_1",
                source_path=Path("ad.mp4"),
                request=SimpleNamespace(goal="分析卖点"),
            )
            histories: list[list[dict[str, object]]] = []

            def answer(question, context, history, current_settings, **kwargs):
                del question, context, current_settings, kwargs
                histories.append(history)
                return ConversationAnswer(
                    answer="广告声称产品具有保湿作用。",
                    evidence_refs=["ocr_cluster_1"],
                    epistemic_status="grounded",
                )

            with (
                patch("ad_vista_agent.agent.chat._session_state", return_value=state),
                patch(
                    "ad_vista_agent.agent.chat._context",
                    return_value=({"ocr_clusters": []}, {"ocr_cluster_1"}),
                ),
                patch("ad_vista_agent.agent.chat._qwen_answer", side_effect=answer),
            ):
                first = ask_agent("run_1", "问题一", settings)
                second = ask_agent("run_1", "问题二", settings)

            self.assertEqual(histories[0], [])
            self.assertEqual([item["role"] for item in histories[1]], ["user", "assistant"])
            self.assertNotEqual(first["version_id"], second["version_id"])
            with sqlite3.connect(root / "memory" / "conversations.sqlite3") as connection:
                message_count = connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
                version_count = connection.execute("SELECT COUNT(*) FROM answer_versions").fetchone()[0]
            self.assertEqual(message_count, 4)
            self.assertEqual(version_count, 2)

    def test_store_preserves_message_order_and_feedback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(Path(directory) / "memory.sqlite3")
            store.create_session(
                session_id="session_1",
                run_id="run_1",
                source_path=Path("ad.mp4"),
                goal="分析卖点",
            )
            first = store.add_message("session_1", "user", "问题一", [])
            second = store.add_message("session_1", "assistant", "回答一", ["ocr_cluster_1"])
            store.add_version("session_1", second, {"answer": "回答一"})
            store.add_feedback("session_1", "revise", "请更保守", second)
            messages = store.messages("session_1")
            self.assertEqual([item["message_id"] for item in messages], [first, second])
            self.assertEqual(messages[1]["citations"], ["ocr_cluster_1"])
            self.assertEqual(store.session("session_1")["status"], "active")

    def test_store_rejects_conflicting_session_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(Path(directory) / "memory.sqlite3")
            store.create_session(
                session_id="session_1",
                run_id="exec_1",
                source_path=Path("ad.mp4"),
                goal="分析卖点",
            )
            with self.assertRaisesRegex(ValueError, "metadata conflict"):
                store.create_session(
                    session_id="session_1",
                    run_id="exec_2",
                    source_path=Path("ad.mp4"),
                    goal="分析风险",
                )

    def test_rejects_unknown_citation(self) -> None:
        answer = ConversationAnswer(
            answer="广告声称产品可以改善皮肤。",
            evidence_refs=["invented"],
            epistemic_status="grounded",
        )
        with self.assertRaisesRegex(ValueError, "unknown evidence"):
            validate_conversation_answer(answer, {"ocr_cluster_1"})

    def test_visual_answer_rejects_non_keyframe_citation(self) -> None:
        answer = ConversationAnswer(
            answer="画面中的产品是蓝色。",
            evidence_refs=["ocr_cluster_1"],
            epistemic_status="visual",
        )
        with self.assertRaisesRegex(ValueError, "presented keyframes"):
            validate_conversation_answer(answer, {"ocr_cluster_1"})

    def test_unknown_answer_must_be_explicit(self) -> None:
        answer = ConversationAnswer(answer="没有资料", epistemic_status="unknown")
        with self.assertRaisesRegex(ValueError, "当前证据不足"):
            validate_conversation_answer(answer, set())

    def test_normalizes_unknown_answer_without_changing_evidence(self) -> None:
        answer = ConversationAnswer(
            answer="没有资料",
            epistemic_status="unknown",
            unsupported_points=["未提供价格"],
        )
        normalized = normalize_conversation_answer(answer)
        self.assertEqual(normalized.answer, "当前证据不足：没有资料")
        self.assertEqual(normalized.evidence_refs, [])
        self.assertEqual(normalized.unsupported_points, ["未提供价格"])
        validate_conversation_answer(normalized, set())

    def test_downgrades_grounded_answer_without_citation(self) -> None:
        answer = ConversationAnswer(
            answer="无法从当前结果确认。",
            epistemic_status="grounded",
        )
        normalized = normalize_conversation_answer(answer)
        self.assertEqual(normalized.epistemic_status, "unknown")
        self.assertTrue(normalized.answer.startswith("当前证据不足"))
        validate_conversation_answer(normalized, set())

    def test_unknown_with_valid_reference_stays_unknown(self) -> None:
        answer = ConversationAnswer(
            answer="部分信息可以确认，但材质无法确认。",
            evidence_refs=["kf_0001_primary"],
            epistemic_status="unknown",
            unsupported_points=["真实材质无法仅凭画面确认"],
        )
        normalized = normalize_conversation_answer(answer)
        self.assertEqual(normalized.epistemic_status, "unknown")
        self.assertEqual(normalized.evidence_refs, [])
        self.assertTrue(normalized.answer.startswith("当前证据不足"))
        validate_conversation_answer(normalized, {"kf_0001_primary"})

    def test_conversational_answer_needs_no_evidence(self) -> None:
        answer = ConversationAnswer(answer="你好，我可以帮助分析当前视频。", epistemic_status="conversational")
        validate_conversation_answer(answer, set())
        self.assertTrue(_is_greeting("你好！"))

    def test_visual_questions_are_detected(self) -> None:
        self.assertTrue(_needs_visual_context("这个鞋子有什么颜色？"))
        self.assertTrue(_needs_visual_context("它的材质是什么？"))
        self.assertFalse(_needs_visual_context("广告的核心卖点是什么？"))

    def test_creative_request_renders_complete_script(self) -> None:
        package = CreativePackage(
            hooks=[CreativeHook(text="开场", angle="利益点", evidence_refs=["speech_1"])],
            script=CreativeScript(
                title="摇椅广告脚本",
                target_audience="家居用户",
                scenes=[
                    ScriptScene(
                        order=1,
                        duration_seconds=5,
                        narration="无需工具即可组装。",
                        visual_direction="展示组装过程。",
                        on_screen_text="轻松组装",
                        evidence_refs=["speech_1"],
                    )
                ],
                call_to_action="立即了解",
            ),
            storyboard=CreativeStoryboard(
                frames=[
                    StoryboardFrame(
                        order=1,
                        shot_description="产品镜头",
                        camera_direction="固定镜头",
                        evidence_refs=["speech_1"],
                    )
                ]
            ),
            ab_variants=[
                ABVariant(name="A", hypothesis="A", hook_text="A", change="A", success_metric="CTR", evidence_refs=["speech_1"]),
                ABVariant(name="B", hypothesis="B", hook_text="B", change="B", success_metric="CTR", evidence_refs=["speech_1"]),
            ],
        )
        answer = _render_creative_answer(package)
        self.assertTrue(_is_creative_request("所以你的脚本在哪里？"))
        self.assertIn("## 镜头 1", answer.answer)
        self.assertIn("**旁白：** 无需工具即可组装。", answer.answer)
        self.assertEqual(answer.evidence_refs, ["speech_1"])

    def test_grounded_answer_requires_citation(self) -> None:
        answer = ConversationAnswer(answer="广告展示了产品。", epistemic_status="grounded")
        with self.assertRaisesRegex(ValueError, "must cite"):
            validate_conversation_answer(answer, {"ocr_cluster_1"})

    def test_does_not_repair_grounded_answer_from_lexical_overlap(self) -> None:
        answer = ConversationAnswer(
            answer="这是一个超大号摇椅广告。",
            epistemic_status="grounded",
        )
        repaired = repair_grounded_references(
            answer,
            "这个广告是卖什么的？",
            {
                "validated_insights": [
                    {
                        "claim": "广告展示 TEMU 超大号摇椅",
                        "evidence_refs": ["ocr_cluster_0001", "speech_0001"],
                    }
                ],
                "speech_evidence": [],
                "ocr_clusters": [],
            },
        )
        self.assertEqual(repaired.evidence_refs, [])

    def test_answer_tracks_unsupported_points_separately(self) -> None:
        answer = ConversationAnswer(
            answer="广告声称产品具有保湿作用。",
            evidence_refs=["ocr_cluster_1"],
            epistemic_status="grounded",
            unsupported_points=["当前证据未提供临床测试数据"],
        )
        validate_conversation_answer(answer, {"ocr_cluster_1"})
        self.assertEqual(len(answer.unsupported_points), 1)

    def test_streamed_answer_matches_persisted_answer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = load_settings(DEFAULT_CONFIG).model_copy(
                update={
                    "paths": load_settings(DEFAULT_CONFIG).paths.model_copy(
                        update={"output_root": root}
                    )
                }
            )
            state = SimpleNamespace(
                session_id="session_stream",
                run_id="run_stream",
                source_path=Path("ad.mp4"),
                request=SimpleNamespace(goal="分析卖点"),
            )
            deltas: list[str] = []
            answer = ConversationAnswer(
                answer="广告明确展示了折扣信息。",
                evidence_refs=["ocr_cluster_1"],
                epistemic_status="grounded",
            )
            with (
                patch("ad_vista_agent.agent.chat._session_state", return_value=state),
                patch(
                    "ad_vista_agent.agent.chat._context",
                    return_value=({"ocr_clusters": []}, {"ocr_cluster_1"}),
                ),
                patch("ad_vista_agent.agent.chat._qwen_answer", return_value=answer),
            ):
                result = ask_agent(
                    "run_stream",
                    "广告有什么优惠？",
                    settings,
                    on_delta=deltas.append,
                )
            messages = ConversationStore(
                root / "memory" / "conversations.sqlite3"
            ).messages("session_stream")
            self.assertEqual("".join(deltas), result["answer"])
            self.assertEqual(messages[-1]["content"], result["answer"])


if __name__ == "__main__":
    unittest.main()
