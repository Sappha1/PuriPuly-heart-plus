## Purpose & Authority

- This file is the authority for **agent operating policy** in this repository.
- Discoverable implementation facts (exact defaults, command options, file formats) must be read from code and README files first.
- If implementation facts and docs disagree, treat code as the source of truth and then align docs.
- Keep this file focused on rules and routing, not long feature or setup explanations.

## Agent-Only Invariants

- Settings compatibility is mandatory:
  - Keep `to_dict` and `from_dict` synchronized.
  - New settings must have defaults so existing `settings.json` continues loading.
  - If a setting key is renamed, accept the old key in `from_dict` for backward compatibility.
- All new user-facing UI text must go through i18n keys, and all locale bundles must be updated.
- Debug UI preview mode may exist for hard-to-reproduce UI states.
  - Verify the exact CLI flag and preview actions in code before use.
  - Preview actions must not persist settings, mutate secrets, or call external providers/brokers.
  - Use preview mode for manual QA of hidden UI states instead of forcing real broker/OpenRouter states.
  - Debug preview controls must remain hidden unless the explicit debug flag is enabled.
- For documentation lookup and code generation, prefer MCP resources/templates first and use Context7 when available.
- For browser or website automation tasks, use the `agent-browser` skill first.
- If a task modifies Rust code, the final step of the overall task must recompile the Rust overlay for Windows.
- Local installer smoke tests must use an alternate `AppId` and an isolated install directory; never reuse the production `AppId` for test installs.
- Prefer a project virtual environment for tests, verification, and development commands whenever one exists.
- If `.venv` exists, Windows shells should use `.venv`.
- If `.venv-wsl` exists, Linux / WSL shells should use `.venv-wsl`.
- In WSL shells, use `direnv exec <repo> ...` or explicit `UV_PROJECT_ENVIRONMENT=.venv-wsl`; do not rely on `bash -i -c`.
- Broker Node verification (`pnpm`, `vitest`, `wrangler`) must run from a Linux / WSL workspace only; do not run it from Windows shells.
- In WSL, install broker Node dependencies inside the Linux workspace; do not reuse Windows-installed `node_modules` from `/mnt/c/...`.

## Security & Async Safety

- Keep provider and I/O calls async; avoid blocking the event loop.
- Use `asyncio.create_task` for long-running loops and ensure cancellation on shutdown.
- Always `await` provider `close()` in teardown paths.
- In Flet UI callbacks, use `page.run_task` for async work.
- Secrets are loaded through `SecretStore` (keyring/encrypted file/env fallback).
- When `secrets.backend` is `encrypted_file`, require `PURIPULY_HEART_SECRETS_PASSPHRASE`.
- Never commit real credentials, API keys, or secret material.

## Freshness Guardrails

- Do not hardcode volatile defaults or file format assumptions in this file.
- Prompt file naming/extensions and fallback order must be verified in `src/puripuly_heart/config/prompts.py`.
- Orchestrator default parameters (including context memory values) must be verified in `src/puripuly_heart/core/orchestrator/hub.py`.
- Keep guidance concise and non-duplicative. Prefer routing to canonical paths over re-documenting details already in code/README.
