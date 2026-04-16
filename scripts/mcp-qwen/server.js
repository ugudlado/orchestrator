import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import { readFile } from "node:fs/promises";
import YAML from "yaml";

// --- Config ---

const ROUTES_PATH = process.argv[2] || process.env.LLM_ROUTES || new URL("../llm-proxy/routes.yaml", import.meta.url).pathname;

let config = { agents: {}, models: {} };

async function loadConfig() {
  const raw = await readFile(ROUTES_PATH, "utf8");
  config = ROUTES_PATH.endsWith(".yaml") || ROUTES_PATH.endsWith(".yml") ? YAML.parse(raw) : JSON.parse(raw);
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
  const m = config.models?.[alias];
  if (!m) return null;
  return {
    url: m.url,
    model: m.model,
    api_key: m.api_key ? resolveEnvVar(m.api_key) : null,
  };
}

// --- MCP Server ---

const server = new McpServer({ name: "local-llm", version: "1.0.0" });

server.tool(
  "llm_submit",
  "Send work to an external LLM. Agent name maps to a model backend via routes.yaml. Only for non-native agents.",
  {
    agent: z.string().describe("Agent name (e.g. developer, discoverer)"),
    messages: z.array(z.object({ role: z.string(), content: z.string() })),
    temperature: z.number().optional(),
    max_tokens: z.number().optional(),
  },
  async ({ agent, messages, temperature, max_tokens }) => {
    await loadConfig();

    const agentConfig = resolveAgent(agent);
    if (!agentConfig) throw new Error(`Unknown agent "${agent}". Available: ${Object.keys(config.agents).join(", ")}`);
    if (agentConfig.mode === "native") throw new Error(`Agent "${agent}" is native (${agentConfig.model}). Use host sub-agent.`);

    const modelConfig = resolveModel(agentConfig.model);
    if (!modelConfig) throw new Error(`No model config for "${agentConfig.model}". Available: ${Object.keys(config.models || {}).join(", ")}`);

    const url = (modelConfig.url || "").replace(/\/$/, "");
    if (!url) throw new Error(`No url for model "${agentConfig.model}"`);
    const headers = { "Content-Type": "application/json" };
    if (modelConfig.api_key) headers.Authorization = `Bearer ${modelConfig.api_key}`;

    const body = { messages, temperature: temperature ?? 0.2, max_tokens: max_tokens ?? 4096 };
    if (modelConfig.model) body.model = modelConfig.model;

    const resp = await fetch(`${url}/chat/completions`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(300_000),
    });
    const data = await resp.json();
    if (data.error) throw new Error(typeof data.error === "string" ? data.error : JSON.stringify(data.error));

    const content = data.choices?.[0]?.message?.content ?? JSON.stringify(data);
    const usage = data.usage ?? {};
    return {
      content: [{
        type: "text",
        text: `${content}\n\n---\n_Agent: ${agent} | Model: ${modelConfig.model || "?"} | Tokens: ${usage.prompt_tokens ?? "?"}in/${usage.completion_tokens ?? "?"}out_`,
      }],
    };
  }
);

await loadConfig();
const transport = new StdioServerTransport();
await server.connect(transport);
