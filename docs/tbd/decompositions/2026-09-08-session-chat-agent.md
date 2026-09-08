# Session chat agent — PR Decomposition Map

- **Created:** 2026-09-08
- **Epic reference:** conversation request: separate chat with multiple sessions and an isolated backend agent
- **Trunk:** `main`
- **Size budgets:** target ≤600 reviewable lines, cap 1000 (see `tbd:sizing-pull-requests`)

## Goal and boundaries

Build a separate general-purpose chat alongside the existing FlowScout role-analysis
workflow. The backend owns process-local session history and calls the LLM only through
an injected provider boundary. Each session has isolated history; the API key remains
backend-only.

The first version is a local/single-user product. Persistence across backend restarts,
authentication, cross-device accounts, semantic memory, tools, attachments, and applying
the existing multi-agent judge to every chat message are out of scope. A judge remains
optional because the assignment requires an agent boundary, not an expensive evaluation
call for every answer.

## Slices

| # | PR title | Purpose (one sentence) | Strategy | Size budget | Depends on | Status |
|---|----------|------------------------|----------|-------------|------------|--------|
| 1 | `feat(chat): add isolated session chat agent` | Add the isolated agent, process-local backend sessions, and a separate complete chat screen in one branch and PR. | branch-isolated | 997 lines | — | in-review |

## Slice details

### Slice 1 — `feat(chat): add isolated session chat agent`

- **In scope:**
  - `ChatAgent` as the public application entity with `respond(...)`;
  - a small `ChatModel` protocol so the agent does not import DeepSeek or the OpenAI SDK;
  - additive support in `DeepSeekProvider` for role-based message history;
  - explicit input policy (roles, non-empty content, message/count/size bounds);
  - explicit output policy (non-empty normalized answer and bounded public contract);
  - a thread-safe in-memory session store;
  - session and message records with UUID, timestamps, role, and content;
  - create/list/get-session and send-message endpoints;
  - one atomic in-memory user-message/assistant-response update;
  - loading history strictly by the requested session ID;
  - deterministic title from the first user message;
  - repository, service, cross-session isolation, and HTTP tests.
  - a separate `chat.html` entry point rather than reshaping the current judge screen;
  - session sidebar with create and switch actions;
  - rendering persisted messages and sending a new message;
  - busy, empty, backend-unavailable, and retryable-error states;
  - a navigation link from the existing FlowScout page;
  - a process-local debug menu for model, thinking, history, token and prompt experiments;
  - frontend syntax/contract checks and README launch instructions.
- **Out of scope:** authentication/ownership, deletion, search, streaming tokens, markdown rendering, tools, attachments, and judge calls.
- **Ships safely because:** all work is completed and tested on `codex/session-chat-agent`; the existing role-analysis endpoints and page remain unchanged until the branch is reviewed and merged.
- **Size justification:** this is above the 600-line target but below the 1000-line cap because the user explicitly requested one PR; the diff is one cohesive end-to-end feature and includes its agent, API isolation tests, and complete separate UI.
- **Cleanup owed:** none.

## Architecture decision

`ChatAgent` is intentionally stateless. `ChatSessionService` owns session orchestration,
loads exactly one session's bounded process-local history, calls the agent, and stores the result.
`DeepSeekProvider` is an infrastructure adapter behind `ChatModel`; neither the agent nor
session service knows about API keys, base URLs, or the OpenAI-compatible SDK.

This separates three concerns:

1. session isolation and process-local history;
2. agent policy and request/response lifecycle;
3. vendor-specific LLM transport.

## Decision log

- 2026-09-08: Chose three naturally-safe additive slices. Deferred auth and the existing
  LLM-as-a-judge pipeline because the first chat is local/single-user and the assignment
  only requires a correctly encapsulated agent call.
- 2026-09-08: User requested one PR on a dedicated branch and no feature toggle. Combined
  the three slices with a hard review-size cap of 1000 lines.
- 2026-09-08: User explicitly removed SQLite and durable memory; sessions now live only
  for the lifetime of the backend process.
- 2026-09-08: Added process-local experiment settings and a compact debug menu at the
  user's request; no feature toggle or durable configuration was introduced.
