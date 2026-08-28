from __future__ import annotations

import argparse
import json
from importlib.metadata import version
from pathlib import Path

from vllm import LLM, SamplingParams
from vllm.sampling_params import StructuredOutputsParams


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    args = parser.parse_args()
    request = json.loads(args.request.read_text(encoding="utf-8"))
    model = LLM(
        model=request["model_path"],
        dtype="bfloat16",
        trust_remote_code=True,
        gpu_memory_utilization=float(request["gpu_memory_utilization"]),
        max_model_len=int(request["max_model_len"]),
        max_num_seqs=1,
        enforce_eager=True,
        enable_prefix_caching=False,
    )
    sampling = SamplingParams(
        temperature=float(request["temperature"]),
        max_tokens=int(request["max_tokens"]),
        seed=42,
        structured_outputs=StructuredOutputsParams(json=request["output_schema"]),
    )
    outputs = model.chat(
        request["messages"],
        sampling_params=sampling,
        use_tqdm=False,
        chat_template_kwargs={"enable_thinking": False},
    )
    attempts = [outputs[0].outputs[0].text]
    try:
        json.loads(attempts[0])
    except json.JSONDecodeError:
        repair_messages = [
            {
                "role": "system",
                "content": (
                    "You repair JSON syntax only. Preserve every claim, ID, confidence, status, "
                    "and list item from the supplied draft. Return one valid JSON object matching "
                    "the required schema. Do not add evidence or analysis."
                ),
            },
            {"role": "user", "content": attempts[0]},
        ]
        repaired = model.chat(
            repair_messages,
            sampling_params=sampling,
            use_tqdm=False,
            chat_template_kwargs={"enable_thinking": False},
        )
        attempts.append(repaired[0].outputs[0].text)
        outputs = repaired
    response = {
        "text": outputs[0].outputs[0].text,
        "attempts": attempts,
        "attempt_count": len(attempts),
        "prompt_tokens": len(outputs[0].prompt_token_ids),
        "completion_tokens": len(outputs[0].outputs[0].token_ids),
        "versions": {
            "vllm": version("vllm"),
            "torch": version("torch"),
            "transformers": version("transformers"),
        },
    }
    Path(request["response_path"]).write_text(
        json.dumps(response, ensure_ascii=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
