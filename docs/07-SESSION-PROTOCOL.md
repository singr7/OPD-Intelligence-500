# 07 — Session Protocol (the ritual)

This file governs **every** build session. It exists so each session is self-sufficient, token-efficient, and leaves the repo in a state a fresh context can pick up in minutes.

## 1. Files that carry state between sessions

- **`HANDOFF.md`** (repo root) — the *only* file the next session must read first. Overwritten at the end of every session. ≤120 lines.
- **`sessions/SESSION-NN.md`** — permanent log per session (append-only history).
- **`STATE.md`** (repo root) — one-page living map: what's built, what's stubbed/faked, env vars needed, how to run. Updated only when it changes.

## 2. Session Start Prompt (paste this to Claude Opus verbatim)

```
You are executing Session NN of the OPD Intelligence Platform build.
Ritual — do these in order before writing any code:
1. Read HANDOFF.md, then STATE.md.
2. Read your session's entry in docs/06-BUILD-PLAN.md and ONLY the docs listed
   in its "Context to load" line. Do not read other docs unless blocked.
3. If frontend work is in scope, docs/04-UIUX-GUIDE.md is mandatory and its
   anti-generic clause (§5) applies to every screen you build.
4. Run `make dev && make test` to verify the baseline is green. If it is not,
   fixing it is your first task and must be noted in the session log.
5. Restate your session's acceptance criteria as a checklist in your own words,
   then begin.
Working rules:
- Commit after each coherent unit (feature/fix/test), imperative message,
  prefixed "S NN:". Never leave >45 min of work uncommitted.
- Write tests alongside code; fakes from the provider layer, never live vendor
  calls in tests.
- Stay inside this session's scope. Discovered work goes to HANDOFF "Backlog",
  not into this session.
- Token discipline: don't re-read files you've already read; don't paste whole
  files into your reasoning; use targeted search; prefer editing over rewriting;
  summarize long tool outputs once and refer back to your summary.
- If you hit ~70% of the context window, stop adding scope: finish the current
  unit, complete the closing ritual early, and note remaining items in HANDOFF.
Closing ritual (mandatory, in order):
1. All AC checklist items pass, or unmet ones are explained in the log.
2. `make test` green; Playwright screenshots taken for any new UI and
   self-critiqued against docs/04 §5.
3. Write sessions/SESSION-NN.md from sessions/_TEMPLATE.md.
4. Overwrite HANDOFF.md (template in _TEMPLATE.md) — write it for a stranger.
5. Update STATE.md if anything in it changed.
6. Final commit "S NN: session close — <one-line summary>" and stop.
```

## 3. Rules for the human operator

- One session per conversation. Do not continue a session in a stale context; start fresh and let the ritual reload state.
- Between sessions, skim `HANDOFF.md` — 2 minutes — and resolve any "Decisions needed" items before starting the next session (answer them in your first message).
- Sessions may be split (NNa/NNb) if context runs out; the closing ritual still applies to each part.
- Never let two sessions run in parallel on the same branch.

## 4. Quality gates (apply to every session)

- No secrets in code — `.env.example` maintained; real values only on server.
- Every external call behind the provider layer; every provider has a fake.
- Every clinical write audited; every patient-facing string in all active languages (or explicitly logged as pending in HANDOFF).
- Anything downgraded/stubbed is registered in `STATE.md → Stubs & fakes` so it can never be silently forgotten.
