import express from "express";
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const SCRIPTS_DIR = join(__dirname, "..");
const ENV_FILE = join(SCRIPTS_DIR, ".env");
const PORT = process.env.PORT || 3456;

const RUNPOD_API = "https://api.runpod.io/graphql";
const DEFAULT_IMAGE = "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04";

// --- Helpers ---

async function readEnv() {
  try {
    const raw = await readFile(ENV_FILE, "utf8");
    const vars = {};
    for (const line of raw.split("\n")) {
      const t = line.trim();
      if (!t || t.startsWith("#")) continue;
      const eq = t.indexOf("=");
      if (eq < 0) continue;
      vars[t.slice(0, eq)] = t.slice(eq + 1).replace(/^"|"$/g, "");
    }
    return vars;
  } catch { return {}; }
}

function getApiKey(env) {
  return env.RUNPOD_API_KEY || process.env.RUNPOD_API_KEY || "";
}

// --- RunPod GraphQL ---

async function runpodGql(apiKey, query, variables = {}) {
  const resp = await fetch(RUNPOD_API, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${apiKey}` },
    body: JSON.stringify({ query, variables }),
    signal: AbortSignal.timeout(30_000),
  });
  const data = await resp.json();
  if (data.errors) throw new Error(data.errors.map((e) => e.message).join("; "));
  return data.data;
}

async function listPods(apiKey) {
  const data = await runpodGql(apiKey, `
    query { myself { pods {
      id name desiredStatus costPerHr
      machine { gpuDisplayName dataCenterId }
      runtime { uptimeInSeconds ports { ip isIpPublic privatePort publicPort type } }
    }}}
  `);
  return data.myself?.pods || [];
}

async function createPod(apiKey, { name, gpu, image, containerDisk, env: podEnv }) {
  const data = await runpodGql(apiKey, `
    mutation($input: PodFindAndDeployOnDemandInput!) {
      podFindAndDeployOnDemand(input: $input) {
        id name desiredStatus costPerHr imageName
        machine { gpuDisplayName location }
      }
    }
  `, {
    input: {
      name, gpuTypeId: gpu, imageName: image || DEFAULT_IMAGE,
      containerDiskInGb: containerDisk || 30, cloudType: "ALL", gpuCount: 1, volumeInGb: 0,
      env: podEnv || [],
    },
  });
  return data.podFindAndDeployOnDemand;
}

async function terminatePod(apiKey, podId) {
  await runpodGql(apiKey, `mutation($input: PodTerminateInput!) { podTerminate(input: $input) }`, { input: { podId } });
}

async function listGpuTypes(apiKey) {
  const data = await runpodGql(apiKey, `query { gpuTypes { id displayName memoryInGb securePrice communityPrice } }`);
  return (data.gpuTypes || []).filter((g) => g.memoryInGb >= 24);
}

// --- Express ---

const app = express();
app.use(express.json());

app.get("/", (_req, res) => res.sendFile(join(__dirname, "index.html")));

app.get("/api/status", async (_req, res) => {
  try {
    const env = await readEnv();
    const apiKey = getApiKey(env);
    if (!apiKey) return res.json({ error: "RUNPOD_API_KEY not set", pods: [] });
    res.json({ pods: await listPods(apiKey) });
  } catch (e) { res.status(500).json({ error: e.message }); }
});

app.get("/api/gpus", async (_req, res) => {
  try {
    const env = await readEnv();
    res.json({ gpus: await listGpuTypes(getApiKey(env)) });
  } catch (e) { res.status(500).json({ error: e.message }); }
});

app.post("/api/pods", async (req, res) => {
  try {
    const { name, model, gpu, container_disk, gpu_fallbacks } = req.body;
    const env = await readEnv();
    const apiKey = getApiKey(env);

    const candidates = [gpu || "NVIDIA A40"];
    if (gpu_fallbacks) {
      for (const fb of gpu_fallbacks.split(",")) { const t = fb.trim(); if (t) candidates.push(t); }
    }

    let pod = null, lastError = "";
    for (const gpuType of candidates) {
      try {
        pod = await createPod(apiKey, {
          name: name || "vllm",
          gpu: gpuType,
          containerDisk: parseInt(container_disk || "50"),
          env: [
            { key: "VLLM_MODEL", value: model || "" },
            { key: "VLLM_MAX_LEN", value: "65536" },
          ],
        });
        break;
      } catch (e) { lastError = `${gpuType}: ${e.message}`; }
    }

    if (!pod) return res.json({ ok: false, error: `All GPUs failed. Last: ${lastError}` });
    res.json({ ok: true, pod });
  } catch (e) { res.status(500).json({ ok: false, error: e.message }); }
});

app.delete("/api/pods/:id", async (req, res) => {
  try {
    const env = await readEnv();
    await terminatePod(getApiKey(env), req.params.id);
    res.json({ ok: true });
  } catch (e) { res.status(500).json({ ok: false, error: e.message }); }
});

// --- OpenAI-compatible API (like OpenRouter, but for your pods) ---
// Model name = pod name. Manager looks up the pod's SSH/port and proxies.

// Auth: Bearer token must match LLM_MANAGER_KEY env var (if set)
function authMiddleware(req, res, next) {
  const requiredKey = process.env.LLM_MANAGER_KEY;
  if (!requiredKey) return next();
  const provided = (req.headers.authorization || "").replace("Bearer ", "");
  if (provided !== requiredKey) return res.status(401).json({ error: "Invalid API key" });
  next();
}

// Cache pod endpoints (TTL 30s)
let podCache = { pods: [], ts: 0 };

async function getCachedPods() {
  if (Date.now() - podCache.ts < 30_000) return podCache.pods;
  try {
    const env = await readEnv();
    podCache = { pods: await listPods(getApiKey(env)), ts: Date.now() };
  } catch {}
  return podCache.pods;
}

function getPodEndpoint(pod) {
  // Find the SSH port to construct the tunnel URL
  // When tunnel is active, vLLM is on localhost:8000
  // For now, pods are accessed via SSH tunnel — the proxy routes to localhost:8000
  // TODO: support direct pod IP access for hosted deployments
  return "http://localhost:8000";
}

app.get("/v1/models", authMiddleware, async (_req, res) => {
  const pods = await getCachedPods();
  const models = pods
    .filter((p) => p.desiredStatus === "RUNNING")
    .map((p) => ({ id: p.name || p.id, object: "model", owned_by: "runpod" }));
  res.json({ object: "list", data: models });
});

app.post("/v1/chat/completions", authMiddleware, async (req, res) => {
  const podName = req.body.model;
  if (!podName) return res.status(400).json({ error: "model (pod name) is required" });

  const pods = await getCachedPods();
  const pod = pods.find((p) => (p.name === podName || p.id === podName) && p.desiredStatus === "RUNNING");
  if (!pod) {
    const available = pods.filter((p) => p.desiredStatus === "RUNNING").map((p) => p.name || p.id);
    return res.status(404).json({ error: `No running pod "${podName}". Available: ${available.join(", ") || "none"}` });
  }

  const endpoint = getPodEndpoint(pod);
  try {
    const { model: _discard, ...rest } = req.body;
    const upstream = await fetch(`${endpoint}/v1/chat/completions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(rest),
      signal: AbortSignal.timeout(300_000),
    });
    const data = await upstream.json();
    data._manager = { pod: pod.name, gpu: pod.machine?.gpuDisplayName };
    res.status(upstream.status).json(data);
  } catch (e) {
    res.status(502).json({ error: `Pod "${podName}" unreachable: ${e.message}` });
  }
});

app.listen(PORT, () => console.log(`LLM Manager on http://localhost:${PORT}`));
