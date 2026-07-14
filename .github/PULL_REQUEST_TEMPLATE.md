## Summary

<!-- What changes, and why? -->

## Scope

- [ ] Documentation only
- [ ] Tests only
- [ ] Game server
- [ ] Web API
- [ ] Database / seed data
- [ ] Combat / world state
- [ ] Client integration
- [ ] Deployment / operations

## Validation

<!-- Include commands run, manual checks, captures, or screenshots. -->

```text
cd server
python -m pytest
```

## Security and data review

- [ ] No credentials, tokens, private keys, production addresses, or personal data are included.
- [ ] No raw packet captures, production databases, or decompiled commercial source are included.
- [ ] Authorization remains enforced by the server rather than only by the client UI.
- [ ] New logging avoids secrets and complete packet payloads.

## Protocol fidelity

<!-- For protocol/gameplay changes, cite packet captures, client behavior, tests, or clearly label design choices. -->

- [ ] Not applicable
- [ ] Capture/client-grounded behavior
- [ ] Intentional server design extension, documented below

## Deployment notes

<!-- Migrations, environment variables, restart requirements, compatibility concerns. -->

## Follow-up work

<!-- Anything deliberately left outside this PR. -->