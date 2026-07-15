# 04 — UI/UX Guide (mandatory reading for every frontend session)

This product's UI must **not** look like a default AI-generated dashboard (no gradient hero cards, no cramped gray admin tables, no purple-on-white SaaS look). It should feel like a **calm, warm, government-hospital-meets-modern-clinic** system: trustworthy, spacious, voice-forward, designed for stressed families and hurried clinicians.

## 1. Design tokens (derived from the approved kiosk demo — use these)

```css
--primary:      #0E7C66;  /* deep clinic green — trust, calm */
--primary-d:    #0A5A4A;
--primary-soft: #E1F0EB;
--accent:       #E2901F;  /* warm marigold — action, tokens, highlights */
--accent-soft:  #FBEBCF;
--danger:       #C73E3E;  --danger-soft:#FBE7E7;
--ink:          #16302B;  --ink-soft:#5C6E69;
--bg:           #F1F5F3;  --surface:#FFFFFF;  --line:#D9E4DF;
--radius:       22px;     --shadow:0 10px 30px rgba(16,48,42,.08);
```

Typography: patient surfaces use a rounded humanist sans with excellent Indic support — **Noto Sans / Noto Sans Devanagari / Noto Sans Telugu** (self-hosted, subset). Doctor/staff surfaces may pair it with a compact grotesk for data (e.g., **IBM Plex Sans**) — numbers in tabular lining. Display moments (token numbers, board) use extra-bold at very large sizes; token numbers are the product's signature visual element: huge marigold numerals on deep green, like a train-platform board — legible at 8 meters.

## 2. Rural-first UX laws (patient surfaces)

1. **Audio is the primary channel; text is the caption.** Every question auto-plays audio; every screen has a replay button; nothing requires reading to proceed.
2. **One decision per screen.** Never two questions. Progress shown as simple dots + "3 of 8" spoken aloud.
3. **Touch targets ≥64px**, spacing ≥16px; entire option card is tappable, not the radio.
4. **Icons carry meaning**: every option has a friendly, culturally neutral duotone icon (consistent custom set — belly, chest, fever thermometer, food plate, sleep moon…). No abstract metaphors, no medical jargon icons.
5. **Language switch is always one tap away**, labeled in its own script (हिंदी / मराठी / తెలుగు / English), never behind a settings gear.
6. **Numbers over sliders**: duration = big steppers with spoken units ("2 din se"); severity = 5 faces scale, colored, each face speaks when tapped.
7. **Reassure constantly**: micro-copy in second person, warm, plain ("Koi jaldi nahi hai. Aaram se boliye."). Never clinical tone to patients.
8. **Errors never blame**: STT failure → "I couldn't hear that properly — let's try once more" + immediate tap alternative appears.
9. **Caregiver visible**: "Answering for someone else?" toggle on first screen, human-illustrated.
10. **Trust markers**: hospital name + logo persistent; "Your answers go only to your doctor" line with a small lock, spoken once at start.
11. **Latency masking**: voice fillers + gentle pulse animation on the assistant avatar while thinking; never a spinner alone.
12. **Interruption safety**: state saved every answer; resume exactly where left; kiosk idle-reset protects privacy (blur + "tap to continue" after 60s).

## 3. Per-surface direction

- **Kiosk**: landscape, generous white cards on mint bg, marigold primary action bottom-right (thumb zone for standing use), assistant avatar top-left with subtle breathing animation. Follow the approved demo HTML's structure/feel; elevate polish, don't reinvent.
- **Queue board (TV)**: dark deep-green background, huge marigold token numerals, room names in two languages, next-3 list, wait estimate ranges. Auto-cycling announcement with chime. Absolutely no clutter, no logos parade.
- **Doctor console**: dense but calm; summary card is the hero — scannable in 20s: red flags as top strip (danger tokens), symptoms as compact table, everything else collapsed. Keyboard shortcuts (N=next patient, D=dictate). Light theme, high contrast, min 14px data text.
- **Android app**: bottom-nav 4 tabs (Home/My File/Queue/Reminders); big cards; voice button is a persistent FAB; onboarding is 3 spoken screens; works one-handed.
- **WhatsApp**: short messages, ≤3 buttons, emoji sparingly as icons (🎫 token, 💊 medicine, 📅 date), voice-note replies mirror the text.
- **Coordinator/admin**: pragmatic tables allowed here, but same tokens; downtime mode flips the app bar to marigold with a clear "OFFLINE — tokens continue" banner.

## 4. Accessibility & environment

- WCAG AA contrast minimum everywhere; board AAA.
- Test at 200% font scale (elderly users); Devanagari/Telugu line-height ≥1.6.
- Kiosk in bright OPD light: avoid low-contrast soft grays for anything meaningful.
- All audio also available as text; all text also as audio. Reduced-motion respected.

## 5. Instructions to the building model (anti-generic clause)

Before coding any screen: restate its single job in one sentence, list the 3 most important elements in order, then build only that. Take one deliberate aesthetic risk per surface (already chosen: the train-board token numerals; the breathing assistant avatar; the faces severity scale) — execute those well and keep everything else quiet. If a screen looks like a generic admin template, redo it. Screenshot (Playwright) and self-critique every patient-facing screen before marking a session complete.
