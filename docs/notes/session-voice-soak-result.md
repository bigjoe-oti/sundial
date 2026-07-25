# SESSION-VOICE soak — result (commitment cf070548)

Registered 2026-07-17, due 2026-07-20, closed 2026-07-25 (late — no live session
existed in the window to observe from; the soak needed real traffic, and the
next real session was this one).

Three questions, answered from observed data rather than reasoning about the
code:

## 1. Do fires route to warm sessions? PASS

Entry `fe0c189f` was queued at `07:19:26Z`. No session was live at that moment
(the previous one ended ~4 days earlier). The entry stayed in the queue — it did
not fire into an empty room and it did not drop — and surfaced on the first
session that claimed. That is the designed behaviour, observed end to end.

## 2. Is the claim fresh from real prompts? PASS

`hooks/prompt_submit.py` writes the claim on `UserPromptSubmit`, debounced to
one write per 60s. The observed claim timestamp (`16:28:27Z`) matched the
owner's last real prompt, not session start — so the claim tracks a human
actually typing, which is what it is for.

## 3. Are stale entries surfaced as corrections? PASS

`fe0c189f`'s message text still asked about Qwirva Batch 3, work that had since
completed. `session_speak_pending()` annotated the entry with
`commitment_status: "done"`, so it surfaced as a one-line correction instead of
a live ask. The stale-entry path is the one most likely to make Sundial look
foolish, and it held.

## Observation — recorded, not fixed

The claim TTL is 3600s and only refreshes on a human prompt. A long autonomous
stretch (this session ran hours past the last prompt) therefore reads as
*unclaimed* while the agent is still warm, and a fire arriving in that window
would queue rather than route.

This is not obviously a bug. The claim answers "is a human present to hear
this?", not "is the agent busy?" — and queueing until someone is actually there
is the better failure mode than speaking to an empty terminal. Left as-is
deliberately; revisit only if a real fire is observed arriving late because of
it.
