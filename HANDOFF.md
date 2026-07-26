# HANDOFF — after Session S17 (Check-in engine)

> **Operator's current priority (2026-07-22):** the pilot is **deployed live** on
> an on-prem RTX 4090 box with **STT + LLM + TTS all local** (kiosk voice-in via
> Whisper, routing/summaries via Qwen3, read-aloud via a Kokoro `/tts` container —
> zero cloud AI) at `https://opd.radpretation.ai`.
>
> **⚠️ CI is off (2026-07-23, operator).** `.github/workflows/ci.yml` is intact but
> its `push`/`pull_request` triggers are commented out. Run it by hand:
> `gh workflow run ci.yml`. **`make test` locally is the only gate** — plus `make lang-qa`.
>
> **🚩 Adaptive intake (S-ADAPT) is on `main` but NEVER PROVEN with its flags on.**
> `INTAKE_ADAPTIVE=0` / `NEXT_PUBLIC_KIOSK_ADAPTIVE=0` are the defaults. Unchanged by S17.

**Repo state:** **`main`**, last commit `S 17: session close`. `make test` green: backend
**1071** (was 932), voice-gw **22**, web typecheck+lint+**48** conformance, **Android 6 JVM**.
`make lang-qa` clean across [en,hi,mr,te]. **Migration `ae3caebf5e9a`** (check-in plan anchor +
personalisation, check-in delivery/grading columns) — run `make migrate` before anything.
Postgres on host port **5433**; voice-gw on **8090**. **The mainline sequence resumes at S18.**

⚠️ `make lint` still fails on the **same pre-existing unformatted files** (none of S17's — new
Python is `ruff format`-clean). Not in `make test`. Still worth one `ruff format .` commit.

**One paragraph:** S17 closed the loop the whole system was for. A signed consult note now
drafts a follow-up: the regimen family is chosen deterministically from the formulary **class**
of the drugs the doctor prescribed, the days and question sets are copied from an authored
protocol bank, and the LLM is allowed to rewrite the covering message and **nothing else** — a
model that adds a day, drops a rung or swaps a question set gets a plain four-language message
and no schedule change. The doctor approves in one tap, which is the only thing that turns a
draft into messages. Delivery walks WhatsApp → voice → SMS, advancing on **silence** rather than
on a failed send, and defers out of 21:00–08:00 without consuming an attempt. Answers are graded
by the **S4 red-flag rule language** over the questions as they were *asked* (frozen on the row),
so no model decides a check-in grade and a `free_voice` answer cannot fire one; the one LLM in
the path may raise a green to an amber and can neither produce a red nor lower anything. A red
answer ends the check-in on the spot and alerts the doctor who signed plus the coordinator — the
nurse who rings her can ask the rest. `Checkin.responses` finally has a writer, so S9's symptom
trendline lights up.

## Next session — S18-late (Admin console remainder)

- Objective: doc 06 S18-late — visual tree node editor, red-flag rule editor, **protocol
  template editor**, editable message-template registry, voice-pack upload, slot-template
  editor, node-level abandonment report, tier-mix what-if. (The analytics dashboard, tree
  publish→live, price book and cost-guard shipped early as S18E — see `sessions/SESSION-18E.md`.)
- **Load:** doc 03 §10/§11, doc 02 §8, `sessions/SESSION-18E.md`.
- **AC:** a non-technical user edits a tree option, publishes, and sees it live on the kiosk
  with no deploy; what-if recompute matches a hand calculation on fixture data.
- **What S17 gives it:** `GET /admin/protocol-templates` is no longer a `{deferred}` marker — it
  returns the real bank and the console renders it read-only. Making it *editable* is the S18
  item, and it wants the bank in a **table** first: today `seeds/protocols.json` is loaded and
  validated once at boot (`app.checkins.protocols.get_bank`, `@cache`). Move it the way S4's
  trees moved (file as the seeded floor, DB as the published source) and keep `parse()` as the
  only constructor — every guarantee in the session hangs off that validator running.
- **Start from `main`.** First commands:
```
make dev && make migrate && make seed && make slots   # 12 slot templates -> ~800 slots
make test                                             # 1071 backend / 22 voice-gw / 48 web / 6 android
make lang-qa                                          # expect clean across [en,hi,mr,te]
make checkin-demo                                     # a plan, a red D+2, a pending D+7
```

## Watch out for (S17 fragile edges)

- **A grading rule is a `app.trees.rules` expression and is validated against the question
  *types*.** Editing `seeds/protocols.json` by hand is safe only because `parse()` refuses an
  orphan question set, a green rule, a rule over `free_voice`, a duplicate day, an uppercase
  keyword (which would silently never match) and a tied precedence. Never read the JSON
  directly — go through `get_bank()`.
- **`Checkin.asked` is a snapshot and is what answers are validated against.** Re-authoring the
  bank must not change what a patient was asked last week. Do not "simplify" it into a lookup.
