# IBM Bob Harness

A Docker container that runs **Bob Shell** (IBM) autonomously, with a
**custom unrestricted mode**, a **REST API** to consume it programmatically, and
an **orchestration loop** that verifies results and retries until they pass.

> Bob Shell has **no** native server mode. This project wraps its headless
> `bob -p "<prompt>" --yolo --chat-mode=unrestricted-dev` invocation and adds a
> verify/retry loop on top (API version `1.2.0`).

## What's included

| File | Role |
|---|---|
| `Dockerfile` | Ubuntu 24.04 + Node 22 + pinned Bob Shell + REST wrapper + `HEALTHCHECK` |
| `docker-compose.yml` | Orchestration: port 8080, `workspace/` volume, `.env`, healthcheck |
| `entrypoint.sh` | Validates the env, accepts the license, starts the API or the CLI |
| `.bob/custom_modes.yaml` | `unrestricted-dev` mode: full access (read/edit/command/browser/mcp) |
| `api/server.py` | FastAPI app that shells out to `bob` (invoke / jobs / run / stream) |
| `api/test_server.py` | Unit tests (mock `subprocess`, offline) |
| `api/requirements.txt` / `requirements-dev.txt` | Runtime / test dependencies |
| `.env` | Holds `BOBSHELL_API_KEY` (**gitignored, never committed**) |

## Endpoints at a glance

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness + resolved config |
| `POST` | `/invoke` | Run one prompt synchronously, return full output |
| `POST` | `/jobs` | Start a prompt as a background job → `{id}` |
| `POST` | `/run` | **Orchestrated** run: execute → verify → retry → `{id}` |
| `GET` | `/jobs` | List runs/jobs |
| `GET` | `/jobs/{id}` | Status + output (+ `attempts` for `/run`) |
| `GET` | `/jobs/{id}/stream` | Stream a run's output live (SSE) |
| `POST` | `/stream` | Start a job **and** stream it in one request (SSE) |
| `GET` | `/docs` | Swagger UI (auto-generated) |

---

## 1. Configure the API key

### Get a Bob API key

The harness authenticates Bob in headless mode with `BOBSHELL_API_KEY`. To
create one:

