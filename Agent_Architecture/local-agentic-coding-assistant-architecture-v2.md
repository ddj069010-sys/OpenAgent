# Local-First Autonomous Agentic Coding Assistant
## Production-Grade System Architecture (v2)

**Document class:** Engineering architecture specification
**Target model:** Single local GGUF model at a time (e.g. Qwen3.6-35B-A3B), served via llama.cpp, OpenAI-compatible API, hot-swappable
**Supersedes:** v1 (initial design). This revision is a critical rewrite, not an incremental patch.

---

## Changelog: What v1 Got Wrong

v1 described a coherent *feature list*. It was not yet a system that survives contact with a long-running, unattended, resource-constrained local process. The gaps fall into five buckets, and this revision adds a subsystem for each:

1. **No concept of the process staying alive.** v1 assumed the backend, the model, and the tools just run. Nothing described what happens under memory pressure, GPU OOM, a hung subprocess, or a laptop that goes to sleep. → **§3 Agent Lifecycle**, **§4 System Health Manager**.
2. **"Single model at a time" was stated but never designed.** Swapping models silently lost KV cache, and nothing described how conversation/task state survives a model swap. → **§5 Model Session Manager**, **§6 Context Persistence Layer**.
3. **Resources were assumed infinite.** Tool execution, browser automation, Docker builds, and embedding generation all compete for the same CPU/GPU/RAM on one machine with no arbitration. → **§7 Resource Scheduler**.
4. **"Retry" and "rollback" were mentioned per-subsystem but not as a system-wide contract.** There was no guarantee that a tool call that crashed mid-execution wouldn't run twice, and no transaction log tying reflection, repair, and rollback together. → **§8 Reliability Layer**.
5. **No way to know if the agent is actually good.** v1 had testing (does the *code* work) but nothing evaluating the *agent* (is it improving, regressing, hallucinating, stalling). → **§14 Evaluation Framework**.

Beyond those five, this revision also fixes concrete design flaws identified in the per-subsystem review below, and adds nine further subsystems (Event Bus formalization, Plugin Framework hardening, Distributed Future Architecture, Prompt Management, Knowledge Layer, Production Features, Security Hardening, Developer Experience, and an updated Database Schema/Folder Structure/Tech Stack to match).

---

## Part I — Critical Review of v1

This is an honest audit. Each row: what was wrong, why it matters in production, and where the fix now lives.

### Orchestration & LangGraph

| Issue | Impact | Fix location |
|---|---|---|
| Single monolithic graph with no sub-graph isolation | A bug in one subtask's execution could corrupt the whole session's state; no way to test a node in isolation | §9 splits the graph into a **supervisor graph** + per-subtask **worker subgraphs**, each independently checkpointed |
| No distinction between "checkpoint for resume" and "checkpoint for audit" | Checkpointing every node write is expensive; auditing needs full history, resume only needs the latest | §8.3 introduces a two-tier checkpoint: lightweight resume checkpoints (SQLite, pruned) + append-only transaction log (audit trail, never pruned) |
| No backpressure if the model is slower than tool execution (or vice versa) | Tool results could queue up unbounded, or the model could be starved waiting on a slow Docker build with no visibility | §7 Resource Scheduler adds bounded queues with explicit backpressure signaling into the state machine (`WAITING` state gets a reason code) |
| Retry budget was per-subtask only | A pathological repo could exhaust retries subtask-by-subtask without ever tripping a session-level circuit breaker | §8.5 adds a session-level circuit breaker (max total repairs per session, not just per subtask) |

### Context Engine

| Issue | Impact | Fix location |
|---|---|---|
| Embedding model choice never specified | "Embeddings" was a bullet point with no model, no dimensionality, no update cost analysis | §11 Knowledge Layer specifies a local embedding model (e.g. a small BGE/E5-class GGUF or ONNX model) and re-embedding cost budget |
| No cache invalidation strategy for LSP servers across model/session switches | LSP servers are expensive to warm; v1 didn't say whether they survive a model swap | §5 Model Session Manager explicitly scopes LSP/embedding state as *shared*, model-independent |
| Context compression described qualitatively, no algorithm | "Summarizes low-relevance chunks" isn't implementable as stated | §6.2 specifies a concrete compression ladder: full text → signature-only → one-line gist → drop, with token-cost thresholds per tier |

### Memory

