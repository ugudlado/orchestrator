import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";

const PROXY_URL = process.env.LLM_PROXY_URL || "http://localhost:4000";

const server = new McpServer({ name: "local-llm", version: "1.0.0" });

server.tool(
  "llm_submit",
  "Send work to an external LLM via the proxy. Agent name maps to a model in routes.yaml. Only for non-native agents.",
  {
    agent: z.string().describe("Agent name (e.g. developer, discoverer)"),
    messages: z.array(z.object({ role: z.string(), content: z.string() })),
    temperature: z.number().optional(),
    max_tokens: z.number().optional(),
  },
  async ({ agent, messages, temperature, max_tokens }) => {
    const resp = await fetch(`${PROXY_URL}/v1/chat/completions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ agent, messages, temperature: temperature ?? 0.2, max_tokens: max_tokens ?? 4096 }),
      signal: AbortSignal.timeout(300_000),
    });
    const data = await resp.json();
    if (data.error) throw new Error(typeof data.error === "string" ? data.error : JSON.stringify(data.error));

    const content = data.choices?.[0]?.message?.content ?? JSON.stringify(data);
    const usage = data.usage ?? {};
    const route = data._proxy ?? {};
    return {
      content: [{
        type: "text",
        text: `${content}\n\n---\n_Agent: ${agent} | Model: ${route.model || "?"} | Tokens: ${usage.prompt_tokens ?? "?"}in/${usage.completion_tokens ?? "?"}out_`,
      }],
    };
  }
);

const transport = new StdioServerTransport();
await server.connect(transport);
