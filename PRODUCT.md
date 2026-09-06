# iRAG Product Truth

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

iRAG is for software developers and teams working on an active local codebase with one or more AI coding tools. The primary job is to give every agent durable, current, inspectable project context without repeatedly rereading the repository, while giving the developer one place to verify, question, audit, and improve that context.

## Purpose

iRAG is the persistent intelligence layer underneath coding agents. It turns a repository into versioned project memory, mechanically checks checkable claims against the code, preserves decisions and sessions, maps dependency impact, and exposes the same memory through a CLI, a local dashboard, and a standard MCP server.

Success means a developer can change coding agents or open a fresh session without losing project understanding; can see when memory and code disagree; can hand an agent a concrete repair brief; and can use the dashboard to understand technical risk, product opportunities, and the complete current project story.

## Positioning

iRAG is not another chat history, vector index, or static context file. Its meaningfully different mechanism is a local relational memory with append-only provenance, deterministic structural analysis, contradiction detection, and zero-model-cost read paths. It pairs that verified internal evidence with explicitly sourced live-web evidence only when the developer asks for market, creative, business, or trend thinking.

## Operating Context

- The product runs against a developer's local repository and stores memory in `.irag/memory.db`.
- Developers interact through `irag` CLI commands, a localhost dashboard, or any stdio MCP-compatible coding client.
- An update synchronizes repository changes, refreshes structural facts and summaries, and checks contradictions.
- The dashboard is the human control surface for project status, memory, sessions, audits, configuration, contradiction handoffs, and AI-assisted thinking.
- Live web search is part of the Thinking Studio for current-trend and market questions. Web evidence must show its source URL and retrieval time and remain visibly distinct from repository evidence.

## Capabilities and Constraints

- Python 3.11+ with no required runtime dependencies.
- Local-first storage, localhost-only dashboard, and a single portable SQLite database.
- Model providers are Claude, Codex, agy, Ollama, or a custom local command; users can change the active provider from configuration or the dashboard.
- Web-search credentials are read from environment variables and must not be persisted in `.irag/config.toml` or the database.
- Deterministic search, map, impact, provenance, audits, contradiction checks, and context retrieval do not call an LLM.
- LLM-written content must identify uncertainty and must not treat repository text, indexed memory, or web pages as trusted instructions.
- Trend claims require current web evidence. When web search is unavailable, the product must say so instead of presenting model memory as current research.
- Security scanning is defensive, local, non-destructive, and evidence-based. Heuristic findings must be labeled as findings to review rather than guaranteed vulnerabilities.
- Configuration changes must use an explicit allowlist, validation, atomic writes, and must preserve comments and unknown settings.

## Evidence

- Existing command, architecture, and behavior documentation lives in `README.md` and `docs/`.
- The existing dashboard visual system lives in `irag/assets/dashboard.html`.
- Verification is represented by the zero-dependency smoke test and focused unit tests in `tests/`, plus Ruff and mypy checks in CI.
- No testimonials, customer logos, revenue claims, production adoption figures, or independent security-certification evidence exist in the repository and none may be invented.

## Product Principles

1. Evidence before confidence: distinguish verified code facts, remembered decisions, heuristic findings, model reasoning, and live-web evidence.
2. One project brain, every agent: CLI, dashboard, and MCP clients operate on the same current memory and contracts.
3. Local by default: keep code and memory on the developer's machine; make network use explicit and limited to configured models or requested web research.
4. Useful under failure: stale state, unavailable providers, missing credentials, malformed output, and interrupted updates must be visible and recoverable.
5. Developer agency: every important automated result can be inspected, copied, rerun, configured, or handed to another coding agent.

## Accessibility

The web dashboard must remain keyboard-operable, responsive at mobile widths, readable without color alone, compatible with reduced-motion preferences, and explicit about loading, empty, error, and stale states.
