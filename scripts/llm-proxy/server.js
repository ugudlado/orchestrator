import express from "express";
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import YAML from "yaml";

const __dirname = dirname(fileURLToPath(import.meta.url));
const PORT = process.env.PORT || 4000;
const CONFIG_PATH = process.env.LLM_PROXY_CONFIG || join(__dirname, "routes.yaml");

let config = { agents: {}, models: {} };

async function loadConfig() {
  try {
    const raw = await readFile(CONFIG_PATH, "utf8");
    config = CONFIG_PATH.endsWith(".yaml") || CONFIG_PATH.endsWith(".yml") ? YAML.parse(raw) : JSON.parse(raw);
    console.log(`Loaded: ${Object.keys(config.agents).length} agents, ${Object.keys(config.models).length} models`);
  } catch (e) {
    console.error(`Failed to load config: ${e.message}`);
  }
}

function resolveAgent(name) {
  const mapping = config.agents[name];
  if (!mapping || typeof mapping !== "string") return null;
  const isNative = mapping.startsWith("native_");
  return { mode: isNative ? "native" : "proxy", model: isNative ? mapping.slice(7) : mapping };
}

function resolveEnvVar(v) {
  if (typeof v !== "string" || !v.startsWith("$")) return v;
  const envVal = process.env[v.slice(1)];
  if (!envVal) throw new Error(`Env var ${v.slice(1)} is not set`);
  return envVal;
}

function resolveModel(alias) {
  const m = config.models[alias];
  if (!m) return null;
  const resolved = { url: m.url, args: {} };
  if (m.args) {
    for (const [k, v] of Object.entries(m.args)) {
      resolved.args[k] = resolveEnvVar(v);
    }
  }
  return resolved;
}

// --- Express ---

const app = express();
app.use(express.json({ limit: "10mb" }));

app.get("/health", (_req, res) => res.json({ status: "ok" }));

app.get("/routes", (_req, res) => res.json({
  agents: config.agents,
  models: Object.fromEntries(
    Object.entries(config.models).map(([k, v]) => [k, { url: v.url, model: v.args?.model }])
  ),
}));

app.get("/routes/:agent", (req, res) => {
  const agent = resolveAgent(req.params.agent);
  if (!agent) return res.json({ agent: req.params.agent, mode: "unknown" });
  res.json({ agent: req.params.agent, mode: agent.mode, model: agent.model });
});

app.post("/reload", async (_req, res) => {
  await loadConfig();
  res.json({ ok: true });
});

// Chat completions — resolves agent to model, forwards request
app.post("/v1/chat/completions", async (req, res) => {
  const agentName = req.body.agent;
  if (!agentName) return res.status(400).json({ error: "agent is required" });

  const agent = resolveAgent(agentName);
  if (!agent) return res.status(404).json({ error: `Unknown agent "${agentName}"` });
  if (agent.mode === "native") return res.status(400).json({ error: `Agent "${agentName}" is native (${agent.model}). Use host sub-agent.` });

  let modelConfig;
  try {
    modelConfig = resolveModel(agent.model);
  } catch (e) {
    return res.status(500).json({ error: e.message });
  }
  if (!modelConfig) return res.status(404).json({ error: `No model config for "${agent.model}"` });

  const url = modelConfig.url.replace(/\/$/, "");
  const headers = { "Content-Type": "application/json" };
  if (modelConfig.args.api_key) headers.Authorization = `Bearer ${modelConfig.args.api_key}`;

  const { agent: _a, ...rest } = req.body;
  const body = { ...rest };
  if (modelConfig.args.model) body.model = modelConfig.args.model;

  try {
    const upstream = await fetch(`${url}/chat/completions`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(300_000),
    });
    const data = await upstream.json();
    data._proxy = { agent: agentName, model: modelConfig.args.model };
    res.status(upstream.status).json(data);
  } catch (e) {
    res.status(502).json({ error: `Upstream error: ${e.message}` });
  }
});

await loadConfig();
app.listen(PORT, () => console.log(`LLM Proxy on http://localhost:${PORT}`));
