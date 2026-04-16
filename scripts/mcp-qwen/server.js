import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import { readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import YAML from "yaml";

// --- Config ---

const ROUTES_PATH = process.argv[2] || process.env.LLM_ROUTES || new URL("../llm-proxy/routes.yaml", import.meta.url).pathname;
const __dirname = dirname(fileURLToPath(import.meta.url));
const PRICING_CACHE_PATH = join(__dirname, ".pricing-cache.json");
const PRICING_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json";
let config = { agents: {}, models: {} };
let pricingDb = {};

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

// --- Pricing ---

async function loadPricing() {
  let cached = null;

  // Load cache if exists
  try {
    if (existsSync(PRICING_CACHE_PATH)) {
      cached = JSON.parse(await readFile(PRICING_CACHE_PATH, "utf8"));
      pricingDb = cached.data;
    }
  } catch { /* cache miss */ }

  // Conditional fetch — only download if changed (ETag)
  try {
    const fetchHeaders = {};
    if (cached?.etag) fetchHeaders["If-None-Match"] = cached.etag;

    const resp = await fetch(PRICING_URL, { headers: fetchHeaders, signal: AbortSignal.timeout(10_000) });
    if (resp.status === 304) return; // unchanged
    if (!resp.ok) return; // non-blocking

    const data = await resp.json();
    const etag = resp.headers.get("etag");
    pricingDb = data;
    await writeFile(PRICING_CACHE_PATH, JSON.stringify({ etag, data })).catch(() => {});
  } catch {
    // Non-blocking — use cached data or cost will be null
  }
}

function lookupCost(model, inputTokens, outputTokens) {
  if (!model || Object.keys(pricingDb).length === 0) return null;

  // Try exact match, then common prefixed variants
  const candidates = [
    model,
    `openrouter/${model}`,
    model.split("/").pop(),  // e.g. "qwen3-coder-30b" from "qwen/qwen3-coder-30b"
  ];

  let entry = null;
  for (const key of candidates) {
    if (pricingDb[key]) { entry = pricingDb[key]; break; }
  }
  if (!entry) return null;

  const inputCost = (entry.input_cost_per_token ?? 0) * inputTokens;
  const outputCost = (entry.output_cost_per_token ?? 0) * outputTokens;
  return Math.round((inputCost + outputCost) * 1_000_000) / 1_000_000; // 6 decimal places
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
    const inputTokens = usage.prompt_tokens ?? 0;
    const outputTokens = usage.completion_tokens ?? 0;
    const totalTokens = usage.total_tokens ?? (inputTokens + outputTokens);
    const costUsd = lookupCost(modelConfig.model, inputTokens, outputTokens);
    const usageLines = [
      "---llm_usage---",
      `agent: ${agent}`,
      `model: ${modelConfig.model || "unknown"}`,
      `input_tokens: ${inputTokens}`,
      `output_tokens: ${outputTokens}`,
      `total_tokens: ${totalTokens}`,
    ];
    if (costUsd !== null) usageLines.push(`cost_usd: ${costUsd}`);
    usageLines.push("---end_usage---");
    const usageBlock = usageLines.join("\n");
    return {
      content: [{
        type: "text",
        text: `${content}\n\n${usageBlock}`,
      }],
    };
  }
);

await Promise.all([loadConfig(), loadPricing()]);
const transport = new StdioServerTransport();
await server.connect(transport);
