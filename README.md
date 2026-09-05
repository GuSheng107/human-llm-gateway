<div align="center">

<img src="docs/images/logo.svg" alt="Human LLM Gateway" width="120" height="120" />

# Human LLM Gateway

**A real LLM API on the outside — answered by *you* (or your own real LLM) on the inside.**

[![License](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![React](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=white)](admin/package.json)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)](pyproject.toml)
[![Tailwind](https://img.shields.io/badge/Tailwind%20CSS-4-06B6D4?logo=tailwindcss&logoColor=white)](admin/package.json)
[![Tests](https://img.shields.io/badge/tests-quality%20gates-brightgreen)]()
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-ff69b4.svg)]()

**English** | [简体中文](README.zh-CN.md)

---

Drop it in, and what you type in your chat becomes a standard LLM API response.

> **Project status: invitation-only beta.** Core features are implemented for a single-instance deployment. Verify real IM accounts, LLM upstreams and the public proxy using the [deployment checklist](docs/DEPLOYMENT.md) before inviting users. The roadmap records remaining acceptance work.

</div>

---

## ✨ What is this

Ever wanted a tool to call "GPT-5" — but the answer is actually written by **you**? Or wished your real LLM's output carried **your custom identity**?

Human LLM Gateway is a self-hostable **LLM identity gateway**:

```
   Caller (SDK / app)                 Your gateway                   Who answers
┌──────────────────┐  protocol-  ┌──────────────────┐   ① human  ┌──────────────────┐
│ openai SDK       │  compatible │  Human LLM       │ ◄───────── │ you, replying in │
│ anthropic SDK    │ ──────────► │  Gateway         │            │ the web console  │
│ any client       │ ◄────────── │                  │   ② LLM   ├──────────────────┤
│                  │  Fake Model │                  │ ────────► │ your private     │
└──────────────────┘  response   └──────────────────┘   ③ both   │ LLM upstream     │
                                                                             └──────────────────┘
```

- **Fake Models** are public identities only (e.g. `gpt-5`) — **never bound to any real upstream**
- **Your real LLM configs** stay private — forward directly, generate editable drafts for you, or take over after a human timeout
- Responses always carry the Fake Model the caller asked for; your real upstream is never revealed

## 🚀 Features

<table>
<tr><td width="50%" valign="top">

### 🎭 Identity Masquerading
- Fake Model catalog: system-wide + per-user private
- `/v1/models` computes the effective set per API key
- Response `model` field always rewritten to the Fake Model

### 📡 Three Protocols
- OpenAI Chat Completions
- OpenAI Responses (incl. `previous_response_id` chain expansion)
- Anthropic Messages (`x-api-key` / `anthropic-version`)
- SSE streaming + pseudo-streaming output

### 👤 Human-in-the-Loop
- Web task console: full raw request, timeline, drafts
- Unified reply workbench with inbox, unread state, conversation context, and draft version protection
- IM delivery: WeCom, webhook, WebSocket, HTTP polling
- IM DSL: `::: reasoning` / `::: tool` fences, shared structure with the web editor
- First valid submission wins — irrevocable

> ⚠️ When replying manually, tool calls may **only reference tools declared by the caller in the original request** (names must match; otherwise the reply is rejected with 400). The gateway never executes any tool and makes no guarantee about execution results — the caller declares and runs tools at their own risk.

</td><td width="50%" valign="top">

### 🤖 Real-LLM Orchestration
- Per-user LLM configs (OpenAI-compatible / Anthropic, secrets encrypted at rest)
- Three strategies: `human` / `llm` direct forward / `human_fallback_llm` on timeout
- Cross-protocol field matrix: convert or explicitly 400 — never silently dropped
- Upstream streaming → full persistence → pseudo-streamed output

### 🛡️ Defense in Depth
- Tiered SSRF protection (cloud metadata always blocked + private-range switch)
- Secret encryption (HKDF + AES-GCM envelope), never echoed back
- Two-layer page-context redaction (closed schema + pattern scrubbing)
- 8 MiB / 1 MiB request caps, stream byte/duration budgets

### 🧰 Tool-Call Passthrough
- Human replies may reference caller-declared tools (name must be declared, otherwise 400)
- The gateway only forges and forwards tool-call output; no execution, no whitelist, no sandbox
- The caller declares and executes tools themselves and bears all consequences

</td></tr>
</table>

## 🏗️ Architecture

```
                    ┌────────────────────────────────────────────┐
                    │                admin/ (React 19)           │
│   login · console · tasks · keys · models  │
│      LLM configs · logs · chat             │
                    └────────────────────┬───────────────────────┘
                                         │ /api/*
┌──────────────┐  /v1/*  ┌───────────────▼────────────────┐  upstream ┌─────────────┐
│ caller SDK   │ ──────► │  app/api/ (FastAPI)            │ ────────► │ your real   │
│ openai/anth. │ ◄────── │  parse · admit · forward ·     │           │ LLM         │
└──────────────┘  reply  │  render                        │           │ (optional)  │
                          │  app/services/      use cases │           └─────────────┘
┌──────────────┐  /conn. │  app/repositories/  persistence│  delivery ┌─────────────┐
│ your IM      │ ──────► │  app/connectors/    IM         │ ────────► │ your IM     │
│ client       │ ◄────── │  app/protocols/     3 protocols│ ◄──────── │ WeChat/…    │
└──────────────┘  DSL    │  app/domain/        pure rules │   reply   └─────────────┘
                          │  app/core/          security  │
                          └────────────────────────────────┘
```

**Stack**: Python 3.12 · FastAPI · SQLAlchemy · Pydantic v2 · Argon2id · AES-256-GCM · React 19 · TypeScript strict · Vite · Tailwind CSS 4 · SSE

## 📦 Quick Start

### Prerequisites

- Python 3.12 with [uv](https://docs.astral.sh/uv/) (pinned by `.python-version`)
- Node.js 24; minimum compatible range: `^20.19.0 || >=22.12.0`

### Three Steps

```bash
# 1. Clone
git clone https://github.com/GuSheng107/human-llm-gateway.git
cd human-llm-gateway

# 2. Configure
python -c "import secrets; print(f'APP_SECRET={secrets.token_urlsafe(32)}')" >> .env
echo "ADMIN_USERNAME=admin" >> .env
echo "ADMIN_PASSWORD=Your-Str0ng!Pass" >> .env
# Public deployments must declare the gateway's own public host(s) so a user's
# LLM upstream can never point back at the gateway itself:
echo "GATEWAY_PUBLIC_HOSTS=gateway.example.com" >> .env

# 3. Build the frontend, then run the server (single port —
#    the backend serves the built SPA itself)
uv sync --locked
(cd admin && npm ci && npm run build)
uv run --locked uvicorn app.api:app --host 127.0.0.1 --port 8000 --ws-max-size 1048576 --timeout-graceful-shutdown 30
```

Open **http://127.0.0.1:8000** — the console and the API share one port. The first run auto-creates the database and seeds default system models. Log in with your admin account, change the password, and start issuing invitations.

### 🚀 Deployment status

Run one FastAPI process serving the built React console; multiple workers or replicas are unsupported. Keep `.env`, the database and logs outside version control. See [Deployment and operations](docs/DEPLOYMENT.md) for TLS, long request timeouts, service management, online backup and restore. Verify both probes after startup:

```bash
cd admin && npm ci && npm run build
cd ..
uv run --locked uvicorn app.api:app --host 127.0.0.1 --port 8000 --ws-max-size 1048576 --timeout-graceful-shutdown 30
curl http://127.0.0.1:8000/healthz
curl http://127.0.0.1:8000/readyz
```

High-frequency records are retained for seven days. The service cleans records older than seven days at startup and every seven days; request tasks and formal reply drafts are retained.

> Frontend hot-reload for development (optional):
> `cd admin && npm run dev` → http://127.0.0.1:5173 (`/api` and `/v1` are proxied to port 8000)

### Five-Minute Tour

```bash
# ① Sign in as a regular user and create an API key (e.g. select deepseek-v4-pro)

# ② Call it just like OpenAI
export OPENAI_API_KEY="sk-xxxx"    # gateway-issued key
export OPENAI_BASE_URL="http://127.0.0.1:8000/v1"

uv run --locked python -c "
from openai import OpenAI
client = OpenAI(timeout=3600, max_retries=0)
stream = client.chat.completions.create(
    model='deepseek-v4-pro',          # a Fake Model
    messages=[{'role': 'user', 'content': 'Hello!'}],
    stream=True,
)
for chunk in stream:
    print(chunk.choices[0].delta.content or '', end='')
"
# ③ Meanwhile, open the web console — the task is waiting for YOUR answer
# ④ Once you submit your reply, the caller receives the (pseudo-)streamed output
```

## 🗺️ Roadmap

| Stage | Scope | Status |
|---|---|---|
| M0–M1 | Structure baseline · product/arch/API/DB/UI specs | ✅ |
| M2 | Atomic domain-model & database rebuild (20 tables) | ✅ |
| M3 | Users · invitations · permission loop | ✅ |
| M4 | IM connections & task delivery (5 connector platforms) | ✅ |
| M5 | Fake Model catalog · groups · API keys · admission control | ✅ |
| M6 | Three-protocol contracts · task console · human reply loop | ✅ |
| M7 | LLM configs · draft generation · auto-forwarding · cross-protocol matrix · streaming | ✅ |
| M8 | Global web assistant (context redaction) | ✅ |
| M9 | Dashboard stats · log auditing · UX polish | ✅ |
| M10 | Deployment & ops baseline | Baseline delivered; online key rotation remains |
| M11 | Release acceptance | Local quality gates; real integration checks required |
| M12 | Tool-call passthrough (sandbox removed) | ✅ |
| M13 | Trace-linked logs, IM ownership isolation, retention | ✅ |
| M14 | Unified reply workbench | ✅ |

Full plan in [ROADMAP](docs/ROADMAP.md) (Chinese). Current test totals are reported by the quality gates below.

M12 originally shipped an isolated tool sandbox; it has been removed. The gateway no longer
executes tools — human replies may only reference tools declared by the caller (validated by
name), and the caller executes them at their own risk.

The deployed service exposes `/api/*` for the console, `/v1/*` for the three supported
inference protocols, `/connectors/*` for connector entry points, and `/healthz` for
liveness. There is no compatibility route for the retired reply page or legacy database
schema.

## 🤝 Contributing

```bash
# Quality gates — must pass before every commit
uv lock --check
uv run --locked ruff format --check app tests
uv run --locked ruff check app tests
uv run --locked python -m pytest -q
cd admin && npm ci && npm run build && npm test
```

See [AGENTS.md](AGENTS.md) and [CONTRIBUTING.md](CONTRIBUTING.md) for conventions.

## 📄 License

[AGPL-3.0](LICENSE) © Human LLM Gateway Contributors

> Modified versions offered over a network must offer corresponding source to remote users.

## ⭐ Star History

[![Star History Chart](https://api.star-history.com/svg?repos=GuSheng107/human-llm-gateway&type=Date)](https://star-history.com/#GuSheng107/human-llm-gateway&Date)

---

<div align="center">

**If this project helps you, please consider giving it a star ⭐**

[Report Issues](https://github.com/GuSheng107/human-llm-gateway/issues) · [Discussions](https://github.com/GuSheng107/human-llm-gateway/discussions)

Special thanks to the [Linux.do community](https://linux.do/) for the discussion,
feedback, and encouragement that helped this project reach deployment.

</div>
