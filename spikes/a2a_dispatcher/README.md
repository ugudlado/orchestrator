# A2A Dispatcher SDK Spike (PRJ-16)

Python spike of the orchestrator dispatcher (backlog → A2A agent → workflows)
using the **official A2A SDK** (`a2a-sdk` 1.1.2, a2a-protocol.org). Demonstrates
Agent Card discovery, skill/interface selection, task delegation
(SendMessage / SendStreamingMessage), tracking (polling + SSE streaming), and
Signed Agent Card (JWS) identity verification.

> Protocol background and platform guidance:
> `/home/trader/vault/notes/research/2026-08-17_a2a-dispatcher-sdk-spike.md`
> (deliverable) and `/home/trader/vault/notes/topics/Tech/a2a-agent-protocols.md`.

## What it proves

| Requirement | Where |
|---|---|
| (a) discover a worker via its Agent Card | `dispatcher.py` [1] via `A2ACardResolver` (`/.well-known/agent-card.json`) |
| (b) select skill / interface | `dispatcher.py` [2] tag-based routing + JSONRPC v1.0 interface pick |
| (c) delegate a task (SendMessage / SendStreamingMessage) | `dispatcher.py` [3] — SDK auto-negotiates: streaming `SendStreamingMessage` when card+client support SSE, else `SendMessage` |
| (d) track the task via polling or SSE streaming | `dispatcher.py` [4] — `GetTask` polling loop + `SubscribeToTask` SSE resubscription |
| Signed Agent Cards (JWS) evaluation | `dispatcher.py` [5] + research note §3 — adopt (recommend) |

## Files

- `worker_agent.py` — A2A worker (FastAPI + SDK JSON-RPC routes), 2 skills,
  simulated long-running workflows, optional ES256-signed card + `/jwks.json`.
- `dispatcher.py` — orchestrator dispatcher demonstrating the full flow.
- `run_spike.sh` — start worker, wait for card, run dispatcher, cleanup.
- `requirements.txt` — pinned deps.

## Run

```bash
pip install -r requirements.txt

# unsigned card
./run_spike.sh

# signed card + JWS verification/tamper demo
./run_spike.sh --signed
```

Manual:

```bash
python3 worker_agent.py --port 41999 --signed      # terminal 1
python3 dispatcher.py --base-url http://127.0.0.1:41999 --verify
```

## Key SDK notes (v1.1.2)

- Client (`a2a.client.ClientFactory.create_from_url`) fetches the Agent Card,
  negotiates transport from `supportedInterfaces` (JSONRPC preferred), and
  creates a `Client`.
- `Client.send_message(request)` auto-chooses wire method:
  `SendStreamingMessage` (SSE) when `ClientConfig.streaming` **and**
  `card.capabilities.streaming`; otherwise `SendMessage` (single response).
- Streaming returns an async iterator of `StreamResponse`
  (status_update / message / artifact_update / task).
- Non-streaming: set `ClientConfig(streaming=False, polling=True)` →
  `returnImmediately` → caller owns the `GetTask` polling loop.
- `Client.subscribe(request)` → `SubscribeToTask` (SSE resubscription).
- `a2a.utils.signing`: `create_agent_card_signer` / `create_signature_verifier`
  implement spec §8.4 (JWS + JCS canonicalization + `signatures` exclusion).
- `A2ACardResolver.get_agent_card(signature_verifier=...)` wires card
  signature verification into discovery.