| Issue | Impact | Fix location |
|---|---|---|
| No versioning on long-term memory | An agent that "learns" a wrong fix has no way to be corrected without deleting the whole entry | §6.3 adds append-only memory versioning with explicit supersession, not overwrite |
| No cross-model compatibility guarantee | Embeddings from one model's embedding space are meaningless to a different embedding model | §6.4 mandates a fixed, model-independent embedding service (not tied to the primary reasoning model) |
| Memory writes had no consistency guarantee against concurrent sessions | Two sessions on the same project could race on bug-memory writes | §8 Reliability Layer adds optimistic-concurrency versioning (same pattern as this memory tool's own `if_version`) |

### Tool System

| Issue | Impact | Fix location |
|---|---|---|
| "Retry" was described per-tool but not idempotency-checked | Retrying a non-idempotent tool (e.g. `git commit`, `POST` to an API) risks double side effects | §8.6 mandates every tool declare an idempotency class; non-idempotent tools require a dedupe key before any retry |
| No timeout/resource ceiling tied to system health | A tool could keep running even as the host approaches OOM | §4 System Health Manager can preempt/pause any running tool when resources cross a critical threshold |
| Sandbox model didn't specify what happens if Docker itself is unavailable | Silent failure or crash | Tool system now requires a capability probe at startup (§3.2) with graceful degradation (fall back to subprocess sandbox with a visible warning) |

### Terminal / Browser / Docker

| Issue | Impact | Fix location |
|---|---|---|
| Background jobs had no lifecycle owner | Nothing said who kills a dev server left running after a session ends | §7.4 Resource Scheduler owns all background job lifecycles; session end triggers a sweep |
| No GPU-awareness for Docker/browser tasks competing with the model for VRAM | A headless Chromium + a 35B model on the same GPU-adjacent box can starve each other | §7 Resource Scheduler treats "model inference" as a first-class, highest-default-priority resource consumer |

### Security

| Issue | Impact | Fix location |
|---|---|---|
| Command allowlist/denylist described but no enforcement mechanism specified | A policy that isn't enforced at a chokepoint is not a policy | §13 Security Hardening specifies the enforcement point (a single `CommandValidator` all terminal calls must pass through, not per-tool ad hoc checks) |
| No dependency/malware scanning | An agent that runs `pip install` / `npm install` autonomously can pull compromised packages | §13 adds mandatory dependency vulnerability scanning before any new package is installed |
| No secret-scanning on agent-authored commits | The agent could write and commit a secret it was given for a debugging task | §13 adds a pre-commit secret scan as a hard gate, independent of the log-masking already in v1 |

### Observability

| Issue | Impact | Fix location |
|---|---|---|
| Metrics existed but no agent-quality metrics | You can have perfect uptime while the agent quietly gets worse at fixing bugs | §14 Evaluation Framework adds agent-quality metrics distinct from infra metrics |
| No prompt versioning | Silent prompt drift across updates with no way to A/B or roll back | §15 Prompt Management System |

### Scalability

| Issue | Impact | Fix location |
|---|---|---|
| "Future" scalability was one shallow table | No actual seams designed into the current architecture to support it later | §16 Distributed Future Architecture defines the actual extension points (message bus, worker protocol, model router interface) that must exist *today*, even if unused, so v3 doesn't require a rewrite |

---

## Part II — Revised High-Level Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│                              FRONTEND LAYER                              │
│  React/Next.js UI · CLI · Debug/Inspector Panels (§17)                   │
└──────────────────────────────────┬───────────────────────────────────--┘
                                    │ REST / WebSocket / SSE
┌──────────────────────────────────▼───────────────────────────────────--┐
│                            BACKEND API LAYER                             │
│      FastAPI · Auth · Rate limiting · Feature flags (§12)                │
└──────────────────────────────────┬───────────────────────────────────--┘
                                    │
┌──────────────────────────────────▼───────────────────────────────────--┐
│                         SUPERVISOR ORCHESTRATION                         │
│   LangGraph Supervisor Graph · Planning · Reflection · Circuit Breaker   │
└──┬────────────┬──────────────┬──────────────┬──────────────┬──────────-┘
   │             │              │              │              │
┌──▼───┐   ┌─────▼─────┐  ┌─────▼─────┐  ┌─────▼─────┐  ┌─────▼──────┐
│Context│   │  Memory /  │  │   Tool    │  │  Model     │  │  Reliability│
│Engine │   │  Context   │  │  System   │  │  Session   │  │  Layer      │
│(§10) │   │Persistence │  │  (Ch. 7†) │  │  Manager   │  │  (§8)       │
│      │   │  (§6)      │  │           │  │  (§5)      │  │             │
└──┬───┘   └─────┬─────┘  └─────┬─────┘  └─────┬─────┘  └──────┬──────┘
   │             │               │              │               │
   │        ┌────▼────┐    ┌─────▼─────────────────────┐        │
   │        │ Chroma/ │    │ Filesystem · Terminal · Git│        │
   │        │ FAISS · │    │ Browser · Docker · Python  │        │
   │        │ Postgres│    │ HTTP · DB · Search · LSP   │        │
   │        │ SQLite  │    │ Package Managers · MCP     │        │
   │        │ Redis   │    └────────────────────────────┘        │
   │        └─────────┘                                          │
┌──▼──────────────────────────────────────────────────────────────▼──────┐
│                         RESOURCE SCHEDULER (§7)                          │
│   CPU/GPU/VRAM/RAM/Disk/Network arbitration · Priority queues · Throttle │
└──────────────────────────────────┬───────────────────────────────────--┘
                                    │
┌──────────────────────────────────▼───────────────────────────────────--┐
│                    INFERENCE LAYER (single model at a time)              │
│      llama.cpp server · KV/Prompt cache · Flash Attention · Batching     │
└──────────────────────────────────┬───────────────────────────────────--┘
                                    │
┌──────────────────────────────────▼───────────────────────────────────--┐
│                   SYSTEM HEALTH MANAGER (§4) — cross-cutting             │
│   Watches every layer above. Can pause/preempt/evict at any layer.       │
└──────────────────────────────────┬───────────────────────────────────--┘
                                    │
┌──────────────────────────────────▼───────────────────────────────────--┐
│                        EVENT BUS (§9) — cross-cutting                    │
│   Every subsystem publishes; Frontend, Observability, Plugins subscribe  │
└──────────────────────────────────────────────────────────────────────--┘

† "Tool System" retains its full internal design from v1 §7 (catalog, permission tiers,
  routing, validation) — unchanged in substance, only its retry/idempotency contract
  is upgraded, see §8.6.
```

**What changed structurally from v1:**

- **System Health Manager and Event Bus are now cross-cutting**, not peer boxes — every other subsystem reports to Health Manager and publishes to the Event Bus, rather than those being "yet another module."
- **Resource Scheduler sits between orchestration and inference**, because in a single-model, single-machine deployment, the scheduler's most important job is arbitrating between "the model needs the GPU" and "the browser/Docker/build tooling needs CPU/RAM" — this wasn't modeled at all in v1.
- **Model Session Manager is now a distinct box**, not folded into "Model Gateway" — because model-swapping is a stateful operation that touches memory, context, and the resource scheduler, not just an API client.
- **Reliability Layer is promoted to a peer of Memory/Tools/Context**, because retries, rollbacks, and transaction logs are cross-cutting concerns that every one of those subsystems depends on, not a feature of any single one.

---

## 3. Agent Lifecycle

The full process lifecycle, not just the per-task state machine from v1 §3 (which remains valid and is nested *inside* the "Running" phase below).

### 3.1 Startup Sequence

```
[1] Process start
      │
[2] Config load          - model.yaml, security_policy.yaml, project_defaults.yaml
      │                     validated against schema; invalid config = fail fast, not silent default
[3] Capability probe      - is Docker available? is a GPU present? which formatters/linters
      │                     exist on PATH? which LSP servers are installed?
      │                     → produces a CapabilityManifest used to gracefully degrade
      │                       (e.g. no Docker → sandbox falls back to subprocess + warning)
[4] Storage init          - SQLite/Postgres migration check (auto-migrate or block with a
      │                     clear error if a manual migration is required)
[5] Model load            - llama.cpp server start, model load into VRAM/RAM, warmup
      │                     inference (throwaway prompt to force weight paging + JIT)
[6] Health check          - System Health Manager confirms model responds, DB reachable,
      │                     disk has headroom, before accepting any session
[7] Project discovery     - scan configured project roots; for each, check index freshness
      │
[8] Repository indexing   - full index for never-seen projects (background, non-blocking);
      │                     incremental index for known projects with changes since last run
[9] Memory warmup         - preload the current project's high-relevance memory
      │                     (repo summary, architecture memory, recent bug memory) into
      │                     an in-process cache — avoids a cold vector-store round trip
      │                     on the very first user message
[10] Checkpoint scan      - find any session left in a non-terminal state from a prior run;
      │                     surface these to the user as "resumable sessions" rather than
      │                     auto-resuming (auto-resume is opt-in configuration)
[11] Ready                - Backend API begins accepting requests; Event Bus emits `system.ready`
```

Steps 7-9 run concurrently across projects and do not block step 11 — the system becomes interactive as soon as the model is loaded and the database is reachable; indexing continues in the background with progress reported over the Event Bus.

### 3.2 Initialization Details

- **Capability probe is mandatory, not optional.** Every tool in the registry declares its runtime dependencies (binary on PATH, Docker daemon, network reachability); the probe runs each check once at startup and caches the result. A tool whose dependency is missing is marked `unavailable` in the registry rather than failing at call time with a confusing error.
- **Config validation is schema-first.** All three config files are validated against a Pydantic/JSON-Schema model before anything else happens; a malformed `security_policy.yaml` must never silently fall back to "no policy."

### 3.3 Health Check (startup gate)

Before the system reports `ready`, it verifies: model server responds to a completion request within timeout; primary datastore is reachable and migrations are current; disk has at least a configured minimum free (default 2 GB); no zombie processes from a prior crash hold a lock on the checkpoint database. Failing any of these keeps the system in a `DEGRADED` startup state with the specific failure surfaced to the user/logs — never a silent partial start.

### 3.4 Graceful Shutdown

```
Shutdown signal (SIGTERM / user-initiated / update trigger)
   │
[1] Stop accepting new sessions/messages (API returns 503 with retry-after)
   │
[2] For each active session: emit a checkpoint (forced, synchronous) regardless
    of the normal checkpoint cadence
   │
[3] Send cancellation to any in-flight tool calls; give them a grace period
    (default 10s) to exit cleanly before SIGKILL
   │
[4] Flush the Event Bus / log buffers to disk
   │
[5] Unload the model from the llama.cpp server (frees VRAM cleanly)
   │
[6] Close DB connections, release file locks
   │
[7] Exit 0
```

No session state is lost as long as step 2 completes — this is the entire point of continuous checkpointing (§8.3): graceful shutdown is just "checkpoint now" plus "stop starting new work," not a special code path.

### 3.5 Crash Recovery & Auto-Restart

- A lightweight **process supervisor** (systemd unit, or an equivalent watchdog for non-Linux) restarts the backend on unexpected exit, with exponential backoff and a max-restart-rate circuit breaker (prevents a crash-loop from hammering the model load repeatedly).
- On restart, Startup Sequence step 10 (checkpoint scan) finds any session that was active at crash time and marks it `INTERRUPTED`; per v1 §25 recovery logic, it is offered for resume with real-world state re-probed before continuing — never blindly trusted.
- If the crash occurred **during a checkpoint write itself**, the checkpoint store uses atomic write-then-rename (same pattern as file edits, §10 v1), so a crash mid-write leaves the *previous* valid checkpoint intact rather than a corrupt one.

### 3.6 Session Migration

Supports moving a session between processes/machines (e.g. laptop → workstation, or local → a future remote worker per §16):

1. Source process performs a forced checkpoint + exports the session's full state bundle: checkpoint blob, referenced file snapshots (or their content hashes if the target has the same repo checked out), and a manifest of which memory entries were used.
2. Bundle is transferred (local file copy, or over the network for remote workers).
3. Target process validates the bundle against its own `CapabilityManifest` (does it have the same tools available?) before accepting — a session migrated to a machine without Docker, for instance, surfaces a clear degraded-capability warning rather than failing silently mid-task.
4. Target process imports the checkpoint, re-probes real file state (same conflict-detection as crash recovery), and resumes.

---

## 4. System Health Manager

A cross-cutting subsystem with one job: **the agent process must never crash the host, and must never silently lose user work.** It watches every layer in the architecture and has the authority to preempt work anywhere.

### 4.1 What It Monitors

| Resource | Signal source | Default thresholds |
|---|---|---|
| CPU | `psutil` / `/proc` sampling | Warn 85%, critical 95% sustained 30s |
| RAM | `psutil` / cgroup limits | Warn 80%, critical 90% of available |
| GPU utilization | `nvidia-smi` / `rocm-smi` / Metal equivalent | Warn 90% sustained (informational, not throttled — inference needs it) |
| VRAM | Same as above | Warn 85%, critical 95% — critical triggers context-length reduction (§4.6) |
| Disk usage | Filesystem stat on data dir + project root | Warn 10% free, critical 3% free |
| Disk I/O | `iostat`-equivalent | Warn on sustained high wait (indicates thrashing) |
| Network | Socket-level throughput/latency for any MCP or HTTP tool calls | Warn on repeated timeouts (signals a flaky external dependency, not a local resource issue) |
| Temperature | Platform sensors where exposed (esp. laptops) | Warn at vendor-defined throttle point, critical triggers workload pause |
| Power | Battery state on portables | Warn on battery + no charger, critical triggers autosave + pause of non-essential background indexing |
| Thread count | Process thread accounting | Warn on unexpected growth (leak signal) |
| File handle count | `ulimit`/`lsof`-equivalent | Warn approaching soft limit (leak signal) |
| Deadlocks | Async task supervisor timeout detection (a task holding a lock with no progress beyond a threshold) | Any detected instance is critical |

### 4.2 Watchdog & Heartbeat

- Every long-running component (model server, each active tool sandbox, each LSP server process, each background indexing job) registers a **heartbeat** with the Health Manager on a fixed interval (default 5s).
- A missed heartbeat beyond a grace window (3 missed in a row) marks that component `unresponsive`; the Health Manager attempts a graceful ping, then a forced restart of just that component (not the whole process) where the component type supports isolated restart (LSP servers and tool sandboxes do; the model server restart is more disruptive and requires session pause, §4.7).
- **Deadlock detection**: async tasks are wrapped with a supervisor that flags any task holding a resource lock without progress beyond a configurable timeout; flagged tasks are logged with a full stack/task-state dump and, if the lock is one the Health Manager owns (e.g. the checkpoint write lock), force-released after an emergency checkpoint of whatever state is safely readable.

### 4.3 Automatic Diagnostics

On any warning-level threshold breach, the Health Manager runs a diagnostic bundle: recent metric history, the currently active session's state, the last N tool calls, and open file handles — written to a `diagnostics/` directory as a single timestamped bundle. This is the artifact a developer (or the agent itself, in a future self-healing mode) uses to understand *why* a threshold was crossed, not just that it was.

### 4.4 Automatic Cleanup

- **Garbage collection strategy**: Python's GC is tuned (generation thresholds adjusted for a long-lived process with many short-lived tool-call objects) and a manual `gc.collect()` is triggered after any large operation (full repo re-index, large file batch edit) rather than relying on automatic cadence alone.
- **Memory compaction**: the embedding cache and LSP response cache (Redis-backed, §6) have explicit eviction policies (LRU with a size cap) rather than growing unbounded.
- **Context cleanup**: stale context (superseded file versions, completed subtask scratch memory) is pruned from working memory immediately on subtask completion, not deferred to end-of-session.
- **Handle leak prevention**: every tool sandbox and subprocess is opened via a context manager that guarantees closure even on exception; the Health Manager's file-handle-count monitor is a backstop, not the primary defense.

### 4.5 Checkpoint Before Critical Operations

Beyond the routine checkpointing cadence (§8.3), the Health Manager forces an **out-of-band checkpoint** immediately before: any Tier-2+ destructive tool call, any operation the Resource Scheduler flags as high-risk-of-preemption (e.g. a large batch edit under memory pressure), and any model swap (§5). This is cheap (checkpoints are incremental) and removes an entire class of "the one operation that mattered wasn't saved" failures.

### 4.6 Preventing OOM / GPU OOM

This is the Health Manager's most important real-time function:

```
RAM approaching critical (90%)
   │
   ▼
[1] Evict non-essential caches (embedding cache, LSP hover cache) — cheapest action first
   │
   ▼
[2] If still critical: reduce active context window for the current inference
    request (drop lowest-ranked context per §16 v1 eviction policy, more aggressively
    than the normal token-budget pass) — the model gets a smaller but still coherent context
   │
   ▼
[3] If still critical: pause any background jobs (indexing, embedding generation,
    non-active-session work) — foreground session always wins resource contention
   │
   ▼
[4] If still critical: pause the current tool execution (if safely pausable — e.g. a
    background dev server can be paused; a mid-write file operation cannot and is
    allowed to complete first) and emit an emergency checkpoint
   │
   ▼
[5] If still critical after all of the above: surface a hard warning to the user —
    the system does not silently kill itself; it degrades transparently and asks
    before anything more disruptive than pausing background work
```

The same ladder applies to VRAM pressure, substituting "reduce active context window" (which directly reduces KV cache VRAM usage) as the primary lever, and "unload unnecessary GPU-resident resources" (e.g. an embedding model that can run on CPU instead) as a secondary one.

### 4.7 Resume After Recovery

Once metrics return below the warning threshold for a sustained period (default 60s, to avoid flapping), paused background jobs resume automatically, evicted caches rewarm lazily on next access, and the context-window reduction from §4.6 step 2 is lifted for subsequent turns (the already-in-flight turn is not retroactively expanded). Every pause/resume transition is an Event Bus event, visible in the frontend's health dashboard (§17) — the user always knows the system throttled itself and why.

### 4.8 The Never-Lose-Work Guarantee

Composed from the mechanisms above, the guarantee is: **no user-visible task result is ever lost due to a resource condition**, because (a) checkpoints happen before risk, not after failure, (b) file writes are atomic (v1 §10), (c) the transaction log (§8.3) is append-only and separate from the resumable checkpoint, and (d) the emergency-checkpoint path in §4.6 step 4 fires before any forced pause of in-flight work. What *can* happen under sustained extreme pressure is degraded performance (smaller context, paused background work) — never silent data loss.

---

## 5. Model Session Manager

Owns the one invariant the whole system is built around: **only one model is loaded at a time, but switching models must be invisible to everything except the Model Gateway itself.**

### 5.1 The Core Design Move

The fix for v1's biggest gap is simple to state and important to enforce: **nothing outside the Model Session Manager is allowed to hold state that depends on which model is currently loaded.** Every other subsystem — LangGraph checkpoints, working memory, long-term memory, the symbol/dependency graphs, the embedding index, tool history — is modeled as **model-independent** by construction. The only genuinely model-dependent state is the llama.cpp server's KV cache, which is *disposable* by design (it's a performance optimization, not a source of truth).

This means a model swap is architecturally cheap: there is no "translation" step required for memory/context/task state, because that state was never coupled to the model in the first place.

### 5.2 What Is Shared (Survives Any Model Swap)

| State | Where it lives | Model-independent because |
|---|---|---|
| Conversation history | SQLite/Postgres | Plain text/structured records |
| Project/task state | LangGraph checkpoint (SQLite) | Serialized graph state, no model-specific tokenization or cache |
| Repository understanding | Symbol/dependency/call graphs (§10) + repo summaries | Derived by Tree-sitter/LSP, not the reasoning model |
| Execution history | `execution_history` table | Structured event log |
| Reasoning summaries | Memory store (episodic/architecture memory) | Text summaries written *after* generation, stored independent of the generating model |
| Tool history | `tool_calls` table | Structured records |
| Context summaries | Context Persistence Layer (§6) | Text, versioned, embedding-store-backed with a fixed embedding model (§6.4) — deliberately *not* the reasoning model |
| Long-term memory (bug/preference/architecture) | Vector + relational store | Same fixed embedding model guarantee |

### 5.3 What Is NOT Shared (Discarded on Swap)

- **KV cache** — must be discarded; the new model recomputes its own cache from the restored conversation/context on first inference after swap. This is the only "cost" of a swap, and it's bounded by prompt-cache reuse of the static prefix (system prompt, tool schemas) where the new model uses the same tokenizer family, or a full recompute otherwise.
- **In-flight reasoning trace** — if a swap is requested mid-generation, the in-flight response is either allowed to complete (default) or cancelled and the turn restarted on the new model, per user choice.

### 5.4 Swap Sequence

```
User/system requests model swap (manual choice, or future auto-routing per §16)
   │
[1] Health Manager forced checkpoint (§4.5) — safety net before any disruptive op
   │
[2] Orchestration transitions active session(s) to WAITING (reason: model_swap)
   │
[3] In-flight tool calls allowed to complete (non-preemptible by design, §7);
    in-flight model generation is completed or cancelled per policy
   │
[4] Model Gateway sends unload to llama.cpp server (or spins down and restarts
    the server process with a new --model flag, depending on llama.cpp's
    hot-swap support at the version in use)
   │
[5] Resource Scheduler reclaims VRAM/RAM
   │
[6] New model loads, warmup inference runs (same as startup §3.1 step 5)
   │
[7] Model Session Manager reconstructs the prompt for the active session from
    model-independent state (§5.2) — system prompt + repo summary + ranked
    context + recent conversation — exactly as if this were a fresh turn on
    a new session, because from the model's perspective, it is
   │
[8] Session transitions back to its prior state (THINKING/EDITING/etc.)
   │
[9] Event Bus emits `model.loaded` / `model.unloaded`; frontend shows a brief
    "switched to <model>" notice — the *content* of the session is unaffected
```

### 5.5 Model Capability Registry

Each model entry in `model.yaml` declares: context window size, whether it supports native grammar-constrained function calling, tokenizer family (for prompt-cache-prefix reuse decisions), and a rough capability tier (used for future auto-routing, §16). Swapping to a model with a smaller context window than the current session's ranked context requires triggers the Context Engine's compression ladder (§6.2) more aggressively for that session, rather than failing.

---

## 6. Context Persistence Layer

Formalizes what was implicit in v1's Memory section into a dedicated, versioned, restorable layer — this is what makes §5's "reconstruct the prompt from model-independent state" actually work fast.

### 6.1 What Is Persisted

| Summary type | Source | Update trigger |
|---|---|---|
| Conversation summary | Rolling digest of older turns (v1 §16) | Every N turns, or on context-budget pressure |
| Repository summary | Repo Analyzer output (v1 §5.3) | On re-index |
| Architecture summary | Design decisions extracted during sessions | On Reflection Engine promotion (§6.3 below) |
| Task summary | Completed subtask outcomes | On subtask completion |
| Bug summary | Verified fixes | On test-pass confirmation, never before |
| Execution summary | Session-level "what happened" digest | On session completion or checkpoint |
| Reasoning summary | Distilled rationale behind non-obvious decisions (not the full reasoning trace — that stays in the raw log) | On subtask completion, only for decisions flagged non-trivial |
| Tool summary | Aggregated tool-usage patterns per project ("this repo's tests are run with `pytest -x tests/unit`") | On repeated pattern detection (3+ occurrences) |
| Decision history | Explicit record of plan changes and why (dynamic replanning events, v1 §13) | On every replan |

### 6.2 Compression Ladder

Applied uniformly across all context sources (repo files, memory entries, conversation history) when assembling a prompt under token-budget pressure:

```
Tier 0: Full content — used for the direct target of the current subtask
Tier 1: Full content, minus comments/docstrings — near-neighbors in the symbol graph
Tier 2: Signature-only (function/class signatures + one-line docstring) — same-file
        siblings not directly touched
Tier 3: One-line gist (auto-generated summary) — related-but-peripheral files
Tier 4: Reference only ("also see auth/session.py, not shown") — everything else
        that ranked above the cutoff but didn't fit
Tier 5: Dropped entirely — below relevance cutoff
```

Each tier has an approximate token-cost multiplier the Context Engine uses when greedily filling the budget (v1 §16), so the packing decision is a knapsack problem over (relevance score, tier, token cost), not a hard cutoff.

### 6.3 Versioning

All persisted summaries are **append-only with explicit supersession**, not overwritten in place: a new architecture-summary version references the prior version's id and a reason for the update. This means the agent can be asked "why did you structure the auth module this way" months later and trace the decision, and — critically — a bad summary (e.g. a bug-memory entry that later turns out to describe a wrong fix) can be marked `superseded` with a correction rather than silently vanishing, preserving the learning signal of "this was tried and didn't work."

### 6.4 Cross-Model Compatibility

All embeddings in the Context Persistence Layer and long-term memory are generated by a **fixed, dedicated embedding model** (a small local embedding model, not the primary reasoning model, per §11 Knowledge Layer) — this is what guarantees vector search remains valid across every reasoning-model swap in §5. Re-embedding is only ever needed if the embedding model itself is upgraded, which is a deliberate, explicit migration (§12.10), never a side effect of switching the reasoning model.

### 6.5 Fast Restoration

On session resume (crash recovery, session migration, or model swap), restoration is O(summary size), not O(full history) — the Model Session Manager (§5.4 step 7) pulls the latest version of each summary type plus the last K raw conversation turns, rather than replaying the entire session from scratch. Target restoration latency: under 500ms for a typical session on local hardware, excluding model warmup time.

---

## 7. Resource Scheduler

Arbitrates every finite resource on the machine between competing consumers: model inference, tool execution (terminal/browser/Docker), embedding generation, and background indexing.

### 7.1 Resource Pools Managed

CPU (thread pool budget), GPU compute, VRAM, RAM, disk I/O bandwidth, network concurrency (max in-flight HTTP/MCP calls), and a logical "background job" slot count independent of raw CPU (prevents e.g. 50 tiny background tasks from starving one important one via scheduling overhead alone).

### 7.2 Priority Model

```
Priority 0 (highest): Active session's model inference, active session's
                        currently-blocking tool call (e.g. the test run the
                        agent is waiting on to proceed)
Priority 1:            Active session's non-blocking tool calls (parallel
                        read-only operations)
