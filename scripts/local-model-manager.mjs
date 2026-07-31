import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { execSync } from "node:child_process";

const PORT = process.env.LOCAL_MODEL_MANAGER_PORT || 19000;
const BACKEND_PORT = process.env.BACKEND_PORT || 18000;
const ODYSSEUS_URL = "http://localhost:7000";
const ODYSSEUS_TOKEN = "Bearer ody_local_model_manager_token_12345";

let currentModel = null;
let isSwitching = false;
let cachedContainerIp = null;

console.log(`[LocalModelManager] Starting orchestrator service on port ${PORT}...`);

// Resilient helper to get Odysseus container's internal IP address
function getContainerIp() {
  if (cachedContainerIp) return cachedContainerIp;
  try {
    const containerName = execSync("docker ps --filter name=odysseus --format '{{.Names}}'", { encoding: "utf8" }).trim().split("\n")[0];
    if (containerName) {
      const ip = execSync(`docker inspect -f '{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}' ${containerName}`, { encoding: "utf8" }).trim();
      if (ip && ip.match(/^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$/)) {
        console.log(`[LocalModelManager] Detected container IP for ${containerName}: ${ip}`);
        cachedContainerIp = ip;
        return ip;
      }
    }
  } catch (e) {
    console.error(`[LocalModelManager] Resilient container IP check failed:`, e.message);
  }
  return "127.0.0.1";
}

// Helper to communicate with Odysseus
async function fetchOdysseus(apiPath, options = {}) {
  return new Promise((resolve, reject) => {
    const url = `${ODYSSEUS_URL}${apiPath}`;
    const headers = {
      "Authorization": ODYSSEUS_TOKEN,
      ...options.headers
    };
    const reqOptions = {
      method: options.method || "GET",
      headers
    };
    const req = http.request(url, reqOptions, (res) => {
      let data = "";
      res.on("data", chunk => data += chunk);
      res.on("end", () => {
        if (res.statusCode >= 400) {
          reject(new Error(`Odysseus returned status ${res.statusCode}: ${data}`));
        } else {
          try {
            resolve(JSON.parse(data));
          } catch (e) {
            resolve(data);
          }
        }
      });
    });
    req.on("error", reject);
    if (options.body) {
      req.write(typeof options.body === "string" ? options.body : JSON.stringify(options.body));
    }
    req.end();
  });
}

// Check health of served model port on the container IP
async function checkHealth() {
  const ip = getContainerIp();
  return new Promise((resolve) => {
    const req = http.request(`http://${ip}:8080/v1/models`, { method: "GET", timeout: 800 }, (res) => {
      resolve(res.statusCode === 200);
    });
    req.on("error", () => resolve(false));
    req.on("timeout", () => {
      req.destroy();
      resolve(false);
    });
    req.end();
  });
}

// Stop all running served models and cleanup endpoints
async function stopAllModels() {
  console.log("[LocalModelManager] Terminating active model sessions...");
  try {
    const endpoints = await fetchOdysseus("/api/model-endpoints");
    if (Array.isArray(endpoints)) {
      for (const ep of endpoints) {
        if (ep.category === "local") {
          console.log(`[LocalModelManager] Deleting local model endpoint: ${ep.id} (${ep.name})`);
          await fetchOdysseus(`/api/model-endpoints/${ep.id}`, { method: "DELETE" });
        }
      }
    }
  } catch (e) {
    console.error("[LocalModelManager] Error cleaning endpoints:", e.message);
  }

  try {
    const execRes = await fetchOdysseus("/api/shell/exec", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: { command: "tmux list-sessions -F '#{session_name}'" }
    });
    if (execRes && execRes.exit_code === 0 && execRes.stdout) {
      const sessions = execRes.stdout.split("\n").map(s => s.trim()).filter(Boolean);
      for (const session of sessions) {
        if (session.startsWith("serve-") || session.includes("serve")) {
          console.log(`[LocalModelManager] Terminating tmux session: ${session}`);
          await fetchOdysseus("/api/shell/exec", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: { command: `tmux kill-session -t ${session}` }
          });
        }
      }
    }
  } catch (e) {
    console.error("[LocalModelManager] Error stopping tmux sessions:", e.message);
  }
  currentModel = null;
}

