# CrossCodex Ingestion Service - Standard Container Image
# Multi-stage build for production deployment

# Stage 1: Builder - Install dependencies and generate proto stubs
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy and install Python dependencies first (layer caching)
COPY pyproject.toml .
RUN uv pip install --system --no-cache .

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

# Copy application source
COPY src/ src/

# Stage 2: Runtime - Minimal production image
FROM python:3.12-slim

WORKDIR /app

# Create non-root user
RUN groupadd -g 1000 appuser && \
    useradd -u 1000 -g appuser -m appuser

# Copy installed packages and source from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /app/src /app/src

# Switch to non-root user
USER appuser

# Expose gRPC and metrics ports
EXPOSE 8080 9090

# Health check via gRPC channel ready check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD python -c "import grpc; ch=grpc.insecure_channel('localhost:8080'); grpc.channel_ready_future(ch).result(timeout=5)" || exit 1

# Run the service
ENTRYPOINT ["python", "-m", "crosscodex_ingestion"]
