# CrossCodex Ingestion Service - Standard Container Image
# Multi-stage build for production deployment

# Stage 1: Builder - Install dependencies, generate proto stubs, install the package, fetch models
FROM docker.io/library/python:3.12-slim AS builder

# Pinned by tag and digest for reproducible builds; 0.13.7 is the uv version the lock was written with.
COPY --from=ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 /uv /usr/local/bin/uv

# Shared libraries OpenCV (imported by Docling's RapidOCR) needs at import time. Also required
# here because docling-tools imports Docling to download the models.
# Debian package versions rotate; pinning would break rebuilds.
# hadolint ignore=DL3008
RUN apt-get update && \
    apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libxcb1 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install third-party dependencies first (layer caching); the package itself needs src/ and README.md.
# Install exactly what uv.lock pins, hash-checked: resolving pyproject.toml afresh drifts from the
# lock, and the model revisions in scripts/download_models.py match the locked Docling.
# torch comes from the CPU-only index recorded in the lock (--emit-index-url); that index also
# hosts non-torch names, so best-match across indexes is needed. --require-hashes keeps it safe.
COPY pyproject.toml uv.lock ./
RUN uv export --locked --no-dev --no-emit-project --emit-index-url --format requirements.txt -o /tmp/requirements.txt && \
    uv pip install --system --no-cache --require-hashes --index-strategy unsafe-best-match -r /tmp/requirements.txt

# Copy proto files and generate gRPC stubs. The code generators (grpcio-tools, mypy-protobuf) come
# from the lock's dev group, hash-checked, into a separate venv: installing them next to the
# runtime packages would change the runtime grpcio/protobuf and ship build tools in the image.
COPY proto/ proto/
RUN uv export --locked --only-group dev --no-emit-project --format requirements.txt -o /tmp/codegen-requirements.txt && \
    uv venv /opt/codegen && \
    uv pip install --python /opt/codegen/bin/python --no-cache --require-hashes -r /tmp/codegen-requirements.txt && \
    mkdir -p src/crosscodex_ingestion/proto/crosscodex/v1 && \
    PATH="/opt/codegen/bin:$PATH" /opt/codegen/bin/python -m grpc_tools.protoc \
      --proto_path=proto \
      --python_out=src/crosscodex_ingestion/proto \
      --grpc_python_out=src/crosscodex_ingestion/proto \
      --mypy_out=src/crosscodex_ingestion/proto \
      crosscodex/v1/common.proto crosscodex/v1/ingestion.proto && \
    touch src/crosscodex_ingestion/proto/__init__.py \
          src/crosscodex_ingestion/proto/crosscodex/__init__.py \
          src/crosscodex_ingestion/proto/crosscodex/v1/__init__.py

# Fix proto imports to match package path
RUN find src/crosscodex_ingestion/proto -name '*_pb2*.py' -exec \
    sed -i 's/from crosscodex\.v1/from crosscodex_ingestion.proto.crosscodex.v1/g' {} +

# Copy application source and install the package (hatchling reads README.md and src/), then
# bake in the Docling and RapidOCR models, pinned by revision (see the script): the
# resource-limited worker runs offline (HF_HUB_OFFLINE=1, which stops model fetches; it is not a network block)
COPY README.md .
COPY src/ src/
COPY scripts/download_models.py scripts/check_lock_parity.py scripts/
RUN uv pip install --system --no-cache --no-deps .

# Fail the build if what is installed differs from the lock. pip is base-image tooling, not part
# of the lock; the project itself is installed above.
RUN uv pip freeze --system --exclude pip --exclude crosscodex-ingestion > /tmp/freeze.txt && \
    python scripts/check_lock_parity.py /tmp/requirements.txt /tmp/freeze.txt

RUN python scripts/download_models.py /opt/docling-models

# Stage 2: Runtime - Minimal production image
FROM docker.io/library/python:3.12-slim

# Runtime shared libraries for OpenCV (see builder stage)
# Debian package versions rotate; pinning would break rebuilds.
# hadolint ignore=DL3008
RUN apt-get update && \
    apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libxcb1 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Create non-root user and the default storage root it must be able to write
RUN groupadd -g 1000 appuser && \
    useradd -u 1000 -g appuser -m appuser && \
    mkdir -p /data/documents && chown appuser:appuser /data/documents

# Copy installed packages (including crosscodex_ingestion and its generated stubs) and models
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /opt/docling-models /opt/docling-models

ENV DOCLING_ARTIFACTS_PATH=/opt/docling-models

# Switch to non-root user
USER 1000:1000

# Expose gRPC and metrics ports
EXPOSE 8080 9090

# Health check: TCP connect to the gRPC port. A gRPC channel check would need a client
# certificate under GRPC_TLS_MODE=mutual; the TLS handshake is not completed here.
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import os, socket; socket.create_connection(('localhost', int(os.environ.get('GRPC_PORT', '8080'))), timeout=5).close()"]

# Run the service
ENTRYPOINT ["python", "-m", "crosscodex_ingestion"]
