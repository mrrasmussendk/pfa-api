# syntax=docker/dockerfile:1.7
#
# The PFA API as a container: the service (src/) and nothing else. The architecture tooling
# under tools/ is a separate, never-deployed package and is not copied in — CI asserts that
# `import eitri` fails inside the image.
#
#   docker build -t pfa-api .                                   # CPU torch, weights downloaded on first request
#   docker build -t pfa-api --build-arg PRELOAD_MODEL=1 .       # bake the 2.2 GB weights into the image
#   docker build -t pfa-api --build-arg TORCH_INDEX=https://download.pytorch.org/whl/cu124 .   # CUDA build
#
ARG PYTHON_VERSION=3.12

# ---------------------------------------------------------------- build: the wheel
FROM python:${PYTHON_VERSION}-slim AS build
WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir build && python -m build --wheel --outdir /dist

# ---------------------------------------------------------------- runtime
FROM python:${PYTHON_VERSION}-slim AS runtime
ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu
ARG PRELOAD_MODEL=0
ARG MODEL_ID=intfloat/multilingual-e5-large

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/models \
    PFA_HOST=0.0.0.0 \
    PFA_PORT=8000 \
    PFA_EMBED_MODEL=${MODEL_ID} \
    PFA_WARMUP=1 \
    PFA_LOG_FORMAT=json \
    PFA_EMBED_CONCURRENCY=1 \
    TOKENIZERS_PARALLELISM=false

RUN useradd --system --uid 10001 --create-home pfa && mkdir -p /models && chown pfa:pfa /models

# torch first, from the chosen index (CPU by default: ~200 MB instead of ~2.5 GB for CUDA);
# the wheel's sentence-transformers dependency then finds it already satisfied.
COPY --from=build /dist/*.whl /tmp/wheels/
RUN pip install --no-cache-dir torch --index-url ${TORCH_INDEX} \
 && pip install --no-cache-dir /tmp/wheels/*.whl \
 && rm -rf /tmp/wheels

# Optionally bake the weights so first startup does not download 2.2 GB.
RUN if [ "${PRELOAD_MODEL}" = "1" ]; then \
      python -c "from huggingface_hub import snapshot_download as s; \
import os; s(os.environ['PFA_EMBED_MODEL'], allow_patterns=['*.json','*.txt','*.model','model.safetensors','sentencepiece.bpe.model'])" \
      && chown -R pfa:pfa /models; \
    fi

USER pfa
EXPOSE 8000
VOLUME ["/models"]
# Liveness only: /health answers while the model loads. Point the orchestrator's readiness
# probe at /ready (503 until the tokenizer and the model are in memory).
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"
CMD ["pfa-api"]
