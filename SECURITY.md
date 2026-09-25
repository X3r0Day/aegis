# Security Policy

## Reporting a vulnerability

Please do not open a public issue for security problems. Use GitHub private
reporting instead:

https://github.com/X3r0Day/aegis/security/advisories/new

Include the version or commit, the exact request or input that triggers the
issue, and what you expected to happen. If you need to attach traffic, strip
any real keys and personal data first.

This is a small project, so expect an initial reply within a few days. There is
no bug bounty.

## What is in scope

- Bypasses of the prompt injection guard, for example inputs that get forwarded when they should be blocked.
- Bypasses of the abuse enforcement, for example clients that keep working after a block.
- Authentication and session problems in the console.
- Ways to make the agent monitor take actions it should not take, or to make it act on forged state.
- Server side request forgery or key leakage through the upstream settings.
- Problems in the Docker setup that expose the database, logs or models.

## What is not in scope

- The model checkpoints themselves, they come from the Laya project.
- The demo API and the scenario bench, they are deliberately artificial and are not meant for production.
- Missing hardening on a deployment that ignores the defaults, for example exposing the console directly to the internet without a proxy or TLS.
- Findings that need an already compromised machine or a modified local database.

## Deployment notes

- The console is the only authenticated surface. The machine endpoints under `/v1/` are meant to sit behind your own network or gateway.
- Console settings and decisions live in SQLite at `guard/data/aegis.db`. Treat that file as sensitive: it holds the password hash, the session secret and the decision history.
- The upstream API key is stored in the database when set from the console, otherwise it is read from the environment. Never commit it.
- Set the console password on first run and change it from Settings if it was shared.
- Run the container or the process as a user that only has access to the directories it needs.

## Supported versions

The `master` branch is the supported version. Fixes land there first.