Priority 2:            Health Manager diagnostic/cleanup operations
Priority 3:            Background indexing/embedding generation for the
                        active project
Priority 4:            Background indexing for other open (inactive) projects
Priority 5 (lowest):   Speculative/prefetch work (e.g. pre-warming an LSP
                        server for a project not yet opened)
```

Preemption only flows downward — a Priority 3 job never blocks a Priority 0 job; the scheduler proactively throttles or pauses lower-priority work when a higher-priority request arrives, rather than relying on the OS scheduler alone.

### 7.3 Resource Reservation

Before starting an expensive operation (Docker build, browser launch, full repo re-index), the initiating component requests a reservation from the Scheduler (estimated CPU/RAM/time). The Scheduler either grants it, queues it behind higher-priority work, or — if the estimate exceeds currently-safe headroom per the Health Manager's thresholds — denies it with a clear reason, rather than starting the operation and letting the Health Manager's reactive ladder (§4.6) handle the fallout after the fact. Reservation is a *soft* admission-control layer; the Health Manager's reactive mechanisms remain the backstop for estimation errors.

### 7.4 Background Job Ownership

Every background job (dev server, watch-mode build, indexing run) is registered with the Scheduler at creation and tagged with the session that owns it. On session end (completion, cancellation, or timeout), the Scheduler sweeps and terminates any job still owned by that session unless the user explicitly marked it "keep running" (common for dev servers the user wants to keep testing against manually).

### 7.5 Dynamic Throttling & Adaptive Behavior

- **Adaptive batching**: the llama.cpp server's continuous batching parameters are tuned by the Scheduler based on current concurrent-session count and available VRAM headroom — fewer concurrent slots reserved when VRAM is tight, more when it's abundant.
- **Adaptive context length**: ties directly into §4.6's OOM-prevention ladder — the Scheduler is the component that actually computes the safe context budget given current VRAM headroom, and hands that number to the Context Engine's packing algorithm.
- **Adaptive token generation**: under sustained CPU/GPU pressure, `max_tokens` for a given turn can be reduced (favoring more, shorter turns over one long generation) — configurable, off by default for interactive use, on by default for unattended long-running tasks where throughput matters more than single-turn length.

### 7.6 Backpressure Into the State Machine

When the Scheduler cannot grant a resource request promptly, the requesting component doesn't block silently — the session transitions to `WAITING` with an explicit reason code (`waiting_for: gpu_headroom`, `waiting_for: docker_build_slot`, etc.), surfaced in the frontend's Task Timeline (v1 §22) and Event Bus, so a long-running agent's apparent "stall" is always attributable to a specific, visible cause.

---

## 8. Reliability Layer

The system-wide contract for "the agent's work is durable and its actions have predictable, bounded effects" — promoted from scattered per-subsystem mentions in v1 to a single owned layer.

### 8.1 Design Contract

Every mutating operation in the system (file write, tool call, memory write, git operation) satisfies three properties enforced at this layer, not left to each subsystem to reimplement:

1. **Recorded before attempted** — a transaction log entry is written *before* the operation executes, capturing enough to know what was attempted even if the process dies mid-operation.
2. **Idempotent or dedupe-guarded** — retrying never silently duplicates a side effect (§8.6).
3. **Reversible or explicitly irreversible** — every operation is tagged with whether a rollback exists; irreversible operations (§7.2 v1 Tier 3) require explicit approval precisely because this layer cannot guarantee a way back.

### 8.2 Retry Strategies

| Failure type | Strategy |
|---|---|
| Transient (network blip, LSP server not yet warm, resource contention) | Exponential backoff, silent, up to 2 attempts |
| Tool crash (segfault, unexpected exception) | Single retry with a fresh sandbox instance; second failure escalates to Reflection |
| Verification failure (lint/test/build) | Not a "retry" — routes through Reflection → Repair → new attempt, tracked against the subtask retry budget (v1 §14) |
| Resource-denied (Scheduler couldn't grant reservation) | Requeue at same priority, does not count against the subtask retry budget (it's a scheduling delay, not a failure) |

### 8.3 Two-Tier Checkpoint & Transaction Log

- **Resume checkpoints** (SQLite, pruned to last N per session): the minimal state needed to resume execution. Optimized for fast read on restart.
- **Transaction log** (append-only, never pruned within a session's lifetime, archived not deleted after session close): every attempted operation, its arguments, its outcome, and timing — the audit trail. This is what §3.5 crash recovery uses to determine "was this operation actually applied to the real world" when the resume checkpoint alone is ambiguous (e.g. checkpoint says "about to write file X" — the transaction log plus a fresh hash-check of file X on disk together disambiguate whether the write completed).

### 8.4 Snapshot System

Builds on v1 §10's file-snapshot mechanism, extended to cover non-file state where meaningful: a database-tool operation snapshots the affected table's relevant rows before a mutating query (where the target DB supports transactions, this is a wrapping transaction; where it doesn't — e.g. some NoSQL targets — it's a best-effort row-level backup with a clear capability warning).

### 8.5 Session-Level Circuit Breaker

Independent of per-subtask retry budgets (v1 §14): if a session accumulates more than a configured number of total repair attempts across *all* subtasks (default 10) without a net-positive completion rate, the session is force-transitioned to a `NEEDS_REVIEW` substate — paused, checkpointed, summarized, and surfaced to the user — rather than continuing to grind. This catches the case where no single subtask looks stuck, but the session as a whole clearly is.

### 8.6 Idempotent Operations & Exactly-Once Tool Execution

Every tool declares one of three idempotency classes in its schema:

- **`safe`** (read-only: file read, search, git status) — retried freely.
- **`idempotent`** (repeated execution with the same args produces the same end state: file write with identical content, `git add`) — retried freely, no dedupe key needed.
- **`effectful`** (git commit, package install, HTTP POST, database write, any command with side effects beyond the target file) — requires a **dedupe key** (a deterministic hash of tool name + args + a monotonic operation sequence number from the transaction log) generated *before* first execution attempt. On any retry, the Reliability Layer checks the transaction log for a prior attempt with the same dedupe key; if one exists and the log shows it reached the "executed" state, the retry is skipped and the prior result is reused rather than re-executing — this is the exactly-once guarantee for the operations where it matters most.

---

## 9. Event Bus

Formalized as a complete event-driven backbone — every subsystem above publishes here; the frontend, observability stack, and plugins all subscribe here rather than polling.

### 9.1 Transport

In-process `asyncio` pub/sub for the single-machine local deployment (zero extra infra); the same interface is backed by **Redis pub/sub** transparently when running in multi-process or future distributed mode (§16) — subsystems publish/subscribe against an abstract `EventBus` interface, never against the transport directly, so this swap requires no changes above the transport layer.

### 9.2 Canonical Event Catalog

| Event | Payload highlights |
|---|---|
| `system.ready` / `system.degraded` | capability manifest, missing capabilities |
| `model.loaded` / `model.unloaded` / `model.swap_started` / `model.swap_completed` | model id, load duration |
| `session.started` / `session.paused` / `session.resumed` / `session.completed` / `session.failed` / `session.needs_review` | session id, state |
| `task.started` / `task.finished` | task id, parent, confidence score |
| `file.changed` | path, change type (created/modified/deleted/renamed), triggering tool call id |
| `tool.started` / `tool.finished` | tool name, args (masked), duration, status |
| `memory.updated` | memory type, entry id, version |
| `checkpoint.saved` | session id, step, checkpoint size |
| `error.detected` | component, severity, diagnostic bundle ref |
| `recovery.started` / `recovery.completed` | trigger reason, recovered session id |
| `browser.event` | navigation, console error, network failure |
| `git.event` | operation, branch, commit hash |
| `plugin.event` | plugin id, custom event type/payload |
| `resource.warning` / `resource.critical` / `resource.recovered` | resource type, current value, threshold |
| `health.heartbeat_missed` | component id |

### 9.3 Delivery Guarantees

At-least-once delivery to subscribers within a process lifetime; events are **not** persisted for replay to late subscribers by default (a frontend that connects mid-session gets current state via a REST snapshot call, then live events going forward) — except for the subset of events also written to the transaction log (§8.3), which *are* replayable for audit/debug purposes via the Execution History API.

### 9.4 Consumers

- **Frontend** — routes events to the appropriate UI panel (v1 §22 / new §17).
- **Observability** — OTel spans and Prometheus counters are driven by the same events, not a separate instrumentation pass, ensuring logs/metrics/UI never drift out of sync with each other.
- **Plugins** — subscribe to a scoped subset per their manifest-declared permissions (§10 Plugin Framework below); a plugin cannot subscribe to events for a project it hasn't been granted access to.
- **Reliability Layer** — subscribes to its own emitted events as the basis for the audit trail cross-check described in §8.3.

---

## 10. Plugin Framework

Expands v1 §23 into a full SDK-grade framework.

### 10.1 Plugin SDK

A typed SDK (Python, matching the backend's own stack) exposing: tool registration, event subscription (scoped), a manifest schema, and a local dev-loop (`plugin dev` command that hot-reloads a plugin against a running local instance without a full restart).

### 10.2 Lifecycle

`discover → validate manifest → resolve dependencies → load (sandboxed subprocess) → capability negotiation (register only what the manifest declares AND what the capability probe confirms is safe) → activate → runtime → hot-reload (dev mode) | deactivate → unload`.

### 10.3 Permissions & Isolation

Unchanged in principle from v1 §23, hardened in enforcement: permissions are checked at the same `CommandValidator`/`ToolRouter` chokepoint as native tools (§13.1), not by a separate, potentially inconsistent plugin-specific path. A plugin cannot request filesystem access outside its declared scope, cannot open network connections unless declared, and runs as a separate OS process (Docker-sandboxed if marked untrusted) communicating over local JSON-RPC (MCP-compatible), so a plugin crash or infinite loop cannot take down the core agent process.

### 10.4 Versioning & Dependency Management

Plugins declare a semver version and a minimum/maximum compatible core-API version in their manifest; the loader refuses to activate a plugin outside its declared compatibility range rather than risking an undefined-behavior integration. Plugin-to-plugin dependencies (one plugin building on another's exposed tools) are declared explicitly and resolved at load time in dependency order; circular dependencies are rejected at validation.

### 10.5 Marketplace Support & Hot Reload

The plugin registry (`GET /api/plugins`, v1 §21) supports a community index lookup in addition to local scanning, with signature verification on any plugin installed from a remote source before it's ever loaded (§13.9). Hot reload (dev mode only, disabled by default in production config) tears down and reloads a single plugin's subprocess without restarting the core agent — the same subprocess-isolation property that makes plugins safe also makes them cheap to reload independently.

---

## 11. Knowledge Layer

New subsystem: where the agent's understanding of the *world* (not just the current repo) lives.

### 11.1 Sources

| Source | Use |
|---|---|
| Local framework/language documentation (cached offline copies of official docs for the project's detected stack) | Grounding for API usage questions without needing network access |
| Project documentation (README, ADRs, wiki content already in the repo) | Highest-priority source — the project's own stated intent beats general knowledge |
| Local doc cache (pre-indexed into the same vector store as code, tagged `doc` not `code`) | Unified retrieval alongside code context |
| API/library documentation (versioned per the project's actual dependency versions, not latest-upstream) | Avoids the classic failure of suggesting an API from a newer/older library version than the project uses |
| Internet search (opt-in, explicit — never automatic for a "local-first" deployment) | Fallback for genuinely novel questions the local corpus can't answer |
| Offline search | Full-text search (e.g. via the same ripgrep/fd primitives, or a local search index like Tantivy) over the cached doc corpus when vector search alone under-serves an exact-match lookup (error codes, exact function signatures) |

### 11.2 Embedding Model

A **dedicated, fixed, local embedding model** (small enough to run alongside the reasoning model without meaningfully competing for VRAM — e.g. a quantized BGE-small/E5-small class model, potentially CPU-only) is used for *all* embeddings across the Context Engine, Memory, and Knowledge Layer. This is deliberately decoupled from the primary reasoning model (§5, §6.4) — it's the architectural guarantee that model-swapping never invalidates the vector index.

### 11.3 Retrieval Priority

When both code context and documentation are relevant, ranking favors project-local sources (actual code, actual README) over general documentation, and general documentation over internet search — matching how a careful engineer would trust sources, most-specific-to-this-project first.

### 11.4 Update Strategy

Framework/library doc caches are refreshed on a slow cadence (weekly, or on-demand when a `package.json`/`pyproject.toml` version bump is detected for a tracked dependency) — not re-fetched per session, to stay genuinely local-first and avoid unnecessary network dependency for routine operation.

---

## 12. Production Features

### 12.1 Feature Flags

A simple local flag store (config file + optional runtime override via API) gates experimental subsystems (e.g. speculative decoding, auto-model-routing) so they can ship dark and be enabled per-project or per-session without a code change.

### 12.2 Configuration Profiles

Named profiles (`dev`, `cautious`, `autonomous-overnight`) bundle coherent settings — approval policy (v1 §11), retry budgets (§8), resource thresholds (§4), and background-job aggressiveness (§7) — so a user switches posture with one selection rather than tuning a dozen independent knobs. `autonomous-overnight`, for instance, defaults to wider retry budgets and auto-approved Tier-1 operations, since no one's watching to click approve.

### 12.3 Secrets Management

Secrets (API keys, DB credentials, git tokens) are never stored in project config or the database in plaintext — they're referenced by name and resolved at runtime from the OS keychain (or a local encrypted store as fallback), consistent with the secret-masking already specified in v1 §20 for anything that touches logs or model input.

### 12.4 Telemetry & Analytics

Fully local by default — the Observability stack (v1 §18-19) *is* the telemetry system; nothing phones home. An explicit, separately-consented opt-in exists for anonymized usage analytics (which features get used, aggregate success/failure rates) if the user wants to contribute to product improvement — off by default, consistent with local-first principles.

### 12.5 Audit Logs

Distinct from debug logs: a human-readable, tamper-evident (hash-chained) record of every Tier-2+ action taken, who/what approved it (user click, or which policy auto-approved it), and the outcome — the record you'd want if you needed to explain six months later exactly what an autonomous overnight run did to a production-adjacent repo.

### 12.6 Access Control

For the multi-user/server deployment mode (v1 §21.4): project-scoped roles (`owner`, `contributor`, `viewer`) gate not just API access but tool permission tiers — a `viewer` role caps at Tier-0 read-only tool execution regardless of the project's own security policy.

### 12.7 Backup & Restore

Scheduled (configurable cadence) backup of the full data directory (checkpoint DB, memory stores, transaction logs) to a user-specified location; restore is the same mechanism as crash recovery (§3.5) pointed at a backup bundle instead of the live data directory, deliberately reusing that code path rather than maintaining a separate restore implementation.

### 12.8 Version Management

The core agent, each installed plugin, and the active model each carry independent version identifiers surfaced in one place (`GET /api/system/versions`) — essential for reproducing a bug report or an unexpected regression.

### 12.9 Database Migrations

Alembic-based (or equivalent) versioned migrations for the relational schema (§24, updated in §18 below); migrations run automatically on startup for local single-user mode (with a pre-migration backup taken automatically) and require explicit operator approval in server/multi-user mode.

### 12.10 Embedding Model Migration

A deliberate, explicit procedure (distinct from routine reasoning-model swaps, §5) for when the embedding model itself changes: triggers a full re-embedding pass of the vector store, run as a background job at low priority (§7.2), with the old and new embeddings coexisting (dual-write) until the pass completes, then an atomic cutover — the system never operates on a half-migrated, semantically-inconsistent index.

---

## 13. Security Hardening

Consolidates and hardens v1 §20 with concrete enforcement mechanisms and additional protections identified in the critical review.

### 13.1 Single Enforcement Chokepoint

Every terminal command, regardless of which tool or plugin originated it, passes through one `CommandValidator` before execution — path validation, allowlist/denylist check, secret-pattern scan of the command string itself (catching an accidentally-embedded credential before it ever reaches a shell), and permission-tier resolution all happen here, in one place, rather than being re-implemented (and potentially inconsistently) per tool.

### 13.2 Path Traversal Protection

Every path argument, from any tool, is canonicalized (resolving `..`, symlinks) and checked against the project root allowlist *before* the underlying OS call — enforced at the same chokepoint as §13.1, not trusted to individual tool implementations.

### 13.3 Command Injection Protection

Tools that shell out use argument-array execution (`subprocess.run([...])`), never string-interpolated shell commands, by default; the one tool tier that explicitly needs shell interpretation (the "raw shell" Tier-2+ tool from v1 §11) has its input scanned for known injection patterns (command chaining, subshells, redirection to sensitive paths) before execution, on top of the allowlist.

### 13.4 Secret Scanning

Two independent gates: (a) outbound — anything sent to the model or logs is scanned/masked (v1 §20, unchanged); (b) inbound/authored — any content the agent is about to *write* (file content, commit message) is scanned for secret patterns before the write completes, and any match blocks the write with a clear explanation rather than silently proceeding — this catches the case v1 missed, where the agent itself authors a leaked secret rather than just handling one that already existed.

### 13.5 Dependency Vulnerability Scanning

Before any package-manager `install` tool call is permitted to run, the target package+version is checked against a local vulnerability database (periodically synced, e.g. an offline OSV/npm-audit-equivalent dataset) — known-critical vulnerabilities block the install pending explicit user approval; lower-severity findings are surfaced as a warning but don't block.

### 13.6 Malware Scanning

For any file the agent downloads or extracts from an external source (not authored by the model itself — e.g. a fetched archive, an npm package's install scripts), a lightweight signature-based scan runs before the content is trusted; this is a best-effort layer, not a substitute for not running untrusted code, and is documented as such.

### 13.7 Network Isolation

Default posture: tool network access is scoped to localhost + explicitly allowlisted domains (mirroring the browser skill's philosophy of an explicit domain allowlist); any tool call attempting an unlisted external host is blocked and surfaced, not silently allowed.

### 13.8 Sandbox Verification

The Docker/subprocess sandbox's actual isolation properties (no host filesystem mount beyond the intended project bind-mount, no host network unless explicitly configured, resource limits actually applied) are verified at startup via the capability probe (§3.2) — the system checks that its sandbox *is* a sandbox, rather than assuming Docker configuration is correct.

### 13.9 Secure Plugin Execution

Plugin code is never executed in-process (§10.3); plugins installed from a remote marketplace source require a valid signature from a known publisher key before first load, with an explicit "install unsigned plugin at your own risk" override for local development use only.

---

## 14. Evaluation Framework

New subsystem: measures whether the *agent* is good, not just whether infrastructure is healthy.

### 14.1 Metrics

| Category | Metrics |
|---|---|
| Task-level | Task success rate, patch quality (does the diff match the stated intent — scored via a rubric, not just "tests passed"), tool call success rate, retry count, repair count |
| Code-quality | Lint/complexity delta introduced, regression rate (did a previously-passing test break), duplicate/dead code introduced |
| Performance | Latency per subtask, tokens/sec, memory/CPU/GPU usage per task (tying directly into §4's metrics, viewed through a per-task lens rather than system-wide) |
| Agent-quality | Hallucination rate (claims about the codebase that don't check out against ground truth — e.g. citing a function that doesn't exist), planning quality (did the initial plan survive without major replanning), reflection quality (did diagnosis correctly identify root cause on the first attempt), long-running stability (drift in success rate over session duration), repository-understanding accuracy (spot-checked against LSP ground truth) |
| Benchmark | Score against a held-out internal task suite (a curated set of representative bug-fix/feature-add tasks with known-good solutions), run on demand (e.g. after a model or prompt change) to catch regressions before they reach real sessions |

### 14.2 How It's Collected

Task-level and code-quality metrics are derived automatically from the Testing Pipeline (v1 §15) and Reflection Engine (v1 §14) outputs — no extra instrumentation needed beyond what those subsystems already produce. Agent-quality metrics require a lightweight secondary evaluation pass: hallucination checking cross-references model claims against the Context Engine's ground truth (symbol graph, LSP); planning/reflection quality are scored by comparing the initial plan/diagnosis against the final outcome after the fact.

### 14.3 Use

Feeds Grafana dashboards (v1 §19) with an "agent quality" board distinct from the "system health" board; feeds the Evaluation Framework's benchmark suite as a CI-style gate before adopting a new model version or a prompt-management change (§15) into default configuration — a new model or prompt is not promoted to default until it matches or beats the current benchmark score.

---

## 15. Prompt Management System

New subsystem: prompts are versioned, tested infrastructure, not embedded strings.

- **Version prompts**: every system prompt, tool-description template, and reflection/repair prompt template is stored with a version id, not hardcoded inline in orchestration code — enabling diffing and rollback.
- **Prompt templates**: parameterized with a typed schema (which variables a template expects), validated at render time so a template change that drops a required variable fails fast in testing, not silently in production.
- **Prompt testing**: each template has an associated test set (representative inputs + expected-shape outputs, e.g. "does the planning prompt reliably produce a valid task graph for these 20 sample requests") run against the Evaluation Framework's benchmark suite.
- **Prompt A/B testing**: two template versions can be run split across sessions (opt-in, local — no external experimentation platform needed for a single-user deployment; meaningful in the team/server deployment mode) with outcomes compared via the same Evaluation Framework metrics.
- **Prompt rollback**: any template can be reverted to a prior version instantly (it's just selecting an earlier version id) — no redeploy needed.
- **Prompt optimization**: manual for now (a human engineer iterates using the A/B and test-set infrastructure above); the versioning/testing infrastructure is the prerequisite for any future automated prompt optimization, not a promise of automation today.
- **Prompt cache**: ties directly into v1 §26's KV/prompt caching — because templates are versioned and stable, the static portions of a rendered prompt are highly cacheable across turns and sessions.
- **Prompt analytics**: which templates are in use, their test-set pass rate, and their real-world downstream task success rate (via the Evaluation Framework), surfaced together so a template that looks fine in isolated testing but underperforms in real sessions is visible.

---

## 16. Distributed Future Architecture

Defines the extension points that must exist *today* — even in a single-machine deployment — so that scaling out later is a configuration change, not a rewrite.

### 16.1 Extension Points Built In Now

| Seam | Local behavior today | What it enables later |
|---|---|---|
| Event Bus abstract interface (§9.1) | In-process asyncio | Swap to Redis/NATS pub-sub for multi-process/multi-machine, zero subsystem changes |
| Tool execution as an RPC-shaped call (`ToolCall` → `ToolResult`, §7 v1) | Executed in-process or local subprocess | Same contract dispatches to a remote worker over the network |
| Model Gateway as a client of an OpenAI-compatible endpoint (v1 §29) | Points at localhost llama.cpp | Points at a remote llama.cpp instance, a GPU-cluster-hosted endpoint, or — with explicit opt-in — a cloud API, with no orchestration-layer changes |
| Checkpoint store as a pluggable backend (SQLite today) | Local file | Swap to Postgres for concurrent multi-worker access without changing the checkpoint interface |
| Resource Scheduler's resource pools (§7.1) modeled as named, quantified capacities | One machine's CPU/GPU/RAM | Multiple machines register their pools; scheduler becomes a distributed bin-packer |

### 16.2 Multiple Models

The Model Capability Registry (§5.5) already models multiple model entries; a future **Model Router** node in the orchestration graph selects among them per-subtask (e.g. a fast small model for mechanical edits, the primary model for complex reasoning) — this requires either multiple concurrent llama.cpp instances (if VRAM allows) or fast hot-swapping (§5.4) between them, both of which the Model Session Manager already supports.

### 16.3 Remote & Cloud Workers

Tool execution — especially Docker builds, browser automation, and large test suites — is the first candidate for offload: a `RemoteToolWorker` implements the same `ToolResult` contract as local execution, dispatched via the Resource Scheduler when a local reservation can't be granted and remote capacity is configured. Cloud model fallback (v1 §27) is opt-in and explicit, never silent, logged identically to any other model swap event.

### 16.4 GPU Clusters & Multi-Repository / Team Collaboration

A server-mode deployment (v1 §21.4 auth model) extends the single-user Checkpoint/Memory stores to Postgres-backed multi-tenant storage, project-scoped (§12.6 access control), with the Event Bus's Redis-backed mode providing the real-time collaboration surface (multiple team members watching/approving the same autonomous session). Shared memory across a team is opt-in per project, respecting the same versioning/supersession model as single-user memory (§6.3) — a team's bug memory is just a project-scoped memory store with multiple writers, not a different mechanism.

---

## 17. Developer Experience

Expands v1 §22's frontend panel list into a full inspector/debugging toolkit — the surface a developer uses to actually trust and debug the agent.

- **Interactive debugging**: step-through mode — pause the orchestration graph between any two nodes, inspect full state, optionally edit it, resume.
- **Execution timeline**: (v1 §22) now additionally shows Resource Scheduler `WAITING` reason codes (§7.6) inline, so a stall is self-explanatory.
- **Visual workflow graph**: live-rendered LangGraph topology (§9 v1 / §16.1 above) with the current node highlighted and edge-traversal history overlaid — turns "what is the agent doing" into a literal picture.
- **Memory inspector**: browse/search/edit long-term memory (v1 §22), now showing version history and supersession chains (§6.3).
- **Prompt inspector**: view the fully-rendered prompt sent for any turn, with each context tier (§6.2) visually distinguished — makes "why did the model do that" answerable by inspection, not guesswork.
- **Context inspector**: shows the ranked-and-compressed context alongside what was evicted and why (relevance score, tier, token cost) — direct visibility into §6.2/v1 §16's packing decisions.
- **Tool inspector**: per-tool-call detail view — args, permission tier, sandbox used, duration, idempotency class, dedupe key if applicable.
- **Performance dashboard**: real-time view of §4's monitored resources plus §7's scheduler queue depths, sourced from the same Prometheus metrics as the ops-facing Grafana boards (v1 §19) but presented developer-first, in-app.
- **Live logs**: structured log stream (v1 §18) filterable by component/session/level, driven by the Event Bus.
- **Trace viewer**: OpenTelemetry trace visualization per session — the flame-graph view of where time actually went across planning, retrieval, inference, and tool execution.
- **Checkpoint viewer**: browse checkpoint history for a session, diff two checkpoints, and — for debugging — manually resume from an arbitrary past checkpoint rather than only the latest.

---

## Updated Database Schema (additions to v1 §24)

```sql
-- Transaction log (append-only, §8.3)
CREATE TABLE transaction_log (
  id BIGSERIAL PRIMARY KEY,
  session_id UUID,
  operation_type TEXT,
  dedupe_key TEXT,                    -- for effectful, non-idempotent ops (§8.6)
  args_json TEXT,
  status TEXT,                        -- attempted/executed/failed/rolled_back
  attempted_at TIMESTAMP,
  resolved_at TIMESTAMP
);
CREATE INDEX idx_txlog_dedupe ON transaction_log(dedupe_key);

