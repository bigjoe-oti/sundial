# Universal Backend Design — Sundial v3

**Date:** 2026-08-26
**Status:** Approved (gatekeeper-audited plan: `docs/superpowers/plans/2026-08-26-v3-hermes-universal-plan.md`)

## The generic hook protocol — THE portability contract

Any agentic runtime that can exec a program with stdin can wear Sundial.
The protocol is deliberately dumb:

```
stdin  (JSON, may be empty/corrupt — must never matter):
{
  "event": "session_start" | "prompt_submit" | "tick",
  "prompt": "<human prompt text, for prompt_submit events>",
  "session_id": "<opaque runtime id>",
  "machine": true|false        # caller-asserted; absent → marker scan decides
}

stdout: the context block to inject (<sundial>…</sundial> etc.), or nothing.

exit code: ALWAYS 0. A clock bug can never block a session/prompt/tick
(fail-safe by construction — the v1 rule, unchanged).
```

## Human vs machine events

Machine re-invocations (cron-injected context, task notifications) MUST NOT:
disarm awaiting-reply asks, stamp last-prompt state, or refresh the session
claim. Detection order:

1. If `"machine": true` is asserted by the caller → machine event.
2. Else if the prompt head contains `<task-notification>` or
   `[SYSTEM NOTIFICATION` (the Claude Code markers, kept for parity) →
   machine event.
3. Else → human event.

## Backend selection

`SUNDIAL_BACKEND` env override (`macos` | `linux` | `headless`), else
platform probe. Every PresenceBackend sensor method returns None when the
underlying capability is missing; None softens, never blocks. Headless has
no sensors at all: wall ceilings drive every ladder, delivery goes to
webhook (`SUNDIAL_WEBHOOK_URL`) or log.

## What does NOT migrate into core/ (v3 scope)

`lib/core.py`'s engine stays where it is — incident-hardened and covered by
298 tests. `core/` is the namespace shell for backends/adapters only;
physical consolidation is deferred to v4 with its own migration spec.
