# Local-First Autonomous Agentic Coding Assistant
## Production-Grade System Architecture (v3 — Enterprise Hardening Pass)

**Supersedes:** v2. This pass takes the system from "resilient" to "enterprise-modular" — every subsystem is redesigned so it can be replaced, tested, and reasoned about in isolation, and every remaining production edge case is given explicit, designed handling.

---

## Changelog: What v2 Still Lacked

v2 fixed *survivability* (the system stays alive and doesn't lose work). It did not yet fix **modularity** (subsystems still referenced each other's concrete implementations in places) or **completeness of edge-case handling** (individual failure modes were designed per-subsystem, but there was no single source of truth enumerating every edge case and its designed response). This pass adds:

1. A **Service Architecture layer** (§18) — dependency injection, an IoC container, and formal registries — so every subsystem in v1/v2 is consumed through an interface, never a concrete import.
2. A fully specified **Global Event/Queue architecture** (§19) that goes beyond v2's Event Bus to add priority, workflow, and notification queues as distinct, purpose-built lanes.
3. **State Management** (§20) formalized as its own subsystem — every kind of state named, owned, versioned, and given an explicit synchronization and recovery story.
4. A **Workflow Engine** (§21) that generalizes v2's supervisor/worker LangGraph split into a reusable compiler + executor, supporting nested workflows, human approval nodes, and rollback as first-class primitives.
5. **Repository Intelligence, Tool Ecosystem, and Reflection Engine** each promoted to full subsystem status with health/metrics/versioning dimensions that v1/v2 only partially covered (§22-24).
6. A single **Edge Case Registry** (§25) — every scenario the prompt enumerated, with detection, recovery, rollback, retry, logging, and user-notification specified per row, so nothing is left as an exercise for the implementer.
7. **Architecture Principles** (§26) stated explicitly, with a worked example of how they constrain design, so future contributors have a rule to check new code against, not just a pile of precedent.

---

## Part IV — Service Architecture

### 18. Dependency Injection, IoC Container, and Registries

#### 18.1 Why This Matters Here Specifically

By v2, the system had the *right* subsystems but they were still described as talking to each other directly (Orchestration "calls" the Context Engine, the Model Session Manager "calls" the Resource Scheduler). In a system meant to run unattended for hours and be extended by third-party plugins, that's a maintainability trap: swapping ChromaDB for pgvector, or replacing the Reflection Engine's strategy, means hunting down every concrete reference. §18 closes that gap: **from this point forward, no subsystem may import another subsystem's implementation — only its interface, resolved through the IoC container.**

#### 18.2 IoC Container

A single composition root (`backend/container.py`) wires every interface to its concrete implementation at process startup, driven by the loaded configuration profile (v2 §12.2). Every subsystem constructor takes its dependencies as interface-typed parameters — nothing reaches into a global singleton or does its own `import` of another subsystem's class. This is standard constructor injection, not a magic framework: a lightweight container (e.g. a `dependency-injector`-style registry, or even a hand-rolled factory map) is sufficient — the discipline matters more than the tooling.

```
Interface                    Default local implementation        Swappable for
─────────────────────────────────────────────────────────────────────────────
VectorStore                  ChromaDBVectorStore                  FAISSVectorStore, PgVectorStore
CheckpointStore               SQLiteCheckpointStore                PostgresCheckpointStore
EventBus                      InProcessEventBus                    RedisEventBus
ModelClient                   LlamaCppClient                       RemoteModelClient, CloudFallbackClient
ToolSandbox                   SubprocessSandbox                    DockerSandbox, RemoteWorkerSandbox
EmbeddingService              LocalEmbeddingService                RemoteEmbeddingService
SecretStore                   OSKeychainSecretStore                EncryptedFileSecretStore
```

Every row is a single binding declared in the composition root; changing environments (dev laptop vs. team server) is a config profile choice, never a code change in the consuming subsystem.

#### 18.3 Service Registry

At runtime, every active subsystem instance registers itself with a lightweight **Service Registry** (name → live instance + health-check callback). This is distinct from the IoC container (which wires *types* at startup) — the registry is what the System Health Manager (v2 §4) and the Developer Experience inspectors (v2 §17) query to enumerate "what's actually running right now" without needing compile-time knowledge of every subsystem.

#### 18.4 Capability Registry

Formalizes v2 §3.2's capability probe into a first-class, queryable registry rather than a one-time startup side effect: `CapabilityRegistry.has("docker")`, `.has("browser.firefox")`, `.get_lsp_for("rust")`. Any subsystem that wants to know "can I do X on this machine" queries this registry rather than probing directly — meaning capability detection logic lives in exactly one place and every consumer (Tool Router, Resource Scheduler, Plugin loader) shares the same, single source of truth about what's actually available.

#### 18.5 Configuration Service

Replaces "read `model.yaml` directly" scattered across subsystems with a single `ConfigService` interface: typed accessors (`config.get_resource_thresholds()`, `config.get_active_profile()`), change notification (subsystems can subscribe to a config-reload event rather than re-reading a file), and layered override resolution (defaults → profile → project-level → environment variable → runtime API override), evaluated in that precedence order every time a value is requested, so "why is this setting what it is" always has one deterministic answer.

#### 18.6 Feature Flags (formalized)

