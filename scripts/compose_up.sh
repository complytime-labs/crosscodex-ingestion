#!/usr/bin/env bash
# Start the compose stack in the foreground and exit non-zero unless the ingestion service ran.
#
# Usage: scripts/compose_up.sh        (run through `task compose:up`, which generates the PKI first)
# Environment: CERTS_DIR (PKI from `task certs`, read by docker-compose.yml)
#
# Compose tool, first match wins: podman-compose, `docker compose`, `podman compose`.
# `up` in the foreground returns only once the containers stop (Ctrl-C included), and the compose
# tools do not agree on exit codes (`--exit-code-from` implies aborting on the one-shot certs-init
# exiting), so the ingestion container's final state is inspected instead.
# No -e on purpose: compose exits non-zero when stopped, and the container state is inspected below.
set -uo pipefail

service_container=crosscodex-ingestion # container_name in docker-compose.yml

if command -v podman-compose >/dev/null 2>&1; then
	compose=(podman-compose)
	engine=podman
elif command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
	compose=(docker compose)
	engine=docker
elif command -v podman >/dev/null 2>&1 && podman compose version >/dev/null 2>&1; then
	compose=(podman compose)
	engine=podman
else
	echo "error: no compose tool found; install podman-compose, docker compose or podman compose" >&2
	exit 1
fi
echo "==> using: ${compose[*]}"

# --force-recreate: podman-compose recreates only on a compose config change, so without it a
# regenerated PKI never reaches the running service (it keeps trusting the old CA) and a rebuilt
# image is not picked up.
"${compose[@]}" -f docker-compose.yml up --build --force-recreate
up_status=$?

if ! state="$("$engine" inspect --format '{{.State.Status}} {{.State.ExitCode}} {{.State.OOMKilled}}' "$service_container" 2>&1)"; then
	echo "error: ingestion container '$service_container' does not exist: $state" >&2
	exit 1
fi
read -r status exit_code oom_killed <<<"$state"

case "$status" in
created)
	echo "error: ingestion service never started" >&2
	exit 1
	;;
running)
	echo "error: compose exited (status $up_status) while the ingestion service is still running" >&2
	exit "$((up_status == 0 ? 1 : up_status))"
	;;
*) ;; # stopped states: judged by the OOM and exit-code checks below
esac
if [[ $oom_killed == "true" ]]; then
	echo "error: ingestion service was killed by the OOM killer (see mem_limit in docker-compose.yml)" >&2
	exit 1
fi
# 0, and 137/143 (SIGKILL after the stop timeout, or SIGTERM before the service installed its
# handler; server.py handles SIGTERM and shuts down with 0), mean the operator stopped a service that was running.
case "$exit_code" in
0 | 137 | 143) exit "$up_status" ;;
*)
	echo "error: ingestion service exited with status $exit_code" >&2
	exit "$exit_code"
	;;
esac
