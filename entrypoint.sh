#!/usr/bin/env bash
# Container entrypoint. Verifies the environment, then serves the REST API
# (default), the Slack bot, both together (serve-all), or the `bob` CLI.
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
  serve-all)
    # Single-container mode: run the REST API AND the Slack bot side by side.
    # The bot reaches the API over localhost. If either process dies, we exit so
    # the container's restart policy brings the whole thing back up.
    echo "Bob harness: starting REST API + Slack bot in one container"
    cd /app
    uvicorn server:app --host 0.0.0.0 --port 8080 &
    api=$!
    # Wait for the API to answer before starting the bot (avoids early errors).
    for _ in $(seq 1 30); do
      curl -fsS http://localhost:8080/health >/dev/null 2>&1 && break
      sleep 0.5
    done
    HARNESS_URL="${HARNESS_URL:-http://localhost:8080}" python3 slack_bot.py &
    bot=$!
    wait -n "$api" "$bot"
    ec=$?
    kill "$api" "$bot" 2>/dev/null || true
    exit "$ec"
    ;;
  slack)
    # Bidirectional Slack bot (Socket Mode). Talks to the REST API over HTTP,
    # so it needs HARNESS_URL + the SLACK_* tokens (see .env / docker-compose).
    echo "Bob harness: starting Slack bot (harness=${HARNESS_URL:-http://localhost:8080})"
    cd /app
    exec python3 slack_bot.py
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
