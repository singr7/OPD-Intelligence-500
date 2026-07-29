# Cancer care companion — three prototypes, one patient

Seeded, responsive prototypes for cancer care. Nothing is wired to real data.

**Start with v3 (`index-v3.html`). It is the one with an actual thesis.**

v1 and v2 are the same mistake in different clothes: a nicer container for
records the hospital already holds. Both are rear-view mirrors — what happened,
what is stored, what to take. Patients already have a filing cabinet.

**v3 — Good Days** starts somewhere else: the hardest parts of cancer are *not
knowing whether what you feel right now is normal or dangerous*, and losing the
ability to plan a life. So it stops being a record and becomes a **forecast**.

| | What it is | Core question it answers |
| --- | --- | --- |
| **v3 — Good Days** | A forecast of how the next 21 days will feel | *Will I be okay tonight, and which days are mine?* |
| v2 — Passage | One river of moments through time | *Where am I in this?* |
| v1 — CareCompass | A contextual home that recomposes | *What do I do in the next ten minutes?* |

## Why a forecast is the right bet

Routine patient-reported symptom monitoring during chemotherapy improved overall
survival by around five months in Basch et al. (JAMA 2017) — better than many
drugs. It fails in practice because patients stop reporting: reporting gives
them nothing back.

So give something back. **Every check-in buys a sharper forecast, and forecasts
are what let you plan a life.** Adherence stops being a compliance problem and
becomes self-interest.

---

# v3 — Good Days

## The signature: your body as terrain

Twenty-one days of a cycle drawn as a landscape. Peaks glow amber (heavy days),
valleys mint (days that are yours). Drag across it. Behind the forecast sits the
dashed line of how your *last* cycle actually went, and the white dots of what
you actually reported — so the forecast is visibly built from your own history,
not handed down.

**Every day has a name, not a number:** Heavy · Tender · Lifting · Guarded ·
Yours · Steady. The vocabulary is the product. "Day 8, burden 3.0" means
nothing. "Guarded" means something you can act on.

## Everything else hangs off the same time axis

Intake, visit prep, records and medicines are all in v3 — but none of them is a
tab. Each one attaches to a day, which is the only reason the app stays simple
while doing more.

**Your intake is already written.** The app holds 34 check-ins, so the form for
2 June is pre-filled from them: nausea peaked 8/10 on day 2, weight down 1.1 kg,
tingling fingertips from day 9. The patient *reviews and corrects* rather than
recalls, and the doctor gets structure instead of "how have you been?" → "fine".
Two rows are flagged **worth raising** because they changed. This is the payoff
of the check-in loop, and the reason the loop keeps running.

**Medicines are drawn on the terrain's own axis.** Not a list — a shape. The
"avoid crowded places" bar sits directly under the amber careful-week band, so
you can see which days ask something of you and which do not. Tap any bar for
the plain-language explanation, tied to the prescription it came from.

