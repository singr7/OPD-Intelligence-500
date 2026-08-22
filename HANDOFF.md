# HANDOFF — after SESSION-AYUR-3 and SESSION-AYUR-4

**Repo state:** branch `docs-omen-hybrid-and-primus`, `make test` green, exit 0 —
backend **1,947** (was 1,908), voice-gw 25, conformance **135**, typecheck, lint,
android. E2E: `ayurveda` 5, `dictation` 8, `doctor` 12.

**No migration this session.** The nine pending on Omen are unchanged:
`c6e3681f5ce1`, `520d07f0b3e4`, `c063fd91e198`, `efb79a43afb3`, `02571a5c1871`,
`9f2ab41c77d3`, `8ef31aa60c55`, `4ce8cb36a165`, `28e0ff23658b` — applied locally
only, and `make deploy` still does not run migrations.

The ANDROID1/CLOUD1/VOICE1 external-release gate is unchanged and still open.
**docs/18 §0 remains the AWS deploy ledger** — update it at the end of every AWS
deploy and read it before one; the previous SHA it records is `rollback.sh`'s
argument, and `/opt/opd/current` is a symlink over two checkouts (`repo` and
`repo-new`), so derive the live one with
`export SRC="$(readlink -f /opt/opd/current)"`.

**Where the build stands.** Doc 24 is **done**. AYUR-0 stored a system of
medicine, AYUR-1 made the hospital configurable, AYUR-2 gave the ayurveda
department something to ask, and AYUR-3/4 made the console *read* the flags.
There is still one doctor console: it widens the capabilities off the day payload
once and renders from booleans, and the proof that this is a derivation rather
than a fork is that the oncology E2E suites pass **untouched** beside a new
ayurveda one. Three things worth understanding before touching any of it:

- **A `Formulary` is one shelf, not the file.** `get_formulary(scope=...)` is
  cached per system of medicine, so the fuzzy neighbour search physically cannot
  offer a cytotoxic as a did-you-mean in an ayurveda consult. The first cut
  filtered a shared index instead and two existing tests failed on it, correctly.
  If you need "all drugs", stop and ask what for.
- **Capabilities come from the visit's own department row.**
  `facility.capabilities_for_visit` is the one derivation the clinical paths use,
  and nothing accepts a scope or a pack from a request. Keep it that way.
- **The assessment fields are merged by key, and the console queues its saves.**
  Both exist because every commit is a blur, and a doctor moving down the note
  blurs the next field before the last save has answered. Fired concurrently, two
  PATCHes read the stored note before either wrote it and the second reply won —
  the doctor watched their own answer disappear. Do not "simplify" either half.

## Next session — the choice is yours, and doc 24 is not it

Doc 24 has no SESSION-AYUR-5; §8's out-of-scope list (ayurveda check-in
protocols, patient-app ayurveda content, voice-tier ayurveda dialogue, a third
care system) is still out of scope and should stay there.

**If the next move is VaidyaSetu:** S-V1 is now complete — that session was
"execute the doc-24 foundation in THIS repo" — and **S-V2 is the fork**. The
build plan (doc 26) and `docs/vaidyasetu/` are **deliberately not in this
repository**: it is public, and the portal source is the plaintext of the deck
the investor portal encrypts. Both are `.gitignore`d here and tracked in the
private `vaidyasetu` repo, which is where the VaidyaSetu work continues.

**If the next move is the pilot**, the three long-standing non-coding items are
unchanged and still the most valuable things nobody has done: **print a pass on
the real printer** (doc 23 §11), **point M3 at the real `RAD-RENVA-PACS`**, and
**have an oncologist read the research assistant's answers** (asked in seven
consecutive handoffs). After those: **deploy the nine pending migrations to Omen**
and give `make deploy` a migration step.

## Watch out for

- **The E2E suites need a fresh seed and a 30-second gap.** `seed_doctor_demo`
  and `seed_ayurveda_demo` are consumed by their suites — signing is terminal —
  so re-seed between runs. And the API refuses a second OTP for the same phone
  within 30s: when that bites, the token is `undefined`, the console 401s and
  signs itself out, and the *first* thing that fails is an assertion about a name
  in the appbar. That reads as "the console is broken" and is nothing of the
  sort. `ayurveda-console.spec.ts` handles it; the two older suites do not.
- **The API container bakes the source in** (only `seeds/` is mounted), so
  `docker compose up -d --build api` after any backend change or the E2E tests
  the code you had ten minutes ago. This cost real time this session.
- **A response model is a filter.** `MappingOut` silently dropped two fields that
  the record stored correctly and the console then lost on refetch. Two tests now
  pin `PatchIn` and `MappingOut` against the stored shape by set equality — if
  you add a field to the note, they will tell you where else to add it.
- **`test_campaign.py::test_a_thirty_seventy_mix_produces_the_documented_split`
  failed once in a full run and passed alone and on every re-run.** Not touched
  by this session; looks order- or seed-dependent. Worth a look if it recurs.
- **Everything ayurveda is model-drafted and UNREVIEWED** — trees, formulary,
  prompts. `sessions/SESSION-AYUR-4.md` carries the six-item content-review
  checklist. The module demos on `LLM_PROVIDER=fake`; it must not be enabled for
  real patients before a BAMS practitioner has signed those off.
