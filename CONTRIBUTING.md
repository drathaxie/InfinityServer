# Contributing to InfinityServer

Thanks for helping build the emulator. This is a small, fast-moving reverse-engineering project —
keep changes focused and verifiable.

## Ground rules

- **Never commit secrets, raw captures, or live credentials.** No `.pg.env`, SSH keys, DB dumps,
  or raw packet captures (they carry account PII). The `.gitignore` blocks the obvious ones, but
  double-check your staged diff. Production credentials are shared out-of-band, never in the repo
  or chat. The server is fully generative — it derives login/state from the DB and replays no
  capture — so nothing under `capture/` is needed at runtime beyond the extract tooling.
- **Don't commit the decompiled client** (`docs/decomp/`). It's Artix's copyrighted code and is
  gitignored. Reference it locally; don't redistribute it.
- **Respect the project's nature.** This is for research/preservation against your own client.

## Branch & PR flow

- Default branch: **`main`**. It's protected — no direct pushes.
- Branch per change: `feature/<short-name>`, `fix/<short-name>`, `content/<short-name>`.
- Open a **PR into `main`**; keep it scoped to one logical change. Write a clear description of
  *what* and *why*, and how you verified it (which tests, or in-game behavior observed).
- At least one review before merge. Squash-merge keeps history readable.

## Dev environment

Local dev uses **SQLite** — no Postgres or cloud access needed (see [README](README.md) → *Run it
locally*). The whole stack runs from `python server/server.py` + `python server/webapi.py`.

```sh
python -m venv .venv && . .venv/Scripts/activate
pip install -r server/requirements.txt pytest
```

## Testing

- Run the suite before opening a PR:
  ```sh
  cd server && python -m pytest
  ```
- Tests are **backend-agnostic** — they isolate via `db.use_throwaway()` (a temp SQLite file, or a
  throwaway Postgres schema). Add tests next to the code: `server/test_<area>.py`.
- For behavior that only shows in-game, describe the manual repro in the PR (the client mod + a
  local server, or a dev account on the live VM if you have access).

## Code style

- **Match the surrounding code.** This codebase favors clear, comment-explained reverse-engineering
  notes over abstraction — when a value or behavior was derived from a capture or the decomp, say so
  in a comment (as the existing code does).
- Python: standard library first; the only runtime dep is `psycopg` (Postgres backend only). Keep
  the SQLite path dependency-free.
- Talk to the DB through `server/db.py` (the dialect wrapper rewrites `?`→`%s` and normalizes rows)
  — don't hardcode Postgres- or SQLite-specific SQL in call sites.
- Keep content edits in the versioned `data/` files where possible so a fresh `seed.run()` is
  reproducible.

## Content & assets

- Custom `.unity3d` bundles live in `customBundles/` and are tracked via **Git LFS** (`git lfs
  install` once after cloning). Note large binaries in your PR.
- Captured/authored content (maps, monsters, apops, shops, items) is seeded from `data/` — prefer
  editing those files over one-off DB writes so the seed stays the source of truth.
