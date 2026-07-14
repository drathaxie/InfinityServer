# Security Policy

## Supported scope

Security fixes are accepted for the current `main` branch. This is a research project rather than a
hosted service offered to the public, so no service-level response time is guaranteed.

## Reporting a vulnerability

Please do **not** publish exploitable details in a public issue.

Use GitHub's private vulnerability-reporting feature when it is enabled for this repository. If that
feature is unavailable, contact the repository owner privately through the contact information on
the GitHub profile and include:

- the affected file, component, or endpoint;
- steps to reproduce the issue;
- the practical impact;
- a minimal proof of concept, if safe;
- suggested remediation, if known.

Do not access accounts, systems, or data that you do not own or have explicit permission to test.

## Sensitive material that must never be committed

- passwords, API tokens, database credentials, or session secrets;
- `.env`, `.pg.env`, `.r2.env`, private keys, or certificates;
- production database exports or local database files;
- raw packet captures, because payloads may contain account or player information;
- decompiled commercial source code;
- copyrighted game assets that are not licensed for redistribution;
- personal addresses, email addresses, account identifiers, or player records.

If a secret is committed, deleting it in a later commit is not sufficient. Revoke or rotate the
credential immediately, then remove it from Git history.

## Deployment guidance

- Bind PostgreSQL to localhost or a private network and use an SSH tunnel for administrative access.
- Terminate public HTTP traffic with TLS.
- Run the game server and API under dedicated, unprivileged service accounts.
- Keep environment files readable only by the service user.
- Restrict staff privileges in the database and enforce authorization on the server, not only in the
  client UI.
- Apply operating-system and dependency updates regularly.
- Do not expose a test deployment as a public commercial game service.

## Research boundaries

InfinityServer is intended for controlled interoperability, preservation, and engineering research
with a legitimately obtained client. Security research must remain within systems and accounts the
researcher owns or has explicit permission to test.