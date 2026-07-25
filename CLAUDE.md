## graphify

When `graphify-out/graph.json` exists, use it as a **navigation index**, not a mandate for every turn.

**Use first** for unknown codebase/architecture questions, call graphs, or A→B relationships.
**Skip** when the exact file/symbol is already known this session, or the task is copy/docs/commit/deploy only.

Query order (token-efficient):

1. `graphify explain "<SymbolOrFile>"`
2. `graphify path "<A>" "<B>"`
3. `graphify query "<specific symbols>" --context call --budget 2500`

Avoid broad English queries on large React apps (UI hubs flood the budget). Prefer `graphify-out/AGENT_NAV.md` over the full wiki index. Do not load `GRAPH_REPORT.md` unless query/path/explain failed. Dirty `graphify-out/` after hooks is normal. After substantive code edits: `graphify update .` (AST-only, no API cost).
