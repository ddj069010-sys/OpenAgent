# Local Autonomous Coding Agent — Implementation Roadmap

**Document class:** Engineering implementation specification (converts the v1-v3 architecture into a buildable plan)
**Target hardware:** RTX 3050 Laptop GPU (4GB VRAM), 24GB system RAM, Pop!_OS (Linux)
**Constraint:** exactly one model loaded at a time; framework must be model-agnostic

---

## 0. Hardware Reality Check — Read This Before Phase 1

The architecture (v1-v3) is hardware-agnostic by design. This roadmap is not — it has to work on a 4GB VRAM laptop GPU. That constraint changes *which model runs when* and *how aggressively the Resource Scheduler/System Health Manager throttle*, but it changes **nothing** about the architecture itself — this is exactly the scenario the Model Session Manager (v2 §5) and Resource Scheduler (v2 §7) were designed for.

### 0.1 What Fits Where

| Model | Params (active) | Approx. size (Q4_K_M) | Fits in 4GB VRAM? | Practical placement |
|---|---|---|---|---|
| Qwen3.6-35B-A3B | 35B total / ~3B active (MoE) | ~20-22GB on disk | No — full weights don't fit in VRAM, but MoE active-param count means CPU/RAM inference is viable | **Primary reasoning model, CPU+partial-GPU offload.** Load with `--n-gpu-layers` tuned to fill available VRAM (shared/attention layers first), remainder on CPU. Expect moderate tokens/sec (CPU-bound), acceptable for autonomous background work, slower for interactive back-and-forth. |
| Qwen2.5-Coder-14B | 14B dense | ~8-9GB | No (partially) | **Secondary/fast-path model.** Q4_K_M with heavy CPU offload, or Q3_K_M pushed further onto GPU for latency-sensitive interactive edits. Faster time-to-first-token than the 35B-A3B for small, mechanical edits. |
| DeepSeek-R1-Distill-Qwen-14B | 14B dense | ~8-9GB | No (partially) | **Reasoning/planning specialist**, loaded only during the Planning/Reflection stages of a workflow if the primary model's plan confidence is low — see §0.3 model-routing note. |
| Qwen2.5-VL-7B / 3B, Gemma 3 Vision 4B | 3-7B | 2-5GB | 3B/4B: yes, mostly. 7B: tight | **Vision/browser-verification model**, loaded only during the Browser Verification workflow stage (v1 §12), unloaded immediately after — never resident alongside a coding model. |
| MiniLM GGUF / BGE Base v1.5 | <200M | <300MB | Yes, easily | **Always-resident embedding service** (v2 §6.4/§11.2) — small enough to stay loaded permanently in a small VRAM/RAM reservation without competing with the reasoning model. |
| BGE Reranker v2 M3 | ~600M | ~600MB-1GB | Yes | **Always-resident reranking pass** on top of vector search results — cheap enough to keep hot; sits after the initial ANN retrieval in the Context Engine's ranking step (v1 §5.2). |

### 0.2 The Core Placement Rule

**Only one *large* (14B+) model is ever loaded at a time — this is the "one model loaded" constraint from the architecture, applied literally.** Embeddings and reranking are small enough to be the one deliberate exception: they are treated as a permanently-resident *service*, not as "a model" in the Model Session Manager's sense, because at <1GB combined they don't meaningfully compete with a large model's VRAM/RAM budget, and constantly loading/unloading them for every retrieval call would be far more disruptive than keeping them warm.

### 0.3 Model Routing Strategy for This Hardware

This is the concrete instantiation of the architecture's "Future Multi-Model" seam (v2 §16.2) — implemented starting in Phase 1, not deferred to Phase 4, because on 4GB VRAM the cost of *not* routing is too high (using the 35B-A3B for every trivial rename would be needlessly slow):

| Task shape | Model selected | Why |
|---|---|---|
| Default coding/editing/tool-use loop | Qwen3.6-35B-A3B | Best general capability; MoE active-param count keeps CPU inference tractable |
| Mechanical, low-ambiguity edits (rename, format-driven fix, single-line patch) | Qwen2.5-Coder-14B | Faster time-to-first-token; swap cost (§5.4) is worth it for a batch of small fixes |
| Planning a complex/ambiguous multi-file task, or Reflection on a repeated failure | DeepSeek-R1-Distill-Qwen-14B | Reasoning-distilled model produces better decomposition/diagnosis; used for a single planning/reflection call, then swapped back |
| Browser Verification screenshot analysis | Qwen2.5-VL-3B (default) / 7B (opt-in, higher fidelity) | Vision-capable, smallest model that fits the task; loaded only for this workflow stage |
| Embedding generation, reranking (continuous, all workflows) | MiniLM/BGE Base v1.5 + BGE Reranker v2 M3 | Always resident, per §0.2 |

This table is itself a config artifact (`model_routing.yaml`), not hardcoded — it is the seed data for the Model Capability Registry (v2 §5.5) and the future Model Router node (v2 §16.2), which this roadmap builds in Phase 1 as a rule-based router (task-shape → model, per this table) rather than deferring routing intelligence to Phase 4.

### 0.4 Resource Scheduler Defaults for This Hardware

Phase 2's Resource Scheduler (v2 §7) ships with hardware-specific defaults for this machine, set in `config/hardware_profile.yaml`:

```yaml
gpu_vram_total_mb: 4096
gpu_vram_reserved_for_embeddings_mb: 768   # MiniLM + BGE Reranker, always resident
gpu_vram_budget_for_active_model_mb: 3072  # remaining, minus a safety margin
ram_total_mb: 24576
ram_reserved_for_os_and_tools_mb: 4096     # browser, Docker, terminal headroom
ram_budget_for_model_offload_mb: 18432
context_window_default: 8192               # conservative default; adaptive per §4.6/§7.5
context_window_max_before_gpu_pressure: 16384
```

These are the concrete numbers the OOM-prevention ladder (v2 §4.6) and adaptive context reduction (v2 §7.5) act against on *this* machine — on different hardware, only this file changes.

---

## Phase 1 — Core Agent

**Goal of this phase:** a working autonomous loop — user gives a task, the agent plans, reads/edits files, runs tools, verifies, reflects, and commits — running entirely on this laptop with model routing already in place (per §0.3). No dashboard, no plugins, no distributed anything. A CLI is the only frontend.

### 1.1 Module Table

| # | Module | Purpose | Folder | Priority | Complexity | Build order |
|---|---|---|---|---|---|---|
| 1 | `config` | Typed, layered config loading (v3 §18.5) | `backend/config/` | P0 | Low | 1st |
| 2 | `container` | IoC composition root (v3 §18.2) | `backend/container.py` | P0 | Low | 2nd |
| 3 | `model_gateway` | llama.cpp client, OpenAI-compatible, streaming | `backend/model_gateway/` | P0 | Medium | 3rd |
| 4 | `model_session` | Model load/unload/swap, capability registry (v2 §5) | `backend/model_session/` | P0 | Medium | 4th |
| 5 | `model_router` | Rule-based task-shape → model selection (§0.3) | `backend/model_session/router.py` | P0 | Low | 5th |
| 6 | `embedding_service` | Always-resident MiniLM/BGE embed + rerank (v2 §11.2) | `backend/knowledge/embedding_service.py` | P0 | Medium | 6th |
| 7 | `checkpoint_store` | SQLite-backed LangGraph checkpointer (v1 §4.2) | `backend/db/checkpoint.py` | P0 | Medium | 7th |
| 8 | `context_engine` (minimal) | Tree-sitter parse, symbol index, embedding-backed retrieval, no full graph yet | `backend/context_engine/` | P0 | High | 8th |
| 9 | `tool_registry` + core tools | Filesystem, terminal, git (read+write, no browser/docker yet) | `backend/tools/` | P0 | High | 9th |
| 10 | `workflow_engine` (minimal) | Single default workflow: plan → edit → verify → reflect → repair, no template system yet | `backend/orchestration/` | P0 | High | 10th |
| 11 | `planning_engine` (basic) | Goal decomposition into a flat task list (no full DAG scheduling yet) | `backend/orchestration/nodes/planner.py` | P0 | Medium | 11th |
| 12 | `reflection_engine` (basic) | Failure classification + single-strategy repair (no alternative-generation yet) | `backend/orchestration/nodes/reflector.py` | P0 | Medium | 12th |
| 13 | `testing_pipeline` | Formatter → linter → unit tests, language-detected (Python/JS first) | `backend/tools/testing.py` | P0 | Medium | 13th |
| 14 | `git_integration` | Status/diff/commit/branch via GitPython | `backend/tools/git_tool.py` | P0 | Low | 14th |
| 15 | `backend_api` (minimal) | FastAPI: create session, send message, get status, WebSocket stream | `backend/api/` | P0 | Medium | 15th |
| 16 | `cli` | Terminal client talking to the backend API | `cli/` | P0 | Low | 16th |

### 1.2 Per-Module Detail

