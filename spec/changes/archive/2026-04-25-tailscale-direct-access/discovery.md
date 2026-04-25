---
feature-id: tailscale-direct-access
linear-ticket: null
---

# Discovery Brief: Tailscale direct access for RunPod vLLM

## Feature Summary

Move the RunPod model-serving path off the localhost SSH tunnel and onto Tailscale-reachable hostnames so the pod can be reached directly from the tailnet once Tailscale is configured. The goal is to keep the existing workflow shape, but make the preferred access path direct and deterministic instead of depending on a local port forward.

## Personas & Actors

- Developer/operator running `scripts/gpu.sh` from their workstation
- RunPod GPU pod that hosts vLLM
- LLM Manager API proxy that forwards OpenAI-compatible requests to pods
- Tailscale daemon running inside the pod

## Use Cases

### Happy Path

UC-1: Tailnet-backed pod startup — the operator wants the pod to join Tailscale and expose vLLM on the pod hostname so that the workstation can call `http://<pod-name>:8000/v1/models` without a localhost tunnel.
UC-2: Direct model routing — the LLM Manager wants to resolve a running pod to its Tailscale hostname so that completions are proxied directly to the model service instead of `localhost:8000`.

### Error & Edge Cases

UC-E1: Missing Tailscale auth — what happens when a pod starts without Tailscale credentials and must fall back to the legacy SSH-tunnel access path.

## Scope

### In Scope

- Bootstrap Tailscale inside the pod when credentials are provided
- Bind vLLM to a tailnet-reachable interface in Tailscale mode
- Resolve pod endpoints in the manager via the pod hostname instead of localhost
- Update docs and route examples to reflect direct tailnet access
- Keep the explicit tunnel command as a fallback for non-tailnet runs

### Out of Scope

- Provisioning or managing the Tailscale account itself — external setup outside this repo
- Removing RunPod SSH management entirely — the pod lifecycle still uses SSH for setup and maintenance
- Replacing the LLM Manager UI/API with a different service — not needed for this cutover

## UI Direction

N/A — no UI components.

## Key Decisions

- Prefer Tailscale direct access when credentials are available, but keep the legacy tunnel as an explicit fallback to avoid blocking non-tailnet environments.
- Use the pod name as the Tailscale hostname so routing stays deterministic and matches the existing model naming convention.

## Open Questions

- OQ-1: Do all target RunPod images allow the Tailscale install script to run cleanly in userspace networking mode without extra capabilities?
