from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
import statistics
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = Path(os.environ.get("MAC3108_DATA_ROOT", PROJECT_ROOT / "data" / "mac3108"))
QUESTIONS = Path(os.environ.get("MAC3108_QUESTIONS", DATA_ROOT / "MAC_QA.jsonl"))
VIDEOS = Path(os.environ.get("MAC3108_VIDEOS", DATA_ROOT / "videos"))
BASELINE = Path(os.environ.get("MAC3108_BASELINE", DATA_ROOT / "baseline"))
ASR = Path(os.environ.get("MAC3108_ASR", DATA_ROOT / "audio_to_text.jsonl"))
VISUAL = Path(os.environ.get("MAC3108_VISUAL", DATA_ROOT / "visual_observations.jsonl"))
MUSIC = Path(os.environ.get("MAC3108_MUSIC", DATA_ROOT / "music_analyses.jsonl"))
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "benchmarks" / "mac3108"
POINT_PATTERN = re.compile(r"(?m)^\s*(\d+)\.\s+\S")


@dataclass(frozen=True)
class CheckpointStore:
    root: Path

    def write(self, kind: str, item_id: str, value: dict[str, Any]) -> None:
        directory = self.root / "items" / kind
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{item_id}.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(target)

    def load(self, kind: str) -> list[dict[str, Any]]:
        directory = self.root / "items" / kind
        if not directory.is_dir():
            return []
        return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(directory.glob("*.json"))]


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError(f"Expected JSON object in {path}")
                rows.append(value)
    return rows


def index_rows(path: Path, *, id_field: str = "id") -> dict[str, dict[str, Any]]:
    result = {}
    for row in load_jsonl(path):
        item_id = str(row[id_field])
        if item_id in result:
            raise ValueError(f"Duplicate ID in {path}: {item_id}")
        result[item_id] = row
    return result


def transcript_index(path: Path) -> dict[str, list[dict[str, Any]]]:
    result = {}
    for row in load_jsonl(path):
        item_id = Path(str(row.get("video", row.get("id", "")))).stem
        if item_id:
            result[item_id] = list(row.get("segments") or row.get("audio_to_text") or [])
    return result


def clean_transcript(segments: list[dict[str, Any]]) -> tuple[str, str]:
    lines = []
    for segment in segments:
        text = re.sub(r"\s+", " ", str(segment.get("text") or "")).strip()
        if text:
            lines.append(
                f"[{float(segment.get('start', 0)):.1f}-{float(segment.get('end', 0)):.1f}] {text}"
            )
    transcript = "\n".join(lines)[:12000]
    words = len(transcript.split())
    return ("low" if words <= 2 else "medium"), transcript or "No usable automatic transcript is available."


def question_needs_music(question: str) -> bool:
    return bool(re.search(r"\b(music|sound|audio|mood|atmosphere|rhythm|beat|audiovisual)\b", question, re.I))


def extract_answer(message: dict[str, Any]) -> str:
    content = str(message.get("content") or "").strip()
    if "</think>" in content:
        content = content.rsplit("</think>", 1)[-1].strip()
    return content


def answer_issues(answer: str) -> list[str]:
    value = answer.strip()
    issues = []
    if not value.startswith("1. "):
        issues.append("answer must start directly with 1.")
    numbers = [int(value) for value in POINT_PATTERN.findall(answer)]
    if not numbers or numbers != list(range(1, len(numbers) + 1)):
        issues.append("numbering must be continuous")
    if len(numbers) > 4:
        issues.append("answer has more than four points")
    if re.search(r"```|^\s*[-*]\s+|\*\*", answer, re.M):
        issues.append("answer contains Markdown")
    return issues


def normalize_answer(answer: str) -> str:
    return re.sub(r"\s+", " ", answer.strip().casefold())


def answer_tokens(answer: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", answer.casefold())


def token_f1(candidate: str, reference: str) -> float:
    candidate_counts = Counter(answer_tokens(candidate))
    reference_counts = Counter(answer_tokens(reference))
    overlap = sum((candidate_counts & reference_counts).values())
    candidate_total = sum(candidate_counts.values())
    reference_total = sum(reference_counts.values())
    if not candidate_total or not reference_total or not overlap:
        return 0.0
    precision = overlap / candidate_total
    recall = overlap / reference_total
    return 2 * precision * recall / (precision + recall)


def point_count(answer: str) -> int:
    return len(POINT_PATTERN.findall(answer))


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(quantile * len(ordered)) - 1))
    return ordered[index]