// Discover and launch a model via Odysseus
async function launchModel(repoId) {
  console.log(`[LocalModelManager] Discovering model files for: ${repoId}`);
  const cached = await fetchOdysseus("/api/model/cached?model_dir=/home/sorbat30/llama.cpp/models");
  const models = cached.models || [];
  const model = models.find(m => m.repo_id === repoId);
  if (!model) {
    throw new Error(`Model not found in cache/local models: ${repoId}`);
  }
  const ggufFile = model.gguf_files?.find(f => f.role === "model");
  if (!ggufFile) {
    throw new Error(`No model GGUF file found for: ${repoId}`);
  }
  const filePath = `${model.path}/${model.repo_id}/${ggufFile.rel_path}`;
  console.log(`[LocalModelManager] Resolved GGUF path: ${filePath}`);

  // Bind to 0.0.0.0 inside container to expose it to the host
  const cmd = `llama-server -m ${filePath} --port 8080 --host 0.0.0.0 -ngl 99`;
  console.log(`[LocalModelManager] Triggering Odysseus serve with command: ${cmd}`);
  const serveRes = await fetchOdysseus("/api/model/serve", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: { repo_id: repoId, cmd }
  });
  console.log(`[LocalModelManager] Model serve triggered:`, serveRes);
  return serveRes;
}

// Poll until port 8080 becomes available
async function waitForServer(timeoutMs = 60000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const ok = await checkHealth();
    if (ok) {
      console.log(`[LocalModelManager] Model server is ready!`);
      return true;
    }
    await new Promise(r => setTimeout(r, 1000));
  }
  throw new Error(`Model server failed to become ready within ${timeoutMs}ms`);
}

// Get OpenHands API key from disk
function getOpenHandsApiKey() {
  const keyPath = path.join(os.homedir(), ".openhands", "agent-canvas", "api-key.txt");
  try {
    if (fs.existsSync(keyPath)) {
      return fs.readFileSync(keyPath, "utf8").trim();
    }
  } catch (e) {
    // Ignore error
  }
  return null;
}

// Patch SettingsService
async function updateSettingsService(modelName) {
  const apiKey = getOpenHandsApiKey() || process.env.LOCAL_BACKEND_API_KEY;
  if (!apiKey) {
    console.log("[LocalModelManager] OpenHands API key not available, skipping SettingsService update");
    return;
  }
  console.log(`[LocalModelManager] Syncing model selection to OpenHands SettingsService: ${modelName}`);
  return new Promise((resolve) => {
    const payload = {
      agent_settings_diff: {
        llm: {
          base_url: `http://localhost:${PORT}/v1`,
          model: modelName
        }
      }
    };
    const req = http.request(`http://127.0.0.1:${BACKEND_PORT}/api/settings`, {
      method: "PATCH",
      headers: {
        "X-Session-API-Key": apiKey,
        "Content-Type": "application/json"
      }
    }, (res) => {
      let data = "";
      res.on("data", chunk => data += chunk);
      res.on("end", () => {
        console.log(`[LocalModelManager] Settings patch status: ${res.statusCode}`);
        resolve();
      });
    });
    req.on("error", (e) => {
      console.error("[LocalModelManager] Error patching settings:", e.message);
      resolve();
    });
    req.write(JSON.stringify(payload));
    req.end();
  });
}

// Handle request proxying
function proxyRequest(req, res, targetUrl, bodyBuffer) {
  const parsedUrl = new URL(targetUrl);
  const proxyReq = http.request(targetUrl, {
    method: req.method,
    headers: {
      ...req.headers,
      host: parsedUrl.host
    }
  }, (proxyRes) => {
    res.writeHead(proxyRes.statusCode, proxyRes.headers);
    proxyRes.pipe(res);
  });

  proxyReq.on("error", (e) => {
    console.error("[LocalModelManager] Proxy error:", e.message);
    res.writeHead(502, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: `Proxy failed: ${e.message}` }));
  });

  if (bodyBuffer) {
    proxyReq.write(bodyBuffer);
  }
  proxyReq.end();
}