-- Model registry
CREATE TABLE models (
  id TEXT PRIMARY KEY,
  context_window INT,
  supports_function_calling BOOLEAN,
  tokenizer_family TEXT,
  capability_tier TEXT
);

-- Context summaries (versioned, §6.3)
CREATE TABLE context_summaries (
  id UUID PRIMARY KEY,
  project_id UUID,
  summary_type TEXT,                  -- conversation/repo/architecture/task/bug/execution/reasoning/tool/decision
  content TEXT,
  supersedes_id UUID REFERENCES context_summaries(id),
  superseded_reason TEXT,
  created_at TIMESTAMP
);

-- Resource metrics (rolling window, §4/§7)
CREATE TABLE resource_metrics (
  id BIGSERIAL PRIMARY KEY,
  timestamp TIMESTAMP,
  resource_type TEXT,
  value REAL,
  threshold_state TEXT                -- normal/warning/critical
);

-- Evaluation results (§14)
CREATE TABLE evaluation_results (
  id UUID PRIMARY KEY,
  session_id UUID,
  task_id UUID,
  metric_name TEXT,
  metric_value REAL,
  benchmark_run_id UUID,
  created_at TIMESTAMP
);

-- Prompt templates (§15)
CREATE TABLE prompt_templates (
  id UUID PRIMARY KEY,
  name TEXT,
  version INT,
  content TEXT,
  variables_schema TEXT,
  test_pass_rate REAL,
  is_active BOOLEAN,
  created_at TIMESTAMP
);