def stable_rank(item_id: str, seed: str) -> str:
    return hashlib.sha256(f"{seed}:{item_id}".encode()).hexdigest()


def select_items(rows: list[dict[str, Any]], size: int | None, seed: str) -> list[dict[str, Any]]:
    if size is None or size >= len(rows):
        return rows
    bins: dict[str, list[dict[str, Any]]] = {"le15": [], "15to30": [], "30to60": [], "60to120": [], "gt120": []}
    for row in rows:
        duration = float(row.get("duration", 0))
        name = "le15" if duration <= 15 else "15to30" if duration <= 30 else "30to60" if duration <= 60 else "60to120" if duration <= 120 else "gt120"
        bins[name].append(row)
    selected = []
    nonempty = [name for name, values in bins.items() if values]
    base, remainder = divmod(size, len(nonempty))
    for position, name in enumerate(nonempty):
        count = base + (position < remainder)
        selected.extend(sorted(bins[name], key=lambda row: stable_rank(str(row["id"]), seed))[:count])
    selected_ids = {str(row["id"]) for row in selected}
    if len(selected) < size:
        remaining = sorted(
            (row for row in rows if str(row["id"]) not in selected_ids),
            key=lambda row: stable_rank(str(row["id"]), seed),
        )
        selected.extend(remaining[: size - len(selected)])
    return sorted(selected, key=lambda row: stable_rank(str(row["id"]), seed))


def load_items(sample_manifest: Path | None, sample_size: int | None, seed: str) -> list[dict[str, Any]]:
    questions = load_jsonl(QUESTIONS)
    by_id = {str(row["id"]): row for row in questions}
    if sample_manifest:
        manifest = load_jsonl(sample_manifest)
        rows = []
        for item in manifest:
            item_id = str(item["id"])
            row = dict(by_id[item_id])
            row["duration"] = float(item.get("duration", item.get("duration_seconds", 0)))
            rows.append(row)
        return select_items(rows, sample_size, seed)
    rows = [dict(row, duration=0.0) for row in questions]
    return select_items(rows, sample_size, seed)


def lowest_prompt_ids(results_dir: Path, count: int) -> set[str]:
    rows = [json.loads(path.read_text(encoding="utf-8")) for path in results_dir.glob("*.json")]
    ranked = sorted(rows, key=lambda row: (int(row["usage"]["prompt_tokens"]), str(row["id"])))
    return {str(row["id"]) for row in ranked[:count]}


def build_user_text(
    row: dict[str, Any],
    transcripts: dict[str, list[dict[str, Any]]],
    visual: dict[str, dict[str, Any]],
    music: dict[str, dict[str, Any]],
) -> str:
    item_id = str(row["id"])
    reliability, transcript = clean_transcript(transcripts[item_id])
    observation = visual[item_id]["visual_observation"]
    music_context = ""
    if question_needs_music(str(row["question"])):
        music_context = "\n\nOFFLINE MUSIC ANALYSIS (model-generated, unverified; background music only)\n" + json.dumps(music[item_id]["music_analysis"], ensure_ascii=False)
    return (
        f"QUESTION\n{row['question']}\n\n"
        f"SPEECH TRANSCRIPT (automatic, reliability: {reliability})\n{transcript}\n\n"
        "OFFLINE VISUAL ANALYSIS (model-generated, unverified)\n"
        f"{json.dumps(observation, ensure_ascii=False)}{music_context}\n\n"
        "Answer the QUESTION using the original video and the supporting offline results above."
    )


