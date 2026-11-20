# Run from source

For developers and operators who want to run the service from a checkout instead of a container. Back to
the [README](../../README.md); the tools you need are listed in its [Prerequisites](../../README.md#prerequisites).

1. Create `.venv` and install all dependencies:

   ```bash
   task setup
   ```

2. Generate protobuf stubs:

   ```bash
   task proto
   ```

3. Download the Docling models once (see [Docling models](../../README.md#docling-models)):

   ```bash
   uv run python scripts/download_models.py ~/.cache/crosscodex/docling-models
   ```

4. Generate the dev PKI and attestation key pair in `.test-output/certs`:

   ```bash
   task certs
   ```

5. Run the server (embedded mode, filesystem storage, memory state) with mutual TLS and attestation:

   ```bash
   C=.test-output/certs
   GRPC_TLS_CERT=$C/server.pem GRPC_TLS_KEY=$C/server.key GRPC_TLS_CA=$C/ca.pem \
     GRPC_ALLOWED_CLIENTS=crosscodex-gateway \
     ATTESTATION_PRIVATE_KEY_PATH=$C/attestation.key ATTESTATION_PUBLIC_KEY_PATH=$C/attestation.pub \
     DOCLING_ARTIFACTS_PATH=~/.cache/crosscodex/docling-models STORAGE_PATH=/tmp/crosscodex-data task run
   ```

   The service refuses to start without the TLS and attestation settings. For a plaintext, unattested
   throwaway run, set `GRPC_TLS_MODE=off ATTESTATION_ENABLED=false` instead; never do that on a shared host.

6. Run tests:

   ```bash
   task test
   ```

7. Run integration tests. The first run downloads the pinned models into `.test-output/docling-models`; a
   `DOCLING_ARTIFACTS_PATH` already set in your shell takes precedence and skips that download. When it is
   set, a broken Docling install fails the tests instead of skipping them:

   ```bash
   task test:integration
   ```

Then [convert a document](../../README.md#convert-a-document).