**Records** are grouped, arrive automatically from the hospital, and can be
added by camera scan, file, or *forwarded from WhatsApp* — where reports
actually arrive in India. Sharing is inline: pick a person, and the rules
(expires in 14 days, watermarked, you're told when opened, revocable) are shown
as part of the act rather than buried in settings. And the app knows what a
complete file for this diagnosis looks like, so it flags **what is not here
yet** — nobody else is checking.

## The app is not the same product at every stage

The switch in the top bar is the most important control in the prototype.

- **Newly diagnosed** — there is no personal history, so there is no personal
  forecast, and the app *says so*: "typical for AC-T, not yours yet." The
  check-in becomes a **baseline** — the version of you that everything later is
  measured against. Intake is the one form the app cannot pre-fill, and it
  admits that too.
- **In treatment** — the 21-day cycle, as above.
- **Living after** — the cycle rhythm is gone. The rhythm is scans, and the
  forecast shows the thing nobody builds for: **the fortnight before one**.
  Scanxiety is real, named, and the most predictable event left in the year.
  Same machinery, weeks instead of days.

## Three things it does that no cancer app does

**1. It makes the invisible risk visible.** Days 7–10 feel completely fine and
are the most dangerous — counts at their lowest. The app names that week
*Guarded* and says out loud: *"You will feel completely normal this week. That
is the trap."* Feeling fine is not the same as being safe.

**2. Reporting pays you back, immediately.** One tap. If you are inside the
range your own cycles 1 and 2 drew, it tells you so — *"this is exactly the
shape we expected"* — which is most of what people ring a helpline at 3am to
find out. If you are outside it, it does one thing only: puts a human in the
loop, with your own past cycles attached so the nurse sees a change rather than
a claim.

**3. It answers "can I still…?"** The wedding on 20 May lands in the careful
week, so the answer is not *no* — it is **"Go. But go differently"**, with four
concrete adaptations. Your daughter's farewell on 26 May is your best day of the
cycle: *say yes without hedging*. This is the difference between an app that
manages your cancer and one that helps you live your life alongside it.

## Why it is dark

Not fashion. The people who open this are checking it at 3am, nauseated, in bed,
next to someone asleep. A white medical UI is hostile at that hour.

## Demo script (2 minutes)

1. Read the lead sentence. That one line is the product.
2. Drag across the terrain into the amber **careful week** — watch the readout
   explain a risk you cannot feel.
3. Tap **Very heavy** in the check-in → *"exactly the shape we expected."*
   Now tap **Unbearable** → deviation, and a human gets involved.
4. Open the **wedding**. Read "Go. But go differently."
5. Ask about **Wednesday 21 May** — it answers, and offers a better day.

## Safety posture

Forecasts how days are likely to *feel*, from the patient's own logs plus the
known timing of AC-T. It says nothing about whether treatment is working, never
changes a dose, and escalation is always to a named human being. Provenance is
listed on the page under "How it knows".

---

# v1 and v2 — the earlier attempts

Kept for comparison. Both share `seed.js`, so all three show the same patient.

| | **v1 — CareCompass** (`index.html`) | **v2 — Passage** (`index-new.html`) |
| --- | --- | --- |
| Paradigm | Places you navigate between | One continuous passage through time |
| Navigation | Nav rail + 5 bottom tabs | **None.** Lenses change what the river shows |
| Home | A contextual surface that recomposes per situation | There is no home; there is *now* |
| Detail | Opens a sheet over the page | Expands in place, nothing ever covers anything |
| Feel | Composed, reassuring, familiar | Fluid, editorial, quiet |
| Best when | Users want to *go and get* something | Users want to know *where am I in this* |

v1 is the safer, more conventional product. v2 is the argument that treatment is
fundamentally a temporal experience, and that an app built on that one idea is
both simpler and more human.

## Run them

```
cd web && npm run dev
# v1 → http://localhost:3000/prototype/carecompass/
# v2 → http://localhost:3000/prototype/carecompass/index-new.html
```

Or open either HTML file directly — plain HTML/CSS/JS, no build step, no
dependencies. Each version links to the other in its top corner.

---

# v1 — CareCompass

## The core design principle

The home screen is **not a dashboard**. It is a contextual care surface: a
deterministic rules layer picks the patient's current situation and the home
shows only the next meaningful actions for it. Same data, three completely
different homes.

Use the **Context** switch in the top bar to move between them. In production
these come from appointment/check-in status, discharge events and elapsed
recovery time, the medication schedule, reported symptoms, consented location,
and outstanding prep tasks — not from a manual toggle. **"Why am I seeing this?"**
on the home screen shows the signals behind the current choice.

## Demo script (about 4 minutes)

1. **At hospital** — check in; tick off the last prep item and watch the card
   resolve to "You're fully prepared."
2. **Just discharged** — the home reorganises around what changed in the
   medicines, red flags, and the ride home.
3. **At home** — hydration, the faces scale and the body map, tonight's medicines.
4. **Records** — tick two documents → build a second-opinion bundle (expiry,
   watermark, open-notification, recorded consent).
5. **Medicines** — tap Capecitabine for a plain-language explanation that stays
   tied to the prescription it came from.
6. **Care Team** — flip a caregiver permission and watch the access history.

The **Laptop / Phone** switch previews the handset layout without leaving the
desktop. Layout is driven by container queries, so the preview and a real phone
run the same ruleset.

## Three deliberate aesthetic bets

- **The cycle ring** — segmented arc, cycle 3 of 6. The signature element, the
  one number a patient on chemotherapy actually holds in their head.
- **A home that recomposes** rather than a home that filters.
- **Body map + faces scale** for symptom capture, so logging never needs reading.

Type pairs a humanist serif for the emotional headline moments with the house
sans for everything functional.

## Design language

Extends `docs/04-UIUX-GUIDE.md` §1 — clinic green `#0E7C66`, marigold `#E2901F`,
plus a warm sand/clay layer for patient surfaces. Deliberately **not** the
purple-on-white SaaS look ruled out by the anti-generic clause.

## Safety rules the prototype already encodes

- Clinical explanations always show their source prescription, report or
  instruction (`.source` blocks).
- AI-written summaries carry a **Draft · not yet confirmed** badge until a
  clinician verifies them.
- The app explains medicines; it never alters a dose. Stated on the Medicines
  screen in as many words.
- Sharing is consented, watermarked, expiring, and written to an access history
  described as un-editable.

---

# v2 — Passage

## The one idea

Treatment is not a set of places. It is one continuous passage through time, and
the patient's real question is never *"which tab holds this?"* — it is *"where am
I in this, and what comes next?"*

So Passage has **no tabs, no pages and no modals**. It is a single river of
moments: what is behind above you, the present at rest in the middle, what is
ahead below. You land on now. Scrolling up is memory; scrolling down is the
plan.

The payoff is that everything becomes one type of object — a moment in time. A
scan, a dose, a milestone, and *"Rahul opened your blood counts at 09:12"* are
all the same shape. Consent and access history stop being a settings screen and
become part of the story, which is exactly what they are.

## What replaces navigation

**Lenses** — Everything · Treatment · My body · Medicines · Reports · Care
circle. They change what the river *shows*, never where you are. You never
leave, so you never have to find your way back.

Everything else is ambient:

- **The compass** (left on desktop, bottom bar on mobile) names the date you are
  looking at and whether it is behind, now or ahead. It reports position; it is
  not a control.
- **The light shifts** — warm sand over the past, green over the present, cool
  over what is ahead. You feel where you are before you read it.
- **Focus falloff** — moments away from the centre soften and desaturate. The
  river always tells you where to look.
- **The magnet** appears only when the present scrolls out of view.
- **"Something's not right"** is permanently reachable, never in the way. Red
  flags and the helpline are one tap from anywhere, by design.

## Demo script (about 3 minutes)

1. You land on **now** — day 2 after chemotherapy. Log how today feels, add a
   glass of water, tick a dose. All inline; nothing opens.
2. **Scroll up** into the past. Watch the light warm and the compass count
   backwards. Open *CT chest* — it expands in place, draft badge and all — then
   *Second opinion* expands again *inside* it. Two levels deep, nothing covered.
3. **Scroll down** past now into what is ahead. The light cools. Tonight's
   tablets, the temperature checks, Cycle 4, radiation, and the five quiet years.
4. Switch to the **Care circle** lens: the whole river becomes who saw what, and
   Rahul's permissions are editable right where the event happened.
5. Hit **Something's not right** from anywhere.

## Files

| File | What it holds |
| --- | --- |
| `index.html` · `styles.css` · `app.js` | v1 — CareCompass |
| `index-new.html` · `styles-new.css` · `app-new.js` | v2 — Passage |
| `seed.js` | Shared seeded data, shaped to the canonical patient model |

## Which to build

They are not mutually exclusive. v2's river is a genuinely better answer to
*"where am I in this?"*; v1's contextual home is a better answer to *"what do I
do in the next ten minutes?"* If v2 tests well, the honest hybrid is Passage's
river as the spine with v1's context engine deciding what the now-moment says.

## Not built (deliberately)

Auth, real records, FHIR APIs, offline storage, push, biometrics, tenant
isolation. If this look tests well, the route to production is a PWA with a
Capacitor wrapper for Play Store distribution, notifications and offline
documents — reusing this markup, not rewriting it.
