import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { randomUUID } from "node:crypto";

const ROOT = dirname(fileURLToPath(import.meta.url));
const HOST = process.env.CODEX_GATEWAY_HOST || "127.0.0.1";
const PORT = Number.parseInt(process.env.CODEX_GATEWAY_PORT || "18790", 10);
const WORKDIR = process.env.CODEX_GATEWAY_WORKDIR || ROOT;
const LOCAL_CODEX_ENTRY = join(ROOT, "node_modules", "@openai", "codex", "bin", "codex.js");
const CONFIGURED_CODEX_BIN = (process.env.CODEX_BIN || "").trim();
const USE_LOCAL_CODEX = !CONFIGURED_CODEX_BIN && existsSync(LOCAL_CODEX_ENTRY);
const CODEX_BIN = CONFIGURED_CODEX_BIN || (USE_LOCAL_CODEX ? process.execPath : "codex");
const CODEX_PREFIX_ARGS = USE_LOCAL_CODEX ? [LOCAL_CODEX_ENTRY] : [];
const CODEX_LABEL = USE_LOCAL_CODEX ? "@openai/codex (local package)" : CODEX_BIN;
const DEFAULT_MODEL = process.env.CODEX_GATEWAY_MODEL || "";
const TIMEOUT_MS = Number.parseInt(process.env.CODEX_GATEWAY_TIMEOUT_MS || "120000", 10);
const MAX_BODY_BYTES = Number.parseInt(process.env.CODEX_GATEWAY_MAX_BODY_BYTES || "1048576", 10);

const server = createServer(async (req, res) => {
  const startedAt = Date.now();
  try {
    if (req.method === "GET" && req.url === "/health") {
      return sendJson(res, 200, {
        ok: true,
        service: "codex-gateway",
        port: PORT,
        model: DEFAULT_MODEL || "codex-default",
        codex: CODEX_LABEL,
      });
    }

    if (req.method !== "POST" || req.url !== "/v1/responses") {
      return sendJson(res, 404, { error: "not_found" });
    }

    const body = await readBody(req);
    const payload = parseRequestBody(body);
    const instructions = asString(payload.instructions);
    const inputText = extractInputText(payload.input);
    const model = normalizeModel(asString(payload.model) || DEFAULT_MODEL);
    const prompt = buildPrompt(instructions, inputText);

    const outputText = await runCodex(prompt, model);
    return sendJson(res, 200, {
      id: `resp_${randomUUID()}`,
      object: "response",
      created_at: Math.floor(Date.now() / 1000),
      model: model || "codex-default",
      output_text: outputText,
      output: [
        {
          type: "message",
          role: "assistant",
          content: [{ type: "output_text", text: outputText }],
        },
      ],
      usage: null,
      gateway_ms: Date.now() - startedAt,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    const status = error instanceof HttpError ? error.status : 500;
    console.error(`[${new Date().toISOString()}] request failed: ${message}`);
    return sendJson(res, status, {
      error: {
        message,
        type: status >= 500 ? "server_error" : "bad_request",
      },
    });
  }
});

server.listen(PORT, HOST, () => {
  console.log(`[codex-gateway] listening on http://${HOST}:${PORT}`);
  console.log(`[codex-gateway] workdir=${WORKDIR}`);
  console.log(`[codex-gateway] codex=${CODEX_LABEL}`);
  console.log(`[codex-gateway] model=${DEFAULT_MODEL || "codex-default"}`);
});

function buildPrompt(instructions, inputText) {
  return [
    "You are running as an HTTP gateway for a desktop Telegram assistant.",
    "Return only the final answer requested by the instructions. Do not mention Codex, tools, files, or gateway internals.",
    "If the instructions ask for JSON, return valid JSON only, without Markdown fences.",
    "",
    "<instructions>",
    instructions || "Return a concise answer.",
    "</instructions>",
    "",
    "<input>",
    inputText || "",
    "</input>",
  ].join("\n");
}

async function runCodex(prompt, model) {
  const runDir = await mkdtemp(join(tmpdir(), "codex-gateway-"));
  const outputPath = join(runDir, "last-message.txt");
  const args = [
    "exec",
    "--skip-git-repo-check",
    "--ephemeral",
    "--sandbox",
    "read-only",
    "--output-last-message",
    outputPath,
  ];
  if (model) {
    args.push("--model", model);
  }
  args.push("-");

  const child = spawn(CODEX_BIN, [...CODEX_PREFIX_ARGS, ...args], {
    cwd: WORKDIR,
    windowsHide: true,
    stdio: ["pipe", "pipe", "pipe"],
    env: {
      ...process.env,
      NO_COLOR: "1",
    },
  });

  let stdout = "";
  let stderr = "";
  const timeout = setTimeout(() => {
    child.kill("SIGTERM");
  }, TIMEOUT_MS);

  try {
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });
    child.stdin.end(prompt, "utf8");

    const exitCode = await new Promise((resolve, reject) => {
      child.on("error", reject);
      child.on("close", resolve);
    });
    if (exitCode !== 0) {
      const details = trimForLog(stderr || stdout);
      const usageLimitMessage = extractUsageLimitMessage(details);
      if (usageLimitMessage) {
        throw new HttpError(429, usageLimitMessage);
      }
      throw new HttpError(500, `codex exited with ${exitCode}: ${details}`);
    }
    const outputText = (await readFile(outputPath, "utf8")).trim();
    if (!outputText) {
      throw new Error(`codex returned empty output: ${trimForLog(stderr || stdout)}`);
    }
    return outputText;
  } finally {
    clearTimeout(timeout);
    await rm(runDir, { recursive: true, force: true });
  }
}

