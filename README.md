# Stedden

**AI receptionist for independent US med spas.** One promise: *never lose another
consultation request.*

A caller dials the spa. The AI greets them, answers questions strictly from the
spa's knowledge base, captures a structured lead, emails it to the owner, and
stores the transcript. Managed service first, SaaS later.

---

## Architecture in one picture

```
Caller → Twilio number → ConversationRelay (Deepgram STT + ElevenLabs TTS)
         → voice_worker (WebSocket) → Brain (OpenAI Responses API, structured output)
         → Tool Executor (save_lead / send_email / end_call)
         → Postgres (Supabase) + Resend email + Next.js admin
```

Three separable pieces:

1. **The Brain** (`backend/core`) — a *pure function*: `(knowledge base + history +
   current state) → { say, lead, next state, actions }`. No side effects, no
   telephony, no database, no framework (no LangChain). Testable by typing.
2. **The Tool Executor** (`backend/executor`) — the *only* place side effects
   happen. It runs the `actions` the Brain asks for.
3. **The Record** — Postgres, Resend, and a thin admin panel.

Every conversation is a **finite state machine**: `GREETING → INTENT_DISCOVERY →
FAQ → LEAD_CAPTURE → CONFIRMATION → ESCALATION → GOODBYE`. The Brain always knows
the current state and returns the next one; illegal transitions are clamped
(`core/states.py`).

### Why these choices

- **Twilio ConversationRelay, not LiveKit** — Twilio owns audio, barge-in, STT and
  TTS; we own only the text Brain. Fastest path to a demo, and the Brain moves to
  LiveKit unchanged if we ever need deeper audio control.
- **Cascaded (STT → text LLM → TTS), not speech-to-speech** — a text model is far
  easier to keep grounded and to extract structured leads from. Grounding is the
  whole product.
- **One structured-output call per turn** — the Brain returns `say`, `lead`,
  `state`, and `actions` in a single strict-JSON Responses call. Keeps us inside
  the ~1s conversational latency budget.

## Repository layout

```
stedden/
├── backend/
│   ├── core/         # Brain + contracts (schemas.py, states.py, prompts/) — no I/O
│   ├── executor/     # Tool Executor — the only place side effects happen
│   ├── api/          # FastAPI REST: admin, leads, KB
│   ├── voice_worker/ # FastAPI WebSocket: ConversationRelay bridge + TwiML
│   ├── simulator/    # Text-only CLI to talk to the Brain — no phone required
│   ├── db/           # SQLAlchemy models + Alembic migrations
│   ├── seed/         # Lumera Aesthetics synthetic demo data
│   └── tests/
└── frontend/         # Next.js 15 admin: Dashboard, Calls, Leads, KB, Settings
```

## Local development

```bash
# 1. Backend deps (uv)
cd backend && uv sync --extra dev
```

```bash
# 2. Database: migrations + Lumera seed (Homebrew Postgres or docker compose up db)
cd backend && createdb stedden && uv run alembic upgrade head && uv run python -m seed.seed_db
```

```bash
# 3. Talk to the AI front desk WITHOUT a phone
cd backend && uv run stedden-sim --persist
```

```bash
# 4. Run the services
cd backend && uv run uvicorn voice_worker.main:app --port 8080 --reload
```

- `api` → http://localhost:8000 · `voice_worker` (Twilio WSS target) → http://localhost:8080
- Copy `backend/.env.example` → `backend/.env` and fill in keys.

## Taking a real phone call

Twilio must reach the voice worker over a public HTTPS/WSS URL.

```bash
cloudflared tunnel --url http://localhost:8080
```

1. Put the tunnel origin in `backend/.env` as `PUBLIC_BASE_URL` (e.g.
   `https://abc-123.trycloudflare.com`) — the TwiML's `wss://` URL is derived
   from it — and restart the worker.
2. Twilio Console → your number → **A call comes in**: Webhook, HTTP POST,
   `https://<tunnel>/voice/incoming`.
3. **Call status changes** → `https://<tunnel>/voice/status` (HTTP POST).
4. Set `TWILIO_AUTH_TOKEN` so webhook signatures are verified (403 otherwise).
5. Call the number. Twilio speaks the greeting, then opens a WebSocket to
   `/voice/relay`; every caller utterance runs through the same Brain → Action
   Engine → providers path as the text simulator.

| Endpoint | Method | Purpose |
|---|---|---|
| `/voice/incoming` | POST | Inbound-call webhook → `<ConversationRelay>` TwiML |
| `/voice/status` | POST | Call status callback; authoritative for completion |
| `/voice/relay` | WS | ConversationRelay session (text in, text out) |
| `/health` | GET | Liveness + active call count |

## Build order

The Brain is proven by typing before any telephony is wired:

1. ✅ Core contracts (FSM states, Lead, TurnResult, actions) — `core/schemas.py`
2. ✅ Brain + Action Engine + **text simulator**
3. ✅ Database + migrations + Lumera seed + SQL providers
4. ✅ Twilio ConversationRelay voice worker
5. Admin API + Next.js panel
6. Deploy (Railway + Vercel + Supabase)

## Compliance (MVP)

**Synthetic demo data only.** Lumera Aesthetics is fictional. No PHI, no real
patients, no production medical data during the MVP. Before onboarding the first
real med spa: HIPAA review, vendor BAAs (Twilio, OpenAI, Deepgram, ElevenLabs,
Supabase, Resend), and a production security pass.

Because the demo business is in Florida (two-party-consent), the greeting
discloses recording and the automated assistant up front.

## Deployment

- `api` + `voice_worker` → Railway (two services, one image)
- Postgres + Storage → Supabase
- Admin → Vercel (behind Vercel password protection — no auth *system*, but not
  world-readable)
