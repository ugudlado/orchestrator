# Tasks — Tailscale direct access for RunPod vLLM

- [x] T-1: Add Tailscale bootstrap to pod startup and bind vLLM for direct access
  Verify: `bash -n scripts/setup-vllm.sh scripts/gpu.sh` passes and `rg -n "0.0.0.0|TAILSCALE_AUTHKEY|TAILSCALE_HOSTNAME" scripts/setup-vllm.sh scripts/gpu.sh` shows the direct-access wiring.

- [x] T-2: Update the manager and route docs to resolve pod hostnames instead of localhost
  Verify: `node --check scripts/llm-manager/server.js` passes and `rg -n "localhost:8000|gpu27b:8000|gpu80b:8000" scripts/llm-manager/server.js scripts/routes.yaml scripts/sample-routes.yaml scripts/README.md` shows the direct tailnet URLs in the model routing docs/config.
  depends: T-1

- [x] T-3: Run the direct-access smoke checks and confirm the legacy tunnel remains an explicit fallback
  Verify: `bash -n scripts/setup-vllm.sh scripts/gpu.sh` and `node --check scripts/llm-manager/server.js` pass, and the `tunnel` command remains present in `scripts/gpu.sh` as the fallback path.
  depends: T-2
