# SESSION-GL1 — The switchboard: channel enablement, capacity, runtime provider config

**Date:** 2026-07-26 · **Scope ref:** [doc 12](../docs/12-GO-LIVE-PLAN.md) §1/§4/§7 → S-GL.1
(Phase 1 of the go-live track; doc 06's tail carries the phase table)

The first session of the kiosk-first go-live. Its whole subject is a sentence from doc 12 §1:
before this, there was **no honest "off"**. A patient who messaged the hospital's WhatsApp
number reached a bot that tried and failed per message; a number was "not live" only in the
sense that nobody had announced it. That is also what made going live conditional on Meta and
Exotel — there was no way to open the kiosk and leave the rest shut.

## Acceptance criteria checklist

- [x] **With every channel but kiosk disabled, a WhatsApp inbound and a phone call are both
      refused politely and audibly, nothing 500s, and the kiosk is untouched.** Proven at three
      levels: unit (`tests/test_channels.py`), the phone applets against a real database
      (`voice-gw/tests/test_channel_closed.py`), and end to end against a live stack with the
      switching done through the console the way an operator would
      (`web/e2e/channels.spec.ts` → *"kiosk-first: closing the other three from the console
      shuts them politely"*).
- [x] **Entering + testing credentials in the console makes a vendor live with no restart.**
      The overlay is read per request on a 10s TTL and the registry rebuilds a provider whose
      credentials changed (`test_changed_credentials_rebuild_the_provider_rather_than_reusing_it`).
      "No restart" is honest rather than magic — see *What I would flag* below.
- [x] **A test fills the phone seat share and a kiosk session is still admitted.**
      `test_phone_cannot_starve_the_patient_standing_at_the_kiosk`.
- [x] **Campaign dry-run at 30/70 produces the documented split**, deterministic per patient id
      (`tests/test_campaign.py`, five tests).
- [x] Credentials are **write-only over the wire** and encrypted at rest; `.env` stays the floor.
- [x] `make test` green: backend **1156** (was 1082), voice-gw **25** (was 22), web typecheck +
      lint + 48 conformance, Android 6. `make lang-qa` clean across [en, hi, mr, te].
      **`make lint` green for the first time** — the carried item is closed.
- [ ] **The seat share is not wired into the live voice path.** The controller takes it, the
      document configures it, the console shows it, and it is proven by test — but routing an
      over-share call *down its ladder* is still S-OSS.2's item ("wire `AdmissionController` +
      `ladder_for()` into voice-gw"), unchanged by this session. Said plainly rather than
      quietly claimed.

## What was built

### 1. The channel document (`app/tiers.py`, `channel_configs`, `app/channels/store.py`)

The **third instance** of the versioned draft→publish→resolve pattern, deliberately identical
to the second rather than a new shape: `parse_tier_config` is the only constructor,
`config/tiers.yaml` is the floor, exactly one published version, publishing an older one is a
rollback.

One document rather than a row per channel, for the same reason the protocol bank is one: the
checks that matter are **document-wide**. A channel cannot reserve more GPU seats than the box
has; a campaign mix must sum to 100. A per-channel editor would pass row-level validation and
fail those at 6pm when beat launches the campaign.

`enabled` and `max_concurrent` join `ladder`, and `campaign.mix` joins `admission`.

**The one place this seam differs from the tree seam, and it matters:** a published row that
does not parse falls back to the file's *open* channels, never to closed. A tree that fails to
parse costs a patient slightly older questions. A channel document that fails to parse decides
whether anything answers at all, and a bad publish that shut the OPD would be a worse outage
than the one it was trying to prevent.

### 2. Two facts, kept apart (`app/channels/state.py`)

A channel is open only if it is **switched on** *and* **ready**, and those are computed
separately on purpose:

- The **switch** is the operator's decision and lives in the published document.
- **Readiness** — whether Meta or Exotel is actually provisioned — is computed from settings
  and **cannot be asserted from a console**.

That inversion is doc 12 §7's note read strictly: *going live must not depend on remembering to
switch things off*. A hospital that forgets to close WhatsApp still gets a closed WhatsApp,
because no credentials means not ready and not ready means closed regardless of the switch. It
is also what lets the console say **which of the two** is wrong, instead of one ambiguous dot.

### 3. The gates

At each channel's entry point, on **start** and never mid-flow — closing a channel means "take
no new ones", not "abandon whoever is mid-sentence":

| Channel | Where | What a patient gets |
|---|---|---|
| kiosk | `POST /kiosk/start` | 503 + `code: channel_closed` + the line in her own language |
| app | `POST /patient/intake/start` | the same; her care file, queue and reminders keep working |
| whatsapp | the Meta webhook | still 200 (a non-200 makes Meta redeliver forever), one civil reply per thread per window, and **no bot logic runs** |
| phone | both voice-gw applets | the call is answered, the line is spoken in her language, and it hangs up — **without taking consent** for an intake that will not happen |
| campaign | `dial_due_calls` | dials nobody; queued rows keep their attempts, because the channel is expected back |

The notices are authored in all four languages and `make lang-qa` now checks them the way it
checks the trees — it is the string most likely to rot, because nobody sees it while everything
is working.

### 4. Vendor credentials in the console (`app/providers/secrets.py`, `runtime.py`, `probe.py`)

The first secret this codebase keeps in its own database, under three rules that are structural
rather than remembered:

1. **Write-only.** No route returns a credential; the test asserts it against the whole
   serialised response rather than field by field, so a field added later cannot quietly open a
   read path.
2. **Only this vendor's fields.** A stored row may write the credential fields named in
   `CREDENTIAL_FIELDS` and nothing else — a compromised console cannot repoint `database_url`,
   enable `otp_debug_echo`, or select a different vendor. Supplying a vendor's credentials and
   *choosing* a vendor stay different acts.
3. **`.env` is the floor.** Clearing returns the box to the environment, and clearing
   **hard**-deletes: a soft-deleted secret is a live credential still sitting in a table after
   somebody decided it should not be.

Fernet from `cryptography` (a new dependency — the alternative was assembling a construction out
of `hashlib`, which is what the handoff warned against). The key is not in the database.

`POST /admin/providers/{name}/test` does one real **read** against the vendor — Meta's
phone-number metadata, Exotel's account; neither sends a message or dials anybody — and keeps
the vendor's own error verbatim. "Meta: Error validating access token: Session has expired" is
the whole value of the button.

### 5. The campaign mix (`app/campaign.py`)

Doc 12 §1 refuses the "50% kiosk, 25% app" framing because a channel is chosen by the patient.
The D-1 campaign is the exception — it decides who we *invite* on what — so it is the one place
a percentage is an instruction. Deterministic in the patient id, because a coordinator re-runs
the dry run before the evening launch and a moving split would mean the list she checked is not
the list that went out. A closed channel's share is **redistributed**, not dropped.

### 6. The Channels tab, and per-channel GPU seats

Console's first tab (it answers "can a patient reach us at all"). `AdmissionController` gained
per-channel shares within the global cap; `config/tiers.yaml` gives phone 6 of 12, because
twelve calls can take every seat and the patient standing at the kiosk is the one who cannot be
rung back.

## What rendering it found (and a test would not have)

- **A false green.** The pilot box runs `ENV=local` — it has to, with no Meta or Exotel account
  to satisfy `assert_production_safe` — so a `fake` provider counted as ready and the tab read
  "WhatsApp · Open · configured" off a box where messaging goes nowhere. Precisely the failure
  the tab exists to prevent. Readiness now carries a note that travels with the answer, and the
  row says *"running the fake provider — no real vendor is connected"* **in place of**
  "configured", not under it.
- **A race.** A `yield` dependency's cleanup runs *after* the response is sent, so a client that
  publishes the version it was just handed can beat its own draft to the database. The e2e hit
  it on its first run; an operator double-clicking would too. Both channel writes now commit
  explicitly.

## Decisions worth recording

- **The shipped `config/tiers.yaml` changes nothing.** Every channel stays `enabled: true` and
  the campaign mix is **commented out**, not set. Absent means "ring everyone we have a number
  for", which is what the campaign did before. Choosing a split changes who a hospital rings
  tomorrow evening; that is a decision an operator makes in the console, not one they inherit
  from a file they upgraded past. The go-live act is three taps in the console, and the
  readiness rule means the two vendor channels are dark on the box regardless.
- **Fake counts as configured on a local box, but says so.** Closing every channel when
  `messaging_provider=fake` would make the dev stack and the demo useless. The caveat is
  surfaced instead of the behaviour being changed.
- **`SECRETS_KEY` derives from `JWT_SECRET` when unset** rather than refusing to boot, so the
  pilot box needs no redeploy to use the feature. The cost is real and recorded: the two secrets
  are coupled, `key_id` on every row makes a rotation say *"entered under a different key, set
  them again"* instead of failing as though nothing were stored, and the console shows a banner.
  Setting it explicitly is the right end state (HANDOFF).

## What I would flag to the operator

- **"No restart" means "within about ten seconds".** Three processes read the credential overlay
  (api, voice-gw, beat) and each re-reads on a TTL rather than listening for an invalidation
  message it could miss while restarting. This is a deliberate trade — a missed message is a
  vendor that silently stays off — but it is not instantaneous, and the console says so.
- **The test button proves the credential, not the channel.** A green test means Meta answered
  us; it does not mean an approved template exists, or that the number is subscribed to our
  webhook. Those are still first-contact items for S-GL.3.
- **Nothing here has been run on the box.** Every screenshot is a local dev stack. The Channels
  tab against real data — including whether the fake-provider note reads right on the pilot box
  — is an S-GL.3 item, and it is now the *first* tab an operator sees.

## Files

New: `backend/app/channels/{__init__,state,store}.py`, `backend/app/providers/{secrets,runtime,probe}.py`,
`backend/tests/{test_channels,test_provider_credentials}.py`, `voice-gw/tests/test_channel_closed.py`,
`web/app/(admin)/admin/_components/ChannelsTab.tsx`, `web/e2e/channels.spec.ts`,
migration `2c978d44c900` (`channel_configs`, `provider_secrets`).

Changed: `app/tiers.py` (the document), `app/providers/local_oss/admission.py` (shares),
`app/providers/registry.py` (credential fingerprints), `app/campaign.py` (the mix),
`app/{admin,lang_qa,config,main}.py`, `app/routes/{admin,kiosk,patient,whatsapp}.py`,
`app/models/content.py`, `app/whatsapp/conversation.py`, `voice-gw/gw/{call,reception}.py`,
`config/tiers.yaml`, `web/app/(admin)/admin/_lib/api.ts`, `web/.../{Console,adminStyles}.tsx`.
