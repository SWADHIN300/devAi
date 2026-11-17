# DevPilot TypeScript Agent

The TypeScript service preserves the local REST contract used by the Rust CLI and serves the dashboard and MCP Streamable HTTP endpoint. Its stateful agent loop follows Pi's agent-core shape: typed agent/turn/tool events, a message context, tool execution, and bounded follow-up turns. Provider calls support OpenRouter, OpenAI, Codex, Anthropic/Claude, and NVIDIA.

The HTTP planner currently uses non-streaming provider requests; `generatePlan` accepts an event callback for agent lifecycle and tool events. Tool calls execute sequentially and are limited to four per provider turn and five total turns.

See the root README for setup and operational details. Start locally with `npm install`, `npx playwright install chromium`, and `npm start`.
