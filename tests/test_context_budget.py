from ad_vista_agent.config import load_settings
from ad_vista_agent.context import (
    build_context_budget,
    compress_evidence_context,
    estimate_tokens,
)


def test_qwen_context_budget_reserves_output_and_protocol_tokens() -> None:
    budget = build_context_budget(load_settings())
    assert budget.max_model_tokens == 262144
    assert budget.soft_limit_tokens < budget.hard_limit_tokens < budget.input_limit_tokens


def test_context_compression_keeps_relevant_rows_and_records_omissions() -> None:
    budget = build_context_budget(load_settings(), output_tokens=256)
    context = {
        "speech_evidence": [{"id": f"speech_{i:04d}", "content": "无关内容"} for i in range(100)],
        "ocr_clusters": [{"id": f"ocr_cluster_{i:04d}", "content": "Real-Life Filter" if i == 99 else "其他"} for i in range(100)],
    }
    compressed, metadata = compress_evidence_context(
        context,
        "Real-Life Filter",
        budget.model_copy(update={"soft_limit_tokens": 1}),
    )
    assert any(row["id"] == "ocr_cluster_0099" for row in compressed["ocr_clusters"])
    assert metadata["strategy"] == "evidence_relevance_v1"
    assert estimate_tokens(compressed) <= budget.hard_limit_tokens


def test_nested_ledger_compression_updates_citation_rules() -> None:
    budget = build_context_budget(load_settings(), output_tokens=256)
    context = {
        "ledger": {
            "speech_evidence": [
                {"evidence_id": f"speech_{i:04d}", "content": "卖点" if i == 99 else "其他"}
                for i in range(100)
            ],
            "ocr_clusters": [
                {"cluster_id": f"ocr_cluster_{i:04d}", "content": "保湿" if i == 99 else "其他"}
                for i in range(100)
            ],
            "citation_rules": {
                "allowed_speech_ids": [f"speech_{i:04d}" for i in range(100)],
                "allowed_ocr_cluster_ids": [f"ocr_cluster_{i:04d}" for i in range(100)],
            },
        }
    }
    compressed, _ = compress_evidence_context(
        context,
        "卖点保湿",
        budget.model_copy(update={"soft_limit_tokens": 1}),
    )
    ledger = compressed["ledger"]
    assert "speech_0099" in ledger["citation_rules"]["allowed_speech_ids"]
    assert "ocr_cluster_0099" in ledger["citation_rules"]["allowed_ocr_cluster_ids"]
    assert len(ledger["citation_rules"]["allowed_speech_ids"]) == 40
    assert len(ledger["citation_rules"]["allowed_ocr_cluster_ids"]) == 80


def test_flat_chat_context_compression_preserves_citation_ids() -> None:
    budget = build_context_budget(load_settings(), output_tokens=256)
    context = {
        "speech_evidence": [
            {"id": f"speech_{i:04d}", "content": "轻便透气" if i == 99 else "其他"}
            for i in range(100)
        ],
        "ocr_clusters": [
            {"id": f"ocr_cluster_{i:04d}", "content": "多种尺码" if i == 99 else "其他"}
            for i in range(100)
        ],
        "citation_rules": {"allowed_speech_ids": [], "allowed_ocr_cluster_ids": []},
    }

    compressed, _ = compress_evidence_context(
        context,
        "轻便透气 多种尺码",
        budget.model_copy(update={"soft_limit_tokens": 1}),
    )

    assert "speech_0099" in compressed["citation_rules"]["allowed_speech_ids"]
    assert "ocr_cluster_0099" in compressed["citation_rules"]["allowed_ocr_cluster_ids"]