**`config`** — *Purpose:* single source of truth for all settings, layered (defaults → profile → project → env → runtime). *Depends on:* nothing (foundation). *Public interface:* `ConfigService.get(key, default=None)`, `.get_profile()`, `.on_change(callback)`. *Config:* `config/model.yaml`, `config/hardware_profile.yaml`, `config/security_policy.yaml`. *Libraries:* `pydantic` (schema validation), `pyyaml`. *DB changes:* none. *Testing:* unit tests for precedence resolution, schema validation rejects malformed YAML. *Acceptance:* invalid config fails startup with a specific error naming the bad key, not a stack trace.

**`container`** — *Purpose:* wire every interface to its implementation (v3 §18.2). *Depends on:* `config` (to select implementations per profile). *Public interface:* `Container.resolve(InterfaceType) -> instance`. *Testing:* every binding resolves without circular dependency; swapping a binding in a test config produces the swapped implementation. *Acceptance:* no module outside `container.py` imports a concrete class of another module — enforced by a lint rule (import-linter or equivalent) added in this phase and never removed.

**`model_gateway`** — *Purpose:* thin, well-tested client for llama.cpp's OpenAI-compatible endpoint, streaming + function-calling. *Depends on:* `config` (endpoint URL, per-model params). *Public interface:* `ModelClient.complete(messages, tools, stream=True) -> AsyncIterator[Token|ToolCall]`. *Libraries:* `httpx` (async), `openai` SDK (compatible client) or a hand-rolled thin wrapper — hand-rolled recommended to avoid pulling in cloud-oriented SDK assumptions. *Failure cases:* server unreachable, malformed streamed JSON, timeout mid-stream. *Recovery:* single retry on connection failure; a mid-stream malformed chunk aborts that generation and surfaces to Reflection as a `model_output_error`, not a crash. *Testing:* mock llama.cpp server for unit tests; one real integration test against a locally running server in CI (skipped if no GPU runner available). *Acceptance:* completes a basic prompt and correctly parses one grammar-constrained tool call end to end.

**`model_session`** — *Purpose:* load/unload/swap models per §0.1-§0.3 without losing session state (v2 §5). *Depends on:* `model_gateway`, `config`, `checkpoint_store` (for the forced-checkpoint-before-swap step). *Public interface:* `ModelSessionManager.swap_to(model_id)`, `.current_model()`, `.capability_for(model_id)`. *Failure cases:* swap requested while VRAM/RAM already under pressure; llama.cpp server fails to load the new model (corrupt GGUF, insufficient memory). *Recovery:* on load failure, remain on the previously loaded model, surface the error, do not leave the system in a no-model-loaded state. *Testing:* swap-under-load integration test (swap while a mock generation is in-flight), verifies session content identical before/after. *Acceptance:* a full swap cycle (35B-A3B → 14B-Coder → back) on this hardware completes within an operator-visible progress indicator, with conversation content byte-identical before and after.

**`model_router`** — *Purpose:* implements the §0.3 routing table as executable logic. *Depends on:* `model_session`. *Public interface:* `ModelRouter.select_for(task_shape: TaskShape) -> model_id`. *Config:* `config/model_routing.yaml` (the §0.3 table, editable without code changes). *Testing:* table-driven test — each task shape in the config maps to the expected model id. *Acceptance:* a mechanical-edit subtask measurably swaps to the 14B-Coder model in an integration test.

**`embedding_service`** — *Purpose:* always-resident embedding + reranking (§0.2). *Depends on:* `model_gateway`-adjacent lightweight loader (separate llama.cpp instance or a `llama-cpp-python` in-process load, since this is small enough to embed directly in the backend process rather than requiring a second server). *Public interface:* `EmbeddingService.embed(text) -> vector`, `.rerank(query, candidates) -> ranked list`. *Performance:* target <50ms per embedding call on this hardware for typical code-chunk sizes. *Testing:* embedding stability test (same input → same vector, deterministic); rerank ordering sanity test against a hand-labeled small fixture set. *Acceptance:* service stays resident and warm across at least one full reasoning-model swap cycle without needing to reload.

