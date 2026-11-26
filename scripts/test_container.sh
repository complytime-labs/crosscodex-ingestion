#!/usr/bin/env bash
# Build one container image and prove it works: the Dockerfile HEALTHCHECK passes against a
# service started with mutual TLS and attestation, and a PDF with headings converts with no network.
#
# Usage: scripts/test_container.sh <standard|fips>
# Environment: CERTS_DIR (PKI from `task certs`, default .test-output/certs)
#
# Any failing step aborts with a non-zero status. Run it through `task test:container`.
set -euo pipefail

variant="${1:?Usage: $0 <standard|fips>}"
certs_dir="$(realpath "${CERTS_DIR:-.test-output/certs}")"
fixtures_dir="$(realpath tests/fixtures/documents)"
check_script="$(realpath tests/container/offline_convert.py)"

# Per-image facts that differ: Dockerfile, service uid (the key files are 0600, so the host
# user is mapped onto it), and the Python executable name.
case "$variant" in
standard)
	dockerfile=Dockerfile
	uid=1000
	python=python
	;;
fips)
	dockerfile=Dockerfile.fips
	uid=1001
	python=python3.12
	;;
*)
	echo "unknown variant '$variant' (expected standard or fips)" >&2
	exit 2
	;;
esac

image="crosscodex-ingestion:test-$variant"
container="crosscodex-ingestion-test-$variant"
health_timeout_seconds=180

cleanup() {
	podman rm --force --ignore --time 2 "$container" >/dev/null
}
trap cleanup EXIT

echo "==> [$variant] build $dockerfile"
# --format docker: the default OCI image format silently drops the HEALTHCHECK instruction.
podman build --format docker -f "$dockerfile" -t "$image" .

echo "==> [$variant] start the service with mutual TLS and attestation"
cleanup
podman run --detach --name "$container" --userns "keep-id:uid=$uid,gid=$uid" \
	--volume "$certs_dir:/certs:ro" \
	--env GRPC_TLS_MODE=mutual \
	--env GRPC_TLS_CERT=/certs/server.pem \
	--env GRPC_TLS_KEY=/certs/server.key \
	--env GRPC_TLS_CA=/certs/ca.pem \
	--env GRPC_ALLOWED_CLIENTS=crosscodex-gateway \
	--env ATTESTATION_ENABLED=true \
	--env ATTESTATION_PRIVATE_KEY_PATH=/certs/attestation.key \
	--env ATTESTATION_PUBLIC_KEY_PATH=/certs/attestation.pub \
	"$image" >/dev/null

echo "==> [$variant] run the image HEALTHCHECK (up to ${health_timeout_seconds}s)"
deadline=$((SECONDS + health_timeout_seconds))
until podman healthcheck run "$container" >/dev/null 2>&1; do
	running="$(podman inspect --format '{{.State.Running}}' "$container")"
	if [[ $running != "true" ]]; then
		echo "service exited before becoming healthy; logs:" >&2
		podman logs "$container" >&2
		exit 1
	fi
	if ((SECONDS >= deadline)); then
		echo "service not healthy after ${health_timeout_seconds}s; logs:" >&2
		podman logs "$container" >&2
		exit 1
	fi
	sleep 2
done
echo "healthy"

echo "==> [$variant] convert nist-sp800-171r3-excerpt.pdf with --network=none"
# MAX_PROCESSING_TIME bounds the conversion, so a hung worker fails the check instead of the CI job.
podman run --rm --network none \
	--env MAX_PROCESSING_TIME=600 \
	--volume "$fixtures_dir:/fixtures:ro" \
	--volume "$check_script:/check/offline_convert.py:ro" \
	--entrypoint "$python" \
	"$image" /check/offline_convert.py /fixtures/nist-sp800-171r3-excerpt.pdf

echo "==> [$variant] OK"