Extends v2 §12.1 into a proper `FeatureFlagService` behind the same Configuration Service — flags are just config values with a boolean/enum type and an audience scope (global, per-project, per-session percentage rollout for A/B use per v2 §15.4's prompt A/B testing). Any subsystem checks a flag through the service, never a raw config read, so flag evaluation logic (percentage rollout, override precedence) is implemented once.

#### 18.7 Version Manager

Tracks the version of every replaceable unit — core agent, each subsystem implementation bound in §18.2, each plugin, the active model, the active embedding model, the schema version of every database — in one place (`GET /api/system/versions`, per v2 §12.8, now formally backed by this component rather than an ad hoc endpoint). Compatibility checks (e.g. "this plugin requires core API >= 2.3") are resolved here, once, rather than duplicated per plugin loader / per subsystem.

#### 18.8 Plugin Manager & Extension Manager

Splits v1/v2's single "Plugin Framework" (v2 §10) into two clearer responsibilities: the **Plugin Manager** owns lifecycle (discover/load/activate/deactivate/unload, v2 §10.2) and permission enforcement; the **Extension Manager** owns the *contribution points* — the specific extension surfaces a plugin can hook into (new tools, new frontend panels, new Context Engine ranking signals, new Reflection Engine failure classifiers). This split matters because it lets the system add a new contribution point (say, a custom repair-strategy extension point) without touching plugin lifecycle code at all — the two concerns evolve independently.

#### 18.9 Lifecycle Manager

Generalizes v2 §3's Agent Lifecycle from "the one big process lifecycle" into a reusable primitive every registered service implements: `on_startup()`, `on_health_check()`, `on_shutdown()`, `on_crash_recovery()`. The System Health Manager and the process-level Startup Sequence (v2 §3.1) simply iterate the Service Registry (§18.3) calling these hooks in dependency order (resolved from the IoC container's dependency graph, §18.2) — meaning a newly added subsystem gets correct startup/shutdown ordering for free, by implementing the interface, rather than requiring a manual edit to a central startup script.

---

## Part V — Event-Driven Architecture, Formalized

### 19. Global Event/Queue Architecture

v2 §9 gave the system a single Event Bus. In practice, "everything is one pub/sub topic" doesn't hold up under real load — a flood of `file.changed` events shouldn't delay a `checkpoint.saved` event, and a plugin's custom event shouldn't compete with the orchestration's own control-flow signals. §19 splits the single bus into purpose-built lanes, all still exposed through the one `EventBus` interface (§18.2) so nothing outside this section needs to know the lanes exist.

### 19.1 Lane Design

```
┌─────────────────────────────────────────────────────────────────┐
│                     GLOBAL EVENT BUS (facade)                     │
│         single publish()/subscribe() API, routes by event class    │
└──┬───────────┬───────────────┬────────────────┬──────────────┬─-┘
   │            │               │                │              │
┌──▼──────┐ ┌───▼────────┐ ┌────▼─────────┐ ┌────▼──────────┐ ┌─▼──────────┐
│ Domain  │ │  Priority  │ │  Workflow    │ │ Notification  │ │  Plugin    │
│ Event   │ │  Queue     │ │  Queue       │ │ Bus           │ │  Event     │
│ Stream  │ │            │ │              │ │               │ │  Lane      │
└─────────┘ └────────────┘ └──────────────┘ └───────────────┘ └────────────┘
```

- **Domain Event Stream** — the append-only, replayable stream of everything that happened (repository events, tool events, checkpoint events, model events — the full catalog from v2 §9.2). This is the audit/observability backbone and feeds the Domain Events Log directly into the Transaction Log (v2 §8.3) where an event corresponds to a state-mutating operation.
- **Priority Queue** — orchestration control-flow signals that must be processed in priority order, not FIFO (v2 §7.2's priority model is implemented *as* this queue — a `resource.critical` event must be dequeued and acted on before a `background_index.progress` event, regardless of arrival order).
- **Workflow Queue** — specifically carries Workflow Engine (§21) step-completion and step-ready signals; kept separate from general domain events because workflow execution needs strict per-workflow ordering guarantees that a general pub/sub topic doesn't provide (two events for the same workflow must be processed in emission order even if the bus itself doesn't guarantee global ordering).
- **Notification Bus** — user-facing notices (approval requests, session-needs-review, resource warnings surfaced to the UI) — deliberately decoupled from the Domain Event Stream so a UI reconnect doesn't replay a flood of low-level tool events, only the notifications relevant to a human.
- **Plugin Event Lane** — scoped, permission-filtered (v2 §10.3) — a plugin only sees events for projects/sessions it's been granted access to, enforced at this lane's subscription boundary rather than trusted to plugin code.

### 19.2 Event Classes (extends v2 §9.2)

| Class | Lane | Examples |
|---|---|---|
| System events | Priority Queue + Domain Stream | `system.ready`, `resource.critical`, `health.heartbeat_missed` |
| Repository events | Domain Stream | `repo.indexed`, `repo.file_changed`, `repo.dependency_detected` |
| Tool events | Domain Stream (+ Workflow Queue if tied to an active workflow step) | `tool.started`, `tool.finished`, `tool.timeout` |
| Checkpoint events | Domain Stream | `checkpoint.saved`, `checkpoint.restored`, `checkpoint.corrupted` |
| Plugin events | Plugin Event Lane | custom, per-plugin manifest declaration |
| Model events | Priority Queue + Domain Stream | `model.swap_started`, `model.swap_completed` |
| Workflow events | Workflow Queue | `workflow.step_ready`, `workflow.step_completed`, `workflow.branch_taken`, `workflow.approval_required` |