1. Go to **[https://bob.ibm.com/](https://bob.ibm.com/)** and sign in with your IBMid.
2. Open the **Admin** tab in the top navigation.
3. In the left sidebar, pick your **Workspace** (e.g. `IBM Internal`) and click
   **API Keys**.
4. Click **Create +**, give the key a **Name** (e.g. `mac`) and a **Scope**
   (`General` is fine), then confirm.
5. **Copy the generated key immediately** — it starts with `bob_prod_...` and is
   shown only once. You can later revoke it from the same **API Keys** table
   (each row shows Name, Scope, Date created, and Status: Active/Expired/Revoked).

### Put it in `.env`

```bash
cp .env.example .env   # then paste your key into BOBSHELL_API_KEY
```

The key already lives in `.env` in this repo; replace it with your own if needed.

## 2. Build and run

```bash
docker compose up --build
```

The API is served at `http://localhost:8080`. The Bob Shell version is pinned
via the `BOB_VERSION` build arg (default `1.0.5`) for reproducible builds — bump
it in `docker-compose.yml` to upgrade.

> **Note:** this machine has no Docker daemon, only Podman. Every command below
> works with `podman` too — use `podman compose ...` and `podman run ...` in
> place of `docker`. (Podman ignores the image `HEALTHCHECK`, so a compose-level
> healthcheck is defined as well.)

---

## 3. How to consume it (REST API)

### `GET /health` — liveness + resolved config

```bash
curl http://localhost:8080/health
```

```json
{
  "status": "ok",
  "bob_present": true,
  "default_mode": "unrestricted-dev",
  "default_workdir": "/workspace",
  "api_key_set": true
}
```

### `POST /invoke` — run a single task (synchronous)

Runs one Bob prompt and returns the combined output. Runs in YOLO +
`unrestricted-dev` by default, so Bob can create, edit, and execute files
inside `/workspace` without asking for confirmation.

**Request body**

| Field | Type | Default | Description |
|---|---|---|---|
| `prompt` | string | (required) | The task for Bob |
| `yolo` | bool | `true` | Auto-approve all tool calls |
| `mode` | string | `unrestricted-dev` | Custom mode slug (`--chat-mode`) |
| `workdir` | string | `/workspace` | Working directory for the run |
| `timeout` | int | `600` | Max seconds before abort (1–3600) |

**Example — curl**

```bash
curl -s http://localhost:8080/invoke \
  -H 'Content-Type: application/json' \
  -d '{"prompt": "Create a file hello.py that prints \"Hello from Bob\" and run it"}'
```

**Response**

```json
{
  "ok": true,
  "exit_code": 0,
  "output": "...Hello from Bob...",
  "error": "",
  "command": ["bob", "--accept-license", "-p", "...", "--chat-mode=unrestricted-dev", "--yolo"]
}
```

Any file Bob creates/edits shows up in `./workspace` on the host.

**Example — Python**

```python
import requests

r = requests.post("http://localhost:8080/invoke", json={
    "prompt": "Refactor @app.py and add tests",
    "timeout": 900,
})
data = r.json()
print(data["ok"], data["exit_code"])
print(data["output"])
```

**Example — JavaScript (fetch)**

```js
const res = await fetch("http://localhost:8080/invoke", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ prompt: "List the files in the workspace" }),
});
const data = await res.json();
console.log(data.output);
```

### `POST /jobs` — run a task asynchronously

For long tasks, start a background job and poll it instead of blocking. Accepts
the same body as `/invoke`.

```bash
# Start -> returns {"id": "...", "status": "running"} (HTTP 202)
JID=$(curl -s http://localhost:8080/jobs \
  -H 'Content-Type: application/json' \
  -d '{"prompt": "Refactor @app.py and add tests"}' | jq -r .id)

# Poll status + output so far
curl http://localhost:8080/jobs/$JID

# List all jobs/runs
curl http://localhost:8080/jobs
```

A job view looks like:

```json
{
  "id": "c4f3d937d1f7",
  "type": "job",
  "status": "completed",
  "exit_code": 0,
  "output": "...",
  "command": ["bob", "--accept-license", "-p", "...", "--chat-mode=unrestricted-dev", "--yolo"]
}
```

`status` is one of `pending | running | completed | failed | timeout`.

### `POST /run` — orchestrated run (verify + retry)

This is what turns the wrapper into a real **harness**: run the prompt, verify
the result with a shell `check` command, and if it fails, feed the failure back
to Bob and retry — up to `max_attempts` times.

**Request body** (extends `/invoke` with):

| Field | Type | Default | Description |
|---|---|---|---|
| `check` | string | `null` | Shell command that verifies success (exit 0 = pass). If omitted, Bob's own exit code decides. |
| `max_attempts` | int | `3` | Max verify/retry attempts (1–10) |
| `check_timeout` | int | `300` | Max seconds per check |

```bash
# Ask Bob to implement something and keep retrying until the tests pass.
RID=$(curl -s http://localhost:8080/run \
  -H 'Content-Type: application/json' \
  -d '{"prompt": "Implement add() in calc.py", "check": "pytest -q", "max_attempts": 4}' | jq -r .id)

curl http://localhost:8080/jobs/$RID
```

**Response** (`GET /jobs/{id}` for a run)

```json
{
  "id": "98fa595b737c",
  "type": "harness",
  "status": "completed",
  "success": true,
  "check": "pytest -q",
  "max_attempts": 4,
  "attempts": [
    {"attempt": 1, "bob_exit_code": 0, "check_exit_code": 1, "check_timed_out": false},
    {"attempt": 2, "bob_exit_code": 0, "check_exit_code": 0, "check_timed_out": false}
  ],
  "output": "...full transcript across attempts..."
}
```

On each retry the harness appends a `--- HARNESS FEEDBACK ---` block (the failing
command and its output) to the prompt so Bob can fix it. Runs are streamable via
`GET /jobs/{id}/stream`; the `[harness]` markers show each attempt and the check
result.

### `GET /jobs/{id}/stream` and `POST /stream` — live output (SSE)

Stream Bob's output line by line as it happens, via Server-Sent Events.
`POST /stream` starts a job and streams it in a single request:

```bash
curl -sN http://localhost:8080/stream \
  -H 'Content-Type: application/json' \
  -d '{"prompt": "Explain @README.md"}'
```

```
data: YOLO mode is enabled. All tool calls will be automatically approved.
data: ...
event: done
data: {"status": "completed", "success": null}
```

The final `event: done` carries `status` and, for `/run`, the `success` flag
(`null` for plain jobs). `GET /jobs/{id}/stream` streams an already-created run
the same way. Consume it with any SSE client (`curl -N`, `EventSource` in the
browser, etc.).

### Interactive API docs

FastAPI ships OpenAPI docs out of the box:

- Swagger UI: `http://localhost:8080/docs`
- OpenAPI schema: `http://localhost:8080/openapi.json`

---

## 4. Use Bob directly (without the API)

```bash
# Interactive session
docker compose run --rm bob shell

# Single headless prompt
docker compose run --rm bob bob -p "Explain @README.md" --yolo --chat-mode=unrestricted-dev
```

## 5. Tests

Unit tests live in `api/test_server.py`. They mock `subprocess`, so they never
call the real `bob` binary or the IBM API — fast and offline. They cover
`/health`, `/invoke`, the async `/jobs` lifecycle, `/stream` (SSE), and the
`/run` orchestration loop (verify + retry). Current suite: **20 tests**.

Run them inside the built image (which already has the runtime deps):

```bash
podman run --rm -v "$PWD/api:/app:ro" -w /app --entrypoint bash bob-harness -lc \
  'pip install -q --break-system-packages -r requirements-dev.txt && python3 -m pytest -v'
```

Or locally, if you have Python 3.12+:

```bash
cd api
pip install -r requirements-dev.txt
pytest -v
```

## 6. Configuration reference

| Env var | Default | Where | Description |
|---|---|---|---|
| `BOBSHELL_API_KEY` | — | `.env` | **Required.** Authenticates Bob in headless mode |
| `BOB_MODE` | `unrestricted-dev` | `.env` / compose | Default custom mode slug |
| `BOB_WORKDIR` | `/workspace` | `.env` / compose | Default working directory |
| `BOB_MAX_JOBS` | `100` | env | Max runs kept in memory (oldest evicted) |
| `BOB_BIN` | `bob` | env | Path/name of the Bob binary |
| `BOB_VERSION` | `1.0.5` | build arg | Pinned Bob Shell version |

## 7. Security

- The `unrestricted-dev` mode grants **full** access to the container's
  filesystem and shell. Use it only inside this disposable container.
- `--yolo` limits edits to the starting directory (`/workspace`).
- `BOBSHELL_API_KEY` is a secret: it lives in `.env` (gitignored). Do not
  publish it, and rotate it if it leaks.
- The REST API has **no authentication** yet — do not expose port 8080 beyond
  localhost until a token layer is added.

## Author

Edgar Bruney
