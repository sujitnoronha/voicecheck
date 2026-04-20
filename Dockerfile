# Multi-stage build — keeps the final image slim.
# Stage 1: build the wheel so the final layer doesn't need build tooling.
FROM python:3.12-slim AS builder

WORKDIR /build

# Copy only what hatchling needs to produce the wheel.
COPY pyproject.toml README.md LICENSE ./
COPY src/ src/

RUN pip install --no-cache-dir build \
    && python -m build --wheel --outdir /build/dist

# Stage 2: runtime image.
FROM python:3.12-slim

# System packages used by the audio stack:
#   ffmpeg      — pydub decodes opus/mp3 TTS output, emits PCM
#   libsndfile1 — faster-whisper (CTranslate2) audio loading
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /build/dist/*.whl /tmp/

# Install with all optional extras so every transport / evaluator works
# out of the box. Image is ~1.5 GB because faster-whisper bundles model
# binaries — the tradeoff is zero extra-install friction for users.
RUN pip install --no-cache-dir /tmp/voicecheck-*.whl[all] \
    && rm /tmp/voicecheck-*.whl

# Users mount their scenarios here: docker run -v $(pwd):/workspace ...
WORKDIR /workspace

ENTRYPOINT ["voicecheck"]
CMD ["--help"]
