"""Download the embedding model the API serves, so first startup is not a 2 GB surprise.

Model: intfloat/multilingual-e5-large  (560M params, 1024-dim embeddings, 100+ languages).
Weights land in the Hugging Face cache (``HF_HOME``, default ``~/.cache/huggingface``) unless
``--local-dir`` is given. The API loads the model by its id, so the cache is what it reads.

Usage:
    python tools/download_model.py                       # into the HF cache
    python tools/download_model.py --local-dir models/e5 # into a project folder

Note for whoever wires the endpoint: e5 models expect an instruction prefix on every input —
"query: " for search queries and "passage: " for documents (both sides use "query: " for
symmetric tasks such as similarity or classification). Without the prefix, quality drops.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

MODEL_ID = "intfloat/multilingual-e5-large"
# Only the PyTorch weights + tokenizer are needed; skip the ONNX/TF/safetensor duplicates.
ALLOW = ["*.json", "*.txt", "*.model", "model.safetensors", "sentencepiece.bpe.model"]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--local-dir", default=None, help="download into this folder instead of the HF cache")
    p.add_argument("--all-files", action="store_true", help="also fetch ONNX/TF variants (~3x larger)")
    args = p.parse_args(argv)

    from huggingface_hub import snapshot_download

    t0 = time.perf_counter()
    path = snapshot_download(
        MODEL_ID,
        local_dir=args.local_dir,
        allow_patterns=None if args.all_files else ALLOW,
    )
    size = sum(f.stat().st_size for f in Path(path).rglob("*") if f.is_file())
    print(f"{MODEL_ID} -> {path}  ({size / 1e9:.2f} GB, {time.perf_counter() - t0:.0f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
