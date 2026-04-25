# Design: Tailscale direct access for RunPod vLLM

## Context

The existing RunPod flow still assumes a localhost SSH tunnel for model traffic, which means the pod's vLLM server is only reachable through a local port forward. The repo already has a Tailscale-based mental model in the notes, so the implementation should shift the happy path to direct tailnet URLs while keeping the tunnel as an escape hatch for pods that are not yet enrolled.

## Goals / Non-Goals

### Goals

- Make the pod hostname the preferred vLLM endpoint when Tailscale credentials are available
- Remove the localhost tunnel from the default create/status path
- Keep the legacy tunnel command available as an explicit fallback
- Update route examples and docs so the direct access path is the primary one

### Non-Goals

- Remove RunPod SSH management entirely
- Build a new model gateway or reverse proxy
- Provision Tailscale accounts or auth keys outside this repo

## Approaches Considered

### Approach 1: Direct tailnet with conditional legacy fallback

Install/start Tailscale in the pod when credentials are present, bind vLLM on a reachable interface, and have the manager resolve `http://<pod-name>:8000`. If Tailscale credentials are missing, keep the existing SSH tunnel path as a fallback.

Pros:
- Safe incremental cutover
- Works for both tailnet-enabled and legacy setups
- Minimal churn to the existing RunPod workflow

Cons:
- Slightly more branching in shell scripts
- Leaves a fallback path in the repo

### Approach 2: Hard cutover to tailnet-only

Require Tailscale for every pod and delete the tunnel path entirely.

Pros:
- Smallest conceptual surface area once complete
- No branching between access modes

Cons:
- Risky rollout because it blocks any environment missing Tailscale credentials
- Harder to validate without a complete infrastructure rollout

### Selected Approach

Approach 1 is selected because the repo currently has a working SSH-tunnel path and the main goal is to make Tailscale the preferred access mode, not to break existing pod workflows. The conditional fallback keeps the change small and deterministic while still removing localhost tunneling from the default happy path.

## High-Level Design

### Architecture Overview

`gpu.sh` remains the entrypoint for pod lifecycle operations. When Tailscale credentials are available, it passes hostname/auth env into the remote setup script. `setup-vllm.sh` starts Tailscale inside the pod, binds vLLM to a reachable interface, and writes logs to the shared volume. `llm-manager/server.js` then proxies directly to the pod hostname via MagicDNS instead of localhost.

### Key Abstractions

- **Tailnet hostname**: the pod name reused as the stable network address
- **Access mode switch**: Tailscale-enabled pods use direct hostnames; non-tailnet runs keep the tunnel fallback
- **Endpoint resolver**: the manager converts a running pod record into a direct HTTP endpoint

## Low-Level Design

### Components

- `scripts/setup-vllm.sh`
  - Detect whether Tailscale credentials are present
  - Install/start Tailscale in userspace networking mode when available
  - Bind vLLM on `0.0.0.0` in direct mode so the pod is reachable over tailnet
  - Keep the current local bind behavior when Tailscale is not configured

- `scripts/gpu.sh`
  - Pass `TAILSCALE_AUTHKEY` and `TAILSCALE_HOSTNAME` to remote setup when configured
  - Stop auto-opening the SSH tunnel in direct mode
  - Keep `tunnel` as an explicit fallback command
  - Update status/health text to report the tailnet URL in direct mode

- `scripts/llm-manager/server.js`
  - Resolve pod endpoints using the pod name rather than `localhost:8000`
  - Preserve the current API surface for `/v1/models` and `/v1/chat/completions`

- `scripts/routes.yaml` and `scripts/sample-routes.yaml`
  - Replace localhost vLLM URLs with tailnet hostname URLs

- `scripts/README.md`
  - Explain the direct tailnet flow and when the fallback tunnel is still appropriate

### Data Flow

1. Operator runs `gpu.sh create`.
2. The script creates the pod, forwards Tailscale env when available, and runs the remote setup.
3. The remote setup joins Tailscale, binds vLLM on the pod network interface, and starts the server.
4. Local tooling reaches the pod directly via `http://<pod-name>:8000`.
5. The manager proxy points at the same tailnet hostname, so OpenAI-compatible requests follow the same path.

### State Management

- Tailscale state is stored on the shared volume so pod restarts do not require a fresh auth flow.
- The repository state is unchanged aside from static config/doc updates.

### Error Handling

- Missing Tailscale credentials trigger the legacy tunnel fallback instead of a hard failure.
- If Tailscale startup fails, the scripts must log the problem and keep the operator informed of which access mode is active.
- Endpoint resolution should fail with a clear message if no running pod can be found.

## Constraints

None beyond standard project conventions.

## Trade-offs

Preserving the tunnel fallback keeps the change safe and backwards compatible, but it means the repo continues to contain some localhost-related code paths for non-tailnet environments.

## Decisions

- Use pod-name MagicDNS URLs for direct access → this is deterministic and matches the existing naming scheme → the manager can resolve endpoints without extra discovery calls.
- Keep the tunnel as a fallback command → this avoids a hard cutover and supports existing environments → the default workflow still moves to tailnet direct access.

## Open Questions

- Do we want a future cleanup pass to remove the fallback tunnel code entirely once every target pod is Tailscale-enabled?
