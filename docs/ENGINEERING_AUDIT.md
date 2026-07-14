# Engineering Audit and Roadmap

This document records a practical engineering review of InfinityServer as a public portfolio project and as a privately deployed multiplayer backend. It focuses on changes that improve reliability, security, maintainability, and the quality of the evidence presented to prospective employers.

## Current strengths

- Asynchronous TCP command server with null-delimited JSON framing.
- Modular command handlers and centralized authorization for staff-only authoring operations.
- Persistent accounts, characters, inventory, equipment, quests, shops, monsters, drops, and authored content.
- Shared SQLite/PostgreSQL persistence layer with disposable test databases.
- Instanced multiplayer world state, combat simulation, monster AI, and shared rewards.
- Reproducible, version-controlled content seeding.
- C# Unity runtime integration using Doorstop and Harmony.
- Ubuntu deployment using systemd, PostgreSQL, Caddy, and HTTPS.
- Detailed evidence-based audits comparing behavior with packet captures and client internals.

## Priority 0 — public-repository safety

### Audit the complete Git history for secrets and private data

The current `.gitignore` correctly excludes environment files, keys, raw packet captures, local databases, and decompiled commercial source. That does not establish that none of those files appeared in an earlier commit.

Recommended checks:

```bash
gitleaks git --redact --verbose
trufflehog git file://. --only-verified
```

If a real credential is found, rotate it before rewriting history. Treat raw packet captures and database snapshots as personal data even when they contain no password.

### Verify public asset ownership

Manually review `customBundles/` and all Git LFS objects. Only original or redistributable content should remain public. Extracted commercial textures, cloned bundles, decompiled assemblies, production databases, and player data should not be distributed.

## Priority 1 — network hardening

### Enforce frame and buffer limits

`handle_client` currently accumulates bytes until it sees `0x00`. A client that sends an indefinitely large unterminated payload can force unbounded memory growth.

Recommended behavior:

- Reject an individual frame above a configurable maximum, initially 1 MiB.
- Reject a buffered unterminated message once it exceeds that limit.
- Log the peer and reason without logging the payload.
- Close only the offending connection.
- Add unit tests covering exact-limit, over-limit, fragmented, empty, and multi-frame reads.

### Gracefully close stream writers

After `writer.close()`, await `writer.wait_closed()` and tolerate connection-reset/cancellation errors. This makes connection lifecycle behavior explicit and reduces noisy shutdown warnings.

### Add idle and handshake timeouts

Use bounded timeouts for unauthenticated sessions so a client cannot hold a socket forever without completing login. Apply more generous activity rules after authentication.

### Add connection-level rate limiting

Protect expensive command families and malformed-message logging from burst abuse. Start with conservative per-session limits and measure before applying global restrictions.

## Priority 2 — authentication and authorization

- Document token lifetime, invalidation, replacement, and double-login behavior.
- Add tests proving staff-only commands remain inaccessible through raw packets from non-staff sessions.
- Apply a uniform maximum username/password/token length before hashing, querying, or logging.
- Ensure authentication failures do not reveal whether a username exists.
- Review every command that mutates shared authored content and keep authorization server-side.

## Priority 3 — observability and operations

- Replace ad-hoc `print` statements with structured logging while retaining readable local output.
- Include event type, command, session/character identifier, area, and latency where appropriate.
- Never log passwords, session tokens, complete packet payloads, or personal data.
- Add health checks for the TCP service, web API, database connectivity, and seed status.
- Add controlled shutdown that stops accepting connections, closes clients, and cancels the AI task.
- Track unhandled commands by count and sample rather than allowing logs to grow indefinitely.

## Priority 4 — automated quality gates

The repository now runs its SQLite-backed pytest suite in GitHub Actions. Useful next gates are:

- `ruff` for formatting-independent lint and import checks.
- `mypy` incrementally, beginning with new modules and typed boundaries.
- `pip-audit` or Dependabot for Python dependencies.
- A PostgreSQL integration job using a temporary service container.
- Coverage reporting, initially informational rather than blocking.
- A smoke test that starts both services and exercises a small login/API path.

Avoid enabling strict checks across the entire historical codebase in one PR. Establish a baseline and tighten it gradually.

## Priority 5 — architecture improvements

- Extract TCP framing and connection policy from `server.py` into a small, independently testable module.
- Continue moving domain commands into focused handler modules.
- Define typed request/response boundaries for high-risk commands without attempting to model every dynamic wire object at once.
- Make background-task ownership explicit so spawned tasks can be cancelled and observed.
- Encapsulate caches with clear invalidation rules and metrics.
- Add migration/version tracking rather than relying only on schema creation and seed behavior as the project evolves.

## Portfolio evidence still needed

### Demonstration assets

Add a short private-safe demo showing:

1. Both services starting locally with SQLite.
2. A client logging in.
3. Two connected players sharing an area.
4. Combat and a quest or shop operation.
5. A small content edit or authoring workflow.

Blur account names, addresses, tokens, IPs, and private server details. Do not include copyrighted source or extracted assets in the recording.

### Measurable project facts

Automate and publish defensible figures such as:

- Number of supported command handlers.
- Number of database tables and versioned content records.
- Number of tests and supported Python versions.
- Number of mapped protocol message types.
- Approximate module/code size, clearly labeled as generated versus authored code.

Measurements should come from scripts or CI rather than estimates in prose.

## Suggested PR sequence

1. TCP framing limits, graceful close, and focused tests.
2. Authentication/authorization regression tests.
3. Structured logging foundation and redaction policy.
4. PostgreSQL CI job and migration/version strategy.
5. Demo media and automated project metrics.
6. Incremental lint/type-check adoption.

Keeping these changes separate makes each design decision reviewable and creates a clean public history of professional engineering work.