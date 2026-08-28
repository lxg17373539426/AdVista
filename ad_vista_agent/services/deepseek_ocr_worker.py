from __future__ import annotations

import argparse
import contextlib
import json
import sys
from importlib.metadata import version
from pathlib import Path

import torch
from transformers import AutoModel, AutoTokenizer


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    args = parser.parse_args()
    request = json.loads(args.request.read_text(encoding="utf-8"))

    tokenizer = AutoTokenizer.from_pretrained(
        request["model_path"],
        trust_remote_code=True,
        local_files_only=True,
    )
    model = AutoModel.from_pretrained(
        request["model_path"],
        _attn_implementation="flash_attention_2",
        trust_remote_code=True,
        use_safetensors=True,
        local_files_only=True,
        torch_dtype=torch.bfloat16,
    )
    model = model.eval().cuda().to(torch.bfloat16)
    items = []
    output_root = Path(request["output_root"])
    output_root.mkdir(parents=True, exist_ok=True)
    for item in request["images"]:
        with contextlib.redirect_stdout(sys.stderr):
            output = model.infer(
                tokenizer,
                prompt=request["prompt"],
                image_file=item["path"],
                output_path=str(output_root / item["keyframe_id"]),
                base_size=int(request.get("base_size", 1024)),
                image_size=int(request.get("image_size", 640)),
                crop_mode=bool(request.get("crop_mode", True)),
                save_results=False,
                test_compress=False,
                eval_mode=True,
            )
        items.append({"keyframe_id": item["keyframe_id"], "text": output})
    response = {
        "items": items,
        "versions": {
            "torch": version("torch"),
            "transformers": version("transformers"),
            "flash_attn": version("flash-attn"),
        },
    }
    json.dump(response, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