-- Audit log (§12.5, hash-chained)
CREATE TABLE audit_log (
  id BIGSERIAL PRIMARY KEY,
  session_id UUID,
  action TEXT,
  permission_tier INT,
  approved_by TEXT,                   -- 'user' or policy name
  prev_hash TEXT,
  entry_hash TEXT,
  created_at TIMESTAMP
);
```

---

## Updated Folder Structure (additions to v1 §28)

```
agent/backend/
├── health/
│   ├── monitor.py               # §4 resource sampling
│   ├── watchdog.py              # heartbeats, deadlock detection
│   └── recovery_ladder.py       # OOM/VRAM-OOM prevention steps
├── model_session/
│   ├── manager.py                # §5 swap sequence
│   └── capability_registry.py
├── context_persistence/
│   ├── summaries.py               # §6
│   └── compression_ladder.py
├── scheduler/
│   ├── resource_pools.py          # §7
│   └── priority_queue.py
├── reliability/
│   ├── transaction_log.py         # §8
│   ├── idempotency.py
│   └── circuit_breaker.py
├── events/
│   ├── bus.py                     # §9, transport-abstracted
│   └── catalog.py
├── evaluation/
│   ├── metrics.py                 # §14
│   └── benchmark_runner.py
├── prompts/
│   ├── registry.py                # §15
│   └── templates/
├── knowledge/
│   ├── doc_cache.py               # §11
│   └── embedding_service.py       # dedicated, model-independent
└── security/
    └── command_validator.py       # §13.1 single chokepoint