// Main HTTP Server
const server = http.createServer(async (req, res) => {
  // CORS Headers
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS, PATCH, DELETE");
  res.setHeader("Access-Control-Allow-Headers", "*");

  if (req.method === "OPTIONS") {
    res.writeHead(200);
    res.end();
    return;
  }

  // Route: GET /v1/models (Model Discovery)
  if (req.method === "GET" && req.url === "/v1/models") {
    try {
      const cached = await fetchOdysseus("/api/model/cached?model_dir=/home/sorbat30/llama.cpp/models");
      const models = cached.models || [];
      const ggufModels = models.filter(m => m.is_gguf || m.is_local_dir);
      const data = ggufModels.map(m => ({
        id: m.repo_id,
        object: "model",
        created: Math.floor(Date.now() / 1000),
        owned_by: "local"
      }));
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ object: "list", data }));
    } catch (e) {
      console.error("[LocalModelManager] Error listing models:", e.message);
      res.writeHead(500, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ error: e.message }));
    }
    return;
  }

  // Route: GET /api/vram/status — live GPU VRAM telemetry (Target Architecture v3 §18.4)
  if (req.method === "GET" && req.url === "/api/vram/status") {
    try {
      const { execSync } = await import("node:child_process");
      let vramFree = 0, vramTotal = 0, gpuName = "Unknown";
      try {
        const raw = execSync(
          "nvidia-smi --query-gpu=memory.free,memory.total,name --format=csv,noheader,nounits",
          { encoding: "utf8", timeout: 3000 }
        ).trim();
        const parts = raw.split(",").map(s => s.trim());
        vramFree  = parseInt(parts[0], 10) || 0;
        vramTotal = parseInt(parts[1], 10) || 0;
        gpuName   = parts[2] || "NVIDIA GPU";
      } catch (_) {
        // nvidia-smi not available or GPU absent — return zeroes
      }
      const vramUsed = vramTotal - vramFree;
      const usagePct = vramTotal > 0 ? ((vramUsed / vramTotal) * 100).toFixed(1) : "0.0";
      const payload = {
        active_model:      currentModel,
        is_switching:      isSwitching,
        gpu_name:          gpuName,
        vram_free_mb:      vramFree,
        vram_used_mb:      vramUsed,
        vram_total_mb:     vramTotal,
        vram_usage_percent: parseFloat(usagePct),
        timestamp:         Date.now()
      };
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify(payload));
    } catch (e) {
      res.writeHead(500, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ error: e.message }));
    }
    return;
  }

  // Route: GET /api/status — orchestrator health summary
  if (req.method === "GET" && req.url === "/api/status") {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({
      status:        "ok",
      active_model:  currentModel,
      is_switching:  isSwitching,
      port:          PORT,
      timestamp:     Date.now()
    }));
    return;
  }

  // Buffer request body for inspection and proxying
  let bodyBuffer = Buffer.alloc(0);
  req.on("data", chunk => {
    bodyBuffer = Buffer.concat([bodyBuffer, chunk]);
  });

  req.on("end", async () => {
    let parsedBody = {};
    if (bodyBuffer.length > 0) {
      try {
        parsedBody = JSON.parse(bodyBuffer.toString("utf8"));
      } catch (e) {
        // Ignore JSON parse error for non-JSON requests
      }
    }

    // Strip provider prefix (e.g. "openai/chat-3b" -> "chat-3b")
    // LiteLLM adds these prefixes but our local model IDs are bare names
    let requestedModel = parsedBody.model;
    if (requestedModel && requestedModel.includes("/")) {
      requestedModel = requestedModel.split("/").pop();
    }

    // Orchestrate model switching if a new model is requested
    if (requestedModel && requestedModel !== currentModel) {
      if (isSwitching) {
        res.writeHead(503, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: "Orchestrator is currently switching models. Please try again." }));
        return;
      }

      isSwitching = true;
      try {
        console.log(`[LocalModelManager] Requested model: ${requestedModel} (current: ${currentModel})`);
        
        // Stop any running servers first
        await stopAllModels();

        // Launch the new model
        await launchModel(requestedModel);

        // Wait for it to become ready
        await waitForServer();

        // Update settings in OpenHands SettingsService
        await updateSettingsService(requestedModel);

        currentModel = requestedModel;
      } catch (e) {
        console.error(`[LocalModelManager] Switching failed:`, e.message);
        res.writeHead(500, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: `Failed to serve model ${requestedModel}: ${e.message}` }));
        isSwitching = false;
        return;
      }
      isSwitching = false;
    }

    // Proxy the request to the active model server on the container IP
    const ip = getContainerIp();
    const targetUrl = `http://${ip}:8080${req.url}`;
    proxyRequest(req, res, targetUrl, bodyBuffer);
  });
});

