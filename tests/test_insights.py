import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from ad_vista_agent.config import DEFAULT_CONFIG, load_settings
from ad_vista_agent.insights.builder import _cache_payload, _extract_json, _model_identity
from ad_vista_agent.runtime.stage_cache import stage_cache_key
from ad_vista_agent.insights.grounding import grounded_executive_summary, validate_analysis_grounding
from ad_vista_agent.schemas import (
    AgentRequest,
    EpistemicStatus,
    GroundedMarketingInsight,
    MarketingAnalysis,
    MarketingDimension,
)


def analysis(refs: list[str]) -> MarketingAnalysis:
    return MarketingAnalysis(
        asset_id="asset_1",
        language="zh-CN",
        subject="产品",
        executive_summary="摘要",
        insights=[
            GroundedMarketingInsight(
                insight_id="insight_0001",
                dimension=MarketingDimension.SELLING_POINT,
                claim="卖点",
                evidence_refs=refs,
                confidence=0.8,
                epistemic_status=EpistemicStatus.STATED_BY_AD,
                reasoning_summary="证据支持",
            )
        ],
        unknowns=["当前 Ledger 无证据表明存在价格信息。"],
    )


class InsightGroundingTests(unittest.TestCase):
    def test_accepts_allowed_reference(self) -> None:
        validate_analysis_grounding(
            analysis(["ocr_cluster_0001"]),
            asset_id="asset_1",
            allowed_references={"ocr_cluster_0001"},
            max_per_dimension=5,
        )

    def test_rejects_unknown_reference(self) -> None:
        with self.assertRaises(ValueError):
            validate_analysis_grounding(
                analysis(["invented_id"]),
                asset_id="asset_1",
                allowed_references={"ocr_cluster_0001"},
                max_per_dimension=5,
            )

    def test_schema_requires_evidence_reference(self) -> None:
        with self.assertRaises(ValidationError):
            GroundedMarketingInsight(
                insight_id="insight_0001",
                dimension=MarketingDimension.SELLING_POINT,
                claim="卖点",
                evidence_refs=[],
                confidence=0.8,
                epistemic_status=EpistemicStatus.STATED_BY_AD,
                reasoning_summary="证据支持",
            )

    def test_rejects_any_id_outside_reference_field(self) -> None:
        value = analysis(["ocr_cluster_0001"])
        value.insights[0].reasoning_summary = "另见 ocr_cluster_0002"
        with self.assertRaises(ValueError):
            validate_analysis_grounding(
                value,
                asset_id="asset_1",
                allowed_references={"ocr_cluster_0001", "ocr_cluster_0002"},
                max_per_dimension=5,
            )

    def test_schema_rejects_confidence_above_point_eight_five(self) -> None:
        with self.assertRaises(ValidationError):
            GroundedMarketingInsight(
                insight_id="insight_0001",
                dimension=MarketingDimension.AUDIENCE,
                claim="受众",
                evidence_refs=["ocr_cluster_0001"],
                confidence=0.86,
                epistemic_status=EpistemicStatus.INFERRED_FROM_AD,
                reasoning_summary="推断",
            )

    def test_summary_is_built_only_from_validated_claims(self) -> None:
        value = analysis(["ocr_cluster_0001"])
        value.executive_summary = "unsupported free-form summary"
        summary = grounded_executive_summary(value)
        self.assertIn("卖点", summary)
        self.assertNotIn("unsupported", summary)


class InsightJsonTests(unittest.TestCase):
    def test_model_identity_accepts_transformers_shard_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.json").write_text("{}", encoding="utf-8")
            (root / "model.safetensors.index.json").write_text("{}", encoding="utf-8")
            (root / "model-00001-of-00002.safetensors").write_bytes(b"one")
            (root / "model-00002-of-00002.safetensors").write_bytes(b"two")
            identity = _model_identity(root)
            shards = identity["shards"]
            self.assertIsInstance(shards, list)
            assert isinstance(shards, list)
            self.assertEqual(
                [item["name"] for item in shards],
                ["model-00001-of-00002.safetensors", "model-00002-of-00002.safetensors"],
            )

    def test_extracts_fenced_json(self) -> None:
        self.assertEqual(_extract_json('```json\n{"a": 1}\n```'), {"a": 1})

    def test_rejects_non_json(self) -> None:
        with self.assertRaises(ValueError):
            _extract_json("not json")

    def test_task_goal_and_mode_are_part_of_insight_cache_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = root / "ledger.json"
            prompt = root / "prompt.txt"
            ledger.write_text("{}", encoding="utf-8")
            prompt.write_text("prompt", encoding="utf-8")
            settings = load_settings(DEFAULT_CONFIG)
            first = AgentRequest(goal="分析卖点", mode="quick", deliverables=["insights"])
            second = AgentRequest(goal="分析转化路径", mode="deep", deliverables=["insights"])
            with patch(
                "ad_vista_agent.insights.builder._model_identity",
                return_value={"model": "test"},
            ):
                first_payload = _cache_payload(
                    settings,
                    ledger_paths=[ledger],
                    prompt_path=prompt,
                    request=first,
                    max_insights_per_dimension=3,
                )
                second_payload = _cache_payload(
                    settings,
                    ledger_paths=[ledger],
                    prompt_path=prompt,
                    request=second,
                    max_insights_per_dimension=5,
                )
            self.assertNotEqual(stage_cache_key(first_payload), stage_cache_key(second_payload))
            self.assertEqual(second_payload["task"]["goal"], "分析转化路径")
            self.assertEqual(second_payload["task"]["mode"], "deep")


if __name__ == "__main__":
    unittest.main()