```

---

## Technology Stack Additions (to v1 §29)

| Category | Choice | Why |
|---|---|---|
| Process supervision | systemd (Linux) / launchd (macOS) / NSSM-equivalent (Windows) | Native, zero-dependency crash-restart with backoff, avoids reinventing a supervisor in-app |
| Resource monitoring | psutil + platform GPU vendor tools (nvidia-smi/rocm-smi) | Cross-platform baseline plus vendor-accurate GPU telemetry |
| Embedding model | Small local embedding model (e.g. quantized BGE-small/E5-small class, GGUF or ONNX) | Deliberately decoupled from the reasoning model so vector search survives every model swap (§5, §6.4, §11.2) |
| Event transport (distributed mode) | Redis pub/sub (or NATS as a lighter alternative) | Same abstract interface as in-process asyncio, swap-in only for multi-process/distributed deployments |
| Secret storage | OS keychain APIs (Keychain/Credential Manager/libsecret) with an encrypted-file fallback | Avoids ever persisting secrets in plaintext config or DB |
| Dependency vulnerability data | Offline-syncable OSV database | Enables §13.5 scanning without requiring live network access per install |
| Migrations | Alembic | Mature, well-understood versioned migration tooling for the SQLite/Postgres schema |

---

## Closing Note on This Revision

Every addition in this document exists to answer one question the v1 draft left implicit: **what happens when things go wrong, or when the machine itself is under pressure, during an unattended multi-hour run?** A feature list describes what the agent can do when everything works. This revision describes what keeps it working — and keeps the user's work safe — when it doesn't. That is the actual difference between a demo and a system a developer would trust to run overnight against a real codebase.