function extractInputText(input) {
  if (typeof input === "string") {
    return input;
  }
  if (!Array.isArray(input)) {
    return input == null ? "" : JSON.stringify(input);
  }
  const chunks = [];
  for (const item of input) {
    if (!item || typeof item !== "object") {
      continue;
    }
    if (typeof item.content === "string") {
      chunks.push(item.content);
      continue;
    }
    if (Array.isArray(item.content)) {
      for (const part of item.content) {
        if (!part || typeof part !== "object") {
          continue;
        }
        if (typeof part.text === "string") {
          chunks.push(part.text);
        } else if (typeof part.input_text === "string") {
          chunks.push(part.input_text);
        }
      }
    }
  }
  return chunks.join("\n");
}

function normalizeModel(model) {
  const value = model.trim();
  if (!value || value === "openclaw" || value === "openclaw/default") {
    return DEFAULT_MODEL.trim();
  }
  return value;
}

function asString(value) {
  return typeof value === "string" ? value : "";
}

function trimForLog(value) {
  return String(value || "").replace(/\s+/g, " ").trim().slice(0, 1200);
}

function extractUsageLimitMessage(value) {
  const text = String(value || "");
  if (!text.toLowerCase().includes("usage limit")) {
    return "";
  }
  const match = text.match(/ERROR:\s+You've hit your usage limit\.[\s\S]*?(?=ERROR:|$)/i);
  return match ? match[0].replace(/\s+/g, " ").trim() : "Codex usage limit reached.";
}

function parseRequestBody(body) {
  try {
    return JSON.parse(body);
  } catch {
    throw new HttpError(400, "Request body must be valid JSON");
  }
}

async function readBody(req) {
  let body = "";
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > MAX_BODY_BYTES) {
      throw new HttpError(413, "Request body too large");
    }
    body += chunk;
  }
  return body;
}

function sendJson(res, status, payload) {
  const raw = JSON.stringify(payload);
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(raw),
  });
  res.end(raw);
}

class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function shutdown(signal) {
  console.log(`[codex-gateway] received ${signal}, shutting down`);
  server.close(() => process.exit(0));
}

process.on("SIGINT", () => shutdown("SIGINT"));
process.on("SIGTERM", () => shutdown("SIGTERM"));