async def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = args.output_root / args.experiment
    output_dir.mkdir(parents=True, exist_ok=args.resume)
    checkpoints = CheckpointStore(output_dir)
    sample_manifest = None if args.full else args.sample_manifest
    sample_size = None if args.full else args.sample_size
    items = load_items(sample_manifest, sample_size, args.seed)
    if args.lowest_prompt_results:
        selected_ids = lowest_prompt_ids(args.lowest_prompt_results, args.lowest_prompt_count)
        items = [item for item in items if str(item["id"]) in selected_ids]
    if args.order == "duration":
        items.sort(key=lambda row: (float(row.get("duration", 0)), str(row["id"])))
    transcripts = transcript_index(ASR)
    visual = index_rows(VISUAL)
    music = index_rows(MUSIC)
    official = index_rows(BASELINE / "answer" / "Qwen3.5-9B-Base-Agent-v3.jsonl")
    system = (BASELINE / "prompts" / "final_answer.txt").read_text(encoding="utf-8").strip()
    semaphore = asyncio.Semaphore(args.concurrency)
    timeout = aiohttp.ClientTimeout(total=args.timeout)
    results: list[dict[str, Any]] = checkpoints.load("results") if args.resume else []
    errors: list[dict[str, Any]] = checkpoints.load("errors") if args.resume else []
    completed_ids = {str(row["id"]) for row in results}
    if args.resume:
        items = [item for item in items if str(item["id"]) not in completed_ids]
        errors = []
    lock = asyncio.Lock()
    started = time.perf_counter()

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async def request(item: dict[str, Any], user_text: str, seed: int) -> tuple[str, dict[str, Any], float, float, int]:
            payload = {
                "model": args.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": [
                        {"type": "video_url", "video_url": {"url": f"file://{VIDEOS / (str(item['id']) + '.mp4')}"}},
                        {"type": "text", "text": user_text},
                    ]},
                ],
                "temperature": 0,
                "top_p": 1.0,
                "repetition_penalty": 1.0,
                "max_tokens": args.max_tokens,
                "seed": seed,
                "chat_template_kwargs": {"enable_thinking": False},
            }
            if args.num_frames is not None:
                payload["media_io_kwargs"] = {"video": {"num_frames": args.num_frames}}
            last_error = None
            for attempt in range(1, args.retries + 1):
                try:
                    queued_at = time.perf_counter()
                    async with semaphore:
                        queue_seconds = time.perf_counter() - queued_at
                        request_started = time.perf_counter()
                        async with session.post(args.url.rstrip("/") + "/chat/completions", json=payload) as response:
                            body = await response.text()
                        service_seconds = time.perf_counter() - request_started
                    if response.status != 200:
                        raise RuntimeError(f"HTTP {response.status}: {body[:500]}")
                    data = json.loads(body)
                    return extract_answer(data["choices"][0]["message"]), dict(data.get("usage") or {}), queue_seconds, service_seconds, attempt
                except Exception as exc:
                    last_error = exc
                    if attempt < args.retries:
                        await asyncio.sleep(attempt)
            raise RuntimeError(str(last_error))

        async def worker(position: int, item: dict[str, Any]) -> None:
            item_id = str(item["id"])
            item_started = time.perf_counter()
            request_count = 0
            transport_attempts = 0
            usages = Counter()
            try:
                user_text = build_user_text(item, transcripts, visual, music)
                answer, usage, first_queue, first_service, attempts = await request(item, user_text, 42)
                request_count += 1
                transport_attempts += attempts
                usages.update({key: int(value) for key, value in usage.items() if isinstance(value, int)})
                queue_times = [first_queue]
                service_times = [first_service]
                issues = answer_issues(answer)
                for repair_index in range(2):
                    if not issues:
                        break
                    repair = (
                        f"Rewrite the rejected answer below so it follows the output format exactly.\n\nQUESTION\n{item['question']}\n\n"
                        f"REJECTED ANSWER\n{answer}\n\nVALIDATION ERRORS\n{'; '.join(issues)}\n\n"
                        "Keep only the strongest supported content. Merge overlapping items. Output no more than 4 numbered points, "
                        "one point per line, starting directly with 1. Do not add any new claim, heading, explanation, or conclusion."
                    )
                    answer, usage, queue_seconds, service_seconds, attempts = await request(item, repair, 43 + repair_index)
                    request_count += 1
                    transport_attempts += attempts
                    usages.update({key: int(value) for key, value in usage.items() if isinstance(value, int)})
                    queue_times.append(queue_seconds)
                    service_times.append(service_seconds)
                    issues = answer_issues(answer)
                official_answer = str(official[item_id]["model_prediction"])
                result = {
                    "position": position,
                    "id": item_id,
                    "duration_seconds": float(item.get("duration", 0)),
                    "model_prediction": answer,
                    "valid": not issues,
                    "issues": issues,
                    "request_count": request_count,
                    "transport_attempts": transport_attempts,
                    "queue_seconds": queue_times,
                    "service_seconds": service_times,
                    "item_seconds": time.perf_counter() - item_started,
                    "usage": dict(usages),
                    "official_normalized_match": normalize_answer(answer) == normalize_answer(official_answer),
                    "official_token_f1": token_f1(answer, official_answer),
                    "official_point_count_match": point_count(answer) == point_count(official_answer),
                }
                async with lock:
                    results.append(result)
                    checkpoints.write("results", item_id, result)
            except Exception as exc:
                error = {"position": position, "id": item_id, "error": str(exc), "item_seconds": time.perf_counter() - item_started}
                async with lock:
                    errors.append(error)
                    checkpoints.write("errors", item_id, error)

        await asyncio.gather(*(worker(position, item) for position, item in enumerate(items)))

    wall_seconds = time.perf_counter() - started
    results.sort(key=lambda row: int(row["position"]))
    errors.sort(key=lambda row: int(row["position"]))
    queue_times = [latency for row in results for latency in row["queue_seconds"]]
    service_times = [latency for row in results for latency in row["service_seconds"]]
    item_latencies = [float(row["item_seconds"]) for row in results]
    total_requests = sum(int(row["request_count"]) for row in results)
    summary = {
        "experiment": args.experiment,
        "requested_items": len(items) + len(completed_ids),
        "executed_items": len(items),
        "resumed_items": len(completed_ids),
        "completed_items": len(results),
        "failed_items": len(errors),
        "valid_items": sum(bool(row["valid"]) for row in results),
        "format_pass_rate": sum(bool(row["valid"]) for row in results) / len(results) if results else 0,
        "official_normalized_match_rate": sum(bool(row["official_normalized_match"]) for row in results) / len(results) if results else 0,
        "official_token_f1_mean": statistics.fmean(float(row["official_token_f1"]) for row in results) if results else 0,
        "official_point_count_match_rate": sum(bool(row["official_point_count_match"]) for row in results) / len(results) if results else 0,
        "wall_seconds": wall_seconds,
        "items_per_second": len(results) / wall_seconds if wall_seconds else 0,
        "items_per_minute": 60 * len(results) / wall_seconds if wall_seconds else 0,
        "request_count": total_requests,
        "repair_request_count": total_requests - len(results),
        "transport_attempt_count": sum(int(row["transport_attempts"]) for row in results),
        "item_latency_seconds": {
            "mean": statistics.fmean(item_latencies) if item_latencies else 0,
            "p50": percentile(item_latencies, 0.5),
            "p95": percentile(item_latencies, 0.95),
        },
        "queue_seconds": {
            "mean": statistics.fmean(queue_times) if queue_times else 0,
            "p50": percentile(queue_times, 0.5),
            "p95": percentile(queue_times, 0.95),
        },
        "service_seconds": {
            "mean": statistics.fmean(service_times) if service_times else 0,
            "p50": percentile(service_times, 0.5),
            "p95": percentile(service_times, 0.95),
        },
        "usage": dict(sum((Counter(row["usage"]) for row in results), Counter())),
        "configuration": {
            "url": args.url,
            "model": args.model,
            "concurrency": args.concurrency,
            "max_tokens": args.max_tokens,
            "num_frames": args.num_frames,
            "sample_manifest": str(sample_manifest) if sample_manifest else None,
            "sample_size": sample_size,
            "full": args.full,
            "lowest_prompt_results": str(args.lowest_prompt_results) if args.lowest_prompt_results else None,
            "lowest_prompt_count": args.lowest_prompt_count if args.lowest_prompt_results else None,
            "seed": args.seed,
            "order": args.order,
        },
    }
    (output_dir / "results.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in results), encoding="utf-8")
    (output_dir / "errors.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in errors), encoding="utf-8")
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark the frozen MAC 3108 V3 answer task")
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--url", default="http://127.0.0.1:8000/v1")
    parser.add_argument("--model", default="AdInsight-RL")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--max-tokens", type=int, default=384)
    parser.add_argument("--num-frames", type=int)
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--sample-manifest", type=Path, default=DATA_ROOT / "dev300.jsonl")
    parser.add_argument("--sample-size", type=int, default=128)
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--lowest-prompt-results", type=Path)
    parser.add_argument("--lowest-prompt-count", type=int, default=32)
    parser.add_argument("--seed", default="advista-inference-optimization-v1")
    parser.add_argument("--order", choices=("stable", "duration"), default="stable")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    return parser.parse_args()


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run_benchmark(parse_args())), ensure_ascii=False, indent=2))