// Auto-provision LLM + Agent profiles for all discovered GGUF models
async function provisionProfiles() {
  const apiKey = getOpenHandsApiKey() || process.env.LOCAL_BACKEND_API_KEY;
  if (!apiKey) {
    console.log("[LocalModelManager] No API key available, skipping profile provisioning");
    return;
  }

  // Exclude embedding models from agent profiles (they aren't chat models)
  const excludeFromAgent = new Set(["embedding", "embedding-small"]);

  try {
    // Discover available models
    const cached = await fetchOdysseus("/api/model/cached?model_dir=/home/sorbat30/llama.cpp/models");
    const models = (cached.models || []).filter(m => m.is_gguf || m.is_local_dir);

    // Fetch existing LLM profiles
    const existingProfiles = await new Promise((resolve, reject) => {
      const req = http.request(`http://127.0.0.1:${BACKEND_PORT}/api/profiles`, {
        headers: { "X-Session-API-Key": apiKey }
      }, (res) => {
        let data = "";
        res.on("data", chunk => data += chunk);
        res.on("end", () => {
          try { resolve(JSON.parse(data)); } catch { resolve({ profiles: [] }); }
        });
      });
      req.on("error", reject);
      req.end();
    });

    const existingNames = new Set((existingProfiles.profiles || []).map(p => p.name));

    // Create LLM profiles for models that don't have one
    for (const model of models) {
      const name = model.repo_id;
      if (existingNames.has(name)) continue;

      console.log(`[LocalModelManager] Creating LLM profile: ${name}`);
      await new Promise((resolve) => {
        const payload = JSON.stringify({
          llm: {
            model: `openai/${name}`,
            base_url: `http://localhost:${PORT}/v1`,
            api_key: "local-no-key-needed",
            drop_params: true,
            modify_params: true,
            native_tool_calling: true,
            stream: false,
            timeout: 300,
            num_retries: 3,
            max_message_chars: 30000
          }
        });
        const req = http.request(`http://127.0.0.1:${BACKEND_PORT}/api/profiles/${name}`, {
          method: "POST",
          headers: { "X-Session-API-Key": apiKey, "Content-Type": "application/json" }
        }, (res) => {
          let d = ""; res.on("data", c => d += c); res.on("end", () => resolve(d));
        });
        req.on("error", () => resolve());
        req.write(payload);
        req.end();
      });
    }

    // Fetch existing agent profiles
    const existingAgentProfiles = await new Promise((resolve, reject) => {
      const req = http.request(`http://127.0.0.1:${BACKEND_PORT}/api/agent-profiles`, {
        headers: { "X-Session-API-Key": apiKey }
      }, (res) => {
        let data = "";
        res.on("data", chunk => data += chunk);
        res.on("end", () => {
          try { resolve(JSON.parse(data)); } catch { resolve({ profiles: [] }); }
        });
      });
      req.on("error", reject);
      req.end();
    });

    const existingAgentNames = new Set((existingAgentProfiles.profiles || []).map(p => p.name));

    // Create agent profiles for chat/code/vision models
    for (const model of models) {
      const name = model.repo_id;
      if (excludeFromAgent.has(name) || existingAgentNames.has(name)) continue;

      console.log(`[LocalModelManager] Creating agent profile: ${name}`);
      await new Promise((resolve) => {
        const payload = JSON.stringify({
          name: name,
          agent_kind: "openhands",
          llm_profile_ref: name
        });
        const req = http.request(`http://127.0.0.1:${BACKEND_PORT}/api/agent-profiles/${name}`, {
          method: "POST",
          headers: { "X-Session-API-Key": apiKey, "Content-Type": "application/json" }
        }, (res) => {
          let d = ""; res.on("data", c => d += c); res.on("end", () => resolve(d));
        });
        req.on("error", () => resolve());
        req.write(payload);
        req.end();
      });
    }

    console.log(`[LocalModelManager] Profile provisioning complete (${models.length} models)`);
  } catch (e) {
    console.error("[LocalModelManager] Profile provisioning failed:", e.message);
  }
}

server.listen(PORT, () => {
  console.log(`[LocalModelManager] Orchestrator listening on http://localhost:${PORT}`);
  // Auto-provision profiles after a short delay to let the backend fully warm up
  setTimeout(() => provisionProfiles(), 2000);
});
