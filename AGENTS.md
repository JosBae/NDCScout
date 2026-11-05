# Instructions for coding agents (Muse Code, Claude Code, Cursor, …)

Repo: NDC Scout, a pharmacy NDC reimbursement ranker built on Meta Model API.

- Domain rules live in `skills/ndc-reimbursement/` (SKILL.md and references). The
  runtime agent loads the same files as its system prompt, so edit them there,
  not in Python strings.
- `ndc_scout/engine.py` is the single source of truth for every dollar figure.
  The model never computes money, and `agent.verify()` rechecks its answers.
  If you change the engine, update `tests/test_core.py`.
- Tools are defined once in `ndc_scout/tools.py` and shared by the agent, the MCP
  server (`ndc_scout/mcp_server.py`) and the HTTP API.
- Run `python -m pytest -q` before committing. Tests never call the paid API.
- Use `ndc-scout ask --mock` for development. Live calls cost money; see
  `skills/meta-spend-safety/SKILL.md`. Never print or commit `META_API_KEY`.
- `sample_data/` is synthetic. Don't present its numbers as real prices.
- Never add clinical or medical advice. The hackathon bans it, and this tool only
  makes purchasing decisions between equivalent packages.
