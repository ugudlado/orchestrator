---
feature-id: tailscale-direct-access
linear-ticket: null
---

# Specification: Tailscale direct access for RunPod vLLM

## Motivation

The current RunPod flow still assumes a localhost SSH tunnel for model traffic, which adds an unnecessary hop once the pod and workstation are already on the same Tailscale network. This feature makes the direct tailnet hostname the preferred access path so pod traffic is reachable without local port forwarding while keeping the legacy tunnel as a fallback.

## What Changes

The pod startup path will join Tailscale when credentials are present, bind vLLM on a tailnet-reachable interface, and expose the model service on the pod hostname. The LLM Manager will resolve running pods to their Tailscale hostname instead of `localhost:8000`, and the docs/route examples will describe the direct URL flow rather than the SSH-tunnel path.

## Requirements

### Functional

1. **FR-1**: When Tailscale credentials are present, the pod startup script must install/start Tailscale and join the tailnet using the pod name as the hostname.
2. **FR-2**: When Tailscale mode is active, vLLM must bind to a reachable interface so the service is available on `http://<pod-name>:8000/v1/...` from the tailnet.
3. **FR-3**: The LLM Manager must resolve pod endpoints to the pod hostname on the tailnet instead of hard-coding `localhost:8000`.
4. **FR-4**: The RunPod lifecycle docs and route examples must describe the direct Tailscale URL path and clearly label the SSH tunnel as fallback-only.

### Non-Functional

1. **NFR-1**: The change must remain deterministic and preserve the legacy tunnel path as a fallback when Tailscale credentials are absent.
2. **NFR-2**: The pod hostname used for routing must be explicit and stable so direct URLs do not depend on runtime discovery.

## Architecture

### Components

| File | Change |
|---|---|
| `scripts/setup-vllm.sh` | Add Tailscale bootstrap and choose a reachable bind host when Tailscale is enabled |
| `scripts/gpu.sh` | Pass Tailscale env into remote setup and stop auto-opening the localhost tunnel in direct mode |
| `scripts/llm-manager/server.js` | Resolve pod endpoints using the pod hostname instead of localhost |
| `scripts/routes.yaml` | Point model URLs at Tailscale hostnames |
| `scripts/sample-routes.yaml` | Mirror the Tailscale hostname URLs in the sample config |
| `scripts/README.md` | Document direct tailnet URLs and fallback behavior |

### Data Flow

1. `gpu.sh create` prepares the pod and forwards Tailscale hostname/auth env when available.
2. `setup-vllm.sh` starts Tailscale in the pod, binds vLLM on the reachable interface, and writes the model log to the shared volume.
3. The workstation reaches the pod directly at `http://<pod-name>:8000/v1/...` through Tailscale MagicDNS.
4. `llm-manager/server.js` proxies requests to that tailnet hostname instead of `localhost:8000`.

### State Management

- Pod-local Tailscale state lives on the shared volume so restarts do not require re-authentication.
- The repository itself only stores static configuration and documentation; no new runtime database or cache state is introduced.

### Error Handling

- If Tailscale credentials are missing, the startup path must fall back to the legacy localhost-tunnel behavior rather than failing silently.
- If the pod cannot join Tailnet, the scripts must emit a clear message explaining that the direct path is unavailable.
- Manager routing must fail fast with a readable error if no running pod endpoint can be resolved.

## Constraints

- The fix must stay within the existing RunPod + vLLM shell workflow; no new daemon manager or proxy service is being introduced.
- The repo already relies on pod name conventions, so the tailnet hostname must follow the same naming scheme.
- Users may still need the SSH tunnel command as an explicit fallback while Tailscale is being rolled out.

## Trade-offs

Keeping the tunnel as a fallback adds a small amount of branching in the shell scripts, but it avoids a hard cutover that would strand current users until Tailscale credentials are configured everywhere.

## Decisions

- Use the pod name as the Tailscale hostname → keeps routing deterministic and matches the existing pod naming convention → removes the need for runtime endpoint discovery.
- Prefer direct tailnet access when credentials exist → removes localhost forwarding from the happy path → preserves a fallback for non-tailnet environments.

## Open Questions

- Whether every target RunPod image can run the Tailscale install/start sequence in userspace networking mode without extra permissions.