### 19.3 Delivery & Ordering Guarantees

Per-lane guarantees are explicit rather than assumed uniform: the Workflow Queue guarantees per-workflow FIFO ordering (required for correctness — a step-completed event must never be processed before its step-ready event); the Domain Event Stream guarantees at-least-once delivery with idempotent consumers (consistent with the Reliability Layer's idempotency model, v2 §8.6) but not strict ordering across unrelated entities; the Priority Queue is explicitly *not* FIFO by design (§19.1).

---

## Part VI — State Management

### 20. Formal State Ownership

v2 scattered state across checkpoints, memory, and context persistence without ever naming "state" as its own concern. §20 fixes that: every kind of state in the system is named, given exactly one owning subsystem, and given an explicit synchronization/recovery/versioning/validation story. **Rule: no two subsystems may claim ownership of the same state.** Consumers read through the owner's interface; they never cache a mutable copy that can drift.

### 20.1 State Inventory

| State type | Owner | Persisted in | Versioned? |
|---|---|---|---|
| Global agent state (which sessions exist, system health snapshot) | System Health Manager + Service Registry (§18.3) | In-memory, rebuilt from Service Registry on startup | No — always current-only |
| Workflow state (current node, branch taken, step statuses) | Workflow Engine (§21) | LangGraph checkpoint (resume tier, v2 §8.3) | Yes — every checkpoint is a version |
| Task state (subtask graph, statuses, confidence scores) | Planning Engine (v1 §13) | Same checkpoint store, `tasks` table (v1 §24) for queryable history | Yes |
| Repository state (indexed symbol/dependency/call graphs) | Repository Intelligence (§22) | Graph store (in-memory + periodic snapshot to disk) | Yes — incremental, tied to file hash |
| Tool state (in-flight tool calls, sandbox handles) | Tool Ecosystem (§23) | In-memory only; recorded to Transaction Log on start/finish, never itself persisted mid-flight | No — ephemeral by design |
| Model state (which model is loaded, KV cache) | Model Session Manager (v2 §5) | In-memory (llama.cpp server); explicitly NOT persisted (v2 §5.3) | No — disposable |
| Memory state (long-term memory entries) | Memory System (v1 §6) | Vector store + relational metadata | Yes (v2 §6.3 supersession model) |
| Checkpoint state (the checkpoints themselves) | Reliability Layer (v2 §8.3) | SQLite/Postgres | Yes — inherently, each checkpoint is a version |
| UI state (which panel is open, filter selections) | Frontend only | Client-side (Zustand, v1 §22); never synced to backend | No — not the backend's concern at all |
| Execution state (the live LangGraph run) | Workflow Engine (§21) | Same as workflow state — they are the same underlying state viewed at different granularity | Yes |

### 20.2 Synchronization Strategy

Because each state type has exactly one owner, "synchronization" reduces to two patterns, applied consistently:

1. **Read-through, no caching of mutable state** — any subsystem needing another's state calls the owner's interface (via the IoC container, §18.2) fresh each time, or subscribes to the owner's change events (§19) to invalidate a read-only cache it maintains locally. A consumer never mutates a copy and expects it to propagate back.
2. **Write-through with a single writer** — every state type has exactly one subsystem authorized to mutate it (per §20.1's Owner column); this is enforced by the IoC container only exposing a mutation interface to the owning subsystem's dependents, and a read-only interface to everyone else — a structural guarantee, not a convention someone has to remember.

### 20.3 State Recovery

Builds directly on v2 §3.5/§8: recovery is always "reload from the owning subsystem's persisted store, then re-validate against ground truth" (e.g. Repository State recovery re-validates the graph against current file hashes, not just trusting the last snapshot — matching v2's crash-recovery philosophy of never blindly trusting a checkpoint).

### 20.4 State Versioning & Migration

Every persisted state type that can outlive a single session (repository state, memory state, checkpoint state) carries a schema version. A **State Migration** step runs at startup (alongside the database migrations of v2 §12.9, but distinct — this covers in-memory graph structures and vector-store schema, not just relational tables): if a persisted state's version is older than the current subsystem's expected schema, a registered migration function transforms it before first use; if no migration path exists for a version gap that large, the system refuses to silently misinterpret old state and instead flags it for the user (rebuild-from-scratch is always a safe fallback for derived state like the repository graph; it is never silently applied to irreplaceable state like long-term memory).

### 20.5 State Validation

Each owning subsystem exposes a `validate()` check (part of the Lifecycle Manager contract, §18.9) run at startup and after any recovery: repository state validation re-hashes a sample of indexed files and checks for drift; checkpoint state validation verifies the checkpoint's internal consistency (referenced snapshot hashes actually exist in the snapshot store) before it's offered for resume; memory state validation checks the vector store and relational metadata are in sync (no orphaned vector ids, no metadata rows pointing at missing vectors). A failed validation never crashes the subsystem — it downgrades that state to "needs rebuild/repair" and surfaces it via the Notification Bus (§19.1), consistent with the graceful-degradation philosophy established in v2 §4.

---

## Part VII — Workflow Engine

### 21. From "One Big Graph" to a Reusable Workflow Engine

v2 §9 (revised architecture) introduced a supervisor graph + per-subtask worker subgraphs, but treated that split as a one-off design rather than a reusable engine. §21 generalizes it: the **Workflow Engine** is a standalone service (bound via the IoC container, §18.2) that compiles a declarative workflow definition into an executable graph and runs it — the main agent loop (v1 §2, v2 Part II) is simply the *default* workflow this engine executes, not special-cased logic.

### 21.1 Workflow Compiler

Takes a declarative workflow definition (nodes, edges, conditional branch predicates, parallel-execution groups, sub-workflow references) and compiles it into a LangGraph-executable form, performing static checks before any execution starts: every referenced node exists, every conditional edge's predicate is well-typed, no unreachable nodes, no cycles except through explicitly-marked retry/repair loops (an accidental infinite-loop-shaped graph is a compile-time error, not a runtime surprise).

### 21.2 Workflow Executor

Runs the compiled graph, one step at a time, coordinating with:
- The **Workflow Queue** (§19.1) for step-ready/step-completed signaling
- The **Reliability Layer** (v2 §8) for per-step checkpointing
- The **Resource Scheduler** (v2 §7) for admission control on resource-heavy steps

### 21.3 Conditional Branches & Dynamic Planning

A branch node evaluates a predicate function (pure, side-effect-free, given current workflow state) to select the next edge — e.g. "does this subtask touch a UI-facing file → include the browser-verification branch, else skip it." **Dynamic replanning** (v1 §13) is modeled as a special node type that can rewrite the *remaining* compiled graph mid-execution (inserting/removing nodes for not-yet-executed portions only — already-executed nodes and their checkpoints are immutable history) when the Planning Engine determines the original plan no longer fits reality.

### 21.4 Parallel Execution & Dependency Resolution

A parallel-group node fans out to N child branches (e.g. independent test suites, or independent read-only context-gathering steps) executed concurrently via the same bounded-concurrency mechanism as v1 §8's parallel tool calls, and fans back in at a join node that waits for all (or, for optional branches, a configurable subset) to complete before proceeding. Dependency resolution between subtasks (v1 §13's task-graph dependency edges) is resolved once at compile time into the workflow's edge structure — the executor never has to re-derive "what can run now" at runtime; it's baked into the graph.

### 21.5 Workflow Templates

Common shapes (the default "plan → edit → verify → reflect → repair" loop from v1 §2, a "read-only investigation" template for pure Q&A tasks that never needs the write-verify-reflect machinery, a "large refactor" template that adds extra checkpointing and confirmation gates) are stored as named, versioned templates (via the Prompt/Config-adjacent template store, reusing the versioning pattern from v2 §15) — a new task type is often "pick the right template and parameterize it," not "hand-design a new graph."

### 21.6 Human Approval Nodes

A first-class node type that pauses the workflow (session → `WAITING`, reason `human_approval`, v2 §7.6's backpressure pattern) and publishes a Notification Bus event (§19.1) with the pending action's full context (diff, command, risk tier). Execution resumes only on an explicit approve/deny API call; a deny routes to a configurable fallback edge (abort the subtask, or route to the Planning Engine for an alternative approach) rather than simply halting.

### 21.7 Nested Workflows

A workflow node can itself be a reference to another compiled workflow (e.g. the top-level "fix this bug" workflow's "verify" step is itself a small workflow: formatter → linter → tests → browser-check, per v1 §15's pipeline). Nesting is checkpointed at both levels — the parent workflow's checkpoint records "currently executing sub-workflow X at its step Y," so crash recovery (v2 §3.5) resumes at the correct nested position, not just the outer step.

### 21.8 Workflow Rollback & Recovery

Rollback at the workflow level composes the primitives already designed in v1 §10 (file snapshot rollback) and v2 §8.4 (extended snapshot system): a workflow-level rollback reverts every mutating step executed since a named checkpoint, in reverse order, using each step's individually-recorded rollback action — not a single "restore everything to a snapshot" operation, which would be too coarse for a long workflow where only the last few steps need undoing. Recovery (resuming after an interruption) reuses v2 §3.5/§8.3 directly — a workflow is just another form of checkpointed session state.

### 21.9 Workflow Visualization

Feeds the Developer Experience workflow visualizer (§25.2) directly from the compiled graph structure plus live execution state — because the graph is a first-class compiled artifact (§21.1), rendering it is a straightforward graph-layout problem, not a reverse-engineering exercise from scattered log lines.

---

## Part VIII — Deep Subsystems: Repository Intelligence, Tool Ecosystem, Reflection Engine

### 22. Repository Intelligence (promoted to full subsystem)

v1 §9/§5 described repository understanding as a set of graphs built by the Context Engine. §22 promotes it to an independently owned subsystem (state-owned per §20.1) with its own health and metrics surface, because repository understanding is consumed by far more than context retrieval — the Planning Engine, the Reflection Engine's diagnosis step, and the Evaluation Framework's repository-understanding-accuracy metric (v2 §14.1) all depend on it directly.

#### 22.1 The Unified Repository Graph

Rather than treating symbol graph, dependency graph, call graph, import graph, and module graph as five separate structures (as v1 implied), §22 unifies them into **one property graph** with typed edges (`imports`, `calls`, `defines`, `references`, `inherits`, `depends_on_package`) — a single graph store (e.g. an in-memory graph structure backed by periodic serialization, or a lightweight embedded graph DB) queried with typed traversals. This avoids the maintainability problem of five structures drifting out of sync with each other after an incremental update.

#### 22.2 Detection Layers (unchanged in substance from v1 §9, now explicit as graph-annotation passes)

Architecture detection, framework detection, and language detection each run as an annotation pass over the unified graph — tagging modules/files with detected roles (`entry_point`, `test`, `config`, `migration`) — rather than separate ad hoc heuristic modules, so a new detection heuristic is "add an annotation pass," not "build a new subsystem."

#### 22.3 Ownership Detection

`git blame`-derived, aggregated per module (v1 §9) — now stored as a graph annotation too, queryable in the same traversal API as everything else ("show me files in this dependency chain not owned by the current task's apparent author area").

#### 22.4 Incremental Indexing & Smart Cache

Formalizes v1 §5.4/§22's incremental indexing as a **Smart Cache** with three tiers: (1) parsed-AST cache keyed by file content hash — never re-parses unchanged content; (2) graph-annotation cache — re-run only for files whose AST changed; (3) embedding cache — re-embedded only for chunks whose semantic content (not just formatting) changed, detected via a normalized-AST hash rather than raw content hash, so a pure reformat (whitespace-only diff) doesn't trigger unnecessary re-embedding.

#### 22.5 Code Metrics, Complexity Analysis, Technical Debt Analysis

New annotation passes over the unified graph: cyclomatic complexity per function, duplicate-code detection (via AST shape hashing, not text diffing — catches renamed-variable duplicates that plain text search would miss), dead-code detection (functions with zero incoming `calls`/`references` edges, cross-checked against dynamic-language caveats — flagged as "likely dead" not "definitely dead" for languages where static call resolution is unreliable), and a technical-debt score combining complexity, duplication, and TODO-density per module, surfaced in the repository summary (v1 §5.3) and available to the Planning Engine when deciding whether a "quick fix" subtask should instead be flagged for a larger refactor recommendation.

---

### 23. Tool Ecosystem (promoted to full subsystem)

v1 §7 specified the tool catalog and permission tiers well; it under-specified the *ecosystem* concerns — discovery, health, metrics, and versioning — that matter once tools come from multiple sources (native, MCP, plugin-contributed).

#### 23.1 Tool Registry & Discovery

Unified registry (per v1 §7.3) now explicitly sourced from three origins — native tools (compiled in), MCP server tools (discovered via v1 §21.6's MCP client at startup and on-demand when a new MCP server connects), and plugin-contributed tools (via the Extension Manager, §18.8) — normalized into the same `ToolSchema` regardless of origin, so the orchestrator never needs to know or care where a tool came from.

#### 23.2 Tool Health

Each registered tool carries a live health status (`healthy`/`degraded`/`unavailable`), derived from the Capability Registry (§18.4) at startup and updated continuously from recent call outcomes (a tool with a rising failure rate over its last N calls is marked `degraded` and surfaced in the Developer Experience Tool Inspector, v2 §17, before it causes a session to burn through its retry budget on a tool that's simply broken).

#### 23.3 Tool Metrics

Per-tool: call count, success rate, p50/p95/p99 latency, timeout rate — feeding both the Evaluation Framework's tool-accuracy metric (v2 §14.1) and the Prometheus/Grafana ops boards (v1 §19), keyed consistently so the same tool's health is visible from both the developer-facing and ops-facing dashboards.

#### 23.4 Tool Versioning

Native tools carry a schema version alongside the core agent version (§18.7); MCP and plugin tools carry their own declared version. A tool schema change that isn't backward compatible (removed required arg, changed return shape) requires a version bump, and the orchestrator's prompt-assembly step includes the tool's version in what's cached (v2 §15's prompt cache) — so a tool upgrade correctly invalidates cached prompt fragments that described the old schema, rather than serving stale tool documentation to the model.

#### 23.5 Tool Dependencies (already covered for plugins in §18.8/v2 §10.4, extended here)

A tool can declare a runtime dependency (e.g. the Docker tool depends on the Docker daemon capability) resolved against the Capability Registry (§18.4) at registration time — a tool whose dependency isn't met registers as `unavailable` rather than being offered to the model and failing at call time, closing the loop opened in v2 §3.2.

---

### 24. Reflection Engine (deepened)

v1 §14/v2's Reflection Engine handled failure diagnosis and repair-strategy selection well. §24 adds the dimensions the prompt specifically calls out that were previously implicit.

#### 24.1 Patch Evaluation

Before a generated patch is even applied, a lightweight pre-application evaluation scores it against: does it plausibly address the diagnosed root cause (semantic check — does the patch touch the function/lines implicated in the failure), does it introduce an obviously-inconsistent change (e.g. a patch that changes a function signature without updating call sites detected via the Repository Graph, §22.1), and does it match the project's detected coding style (v1 §Intelligence Features "coding style detection"). A low-scoring patch is regenerated before being applied and tested — cheaper than a full apply-test-fail-reflect cycle for obviously-flawed patches.

#### 24.2 Planning Evaluation

After a subtask completes (success or failure), the Reflection Engine retrospectively scores the *original plan* for that subtask: was the estimated complexity accurate, did the chosen tool sequence match what was actually needed, did dynamic replanning have to intervene. This feeds directly into the Evaluation Framework's planning-quality metric (v2 §14.1) and, over time, into Bug/Architecture Memory as a pattern ("subtasks touching the ORM layer in this repo are consistently underestimated") — a form of calibration feedback distinct from bug-fix memory.

#### 24.3 Alternative Solution Generation

When a repair attempt's confidence score (v1 §13) is low even before execution, the Reflection Engine can generate 2-3 candidate approaches (not just one repair) and use a cheap static check (does each candidate at least parse, does each avoid the specific anti-pattern that caused the original failure) to pick the most promising before spending a full apply-test cycle on any of them — a lightweight beam-search over repair strategies rather than committing to the first idea.

#### 24.4 Quality Scoring, Learning Memory, Reflection History

Quality scoring is the composite of §24.1-24.3's checks plus the eventual test outcome, stored per-attempt. **Learning Memory** is the subset of this history promoted to long-term Bug/Architecture Memory (v1 §6, v2 §6.3) — specifically, only verified-successful patterns are promoted (unchanged principle from v2 §14.3's Reflection quality metric definition), while the full **Reflection History** (every attempt, successful or not, with its quality score) stays in the session's transaction log / execution history (v1 §24 `execution_history` table) as the raw material the Evaluation Framework's reflection-quality metric is computed from.

#### 24.5 Termination Detection (formalized from v1 §14/v2 §8.5)

Explicitly defined as a pure function of: retry count vs. subtask budget, session-level circuit breaker state (v2 §8.5), quality-score trend across attempts (declining or flat scores across repairs is itself a termination signal, distinct from simply exhausting a retry count), and repeated identical failure signatures (v1 §14) — any one tripping routes to `NEEDS_REVIEW`, never silent infinite retry.

---

## Part IX — Edge Case Registry

### 25. Every Named Scenario, Fully Specified

A single source of truth: every edge case gets Detection / Recovery / Rollback / Retry / Logging / User Notification / Auto-continuation-if-safe, so no failure mode is left as an implicit assumption.

| Scenario | Detection | Recovery | Rollback | Retry | Logging & notification | Auto-continue if safe? |
|---|---|---|---|---|---|---|
| Model crash during file write | Atomic write (v1 §10) means the write either completed or didn't — post-crash file-hash check against pre-write snapshot | Restore from snapshot store | Yes — snapshot restore is the rollback | Retry the write once state is confirmed clean | `error.detected` + diagnostic bundle | Yes, once file state confirmed consistent |
| GPU crash (driver reset, VRAM ECC error) | llama.cpp server health check fails (v2 §3.3-style) | Model Session Manager reloads the model fresh (§5.4-equivalent, treated as an implicit swap-to-same-model) | N/A (no file mutation) | Reload attempt, bounded (3x), then fall back to CPU-only inference for the current model if configured | `resource.critical` + notification | Yes, transparently, with a visible perf-degradation notice |
| RAM exhaustion | Health Manager critical threshold | OOM-prevention ladder (v2 §4.6) | N/A unless mid-write (handled by the file-write case above) | N/A — this is proactive prevention, not a failure to retry | `resource.critical` | Yes, via the ladder's graceful degradation |
| Disk full | Health Manager disk-usage critical | Pause all write operations; surface immediately (unlike RAM, there's no graceful degradation for disk — writes simply cannot proceed) | N/A | No retry until space freed | `resource.critical`, hard user notification, no auto-continue | No — requires user action |
| Power loss (laptop battery dies, no graceful shutdown) | Next startup's checkpoint scan (v2 §3.6) finds a session with no matching clean-shutdown marker | Standard crash recovery (v2 §3.5) | Snapshot-based, same as any crash | Standard resume flow | `recovery.started` on next boot | Yes, on next boot, offered for resume |
| System restart (planned) | Graceful shutdown path (v2 §3.4) | N/A — this is the clean path | N/A | N/A | `session.paused` for each active session | Yes, auto-resume offered next start |
| Plugin failure (crash, hang, malicious behavior) | Subprocess exit code / heartbeat miss (v2 §4.2, plugin-scoped) | Plugin subprocess killed and unloaded; core agent unaffected (v2 §10.3 isolation) | Any tool call the plugin had in flight is treated as failed, standard tool-failure handling applies | Plugin restart attempted once (dev mode) or left disabled (production) pending user action | `plugin.event` (crash), notification | Yes for the core session; plugin itself stays disabled until addressed |
| Tool timeout | Per-tool timeout (v1 §11/§8) | Kill process (SIGTERM→SIGKILL), partial output preserved | N/A unless the tool was mutating (then standard rollback) | Per tool's idempotency class (v2 §8.6) | `tool.timeout` | Yes, routes to Reflection which decides |
| Infinite loop (agent stuck repeating similar actions) | Reflection's repeated-failure-signature detection (v1 §14) + Termination Detection (§24.5) | Session-level circuit breaker trips (v2 §8.5) | N/A directly, though individual failed attempts may be rolled back | Retry budget already exhausted by definition of this case | `session.needs_review`, user notification | No — always escalates to user |
| Infinite retries | Prevented structurally by per-subtask and session-level retry budgets (v1 §14, v2 §8.5) — this scenario should be structurally unreachable | N/A | N/A | N/A | Budget-exhaustion event logged before it could become infinite | No — by design, never reached |
| Context corruption (malformed context assembled, e.g. a compression bug producing garbled text) | Prompt Inspector-visible sanity check: rendered prompt fails a basic well-formedness check before being sent to the model | Rebuild context from source (repository state + memory) rather than from any cached/compressed intermediate | N/A — nothing was mutated in the world, only the in-memory prompt | Rebuild attempt, bounded, then escalate | `error.detected` (context_engine) | Yes, if rebuild succeeds |
| Checkpoint corruption | State validation (v3 §20.5) at load time | Fall back to the last-known-valid checkpoint (checkpoints are versioned, v3 §20.4) | Effectively a rollback to that prior checkpoint | N/A | `checkpoint.corrupted`, notification if it means losing recent progress | Yes, resuming from the older valid checkpoint, with a clear notice of how much (if any) progress was lost |
| Repository changes during execution (external edit mid-session) | Conflict detection via file-hash comparison before every patch application (v1 §10) | Re-read current file, re-generate the patch against the new base | N/A — nothing was overwritten because the check happens before the write | The regenerated patch is a fresh attempt, not counted as a "failure" retry | `file.changed` (external), notification if it altered planned work meaningfully | Yes, transparently re-planned |
| User edits file during execution | Same mechanism as above — this is a specific instance of "repository changes during execution" | Same | Same | Same | Same, phrased to the user as "we noticed you edited X, re-reading it" | Yes |
| Git conflicts (agent's branch vs. another change) | `git.event` from a merge/rebase tool call returning a conflict status | Surface the conflict; do not attempt an automated resolution of a real merge conflict without approval (too risky to guess silently) | The attempted merge/rebase itself is abortable (`git merge --abort` equivalent) | No auto-retry — requires either user resolution or an explicit agent-assisted-resolution workflow (human approval node) | `git.event` (conflict), notification | No — requires user or approved-agent resolution |
| Browser crash | Playwright process health check | Restart the browser context (new isolated context, v1 §12) | N/A unless mid-verification-write (rare — browser tools are mostly read/verify) | Restart attempt, bounded | `browser.event` (crash) | Yes |
| Docker crash / daemon unavailable | Capability probe re-check (v2 §3.2) on tool-call failure | Fall back to subprocess sandbox with a visible capability-degradation warning (v2 §3.2) | Any in-flight container operation's result is treated as failed/unknown, not assumed successful | Bounded retry against Docker; if daemon genuinely down, don't keep retrying — degrade | `resource.warning`/`system.degraded` | Yes, degraded mode |
| Database corruption | Startup health check (v2 §3.3) + periodic integrity check (SQLite `PRAGMA integrity_check` equivalent) | Restore from the most recent backup (v2 §12.7) | The corrupted DB itself is the thing being "rolled back" from, to the backup | N/A | Critical, blocks startup until resolved or backup restored | No — requires explicit backup restore |
| Network interruption | Timeout/connection-error on any network-dependent tool (HTTP, MCP server, remote worker) | Standard tool retry per idempotency class; local-first design means most work continues unaffected | N/A for read operations; effectful operations use dedupe keys (v2 §8.6) | Exponential backoff, bounded | `tool.timeout` or `error.detected` | Yes, for anything not strictly requiring the network |
| Terminal hangs (command never returns, no natural timeout signal) | Per-command timeout (v1 §11) is the actual detection mechanism — treated as a timeout, not a distinct case | Same as Tool timeout row | Same | Same | Same | Same |
| Model switching during execution | Explicitly designed sequence, not really a "failure" (v2 §5.4) | N/A — this is the happy path for a deliberate operation | N/A | N/A | `model.swap_started/completed` | Yes, by design |
| Large repositories exceeding context | Context Engine's token-budget packing (v1 §16) + Smart Cache size ceiling (v3 §22.4) | Compression ladder (v2 §6.2) applies progressively more aggressively; background/prioritized indexing (v1 §5.4) for repos exceeding the full-index budget | N/A | N/A | Informational, visible "still indexing" / "context compressed" indicators | Yes, always — this is graceful degradation, not a hard failure |
| Very large binary files | File-type detection before any parse/embed attempt; binaries are never sent to Tree-sitter or the embedding model | Skip indexing content, index only metadata (path, size, type) | N/A | N/A | Informational only | Yes |
| Permission denied (filesystem) | OS-level error caught by the filesystem tool | Surface clearly; do not attempt to escalate privileges automatically (a hard security boundary, not a bug to work around) | N/A | No auto-retry (retrying an OS permission error rarely helps) | `tool.finished` (status=error), notification | No — requires user to fix permissions or explicitly redirect |
| Locked files (another process holds a lock) | OS-level lock error | Bounded retry with backoff (locks are often transient) | N/A | Yes, bounded | `tool.finished` (status=error) if retries exhausted | Yes, if lock releases within retry window |
| Read-only filesystem | Write attempt fails at the OS level, or detected proactively via a mount-flag check in the capability probe (v2 §3.2) | Surface clearly, no silent fallback | N/A | No | `system.degraded` if detected at startup for the project root | No — fundamentally can't write; agent should switch to read-only/investigation workflows |
| Concurrent workflows editing the same file | File-hash conflict detection (v1 §10) fires for the second workflow attempting to write | Second workflow's patch is regenerated against the post-first-workflow state (same mechanism as external-edit handling) | N/A | The regenerated patch is a fresh attempt | `file.changed` (internal, from the other session), notification if user is watching both | Yes, transparently |
| Cancelled execution | User-initiated cancel signal (v1 §8) | In-flight tool cancellation (cooperative or forced, v1 §8) + forced checkpoint before teardown | Any partially-applied change from the cancelled step is rolled back via its snapshot | N/A — cancellation is terminal for that attempt, not retried automatically | `session.cancelled`-equivalent event | N/A — explicit user action, no auto-continue |
| Partial writes | Prevented structurally by atomic write-then-rename (v1 §10) — this scenario should be structurally unreachable for files the system itself wrote | If detected anyway (e.g. a tool bypassed the atomic-write path, which would itself be a bug) | Restore from snapshot | N/A | `error.detected`, treated as a P0 bug report, not a routine recoverable case | No — this indicates a defect, always surfaced loudly |
| Unexpected exceptions (uncaught) | Global exception handler at the orchestration boundary (v1 §7's "no raw exception escapes a tool boundary" extended to every layer) | Forced checkpoint, session → `NEEDS_REVIEW`, never silently swallowed | Whatever the last consistent checkpoint captured | N/A | `error.detected` with full stack trace in the diagnostic bundle, always | No — always surfaces for review, since an uncaught exception means the failure mode wasn't anticipated |
| Unknown tool failures (a tool returns an error the Reflector's classifier doesn't recognize) | Failure classification (v1 §14) has an explicit `unknown` category rather than forcing a bad guess | Treated conservatively — no automated repair attempted for `unknown`-classified failures | N/A beyond what the tool itself reports | Bounded retry (generic backoff) then escalate | `error.detected`, flagged for classifier improvement (feeds back into Reflection's design, not just this instance) | No — escalates rather than guessing |

---

## Part X — Architecture Principles

### 26. Stated Explicitly

The design decisions across v1-v3 all trace back to a small set of principles. Stating them explicitly gives future contributors something to check new code against, rather than only precedent to pattern-match:

- **SOLID.** Every module in this document has one responsibility (single-responsibility is what defines "a module" — see the granularity of v1 §7's tool catalog or v3 §18's registries); new capability is added via the Extension Manager's contribution points (open/closed, §18.8) rather than by editing core orchestration; every interface implementation (VectorStore, CheckpointStore, ToolSandbox, §18.2) is fully substitutable for another (Liskov); interfaces are kept narrow and role-specific rather than one monolithic backend interface (interface segregation); and the IoC container is dependency inversion made literal, not just a principle stated and hoped for (§18.2).

- **Clean / Hexagonal Architecture.** Core orchestration (the Workflow Engine, Planning Engine, Reflection Engine) depends only on interfaces (ports) — VectorStore, ModelClient, ToolSandbox, EventBus. Concrete implementations (ChromaDB, llama.cpp, Docker, Redis) are adapters plugged in at the composition root (§18.2), never referenced by name inside core logic. This is what makes v2 §16's "swap SQLite for Postgres, swap in-process bus for Redis" a configuration change rather than a rewrite — the ports never change, only which adapter is bound to them.

- **Event-Driven Design.** Cross-cutting concerns (health, observability, plugin integration, UI updates) are driven by the Event Bus (v3 §19), not by direct method calls scattered across subsystems — a new consumer of "when a tool finishes" subscribes to `tool.finished`, it never requires modifying the Tool System to add a new callback.

- **Domain-Driven Design, where useful — deliberately not everywhere.** The system uses DDD-style bounded contexts for the genuinely complex domains (Workflow/Task state, §20; Repository Intelligence's unified graph, §22) where a rich domain model earns its complexity. It deliberately does *not* impose DDD ceremony (aggregates, repositories-as-a-pattern-name, ubiquitous-language glossaries) on simple CRUD-shaped subsystems like Configuration or Feature Flags, where that would be process for its own sake — matching this document's own instruction not to over-engineer what doesn't need it.

- **Dependency Injection.** Constructor injection via the IoC container (§18.2) throughout; no service locator anti-pattern (subsystems don't reach into a global registry to *pull* their own dependencies — dependencies are *pushed* in at construction, making every subsystem's requirements visible in its constructor signature and trivially mockable in tests).

- **Plugin-First Design.** The Extension Manager's contribution points (§18.8) are not an afterthought bolted onto a closed system — new tools, new ranking signals, new failure classifiers, and new frontend panels are designed from v1 onward as things *both* the core team *and* third-party plugins register through the same mechanism. There is no privileged "core-only" registration path that plugins are excluded from except where permission tiers (v1 §7.2) intentionally restrict capability, not architecture.

- **Capability-Based Routing.** Nothing in the system assumes a capability (Docker, a specific GPU, a specific LSP server, a specific model) is present — every consumer checks the Capability Registry (§18.4) and degrades gracefully (v2 §3.2, and every row of the Edge Case Registry, §25) rather than assuming an idealized environment. This is precisely what makes the same architecture correct for a 4GB VRAM laptop and a GPU cluster — the architecture never hardcodes an assumption about what's available, only a registry-mediated question of what's available *right now*.

- **Message-Based Communication.** Tool calls are message-shaped contracts (`ToolCall`/`ToolResult`, v1 §1.3) rather than direct function calls with implementation-specific signatures — this is what allows a tool to be local, MCP-provided, or remote-worker-executed (v2 §16.3) behind the identical contract. The same is true of the Event Bus's events and the Workflow Engine's step-ready/step-completed signals (§19, §21) — coordination between subsystems happens via well-defined messages, not shared mutable state or tightly-coupled direct calls.

- **The overarching rule.** Every subsystem in this document must be replaceable without affecting the others, and no subsystem may depend on another's implementation details — only on its published interface. Where a design in v1 violated this (direct references between subsystems), v2/v3 corrected it (§18's IoC container, §20's single-owner state model). This is the standard every future addition to the system is held to, not just the standard this document was written to.
