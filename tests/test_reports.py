import tempfile
import unittest
from pathlib import Path
from html.parser import HTMLParser

from ad_vista_agent.reports.critic import audit_analysis, expand_reference
from ad_vista_agent.reports.render import render_html_report, render_markdown_report
from ad_vista_agent.runtime import sha256_file
from ad_vista_agent.schemas import (
    BoundingBox,
    EpistemicStatus,
    Evidence,
    EvidenceCluster,
    EvidenceModality,
    GroundedMarketingInsight,
    InsightAuditStatus,
    MarketingAnalysis,
    MarketingDimension,
)


def make_evidence(root: Path, *, content: str = "健康风险") -> Evidence:
    artifact = root / "timeline" / "frame.jpg"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(b"image")
    return Evidence(
        evidence_id="ocr_0001",
        asset_id="asset_1",
        modality=EvidenceModality.OCR,
        start_ms=100,
        end_ms=100,
        content=content,
        confidence=0.9,
        epistemic_status=EpistemicStatus.OBSERVED,
        tool="test",
        artifact_path=Path("timeline/frame.jpg"),
        artifact_hash=sha256_file(artifact),
        region=BoundingBox(x1=0.1, y1=0.1, x2=0.9, y2=0.2),
    )


def make_analysis(*, claim: str = "存在健康风险") -> MarketingAnalysis:
    return MarketingAnalysis(
        asset_id="asset_1",
        language="zh-CN",
        subject="<script>alert(1)</script>",
        executive_summary="摘要",
        insights=[
            GroundedMarketingInsight(
                insight_id="RISK_001",
                dimension=MarketingDimension.RISK,
                claim=claim,
                evidence_refs=["ocr_cluster_0001"],
                confidence=0.8,
                epistemic_status=EpistemicStatus.STATED_BY_AD,
                reasoning_summary="广告风险声明",
            )
        ],
        unknowns=["当前 Ledger 无证据表明存在价格。"],
    )


def make_cluster() -> EvidenceCluster:
    return EvidenceCluster(
        cluster_id="ocr_cluster_0001",
        asset_id="asset_1",
        modality=EvidenceModality.OCR,
        canonical_content="健康风险",
        start_ms=100,
        end_ms=100,
        representative_evidence_id="ocr_0001",
        member_evidence_ids=["ocr_0001"],
        confidence=0.9,
    )


class CriticTests(unittest.TestCase):
    def test_expands_cluster_to_representative_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = make_evidence(root)
            expanded = expand_reference(
                "ocr_cluster_0001",
                evidence_by_id={evidence.evidence_id: evidence},
                clusters_by_id={"ocr_cluster_0001": make_cluster()},
            )
            self.assertEqual(expanded.source_evidence_id, "ocr_0001")
            self.assertEqual(expanded.reference_id, "ocr_cluster_0001")

    def test_single_modality_risk_claim_requires_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = make_evidence(root)
            audit = audit_analysis(
                make_analysis(),
                evidence=[evidence],
                clusters=[make_cluster()],
                duration_ms=1000,
                run_dir=root,
            )
            self.assertEqual(audit.status, InsightAuditStatus.REVIEW)
            self.assertIn("single_modality_risk_claim", {item.code for item in audit.insights[0].findings})

    def test_artifact_hash_mismatch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = make_evidence(root).model_copy(update={"artifact_hash": "0" * 64})
            audit = audit_analysis(
                make_analysis(claim="普通广告主张"),
                evidence=[evidence],
                clusters=[make_cluster()],
                duration_ms=1000,
                run_dir=root,
            )
            self.assertEqual(audit.status, InsightAuditStatus.FAIL)


class ReportRenderingTests(unittest.TestCase):
    def test_html_escapes_model_text_and_embeds_image(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = make_evidence(root)
            analysis = make_analysis()
            audit = audit_analysis(
                analysis,
                evidence=[evidence],
                clusters=[make_cluster()],
                duration_ms=1000,
                run_dir=root,
            )
            output = render_html_report(
                analysis,
                audit,
                run_dir=root,
                embed_images=True,
                max_image_bytes=1000,
                max_evidence_per_insight=4,
            )
            self.assertIn("&lt;script&gt;", output)
            self.assertNotIn("<script>alert", output)
            self.assertIn("data:image/jpeg;base64", output)
            self.assertIn('name="viewport"', output)
            self.assertNotIn('src="http', output)
            self.assertIn('class="rail"', output)
            self.assertIn('class="scoreboard"', output)
            self.assertIn('class="status review"', output)
            self.assertIn('media print', output)
            parser = HTMLParser()
            parser.feed(output)
            parser.close()

    def test_markdown_contains_evidence_reference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = make_evidence(root)
            analysis = make_analysis()
            audit = audit_analysis(
                analysis,
                evidence=[evidence],
                clusters=[make_cluster()],
                duration_ms=1000,
                run_dir=root,
            )
            output = render_markdown_report(analysis, audit)
            self.assertIn("ocr_cluster_0001", output)
            self.assertIn("建议复核", output)


if __name__ == "__main__":
    unittest.main()
