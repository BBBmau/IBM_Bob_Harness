# IBM Bob Harness

<p align="center">
  <img src="https://media2.giphy.com/media/v1.Y2lkPTc5MGI3NjExendtcTVyMmRhejk3MngzNTMxdnk1NWxkd3dhcnJzYnFhb3N3enl4dSZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/V5Zao1FEKouvd4p2Wd/giphy.gif" alt="Bob Harness" width="480">
</p>

A Docker container that runs **Bob Shell** (IBM) autonomously, with a
**custom unrestricted mode**, a **REST API** to consume it programmatically, an
**orchestration loop** that verifies results and retries until they pass, and a
**bidirectional Slack bot**.

> Bob Shell has **no** native server mode. This project wraps its headless
> `bob -p "<prompt>" --yolo --chat-mode=unrestricted-dev` invocation and adds a
> verify/retry loop on top (API version `1.2.0`).

## What's included

| File | Role |
|---|---|
| `Dockerfile` | Ubuntu 24.04 + Node 22 + pinned Bob Shell + REST wrapper + `HEALTHCHECK` |
| `docker-compose.yml` | Orchestration: single container (`serve-all` = API + Slack bot), port 8080, `workspace/` volume, `.env`, healthcheck |
| `entrypoint.sh` | Validates the env, accepts the license, starts the API / bot / CLI |
| `.bob/custom_modes.yaml` | `unrestricted-dev` mode: full access (read/edit/command/browser/mcp) |
| `.bob/rules-unrestricted-dev/AGENT.md` | Persistent context/rules for the mode (loaded by Bob at runtime) |
| `api/server.py` | FastAPI app that shells out to `bob` (invoke / jobs / run / stream) |
| `api/slack_bot.py` | Bidirectional Slack bot (Socket Mode) that forwards messages to `/invoke` |
| `slack/manifest.yaml` | Slack App manifest (scopes + `message.channels` + Socket Mode) |
| `api/test_server.py` / `test_slack_bot.py` | Unit tests (mock `subprocess`/network, offline) |
| `api/requirements.txt` / `requirements-dev.txt` | Runtime / test dependencies |
| `.env` | Holds `BOBSHELL_API_KEY` + Slack tokens (**gitignored, never committed**) |

### Where the `.bob` config lives

There is a single source of truth for Bob's config — the `.bob/` directory in
this repo:

```
.bob/
├── custom_modes.yaml              # the unrestricted-dev mode ("settings")
└── rules-unrestricted-dev/
    └── AGENT.md                   # persistent context/rules for that mode
```

The `Dockerfile` copies it verbatim to the **container root**: `/.bob/`. Bob runs
with its working directory set to `/` (see `BOB_WORKDIR` below), so `/.bob/` is
the **project-level** config for the *whole* container — that's why Bob governs
the entire filesystem, not just `/workspace`. This has been verified end to end:
Bob reads `/.bob/rules-unrestricted-dev/AGENT.md` at runtime.

> **Not the same as `/root/.bob/`.** At startup Bob auto-creates its own
> runtime state under `/root/.bob/` (`settings.json` with the license/auth,
> `installation_id`, `trustedFolders.json`, `tmp/`). That directory is managed
> by Bob itself and holds **none** of our config — edit `.bob/` in the repo, not
> `/root/.bob/`. To pick up changes, rebuild the image.

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

## How to start (quickstart)

Get the container up in three steps. Requires Docker (or Podman) installed.

```bash
# 1. Configure secrets: copy the template and fill in your keys.
cp .env.example .env
#    - Paste your Bob API key into BOBSHELL_API_KEY (see §1 to create one).
#    - (Optional, for Slack) paste SLACK_BOT_TOKEN + SLACK_APP_TOKEN (see §4).

# 2. Build the image and start the container (REST API + Slack bot).
docker compose up --build
#    Add -d to run it detached in the background:
#    docker compose up --build -d

# 3. Check it's alive.
curl http://localhost:8080/health
```

The API is now at **`http://localhost:8080`** (Swagger UI at `/docs`). If you set
the Slack tokens, the bot connects automatically and replies in any channel it's
invited to.

**Managing the container**

```bash
docker compose logs -f bob   # follow logs (watch the API + Slack bot start up)
docker compose ps            # show status / health
docker compose down          # stop and remove the container
docker compose restart bob   # restart after changing .env
docker compose up --build    # rebuild after editing code or .bob/ config
```

> **API only (no Slack)?** Override the command to skip the bot:
> `docker compose run --rm --service-ports bob serve`
> (or change `command: ["serve-all"]` to `["serve"]` in `docker-compose.yml`).

> Using Podman instead of Docker? Replace `docker` with `podman` in every
> command above — see the note under §2.

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
  "default_workdir": "/",
  "api_key_set": true
}
```

### `POST /invoke` — run a single task (synchronous)

Runs one Bob prompt and returns the combined output. Runs in YOLO +
`unrestricted-dev` by default, so Bob can create, edit, and execute files
anywhere in the container (the default `workdir` is `/`) without asking for
confirmation.

**Request body**

| Field | Type | Default | Description |
|---|---|---|---|
| `prompt` | string | (required) | The task for Bob |
| `yolo` | bool | `true` | Auto-approve all tool calls |
| `mode` | string | `unrestricted-dev` | Custom mode slug (`--chat-mode`) |
| `workdir` | string | `/` | Working directory for the run (`/` = whole container) |
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

Bob works from `/` by default, so it can touch the whole container. Only files
written under `/workspace` show up in `./workspace` on the host (it's the mounted
volume); edits elsewhere are ephemeral and vanish when the container is recreated.

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

## 4. Talk to Bob from Slack (bidirectional bot)

The Slack bot lets you talk to Bob **in a Slack channel without @-mentioning
it**: write a message, Bob runs it through `POST /invoke` and replies in the
thread.

It connects to Slack over **Socket Mode** (an outbound WebSocket), so the
container needs **no public URL**. By default the compose command is
`serve-all`, which runs the REST API **and** the Slack bot in the **same
container** — the bot calls the API over `http://localhost:8080`. (You can still
run them apart: override the command to `serve` for API-only, or `slack` for a
bot-only container that points at a remote API via `HARNESS_URL`.)