- **The escalation is synchronous with the answer**, not a job. `grading.answer_one` is the one
  entry point every channel uses; adding a second write path for answers would give you two
  places where a red does or does not ring a phone.
- **`app.checkins.plan.draft_from_dictation` swallows everything** (it runs inside
  `dictation.sign`). A bug in drafting shows up as *no plan*, not as an error — check the api
  log for `check-in plan drafting failed` before assuming a protocol did not match.
- **Quiet hours live in `app.checkins.window`, not in the beat schedule.** `opd.checkins.send`
  fires every 10 minutes round the clock on purpose; do not "optimise" it to daytime-only, or
  the timezone of the box becomes the only thing protecting a patient's night.

## Decisions needed from the human

- **The check-in protocol bank needs an oncologist.** Six regimen families, seven question sets,
  41 grading rules — all model-drafted, none reviewed. It is the first content in this system
  that **rings a doctor's phone at a threshold nobody has signed off** (fever `yes`, temp ≥38,
  5 vomits, orthopnoea…). Worth pulling forward from S21 for this file alone. *(new)*
- **Is the LLM assist allowed to escalate free text to red?** S17 says no (it may only raise
  green→amber; a nurse reads the sentence). If the clinical view is that "coughing blood" typed
  into the palliative free-text box must ring a phone, that is a one-line change plus a much
  louder prompt — but it makes escalation depend on the transcriber. *(new)*
- **A live Exotel number + creds** — still blocking *three* proofs now: the S14 intake bridge,
  the S15 receptionist/campaign, and S17's voice rung. *(carried)*
- **Who is the coordinator on `COORDINATOR_PHONE`** — S17 now sends them every red check-in
  alert, so this is no longer only about the whisper handoff. *(carried, sharpened)*
- **Does the app go on the Play Store, or sideload at the OPD desk?** *(carried)*
- **mr/te still need a native + clinical review before a patient reads them** (S21). S17 adds
  the whole protocol bank to that pile. *(carried)*

## Owed on omen (before the pilot's continuity loop faces real use)

- **One check-in, end to end, on the box** — `make checkin-demo`, then
  `python -m app.worker opd.checkins.send`, and answer it from a real WhatsApp thread. Nothing
  in S17 has ever reached a handset. *(new)*
- **A real Qwen3 personalisation** — on a fake stack `checkin_personalize` has no canned reply,
  so every demo message is the plain fallback. Worth one look at what the box's model writes,
  because that text is what a frightened person reads. *(new)*
- **The app on a real handset** — everything is proven on an emulator. *(carried)*
- **Live Exotel smoke, both applets** *(carried)*; **the campaign for one evening on real
  numbers** *(carried)*; **phone-on-GPU contention** *(carried)*.
- **Admin console visual pass** (S18E) — walk the tabs; the protocols tab is new. *(carried)*
- **Telugu kiosk render** *(carried)*; **adaptive on** *(carried)*; **doctor console + consult
  note on-box** *(carried)*.

## Backlog additions (S17)

- **A voice-gw check-in applet** — `WS /exotel/checkin`, the third handler after intake (S14)
  and receptionist (S15). Until it exists `EXOTEL_CHECKIN_APPLET_URL` stays empty and the ladder
  skips straight to SMS.
- **Check-ins in the Android app** — a fourth delivery channel that already knows the patient
  and needs no 24h window. Cheapest channel there is; suggest S18-late.
- **Merge a doublet's question sets** — carboplatin + paclitaxel follows the taxane protocol
  only; the platinum GI questions are dropped. Wants a merge rule an oncologist signs off.
- **A canned `checkin_personalize` / `checkin_triage` fake reply**, so a local demo shows the
  feature rather than its fallback (the pattern `dictation_map` and `receptionist` already use).
- **A real task table** for "immediate call task" — today a red is the nurse-queue entry plus an
  SMS; there is nowhere to record that someone actually rang her, only that she was resolved.
- **The app's chemo calendar could count real cycles now** — `CheckinPlan.next_cycle_at` and the
  protocol's `cycle_days` exist; `patient_app.chemo_calendar` still counts `chemo_review`
  appointments (S16 stub). Small rewire, real improvement.
- **`Checkin` on the patient timeline** — doc 03 §9 says "all visible on patient timeline"; the
  doctor card reads `Checkin.responses` for its trendline but the grades and reasons are not on
  the timeline yet.
- Carried, unchanged: `make lint` red on pre-existing files; mr/te unreviewed (S21); Telugu never
  seen rendered; admin console never seen on a screen (S18E); report photos in the care file;
  booking from the app; Play Store signing; `TokenStore` at rest; appointment waitlist (S18-late);
  language detection from the caller's greeting; campaign observability; tier-mix what-if; V1
  continuous caller-audio streaming; surface STT confidence instead of the energy proxy; tune
  VAD/DTMF thresholds on real Alwar telephony.
