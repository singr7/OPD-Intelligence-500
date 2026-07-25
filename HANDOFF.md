# HANDOFF — after Session S13 (Multilingual completion + language QA harness)

> **Operator's current priority (2026-07-22):** the pilot is **deployed live** on
> an on-prem RTX 4090 box with **STT + LLM + TTS all local** (kiosk voice-in via
> Whisper, routing/summaries via Qwen3, read-aloud via a Kokoro `/tts` container —
> zero cloud AI) at `https://opd.radpretation.ai`.
>
> **⚠️ CI is off (2026-07-23, operator).** `.github/workflows/ci.yml` is intact but
> its `push`/`pull_request` triggers are commented out. Run it by hand:
> `gh workflow run ci.yml`. **`make test` locally is the only gate right now** — and
> S13 added `make lang-qa` (also a CI step and a pytest test).
>
> **🚩 Adaptive intake (S-ADAPT) is on `main` but NEVER PROVEN with its flags on.**
> `INTAKE_ADAPTIVE=0` / `NEXT_PUBLIC_KIOSK_ADAPTIVE=0` are the defaults, so `main`
> behaves as the pure-tap kiosk until a human flips them. See "Owed on omen". Unchanged by S13.

**Repo state:** **`main`** — S13 built straight on it (four feature commits + close).
`make test` green: backend **826** (816→826), voice-gw 1, web typecheck+lint clean, 48
conformance. `make lang-qa` clean across [en, hi, mr, te]. **No migration in S13** — mr/te
is content + config, no schema change. Postgres on host port **5433**; voice-gw on 8090.
**Start S14 from `main`.**

⚠️ `make lint` is still **failing on 11 pre-existing unformatted files** (none S13's — the
new code is `ruff format`-clean). Not in `make test`, red a while. Worth one `ruff format .`
commit before it grows.

**One paragraph:** S13 made the pilot speak all **four** languages instead of two. Marathi
and Telugu now fill every patient-facing surface — the whole tree bank (11 trees, 258 unique
strings applied by one `en→(mr,te)` map so a repeated phrase reads identically everywhere),
the kiosk shell, the print slip, the three WhatsApp templates, and the offline read-back. The
mechanism that makes "all four" a fact rather than a hope is two-sided: the tree validator
already enforces per-string completeness within a tree, `KioskLang` makes a missing UI string
a compile error, and the new **`app/lang_qa.py`** harness covers the surfaces neither sees —
plus the failure a completeness check misses (English pasted into an `mr` slot passes "is the
key set?"; it fails "does it contain a Devanagari character?"). `PILOT_LANGUAGES` now lives in
`app/languages.py` as one source of truth. **mr/te are model-drafted and unreviewed** — the
loudest owed item, flagged for S21's clinical + native read, exactly as the hi text has been.

## Next session — S14 (Telephony voice gateway (Exotel) part 1: pipeline)
- Objective: `voice-gw` service — Exotel Voicebot WS ↔ **V1 Gemini Live bridge** (audio
  relay + tool loop) **and** a **V2 STT↔TTS pipeline** path per tier config; per-minute
  audio metering into `usage_events`; barge-in; DTMF fallback; consent line; call-state
  persistence; a local harness replaying recorded audio as a fake Exotel client.
- **Load:** docs 02 §5, 03 §1b.
- **AC:** fake-client e2e intake completes on both V1 and V2 (V1 <1.5s, V2 <3.5s p90 turn
  latency, measured); barge-in works; partial saves on hangup; call cost recorded per intake.
- **S14 inherits four-language text for free** — the tree walker, summaries, read-back and
  BCP-47 map all speak mr/te now, so the voice pipeline should drive them per `Intake.lang`
  without new strings. The one real content gap for voice is `audio` clips (still empty on
  every node; TTS covers it — S7/S21), unchanged by S13.
- **Start from `main`.** First commands:
```
make dev && make migrate && make seed && make test    # expect 826 backend green
make lang-qa                                           # expect clean across [en,hi,mr,te]
```

## Watch out for (S13 fragile edges)
- **`PILOT_LANGUAGES` is the one source of truth** (`app/languages.py`). Adding a fifth
  language means: extend it, add a `SCRIPT_RANGES` entry, add the font (web), translate every
  surface — the harness will then enumerate exactly what is still missing. Do not re-hardcode
  a language list anywhere; the point of S13 was to delete those copies.
- **The tree translations came from a scratchpad `en→(mr,te)` map, not hand-editing** — the
  seed JSON is now the source of truth (the map is gone). Edit the JSON directly; the validator
  and `lang_qa` will catch a broken block.
- **The harness's script check is "at least one character in-script"** — deliberately lenient
  so `38°C ताप` and acronyms (TB, BP, ESAS) pass. It catches a *wholly* English/empty string,
  not a partial mistranslation. Only a human catches the latter (S21).
- **`get_template` still raises on a missing (name, lang)** — that is the feature. If S15 adds
  a template, add all four languages or `lang_qa` (and the first out-of-window send) fails.
- **Telugu has never rendered on a real screen** — `next/font` Telugu subset needs a network
  fetch at build; the local `make dev` web build must succeed for the box to have the font.

## Decisions needed from the human
- **mr/te need a native + clinical review before a patient reads them.** This is the one thing
  S13 cannot self-certify. Until then they are as trustworthy as the hi text has been (i.e.
  structurally sound, not vouched-for). Whoever owns the Alwar clinical sign-off (S21) should
  see the mr/te tree/template/read-back text specifically.
- **Whoever next has the box: "Owed on omen" is still unclaimed** (below) — the only remaining
  reason to doubt anything in `main`. S13 adds the Telugu-render visual check to that list.

## Owed on omen (before adaptive / mr-te face a patient)
- **Telugu kiosk render** — bring the stack up, switch to తెలుగు, confirm the glyphs render
  (not tofu) and the ≥1.6 line-height holds at 200% font scale (doc 04 §4). Cheapest new item.
- **Adaptive on:** flags to `1`, mark 1–2 live-tree nodes `adaptive: true`, re-seed,
  `docker compose up -d --build api web` (**web rebuild required** — the flag is a build arg).
  Provoke a vague answer, a volunteered fact, an unmappable answer. Read `adaptive_report.py`,
  tune node wording. **Rollback is the flags back to `0` + a web rebuild.**
- **Doctor console + consult note on-box** (`/doctor`, `+915550001001`) — the real-Qwen3
  dictation + `_was_said` pass is still owed. `make eval-dictation` wants the same session.

## Backlog additions (S13)
- **mr/te unreviewed** by a native speaker or clinician (loudest; S21).
- **Telugu never seen rendered** — visual proof owed on the box (Owed on omen).
- **No mr/te STT/TTS has hit a live vendor** — fakes + BCP-47 map only, same first-vendor
  caveat every channel carries.
- **Glossary consistency is exact-block only** — pins canonical short forms, not terms inside
  sentences; substring/fuzzy consistency is a later, harder check (pairs with S18's tree editor).
- Carried, unchanged: `make lint` red on 11 pre-existing files (one `ruff format .` clears it);
  the S11/S12 backlog (WhatsApp multi-select single-pick, no live Meta send, conversation-level
  billing → S18, RxPanel silent read error, server-side PDF); adaptive-tree on-box scenario testing.
