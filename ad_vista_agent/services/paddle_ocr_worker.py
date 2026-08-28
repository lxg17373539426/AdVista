from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from importlib.metadata import version
from pathlib import Path

os.environ.setdefault("FLAGS_use_mkldnn", "0")

from paddleocr import PaddleOCR


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    args = parser.parse_args()
    request = json.loads(args.request.read_text(encoding="utf-8"))
    with contextlib.redirect_stdout(sys.stderr):
        ocr = PaddleOCR(
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            device="cpu",
            enable_mkldnn=False,
        )
    items = []
    for item in request["images"]:
        with contextlib.redirect_stdout(sys.stderr):
            predictions = ocr.predict(item["path"])
        result = predictions[0].json["res"] if predictions else {}
        items.append(
            {
                "keyframe_id": item["keyframe_id"],
                "texts": result.get("rec_texts", []),
                "scores": result.get("rec_scores", []),
                "polygons": result.get("rec_polys", result.get("dt_polys", [])),
            }
        )
    json.dump(
        {"items": items, "versions": {"paddleocr": version("paddleocr"), "paddlepaddle": version("paddlepaddle")}},
        sys.stdout,
        ensure_ascii=False,
    )
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
