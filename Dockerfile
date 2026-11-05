# CrossCodex Ingestion Service - Standard Container Image
# Multi-stage build for production deployment

# Stage 1: Builder - Install dependencies, generate proto stubs, install the package, fetch models
FROM docker.io/library/python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Shared libraries OpenCV (imported by Docling's RapidOCR) needs at import time. Also required
# here because docling-tools imports Docling to download the models.
# Debian package versions rotate; pinning would break rebuilds.
# hadolint ignore=DL3008
RUN apt-get update && \
    apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libxcb1 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install third-party dependencies first (layer caching); the package itself needs src/ and README.md
COPY pyproject.toml .
RUN uv pip install --system --no-cache -r pyproject.toml

# Copy proto files and generate gRPC stubs
COPY proto/ proto/
RUN uv pip install --system --no-cache grpcio-tools mypy-protobuf && \
    mkdir -p src/crosscodex_ingestion/proto/crosscodex/v1 && \
    python -m grpc_tools.protoc \
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
# pre-download Docling models: the sandboxed worker runs offline (HF_HUB_OFFLINE=1)
COPY README.md .
COPY src/ src/
RUN uv pip install --system --no-cache --no-deps . && \
    docling-tools models download -o /opt/docling-models

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
  CMD python -c "import os, socket; socket.create_connection(('localhost', int(os.environ.get('GRPC_PORT', '8080'))), timeout=5).close()" || exit 1

# Run the service
ENTRYPOINT ["python", "-m", "crosscodex_ingestion"]