### 4.1 Create the Slack App

1. Go to **[api.slack.com/apps](https://api.slack.com/apps)** → **Create New App**
   → **From a manifest**, pick your workspace, and paste `slack/manifest.yaml`
   from this repo. It pre-configures the scopes (`chat:write`,
   `channels:history`), the `message.channels` event, and Socket Mode.
2. **Install** the app to the workspace, then copy the **Bot User OAuth Token**
   (`xoxb-...`) → `SLACK_BOT_TOKEN`.
3. Under **Basic Information → App-Level Tokens**, generate a token with the
   `connections:write` scope and copy it (`xapp-...`) → `SLACK_APP_TOKEN`.
4. **Invite the bot to your channel:** `/invite @Bob`.

### 4.2 Configure and run

Paste the two tokens into `.env` (see `.env.example`), then:

```bash
docker compose up --build   # single container: REST API + Slack bot (serve-all)
docker compose logs -f bob  # watch the bot connect and handle messages
```

Now any message in a channel the bot is in (no mention needed) gets a reply
from Bob in-thread. To limit the bot to specific channels, set
`SLACK_ALLOWED_CHANNELS` to a comma-separated list of channel IDs.

### 4.3 How it works / notes

- The bot ignores messages from bots (including its own) — this is what prevents
  a reply loop — and skips message edits/joins (`subtype`) and empty messages.
- It calls `/invoke` (synchronous prompt → reply); the verify/retry `/run` loop
  is not used for chat.
- The bot pulls prior thread messages back as context, so Bob answers with
  continuity within a thread.
- Very long transcripts are truncated in the reply; re-run in a terminal for the
  full log.
- **Security:** the bot runs Bob in `unrestricted-dev` + YOLO. Anyone who can
  post in a channel the bot is in can run commands inside the container — only
  add it to trusted channels (and use `SLACK_ALLOWED_CHANNELS`).

| Env var | Default | Description |
|---|---|---|
| `SLACK_BOT_TOKEN` | — | **Required.** Bot token (`xoxb-...`) |
| `SLACK_APP_TOKEN` | — | **Required.** App-level token for Socket Mode (`xapp-...`) |
| `HARNESS_URL` | `http://localhost:8080` | REST API base URL (same container under `serve-all`; override for a remote API) |
| `SLACK_ALLOWED_CHANNELS` | — | Optional CSV of channel IDs to restrict to |
| `BOB_INVOKE_TIMEOUT` | `600` | Max seconds per Bob invocation |

---

## 5. Use Bob directly (without the API)

```bash
# Interactive session
docker compose run --rm bob shell

# Single headless prompt
docker compose run --rm bob bob -p "Explain @README.md" --yolo --chat-mode=unrestricted-dev
```

## 6. Tests

Unit tests live in `api/test_server.py` (REST API) and `api/test_slack_bot.py`
(Slack bot logic). They mock `subprocess` and the network, so they never call
the real `bob` binary, the IBM API, or Slack — fast and offline. They cover
`/health`, `/invoke`, the async `/jobs` lifecycle, `/stream` (SSE), the `/run`
orchestration loop (verify + retry), and the bot's `should_handle` / `run_prompt`
/ `build_reply` helpers.

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

## 7. Configuration reference

| Env var | Default | Where | Description |
|---|---|---|---|
| `BOBSHELL_API_KEY` | — | `.env` | **Required.** Authenticates Bob in headless mode |
| `BOB_MODE` | `unrestricted-dev` | `.env` / compose | Default custom mode slug |
| `BOB_WORKDIR` | `/` | `.env` / compose | Default working directory (`/` = whole container) |
| `BOB_MAX_JOBS` | `100` | env | Max runs kept in memory (oldest evicted) |
| `BOB_BIN` | `bob` | env | Path/name of the Bob binary |
| `BOB_VERSION` | `1.0.5` | build arg | Pinned Bob Shell version |
| `SLACK_BOT_TOKEN` | — | `.env` | Slack bot token (`xoxb-...`); required for the Slack bot |
| `SLACK_APP_TOKEN` | — | `.env` | Slack app-level token (`xapp-...`) for Socket Mode |
| `SLACK_ALLOWED_CHANNELS` | — | `.env` | Optional CSV of channel IDs the bot answers in |
| `HARNESS_URL` | `http://localhost:8080` | compose | REST API URL the Slack bot calls (same container under `serve-all`) |

## 8. Security

- The `unrestricted-dev` mode grants **full** access to the container's
  filesystem and shell. Use it only inside this disposable container.
- `--yolo` limits edits to the starting directory, which here is `/` — i.e. the
  **whole container**. Keep this container disposable and never mount anything
  sensitive from the host.
- Secrets (`BOBSHELL_API_KEY`, the Slack tokens) live only in `.env`
  (gitignored) — the committed files carry placeholders. Do not publish them,
  and rotate any that leak.
- The REST API has **no authentication** yet — do not expose port 8080 beyond
  localhost until a token layer is added.

## Author

Edgar Bruney