**`checkpoint_store`** — *Purpose:* SQLite-backed LangGraph checkpointer (v1 §4.2, v2 §8.3's resume tier only in this phase — transaction log comes in Phase 2). *Depends on:* `config`. *DB changes:* creates `checkpoints` table (v1 schema). *Testing:* kill-and-resume test — start a workflow, SIGKILL the process mid-tool-call, restart, verify resume from last checkpoint. *Acceptance:* zero data loss on the kill-and-resume test across 20 repeated runs (flakiness here is disqualifying, not acceptable-with-caveats).

**`context_engine` (minimal)** — *Purpose:* Tree-sitter parsing for Python/JS/TS first (expand languages in Phase 2), a flat symbol index (full unified graph deferred to Phase 2 per v3 §22.1), and embedding-backed retrieval via `embedding_service`. *Depends on:* `embedding_service`, `checkpoint_store` (for context summaries, minimal version). *Public interface:* `ContextEngine.retrieve(query, budget_tokens) -> RankedContext`. *Libraries:* `tree-sitter`, `tree-sitter-python`, `tree-sitter-javascript`, `tree-sitter-typescript`. *Performance concern:* full-repo first index on a 24GB RAM machine needs a size ceiling — cap initial full index at repos under ~5000 files in Phase 1, background/incremental indexing for anything larger deferred to Phase 2's Smart Cache (v3 §22.4). *Testing:* retrieval relevance test against a small fixture repo with known expected top-k results. *Acceptance:* indexes a 500-file Python repo in under 30 seconds on this hardware.

**`tool_registry` + core tools** — *Purpose:* filesystem (read/write/patch/diff), terminal (subprocess, PTY for interactive), git — the minimum tool set for the edit-verify loop. *Depends on:* `config` (security policy), `container`. *Public interface:* `ToolRegistry.get(name) -> Tool`, `Tool.execute(args) -> ToolResult`. *Security concerns:* path validation and command allowlist (v3 §13.1's chokepoint) must exist from day one, not retrofitted — this is a hard Phase-1 requirement, not a Phase-2 hardening item, because an agent with unrestricted filesystem/terminal access is unsafe even in a minimal prototype. *Failure cases:* command not found, permission denied, timeout. *Recovery:* per v1 §7.5 — read-only tools retry, write tools do not auto-retry. *Testing:* permission-boundary test (attempt path traversal, verify blocked); timeout test; atomic-write-then-rename test (kill process mid-write, verify no corrupt file). *Acceptance:* cannot write outside the configured project root under any tested input.

**`workflow_engine` (minimal)** — *Purpose:* execute the single default LangGraph workflow (v1 §2 pipeline), no template system or nested workflows yet (those are Phase 2/3, v3 §21.5/§21.7). *Depends on:* everything above. *Public interface:* `WorkflowEngine.run(task_description, project_id) -> Session`. *Testing:* end-to-end test — "add a function that reverses a string" against a fixture repo, verify file created, test passes, commit made. *Acceptance:* the canonical end-to-end test passes reliably (10/10 runs) on this hardware within a defined time budget (e.g. under 5 minutes for a trivial task, generous given CPU-bound 35B-A3B inference).

**`planning_engine` (basic)**, **`reflection_engine` (basic)**, **`testing_pipeline`**, **`git_integration`** — each scoped to the minimum viable version of their v1/v2/v3 design (flat task list, single repair strategy, formatter+linter+unit-tests only, basic git ops) — full designs (DAG scheduling, alternative-solution generation, full pipeline with browser/security-scan stages, full git feature set) are explicitly Phase 2/3 work, called out again in the Missing Components Audit (§9).

**`backend_api` (minimal)** — *Purpose:* the smallest FastAPI surface that lets the CLI drive a session. *Endpoints (full detail in §5):* `POST /api/sessions`, `POST /api/sessions/{id}/messages`, `GET /api/sessions/{id}`, `WS /ws/sessions/{id}`. *Testing:* API contract tests (schema validation on every endpoint). *Acceptance:* a full session lifecycle (create → message → stream tokens/tool events → completion) works over the CLI end to end.

**`cli`** — *Purpose:* terminal client, not a toy — this is the Phase 1 user interface. *Depends on:* `backend_api`. *Libraries:* `rich`/`textual` for a readable streaming terminal UI. *Acceptance:* a developer can `agent run "fix the failing test in tests/test_x.py"` against a real small repo and watch it work.

### 1.3 Phase 1 Completion Checklist

- [ ] Config loads and validates from all three YAML files with correct precedence
- [ ] IoC container resolves every Phase-1 interface with zero circular dependencies
- [ ] Model swap cycle (35B-A3B ↔ 14B-Coder) completes with byte-identical session content before/after, verified under load
- [ ] Embedding service stays resident across a model swap
- [ ] Kill-and-resume test passes 20/20
- [ ] Path-traversal and command-injection test suite passes with zero bypasses
- [ ] Canonical end-to-end task ("add a function, verify, commit") passes 10/10 on this hardware
- [ ] CLI can drive a full session start-to-finish with live streaming output
- [ ] No module outside `container.py` directly imports another module's concrete implementation (enforced by lint rule, checked in CI)

---

## Phase 2 — Production Infrastructure

**Goal of this phase:** the agent survives crashes, resource pressure, and multi-hour unattended runs on this specific 4GB VRAM / 24GB RAM machine without losing work or taking down the host.

### 2.1 Module Table

| # | Module | Purpose | Folder | Priority | Complexity | Build order |
|---|---|---|---|---|---|---|
| 17 | `health_manager` | Resource monitoring, watchdog, OOM-prevention ladder (v2 §4) | `backend/health/` | P0 | High | 17th |
| 18 | `resource_scheduler` | Priority queues, reservations, adaptive throttling (v2 §7) | `backend/scheduler/` | P0 | High | 18th |
| 19 | `reliability_layer` | Transaction log, idempotency, circuit breaker (v2 §8) | `backend/reliability/` | P0 | High | 19th |
| 20 | `event_bus` (full) | Domain/priority/workflow/notification/plugin lanes (v3 §19) | `backend/events/` | P0 | Medium | 20th |
| 21 | `context_persistence` | Versioned summaries, compression ladder (v2 §6) | `backend/context_persistence/` | P1 | Medium | 21st |
| 22 | `repository_intelligence` | Unified graph, incremental smart cache (v3 §22) | `backend/context_engine/repo_graph.py` | P1 | High | 22nd |
| 23 | `memory_system` (full) | Episodic/repo/bug/preference/architecture memory (v1 §6, v2 §6.3) | `backend/memory/` | P1 | High | 23rd |
| 24 | `logging_observability` | Structured logs, OTel, Prometheus (v1 §18-19) | `backend/observability/` | P0 | Medium | 24th |
| 25 | `security_hardening` | Command validator chokepoint, secret scan, dependency scan (v3 §13) | `backend/security/` | P0 | High | 25th |
| 26 | `state_management` | Formal state ownership, validation, migration (v3 §20) | `backend/state/` | P1 | Medium | 26th |
| 27 | `lifecycle_manager` | Startup sequence, graceful shutdown, crash recovery (v2 §3) | `backend/lifecycle.py` | P0 | Medium | 27th |
| 28 | `evaluation_framework` (basic) | Task-level metrics only; agent-quality metrics deferred to Phase 3 | `backend/evaluation/` | P2 | Medium | 28th |

### 2.2 Per-Module Detail (condensed — Phase 2 builds directly on Phase 1's patterns)

**`health_manager`** — *Failure cases specific to this hardware:* the 35B-A3B model's CPU-offloaded layers make RAM pressure the dominant risk (more than VRAM, which is already conservatively budgeted per §0.4) — tune the OOM-prevention ladder's RAM thresholds tighter than the architecture's generic defaults: warn at 75% (not 80%), critical at 85% (not 90%), because a 24GB machine running a ~20GB model on disk leaves less headroom than the architecture assumed for a generic "production server" profile. *Testing:* synthetic memory-pressure test (allocate dummy memory to force the ladder to trigger) verifies each rung fires in order. *Acceptance:* the ladder demonstrably prevents an OOM kill in a stress test that would otherwise trigger one.

**`resource_scheduler`** — *Configuration:* seeded from `config/hardware_profile.yaml` (§0.4). *Testing:* contention test — start a Docker-based tool call and a model swap simultaneously, verify priority ordering (model inference wins). *Acceptance:* no session ever silently stalls without a `WAITING` reason code visible via the API.

**`reliability_layer`** — *DB changes:* adds `transaction_log` table (v3 schema). *Testing:* exactly-once test — force a retry of an `effectful` tool call (e.g. git commit) via fault injection, verify the dedupe key prevents a duplicate commit. *Acceptance:* zero duplicate side effects across a fault-injection suite covering every `effectful` tool.

**`event_bus` (full)** — *Migration from Phase 1:* Phase 1 used a minimal single-topic bus; this phase splits it into the five lanes (v3 §19.1) behind the same facade interface, so no Phase-1 consumer code changes. *Testing:* ordering test on the Workflow Queue (per-workflow FIFO guaranteed even under concurrent unrelated events).

**`context_persistence`**, **`repository_intelligence`**, **`memory_system`** — expand Phase 1's minimal context engine into the full v2/v3 design. *Performance concern specific to this hardware:* the unified repository graph (v3 §22.1) for a large repo must stay within the RAM budget in `hardware_profile.yaml` — cap in-memory graph size and spill to the on-disk Smart Cache tiers (v3 §22.4) beyond a configurable node count (default 200k nodes) rather than growing unbounded.

**`security_hardening`** — promotes Phase 1's basic path/command validation into the full chokepoint design (v3 §13.1) plus secret scanning, dependency vulnerability scanning (offline OSV dataset sync). *Acceptance:* the full security test suite from v3's Edge Case Registry categories (path traversal, command injection, secret leakage) passes with zero bypasses.

**`state_management`**, **`lifecycle_manager`** — formalize what Phase 1 built ad hoc; every Phase-1 module gets retrofitted to implement the `on_startup/on_health_check/on_shutdown/on_crash_recovery` interface (v3 §18.9) in this phase — this is the one planned piece of Phase-1-code revisiting in the whole roadmap, and it's scoped narrowly (interface conformance, not redesign).

**`evaluation_framework` (basic)** — task-success-rate and code-quality metrics only in this phase (derived automatically from the testing pipeline, v2 §14.2) — agent-quality metrics (hallucination rate, planning quality) require the deeper Reflection Engine work and are Phase 3.

### 2.3 Phase 2 Completion Checklist

- [ ] Synthetic RAM-pressure test triggers the OOM-prevention ladder correctly, no OOM kill
- [ ] Resource contention test confirms priority ordering under load
- [ ] Exactly-once fault-injection suite passes for every `effectful` tool
- [ ] Full security test suite passes with zero bypasses
- [ ] A 6-hour unattended stress run (synthetic repeated tasks against a fixture repo) completes without process crash, without silent stalls, and with correct checkpoint resume if manually killed partway through
- [ ] Every Phase-1 module implements the Lifecycle Manager interface

---

## Phase 3 — Developer Experience

**Goal of this phase:** the agent is inspectable, trustworthy, and pleasant to actually watch/debug — this is where the frontend from v1 §22/v2 §17 gets built.

### 3.1 Module Table

| # | Module | Purpose | Folder | Priority | Complexity |
|---|---|---|---|---|---|
| 29 | `frontend_shell` | Next.js app shell, routing, Zustand store, WS/SSE client | `frontend/` | P1 | Medium |
| 30 | `file_explorer` | Repo tree + git status | `frontend/components/FileExplorer/` | P1 | Low |
| 31 | `diff_viewer` | Monaco diff mode, approve/reject UI for Tier-2 edits | `frontend/components/DiffViewer/` | P1 | Medium |
| 32 | `terminal_panel` | xterm.js bound to live PTY stream | `frontend/components/Terminal/` | P1 | Medium |
| 33 | `execution_timeline` | Gantt-style state-transition view, incl. `WAITING` reasons | `frontend/components/TaskTimeline/` | P1 | Medium |
| 34 | `workflow_visualizer` | Live-rendered compiled workflow graph (v3 §21.9) | `frontend/components/WorkflowGraph/` | P2 | High |
| 35 | `prompt_inspector` | Fully-rendered prompt per turn, tier-annotated (v3 §17) | `frontend/components/PromptInspector/` | P2 | Medium |
| 36 | `context_inspector` | Ranked/compressed context with eviction reasons | `frontend/components/ContextInspector/` | P2 | Medium |
| 37 | `memory_inspector` | Browse/edit memory with version history | `frontend/components/MemoryViewer/` | P2 | Medium |
| 38 | `checkpoint_viewer` | Browse/diff/resume-from-arbitrary-checkpoint | `frontend/components/CheckpointViewer/` | P2 | Medium |
| 39 | `performance_dashboard` | Live resource + scheduler queue depth view | `frontend/components/PerfDashboard/` | P2 | Medium |
| 40 | `live_logs` | Filterable structured log stream | `frontend/components/Logs/` | P1 | Low |
| 41 | `interactive_debugger` | Step-through pause/resume between workflow nodes | `backend/orchestration/debug.py` + `frontend/components/Debugger/` | P2 | High |
| 42 | `replay_system` | Deterministic replay of a past session's event stream | `backend/evaluation/replay.py` | P2 | Medium |
| 43 | `settings_ui` | Configuration profiles, security policy editing | `frontend/components/Settings/` | P1 | Low |
| 44 | `plugin_ui` | Install/enable/configure plugins | `frontend/components/Plugins/` | P2 | Medium |

### 3.2 Notes

All Phase 3 backend support (Tool Inspector data, Prompt Inspector data, etc.) is a thin read API over state Phase 1/2 already persist — this phase is front-loaded toward frontend work specifically because the backend observability data model was built correctly in Phase 2. The **Replay System** is the one backend-heavy item: it re-drives the Domain Event Stream (v3 §19.2) for a completed session through a read-only renderer, reusing the same event schema the live UI consumes — replay and live view share one rendering code path, they only differ in event source (historical vs. live).

### 3.3 Phase 3 Completion Checklist

- [ ] A developer can watch a live session end-to-end through the dashboard with no need to read raw logs
- [ ] Diff approval UI correctly blocks a Tier-2 tool call until clicked
- [ ] Prompt Inspector shows the exact rendered prompt, tier-annotated, matching what was actually sent to `model_gateway`
- [ ] Replay reproduces the same visual timeline for a completed session as it showed live
- [ ] Interactive debugger can pause between any two workflow nodes and resume correctly

---

## Phase 4 — Advanced Features

**Goal of this phase:** the differentiating capabilities — vision, plugins-as-an-ecosystem, distributed/cloud extension points actually exercised, continuous learning. Everything here is additive; Phases 1-3 do not depend on any Phase 4 module existing.

### 4.1 Module Table

| # | Module | Purpose | Depends on | Priority | Complexity |
|---|---|---|---|---|---|
| 45 | `vision_pipeline` | Qwen2.5-VL-3B/7B, Gemma 3 Vision routing for browser-verification screenshots and OCR | `model_router`, `browser` tool | P2 | Medium |
| 46 | `voice_interface` | Speech-to-text task input (local STT model, e.g. whisper.cpp) | `backend_api` | P3 | Medium |
| 47 | `plugin_marketplace` | Remote plugin index, signature verification, install flow | `plugin_manager` (v3 §18.8) | P2 | Medium |
| 48 | `remote_workers` | `RemoteToolWorker` implementing the `ToolResult` contract over the network (v2 §16.3) | `resource_scheduler`, `event_bus` (Redis mode) | P3 | High |
| 49 | `cloud_fallback` | Opt-in cloud model client, logged identically to any model swap | `model_session` | P3 | Low |
| 50 | `distributed_execution` | Multi-machine resource pool registration (v2 §16.1) | `remote_workers`, `resource_scheduler` | P3 | High |
| 51 | `multi_agent` | Multiple concurrent workflow sessions collaborating on one project (e.g. one planning, one implementing) | `workflow_engine`, `event_bus` | P3 | High |
| 52 | `finetuning_support` | Export verified fix history (Learning Memory, v3 §24.4) as a fine-tuning dataset | `memory_system` | P3 | Medium |
| 53 | `background_tasks` | Scheduled/recurring tasks (e.g. nightly dependency audit) | `resource_scheduler`, `lifecycle_manager` | P2 | Low |
| 54 | `project_templates` | Scaffold new projects from templates | `tool_registry` | P2 | Low |
| 55 | `workspace_manager` | Multi-project switching, per-project resource budgets | `config`, `resource_scheduler` | P2 | Medium |
| 56 | `auto_documentation` | Repository-summary-driven README/API-doc generation | `repository_intelligence` | P2 | Low |
| 57 | `auto_refactoring` | Technical-debt-score-driven refactor proposals (v3 §22.5) | `repository_intelligence`, `planning_engine` | P3 | Medium |
| 58 | `repository_learning` | Promotes recurring patterns (v3 §24.2's calibration feedback) into project-specific planning heuristics | `memory_system`, `planning_engine` | P3 | Medium |
| 59 | `continuous_learning` | Periodic benchmark re-run (v2 §14.3) gating prompt/model-routing config changes | `evaluation_framework`, `prompt_management` | P3 | Medium |

### 4.2 Hardware Note for Phase 4

`vision_pipeline` and `voice_interface` both compete for the same constrained VRAM budget as the reasoning model. Both are designed as **transient loads**: loaded only for the duration of a single verification/transcription call, then unloaded — never resident alongside a reasoning model, consistent with §0.1's placement table. `remote_workers`/`distributed_execution` are the actual escape valve for this hardware's ceiling — offloading Docker builds and browser automation to a second machine, if available, meaningfully raises what's practical on a 4GB VRAM laptop, and is worth prioritizing within Phase 4 over the more exotic items (`multi_agent`, `finetuning_support`) if hardware headroom remains the binding constraint.

### 4.3 Phase 4 Completion Checklist

- [ ] Vision model loads/unloads cleanly around a browser-verification step without disturbing the active reasoning-model session
- [ ] A plugin installed from the marketplace passes signature verification and loads in an isolated subprocess
- [ ] A remote worker (if a second machine is available) successfully executes a Docker build dispatched from this laptop
- [ ] Cloud fallback, when explicitly enabled, is visibly logged and never silently invoked

---

## 5. Complete API Specification

### 5.1 REST

```
POST   /api/sessions                       create session          → {id, state}
GET    /api/sessions/{id}                  session status          → full state snapshot
POST   /api/sessions/{id}/messages         send task/message       → {message_id}
POST   /api/sessions/{id}/pause            pause                   → {state}
POST   /api/sessions/{id}/resume           resume from checkpoint  → {state}
POST   /api/sessions/{id}/cancel           cancel                  → {state}
GET    /api/sessions/{id}/history          full event history      → paginated event list
GET    /api/sessions/{id}/checkpoints      list checkpoints        → [{step, created_at}]
POST   /api/sessions/{id}/checkpoints/{n}/resume   resume from arbitrary checkpoint (debug)
GET    /api/projects                       list indexed projects
POST   /api/projects                       register a new project root
POST   /api/projects/{id}/reindex          force re-index
GET    /api/projects/{id}/graph            repository graph summary (v3 §22.1)
GET    /api/memory/{project_id}            browse memory           → filterable list
PATCH  /api/memory/{project_id}/{entry_id} edit/supersede a memory entry
POST   /api/approvals/{tool_call_id}       approve/deny pending Tier-2+ call
GET    /api/plugins                        list installed plugins
POST   /api/plugins/install                install (local path or marketplace id)
DELETE /api/plugins/{id}                   uninstall
GET    /api/system/versions                core/plugin/model/schema versions (v3 §18.7)
GET    /api/system/health                  live health snapshot (v2 §4)
GET    /api/system/capabilities            capability registry dump (v3 §18.4)
GET    /api/models                         model capability registry (v2 §5.5)
POST   /api/models/{id}/swap               manual model swap
GET    /api/evaluation/benchmarks          benchmark run history (v2 §14)
POST   /api/evaluation/benchmarks/run      trigger a benchmark run
```

### 5.2 WebSocket

`WS /ws/sessions/{id}` — single multiplexed connection, frames tagged by `type` matching the Domain Event Stream catalog (v3 §19.2): `token`, `tool.started`, `tool.finished`, `state.transition`, `log`, `resource.warning`, `approval.required`.

### 5.3 SSE

`GET /sse/sessions/{id}` — read-only fallback transport, same event types as WebSocket, used when WS is unavailable; user actions still go through REST POST endpoints.

### 5.4 Internal APIs (backend-internal, not exposed externally)

`ToolRegistry.get/execute`, `EventBus.publish/subscribe`, `Container.resolve`, `ConfigService.get`, `CheckpointStore.save/load`, `EmbeddingService.embed/rerank` — each is the public interface of its owning module (§1.2/§2.2) and is what the IoC container wires (v3 §18.2); these are documented here because they're the seam every plugin and every new module builds against.

### 5.5 Event APIs

Publish/subscribe contract per v3 §19: `EventBus.publish(event: DomainEvent)`, `EventBus.subscribe(event_class: str, handler, scope: Optional[ProjectScope])`. Full event catalog in §6.

### 5.6 Plugin APIs

Per v3 §18.8's Extension Manager: `register_tool(schema, handler)`, `register_panel(manifest)`, `register_ranking_signal(fn)`, `register_failure_classifier(fn)` — each contribution point is a typed registration call available to a loaded plugin's subprocess via the JSON-RPC bridge.

### 5.7 MCP APIs

Standard MCP client per v1 §21.6 — the backend connects to configured MCP servers (`config/mcp_servers.yaml`) at startup, and any tool an MCP server exposes is normalized into the same `ToolSchema` as native tools (v3 §23.1).

### 5.8 AuthN/AuthZ

Phase 1-3 (local single-user): disabled, localhost-only binding. Phase 4, if `workspace_manager`/team features are pursued: JWT bearer tokens, project-scoped roles (v2 §12.6) — deferred implementation, interface reserved from Phase 1 (`backend_api` accepts an optional auth dependency that's a no-op until Phase 4).

---

## 6. Complete Database Schema

Consolidates and finalizes every table referenced across v1/v2/v3 into migration order. SQLite for Phase 1-3 (this hardware, single user); Postgres-compatible DDL throughout so Phase 4's optional server mode is a connection-string change, not a schema rewrite.

### 6.1 Migration Order

```
0001_projects_and_sessions
0002_tasks_and_checkpoints
0003_tool_calls_and_execution_history
0004_memory_and_embeddings
0005_snapshots
0006_transaction_log            -- Phase 2
0007_resource_metrics           -- Phase 2
0008_evaluation_and_prompts     -- Phase 2/3
0009_audit_log                  -- Phase 2
0010_context_summaries          -- Phase 2
0011_models_registry            -- Phase 1 (but migrated after core tables exist)
0012_plugin_registry            -- Phase 4
```

### 6.2 Full Table Definitions

```sql
-- 0001
CREATE TABLE projects (
  id UUID PRIMARY KEY,
  root_path TEXT NOT NULL UNIQUE,
  language TEXT,
  framework TEXT,
  last_indexed_at TIMESTAMP,
  architecture_summary TEXT,
  created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE sessions (
  id UUID PRIMARY KEY,
  project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
  state TEXT NOT NULL,
  active_model_id TEXT,
  created_at TIMESTAMP DEFAULT now(),
  updated_at TIMESTAMP DEFAULT now()
);
CREATE INDEX idx_sessions_project ON sessions(project_id);
CREATE INDEX idx_sessions_state ON sessions(state);

-- 0002
CREATE TABLE tasks (
  id UUID PRIMARY KEY,
  session_id UUID REFERENCES sessions(id) ON DELETE CASCADE,
  parent_task_id UUID REFERENCES tasks(id),
  description TEXT NOT NULL,
  status TEXT NOT NULL,
  confidence_score REAL,
  done_condition TEXT,
  created_at TIMESTAMP DEFAULT now()
);
CREATE INDEX idx_tasks_session ON tasks(session_id);
CREATE INDEX idx_tasks_parent ON tasks(parent_task_id);

CREATE TABLE checkpoints (
  session_id UUID REFERENCES sessions(id) ON DELETE CASCADE,
  step INTEGER NOT NULL,
  state_blob BYTEA NOT NULL,
  created_at TIMESTAMP DEFAULT now(),
  PRIMARY KEY (session_id, step)
);

-- 0003
CREATE TABLE tool_calls (
  id UUID PRIMARY KEY,
  session_id UUID REFERENCES sessions(id) ON DELETE CASCADE,
  task_id UUID REFERENCES tasks(id),
  tool_name TEXT NOT NULL,
  tool_origin TEXT NOT NULL,          -- native/mcp/plugin (v3 §23.1)
  args_json TEXT,
  permission_tier INTEGER,
  status TEXT,
  duration_ms INTEGER,
  created_at TIMESTAMP DEFAULT now()
);
CREATE INDEX idx_toolcalls_session ON tool_calls(session_id);
CREATE INDEX idx_toolcalls_tool ON tool_calls(tool_name);

CREATE TABLE execution_history (
  id UUID PRIMARY KEY,
  session_id UUID REFERENCES sessions(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL,
  payload_json TEXT,
  created_at TIMESTAMP DEFAULT now()
);
CREATE INDEX idx_exechist_session ON execution_history(session_id);

-- 0004
CREATE TABLE memory_entries (
  id UUID PRIMARY KEY,
  project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
  memory_type TEXT NOT NULL,
  content TEXT NOT NULL,
  vector_id TEXT,
  supersedes_id UUID REFERENCES memory_entries(id),
  superseded_reason TEXT,
  verified BOOLEAN DEFAULT false,
  created_at TIMESTAMP DEFAULT now()
);
CREATE INDEX idx_memory_project_type ON memory_entries(project_id, memory_type);

CREATE TABLE embeddings (
  id UUID PRIMARY KEY,
  project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
  file_path TEXT NOT NULL,
  symbol_name TEXT,
  chunk_hash TEXT NOT NULL,
  vector_id TEXT NOT NULL,
  updated_at TIMESTAMP DEFAULT now()
);
CREATE UNIQUE INDEX idx_embeddings_chunk ON embeddings(project_id, chunk_hash);

-- 0005
CREATE TABLE snapshots (
  content_hash TEXT PRIMARY KEY,
  file_path TEXT NOT NULL,
  session_id UUID REFERENCES sessions(id),
  step INTEGER,
  storage_ref TEXT NOT NULL
);

-- 0006 (Phase 2)
CREATE TABLE transaction_log (
  id BIGSERIAL PRIMARY KEY,
  session_id UUID REFERENCES sessions(id),
  operation_type TEXT NOT NULL,
  dedupe_key TEXT,
  args_json TEXT,
  status TEXT NOT NULL,
  attempted_at TIMESTAMP DEFAULT now(),
  resolved_at TIMESTAMP
);
CREATE INDEX idx_txlog_dedupe ON transaction_log(dedupe_key);
CREATE INDEX idx_txlog_session ON transaction_log(session_id);

-- 0007 (Phase 2)
CREATE TABLE resource_metrics (
  id BIGSERIAL PRIMARY KEY,
  timestamp TIMESTAMP DEFAULT now(),
  resource_type TEXT NOT NULL,
  value REAL NOT NULL,
  threshold_state TEXT NOT NULL
);
CREATE INDEX idx_resmetrics_time_type ON resource_metrics(resource_type, timestamp);

-- 0008 (Phase 2/3)
CREATE TABLE evaluation_results (
  id UUID PRIMARY KEY,
  session_id UUID REFERENCES sessions(id),
  task_id UUID REFERENCES tasks(id),
  metric_name TEXT NOT NULL,
  metric_value REAL NOT NULL,
  benchmark_run_id UUID,
  created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE prompt_templates (
  id UUID PRIMARY KEY,
  name TEXT NOT NULL,
  version INTEGER NOT NULL,
  content TEXT NOT NULL,
  variables_schema TEXT,
  test_pass_rate REAL,
  is_active BOOLEAN DEFAULT false,
  created_at TIMESTAMP DEFAULT now(),
  UNIQUE(name, version)
);

-- 0009 (Phase 2)
CREATE TABLE audit_log (
  id BIGSERIAL PRIMARY KEY,
  session_id UUID,
  action TEXT NOT NULL,
  permission_tier INTEGER,
  approved_by TEXT NOT NULL,
  prev_hash TEXT,
  entry_hash TEXT NOT NULL,
  created_at TIMESTAMP DEFAULT now()
);

-- 0010 (Phase 2)
CREATE TABLE context_summaries (
  id UUID PRIMARY KEY,
  project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
  summary_type TEXT NOT NULL,
  content TEXT NOT NULL,
  supersedes_id UUID REFERENCES context_summaries(id),
  superseded_reason TEXT,
  created_at TIMESTAMP DEFAULT now()
);
CREATE INDEX idx_ctxsum_project_type ON context_summaries(project_id, summary_type);

-- 0011 (Phase 1)
CREATE TABLE models (
  id TEXT PRIMARY KEY,
  context_window INTEGER,
  supports_function_calling BOOLEAN,
  tokenizer_family TEXT,
  capability_tier TEXT,
  vram_estimate_mb INTEGER,
  ram_estimate_mb INTEGER
);

-- 0012 (Phase 4)
CREATE TABLE plugins (
  id TEXT PRIMARY KEY,
  version TEXT NOT NULL,
  min_core_version TEXT,
  max_core_version TEXT,
  permissions_json TEXT,
  source TEXT,                       -- 'local' or 'marketplace'
  signature_verified BOOLEAN DEFAULT false,
  enabled BOOLEAN DEFAULT true,
  installed_at TIMESTAMP DEFAULT now()
);
```

---

## 7. Complete Event Catalog

| Event | Producer | Consumer(s) | Payload | Retry | Failure handling |
|---|---|---|---|---|---|
| `system.ready` | `lifecycle_manager` | frontend, health dashboard | capability manifest | n/a | n/a |
| `system.degraded` | `lifecycle_manager` | frontend, notification bus | missing capabilities list | n/a | surfaced as persistent banner until resolved |
| `model.swap_started` / `model.swap_completed` | `model_session` | orchestration, frontend | model id, duration | n/a | swap failure → `model.swap_failed`, session stays on prior model |
| `session.started/paused/resumed/completed/failed/needs_review` | `workflow_engine` | frontend, evaluation | session id, state | n/a | `needs_review` triggers notification |
| `task.started/finished` | `planning_engine` | frontend, evaluation | task id, confidence | n/a | — |
| `file.changed` | `tool_registry` (filesystem tool) | `repository_intelligence`, frontend | path, change type, tool_call_id | n/a | triggers incremental re-index |
| `tool.started/finished` | `tool_registry` | frontend, `reliability_layer`, evaluation | tool name, args (masked), status, duration | idempotent tools: silent retry up to 2x; effectful: dedupe-guarded | non-idempotent failure → `reflection` node |
| `tool.timeout` | `tool_registry` | `reflection_engine`, frontend | tool name, elapsed | n/a | routes to Reflection |
| `memory.updated` | `memory_system` | frontend, `context_persistence` | memory type, entry id, version | n/a | — |
| `checkpoint.saved` | `reliability_layer` | frontend, `state_management` | session id, step, size | n/a | write failure → emergency-save path (§ health manager) |
| `checkpoint.corrupted` | `state_management` (validation) | notification bus | checkpoint id | n/a | flagged, prior valid checkpoint used |
| `error.detected` | any subsystem | `health_manager`, frontend | component, severity, diagnostic ref | n/a | — |
| `recovery.started/completed` | `lifecycle_manager` | frontend, audit log | trigger reason, session id | n/a | — |
| `browser.event` | `vision_pipeline`/browser tool | `reflection_engine`, frontend | navigation/console/network detail | n/a | console errors feed Reflection same as test failures |
| `git.event` | `git_integration` | frontend, audit log | operation, branch, commit hash | n/a | — |
| `plugin.event` | plugin (via Extension Manager) | scoped subscribers only | plugin-defined | plugin-defined | isolated — plugin crash never propagates |
| `resource.warning/critical/recovered` | `health_manager` | `resource_scheduler`, frontend | resource type, value, threshold | n/a | drives OOM-prevention ladder |
| `health.heartbeat_missed` | `health_manager` | `lifecycle_manager` | component id | n/a | triggers isolated component restart where supported |
| `workflow.step_ready/step_completed/branch_taken/approval_required` | `workflow_engine` | `event_bus` (Workflow Queue), frontend | workflow id, step id | strict FIFO per workflow | `approval_required` blocks until API call |

---

## 8. Complete Tool Specification

| Tool | Inputs | Outputs | Permission tier | Timeout default | Retry | Approval required |
|---|---|---|---|---|---|---|
| `fs.read` | path | content | 0 | 5s | yes (idempotent) | no |
| `fs.write` | path, content | diff, snapshot ref | 1 | 5s | no | no (auto, logged) |
| `fs.patch` | path, unified diff | applied diff, snapshot ref | 1 | 5s | no | no |
| `fs.delete` | path | snapshot ref | 2 | 5s | no | yes |
| `fs.rename`/`fs.move`/`fs.copy` | src, dest | result | 1 | 5s | no | no |
| `fs.search`/`fs.glob` | pattern | matches | 0 | 10s | yes | no |
| `fs.batch_edit` | list of patches | combined diff | 1 | 30s | no | no (auto if each patch is Tier 1) |
| `terminal.run` | command, cwd, env, timeout | stdout, stderr, exit code | 1-2 (allowlist-dependent) | 120s (configurable) | no | policy-dependent (v1 §11) |
| `terminal.run_background` | command | job_id | 2 | n/a (detached) | no | yes |
| `terminal.kill` | job_id | status | 1 | 5s | no | no |
| `git.status/diff/log/blame` | — | data | 0 | 10s | yes | no |
| `git.commit` | message, files | commit hash | 1 | 10s | no (dedupe-guarded) | no (auto per policy) |
| `git.branch/merge/rebase/cherry_pick/stash` | args | result | 1-2 | 15s | no | Tier 2 ops require approval |
| `git.reset --hard`/`push --force` | args | result | 3 | 15s | no | always |
| `browser.navigate/screenshot/get_dom/get_console_logs` | url/selector | data/image | 1 | 30s | limited | no |
| `browser.click/fill/upload` | selector, value | result | 1 | 30s | no | no |
| `docker.build/run/compose_up` | dockerfile/image, args | logs, container id | 2 | 600s (build) | no | yes (first use per project, then policy-cached) |
| `python.run_snippet` | code | stdout, result | 1 (sandboxed) | 30s | no | no |
| `python.install_package` | package, version | result | 2 | 60s | no | yes (unless vulnerability scan clean and policy allows) |
| `http.request` | method, url, headers, body | response | 2 (non-localhost) / 0 (localhost) | 30s | limited | non-localhost requires allowlist |
| `db.query` | connection, parameterized query | rows | 1-2 | 30s | no | writes require approval |
| `search.symbol/semantic` | query | ranked results | 0 | 5s | yes | no |
| `package_manager.install/uninstall/audit` | package | result | 2 | 60s | no | install: vulnerability-gated; audit: no |
| `lsp.goto_definition/find_references/hover/diagnostics` | file, position | data | 0 | 5s | yes | no |

---

## 9. Workflows

Each is a compiled workflow per the Workflow Engine (v3 §21), expressed here as its step sequence and key branch points.

**New Project** — requirement analysis → template selection (v4 `project_templates`) → scaffold via `fs` tools → dependency install (vulnerability-gated) → initial git commit → repository index (full).

**Bug Fix** — requirement analysis → repository analysis (incremental) → context retrieval (bug-relevant: stack trace, failing test) → task decomposition (usually flat, single subtask) → edit → validate → test → [pass: summarize+commit | fail: reflect → repair → retry, bounded].

**Feature Addition** — requirement analysis → project planning (task graph, often multi-subtask) → per-subtask: context retrieval → edit → validate → test → next subtask → integration test across all changed subtasks → browser verify (if UI-facing) → summarize → commit.

**Repository Analysis** (read-only) — repository indexing → detection passes (language/framework/architecture, v3 §22.2) → technical debt analysis (v3 §22.5) → summary generation → no write tools ever invoked; uses the "read-only investigation" template (v3 §21.5).

**Refactoring** — repository analysis → technical-debt-score-driven target selection (auto-suggested or user-specified) → planning (typically larger task graph, extra checkpointing per v3 §21.5's large-refactor template) → per-subtask edit/validate/test loop → full regression test suite (not just narrow scope) → summarize → commit.

**Documentation** — repository analysis → per-module summary generation → README/API-doc drafting → user review gate (human approval node, v3 §21.6) → commit.

**Dependency Update** — dependency detection → vulnerability scan of proposed new versions → per-dependency: update → run full test suite → [pass: next dependency | fail: reflect — is this dependency's breaking change fixable, or does it need to be skipped and flagged] → summarize → commit.

**Code Review** (read-only, on a diff/PR the user provides or a completed session's diff) — context retrieval scoped to changed files → static checks (lint, complexity, duplicate/dead code per v3 §22.5) → LLM review pass → structured findings output, no write tools invoked.

**Testing** — detect existing test framework → generate missing test cases for under-covered code (coverage-gap-driven) → validate new tests actually fail without the target code and pass with it (mutation-adjacent sanity check) → commit.

**Debugging** (interactive, human-in-the-loop heavy) — reproduce the reported issue (run the failing scenario) → capture full diagnostic bundle (v2 §4.3-style) → reflection-driven root-cause hypothesis → present hypothesis + proposed fix via human approval node before applying → apply → verify.

**Failure Recovery** — triggered internally, not user-invoked: reflection → repair → retry per v1 §14/v3 §24, bounded by the session circuit breaker (v2 §8.5).

**Model Switching** — triggered by `model_router` or manual API call → forced checkpoint → session `WAITING` → swap sequence (v2 §5.4) → session resumes.

**Resume Interrupted Task** — checkpoint scan on startup (v2 §3.1 step 10) → surfaced to user → on confirm: real-world state re-probe → resume at last checkpointed node.

**Crash Recovery** — process supervisor restart → same as Resume Interrupted Task, auto-triggered rather than user-initiated, session marked `INTERRUPTED` first.

**Checkpoint Restore** (developer/debug workflow) — Checkpoint Viewer (§Phase 3) selects an arbitrary past checkpoint → validation (v3 §20.5) → resume from that point, subsequent history from the original run is preserved as a divergent branch, not overwritten.

**Large Repository Indexing** — capability/size check (v3 §22.4's Smart Cache thresholds) → prioritized indexing (entry points + task-relevant files first, per v1 §5.4) → background continuation → progress events on the Domain Event Stream → session can begin working before indexing fully completes, using partial results with a visible "still indexing" indicator.

---

## 10. Coding Standards

**Architecture rules.** Every subsystem is consumed through an interface resolved via the IoC container (v3 §18.2) — no direct imports of another subsystem's concrete class, enforced by an import-linter CI check from Phase 1 onward (v3 §1.1's completion checklist). State has exactly one owner (v3 §20.1) — a `grep` for direct SQL/file access to another subsystem's table/store outside its owning module is a review-blocking finding.

**Dependency rules.** Dependencies point inward, hexagonal-style (§26 below): tools, model gateway, and storage adapters depend on core orchestration interfaces; core orchestration never imports a concrete adapter. New third-party libraries require a one-line justification in the PR description and must be wrapped behind an internal interface if they touch a subsystem boundary (e.g. swapping ChromaDB for FAISS should never require touching `context_engine`'s own code, only the adapter).

**SOLID rules.** Single Responsibility enforced structurally by the module table in §1-4 (one module, one purpose); Open/Closed via the Extension Manager's contribution points (v3 §18.8) rather than modifying core orchestration to add new capability; Liskov via every tool/store adapter satisfying its interface's full contract (tested via a shared interface-conformance test suite run against every implementation, not just the default one); Interface Segregation — `ToolSchema`, `VectorStore`, `CheckpointStore` etc. are each narrow, single-purpose interfaces, not one giant `Backend` interface; Dependency Inversion is the IoC container's entire purpose.

**Naming conventions.** Python: `snake_case` modules/functions, `PascalCase` classes, interfaces prefixed with nothing special but suffixed `...Store`/`...Service`/`...Manager`/`...Engine` to signal role (matches the module tables throughout this document). Frontend: standard React/TS conventions (`PascalCase` components, `camelCase` everything else). Event names: `<domain>.<action>` (`tool.started`, `checkpoint.saved`) — always present-tense-completed for "finished" events, imperative-less.

**Folder conventions.** Mirrors §11's folder tree exactly — a new module's location is looked up in the relevant phase's module table, never improvised; if a module doesn't fit an existing folder, that's a signal to update this document, not to place it wherever's convenient.

**Error handling rules.** No bare `except:`; every caught exception either recovers (per the module's documented Recovery strategy) or re-raises as a typed domain exception the orchestration layer knows how to route to Reflection. Every tool's failure path returns a `ToolResult(status=error, ...)`, never lets a raw exception escape the tool boundary (v1 §7.4/§8).

**Logging rules.** Structured (JSON-lines, v1 §18) exclusively — no bare `print`/unstructured string logs in backend code, enforced by a lint rule. Every log line includes `session_id` when applicable, for correlatability with the Domain Event Stream.

**Testing rules.** Every module ships with: unit tests for its own logic, an interface-conformance test if it implements a shared interface, and at least one integration test exercising it through the real (not mocked) dependency chain where that dependency is local and fast (SQLite, in-process event bus) — mocked where the dependency is expensive/external (the actual llama.cpp server, in most unit tests). Coverage target: 80% for `backend/` core modules, informational (not gating) for `frontend/`.

**Security rules.** Every new tool must declare its permission tier and idempotency class before merge (v3 §23, §8.6) — a tool without both is rejected by a schema-validation CI check, not caught in review. Every path-accepting or command-accepting function must route through the `CommandValidator`/path-canonicalization chokepoint (v3 §13.1) — a direct `os.path` or `subprocess` call outside `backend/tools/` and `backend/security/` is a review-blocking finding.

**Documentation rules.** Every module's public interface carries docstrings sufficient to regenerate its §1-4 table entry (Purpose/Responsibilities/Inputs/Outputs) — the module tables in this document are meant to be kept in sync with docstrings, not maintained as a separate source of truth that drifts.

**Performance rules.** Any code path invoked per-token or per-tool-call (hot path) requires a benchmark before merge if it's plausibly O(n) or worse in repository size or conversation length — this hardware's 4GB VRAM / CPU-bound-inference profile means backend overhead competes directly with inference time for the user's patience budget.

**Review checklist / Definition of Done** (a PR is not done until):
- [ ] Passes the import-linter architecture check (no concrete cross-module imports)
- [ ] New/changed tools declare permission tier + idempotency class
- [ ] New/changed paths/commands route through the security chokepoint
- [ ] Unit + interface-conformance + at least one integration test included
- [ ] Structured logging only, no bare prints
- [ ] Module table entry (§1-4) updated if a module's purpose/interface changed
- [ ] If touching a hot path: benchmark numbers included in the PR description

---

## 11. Complete Folder Structure

```
agent/
├── backend/
│   ├── config/                    config service, YAML schemas
│   ├── container.py                IoC composition root
│   ├── model_gateway/               llama.cpp client
│   ├── model_session/                swap manager, capability registry, router
│   ├── knowledge/                    embedding_service, doc_cache
│   ├── context_engine/               tree-sitter, repo_graph (unified graph), retrieval
│   ├── context_persistence/          versioned summaries, compression ladder
│   ├── memory/                       episodic, repository, bug, preference memory
│   ├── orchestration/
│   │   ├── graph.py, state.py, debug.py
│   │   └── nodes/                    planner, repo_analyzer, context_retriever,
│   │                                 task_decomposer, tool_selector, validator,
│   │                                 test_runner, reflector, repair_planner,
│   │                                 rollback_handler, summarizer
│   ├── workflow_engine/               compiler, executor, templates/
│   ├── tools/                         registry + one file per tool category
│   ├── mcp/                           client, server_registry
│   ├── health/                        monitor, watchdog, recovery_ladder
│   ├── scheduler/                     resource_pools, priority_queue
│   ├── reliability/                   transaction_log, idempotency, circuit_breaker
│   ├── events/                        bus (5-lane), catalog
│   ├── evaluation/                    metrics, benchmark_runner, replay
│   ├── prompts/                       registry, templates/
│   ├── security/                      command_validator, secret_masking,
│   │                                  path_validation, dependency_scan
│   ├── plugins/                       loader, manifest, registry, extension_points
│   ├── state/                         ownership registry, validation, migration
│   ├── db/                            models, migrations/, session
│   ├── observability/                 logging, otel, metrics
│   ├── api/                           rest, websocket, sse
│   ├── lifecycle.py
│   └── main.py
├── frontend/
│   ├── app/                           Next.js app router
│   ├── components/                    FileExplorer, DiffViewer, Terminal,
│   │                                  TaskTimeline, WorkflowGraph, PromptInspector,
│   │                                  ContextInspector, MemoryViewer,
│   │                                  CheckpointViewer, PerfDashboard, Logs,
│   │                                  Debugger, Settings, Plugins
│   ├── store/                          Zustand
│   └── lib/                            React Query hooks, API client
├── cli/                                 Phase 1 terminal client
├── plugins/                             user-installed plugins
├── data/
│   ├── sqlite/, chroma/, snapshots/, logs/, diagnostics/
├── config/
│   ├── model.yaml
│   ├── model_routing.yaml
│   ├── hardware_profile.yaml
│   ├── security_policy.yaml
│   ├── project_defaults.yaml
│   └── mcp_servers.yaml
├── tests/
│   ├── unit/, integration/, e2e/, stress/, security/
├── docker-compose.yml
└── README.md
```

---

## 12. Development Order & Milestones

The per-module build order is already given in §1.1/§2.1/§3.1/§4.1's tables (columns "Build order" / row order = dependency order — nothing in a later row depends on something in an earlier row that doesn't exist yet). Milestones group them into checkpoints worth pausing at:

```
M0  Foundation          config → container → model_gateway            (modules 1-3)
M1  Model layer alive    model_session → model_router → embeddings     (modules 4-6)
M2  Memory substrate     checkpoint_store                              (module 7)
M3  Understanding        context_engine (minimal)                     (module 8)
M4  Hands                tool_registry + core tools                   (module 9)
M5  The Loop             workflow_engine + planner + reflector         (modules 10-12)
      ── MILESTONE: first end-to-end task completes on this hardware ──
M6  Verification         testing_pipeline, git_integration             (modules 13-14)
M7  Interface            backend_api, cli                              (modules 15-16)
      ══ PHASE 1 COMPLETE ══
M8  Survivability        health_manager, resource_scheduler            (modules 17-18)
M9  Durability           reliability_layer, event_bus (full)           (modules 19-20)
M10 Deep understanding   context_persistence, repository_intelligence,
                         memory_system (full)                          (modules 21-23)
M11 Trust                logging_observability, security_hardening     (modules 24-25)
M12 Formalization        state_management, lifecycle_manager,
                         evaluation_framework (basic)                  (modules 26-28)
      ══ PHASE 2 COMPLETE — 6-hour unattended stress test passes ══
M13 Visibility           frontend_shell, file_explorer, diff_viewer,
                         terminal_panel, execution_timeline, live_logs (modules 29-33, 40)
M14 Depth                workflow_visualizer, prompt/context/memory/
                         checkpoint inspectors, performance dashboard  (modules 34-39)
M15 Control              interactive_debugger, replay_system,
                         settings_ui, plugin_ui                        (modules 41-44)
      ══ PHASE 3 COMPLETE ══
M16 Perception            vision_pipeline, voice_interface              (modules 45-46)
M17 Ecosystem              plugin_marketplace, background_tasks,
                           project_templates, workspace_manager        (modules 47, 53-55)
M18 Horizon                remote_workers, cloud_fallback,
                           distributed_execution, multi_agent          (modules 48-51)
M19 Learning                finetuning_support, repository_learning,
                            continuous_learning, auto_documentation,
                            auto_refactoring                            (modules 52, 56-59)
      ══ PHASE 4 COMPLETE ══
```

Nothing in this ordering requires refactoring already-shipped code except the single called-out retrofit at M12 (Phase-1 modules implementing the Lifecycle Manager interface, §2.2) — every other milestone is additive.

---

## 13. Testing Strategy

| Test class | Scope | Tooling | Cadence |
|---|---|---|---|
| Unit | Single module, dependencies mocked via the IoC container's test bindings | `pytest`, `pytest-asyncio`, `unittest.mock` | Every commit (CI) |
| Integration | Real local dependencies (SQLite, in-process event bus, real Tree-sitter), mocked model server | `pytest` with local fixture services | Every PR |
| End-to-end | Full stack including a real llama.cpp server against a small model, against fixture repos | `pytest` + a lightweight orchestration harness driving the CLI/API | Nightly + pre-release |
| Stress | Multi-hour unattended runs, synthetic repeated tasks, deliberate resource pressure | Custom harness (`tests/stress/`) driving `resource_metrics` assertions | Weekly + before each Phase completion |
| Memory | Leak detection (thread count, handle count, RSS growth over N iterations) | `tracemalloc`, `psutil`-based assertions | Weekly |
| Performance | Latency/throughput benchmarks on hot paths (§10's performance rule) | `pytest-benchmark` | Every PR touching a hot path |
| Long-running stability | Session success-rate trend over many hours, feeds Evaluation Framework | Same stress harness, longer duration (24h+) | Before each Phase completion |
| Recovery | Kill-and-resume (v1 §Phase1 acceptance), crash-mid-checkpoint, corrupted-checkpoint handling | Fault-injection harness (SIGKILL at randomized points) | Every PR touching `reliability/`, `state/`, `lifecycle.py` |
| Security | Path traversal, command injection, secret leakage, dependency vuln gating — the full v3 §25 Edge Case Registry's security rows | Dedicated `tests/security/` suite, adversarial fixtures | Every PR touching `tools/`, `security/` |
| Acceptance | The completion checklists in §1.3/§2.3/§3.3/§4.3 | Manual + automated where feasible | End of each phase |

---

## 14. Missing Components Audit

Cross-checking this roadmap against the full v1-v3 architecture, every subsystem has a home:

| Architecture subsystem | Roadmap location |
|---|---|
| Overall architecture, agent workflow, state machine (v1 §1-3) | Phase 1, `workflow_engine`/`planning_engine` |
| LangGraph design (v1 §4) | Phase 1 `checkpoint_store` + Phase 1 `workflow_engine`, formalized Phase 2 `state_management` |
| Context Engine (v1 §5) | Phase 1 minimal `context_engine`, Phase 2 `repository_intelligence`/`context_persistence` |
| Memory (v1 §6) | Phase 2 `memory_system` |
| Tool System, Tool Calling (v1 §7-8) | Phase 1 `tool_registry`, Phase 2 `security_hardening`, Phase 3 §23 Tool Ecosystem folded into `tools/` health/metrics additions |
| Repository Intelligence (v1 §9, v3 §22) | Phase 2 `repository_intelligence` |
| File Editing Strategy (v1 §10) | Phase 1 `tool_registry` (fs tools), snapshot table 0005 |
| Terminal, Browser, Docker (v1 §11-12) | Phase 1 terminal/git tools, Phase 4 `vision_pipeline` for browser-verification's vision component, Docker tool in Phase 1 tool table §8 |
| Planning Engine, Reflection System (v1 §13-14, v3 §24) | Phase 1 basic versions, Phase 3 deepened via evaluation-driven quality scoring |
| Testing Pipeline (v1 §15) | Phase 1 `testing_pipeline` |
| Context Window Optimization (v1 §16, v2 §6.2) | Phase 2 `context_persistence`'s compression ladder |
| Multi-language Support (v1 §17) | Phase 1 ships Python/JS/TS; additional languages added incrementally as `context_engine` grammar/LSP/formatter registrations — no dedicated phase needed, it's a config/data addition, not a subsystem |
| Logging, Monitoring (v1 §18-19) | Phase 2 `logging_observability` |
| Security (v1 §20, v3 §13) | Phase 1 baseline (mandatory from day one, §1.2's `tool_registry` note), Phase 2 `security_hardening` full |
| API Design, Frontend (v1 §21-22) | Phase 1 `backend_api`, Phase 3 full frontend modules |
| Plugin System (v1 §23, v3 §18.8) | Phase 4 `plugin_marketplace` for marketplace; core plugin loader/isolation is actually needed by Phase 3's `plugin_ui` — **flagged below as a sequencing fix** |
| Database Schema (v1 §24) | §6 of this document |
| Failure Recovery (v1 §25) | Phase 2 `reliability_layer` + `lifecycle_manager` |
| Performance Optimizations, Scalability (v1 §26-27) | Phase 1 model routing (§0.3), Phase 4 `remote_workers`/`distributed_execution` |
| Agent Lifecycle, System Health Manager (v2 §3-4) | Phase 2 `health_manager`, `lifecycle_manager` |
| Model Session Manager, Context Persistence (v2 §5-6) | Phase 1 `model_session`, Phase 2 `context_persistence` |
| Resource Scheduler, Reliability Layer (v2 §7-8) | Phase 2 `resource_scheduler`, `reliability_layer` |
| Event Bus (v2 §9, v3 §19) | Phase 1 minimal, Phase 2 full 5-lane |
| Plugin Framework (v2 §10) | See flagged sequencing fix below |
| Knowledge Layer (v2 §11) | Phase 1 `embedding_service` (the model-independent embedding half); doc-cache half is Phase 2, folded into `repository_intelligence`'s summary generation — **flagged below** |
| Production Features (v2 §12) | Distributed: config profiles in Phase 1 `config`, secrets in Phase 2 `security_hardening`, backups/audit log in Phase 2, feature flags in Phase 1 `config` (v3 §18.6) |
| Evaluation Framework, Prompt Management (v2 §14-15) | Phase 2 basic evaluation, Phase 3 prompt management — **flagged below, needs explicit module** |
| Distributed Future Architecture (v2 §16) | Phase 4 `remote_workers`/`distributed_execution`; seams (§16.1) built into Phase 1-2 interfaces themselves, no separate module needed |
| Developer Experience (v2 §17) | Phase 3, full |
| Service Architecture / DI / IoC / Registries (v3 §18) | Phase 1 `container`, `config`; Capability Registry folded into `model_session`/`lifecycle_manager` — **flagged below, deserves its own explicit module** |
| Global Event/Queue Architecture (v3 §19) | Phase 2 `event_bus` (full) |
| State Management (v3 §20) | Phase 2 `state_management` |
| Workflow Engine (v3 §21) | Phase 1 minimal, generalized incrementally — templates/nesting/visualization added in Phase 2-3 rather than a single big-bang module |
| Repository Intelligence, Tool Ecosystem, Reflection Engine deepened (v3 §22-24) | Phase 2 `repository_intelligence`, tool health/metrics folded into `tools/`, Phase 3 deepened reflection |
| Edge Case Registry (v3 §25) | Testing strategy §13 of this document (`tests/security/`, fault-injection harness) is the implementation home; no runtime "module" — it's a cross-cutting test/design obligation, correctly not a standalone module |
| Architecture Principles (v3 §26) | §10 of this document (Coding Standards) |

### 14.1 Sequencing Fixes Required

Three items above were placed too late relative to what actually depends on them — corrected here rather than left as a silent gap:

1. **Plugin loader/isolation core** (not the marketplace, just load/sandbox/permission-check) must move from "Phase 4" to **Phase 2**, module 25.5 (`plugin_manager_core`), because Phase 3's `plugin_ui` (module 44) has nothing to manage without it, and because the Extension Manager's contribution points (v3 §18.8) are how several Phase 3 inspector panels would ideally be built if a team wanted them plugin-replaceable rather than hardcoded. Marketplace/remote install (signature verification, remote index) remains Phase 4.
2. **Capability Registry** (v3 §18.4) deserves its own small module in Phase 1 (module 3.5, `capability_registry`, right after `config`) rather than being folded into `model_session` — every Phase 1 module from `tool_registry` onward needs to query it, so it must exist before module 9, not be retrofitted from `model_session` later.
3. **Prompt Management System** (v2 §15) needs its own module in **Phase 1** (module 6.5, `prompt_registry`), not deferred to Phase 3 as originally implied — every one of Phase 1's orchestration nodes (planner, reflector, tool_selector) renders a prompt from day one, and rendering it through an unversioned inline string now means a real migration later; the *testing/A/B* features of Prompt Management remain Phase 3, but versioned template storage is foundational.

Updated Phase 1 build order incorporating these fixes: `config → capability_registry → container → model_gateway → model_session → model_router → prompt_registry → embedding_service → checkpoint_store → context_engine → tool_registry → workflow_engine → planner → reflector → testing_pipeline → git_integration → backend_api → cli`, with `plugin_manager_core` inserted into Phase 2 immediately after `security_hardening`.

---

## Closing Note

This roadmap is deliberately hardware-honest: every phase and every module was checked against a 4GB VRAM / 24GB RAM laptop, not an idealized deployment target. The one discipline that makes the whole plan work is the one stated in §10 and enforced from module 3 onward — **everything talks through an interface, resolved by the container** — because that's what lets Phase 4's distributed/cloud/multi-agent ambitions bolt on later without touching Phase 1's foundation. Build in the order given, don't skip the completion checklists, and the 6-hour unattended stress test at the end of Phase 2 is the single most important gate in this entire document: everything after it is capability, everything before it is trust.
