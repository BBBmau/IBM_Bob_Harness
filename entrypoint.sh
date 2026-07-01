#!/usr/bin/env bash
# Container entrypoint. Verifies the environment, then either serves the REST
# API (default) or passes through to the `bob` CLI.
set -euo pipefail

# Ensure bob is reachable even in a non-login shell.
export PATH="/root/.local/bin:/root/.bob/bin:/usr/local/bin:/usr/bin:${PATH}"

if [ -z "${BOBSHELL_API_KEY:-}" ]; then
  echo "ERROR: BOBSHELL_API_KEY is not set." >&2
  echo "       Pass it with:  docker run --env-file .env ...  (or -e BOBSHELL_API_KEY=...)" >&2
  exit 1
fi

if ! command -v bob >/dev/null 2>&1; then
  echo "ERROR: 'bob' not found on PATH after install." >&2
  echo "       PATH=$PATH" >&2
  exit 1
fi

# Accept the IBM license once, non-interactively (idempotent).
bob --accept-license -p "print: ready" >/dev/null 2>&1 || true

case "${1:-serve}" in
  serve)
    echo "Bob harness: starting REST API on 0.0.0.0:8080 (mode=${BOB_MODE:-unrestricted-dev})"
    cd /app
    exec uvicorn server:app --host 0.0.0.0 --port 8080
    ;;
  shell)
    # Interactive bob session:  docker run -it bob-harness shell
    shift
    exec bob "$@"
    ;;
  bob)
    # Direct bob CLI passthrough:  docker run bob-harness bob -p "..." --yolo
    shift
    exec bob "$@"
    ;;
  *)
    # Anything else is executed verbatim (e.g. `bash`).
    exec "$@"
    ;;
esac
