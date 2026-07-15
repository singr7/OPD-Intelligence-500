# 01 — Concept & Workflows

## 1. One-line concept

Turn the waiting time of 500 oncology OPD patients/day into structured clinical preparation — captured by voice in the patient's own language, summarized by AI for the doctor, and extended into automatic post-treatment check-ins — without disrupting how the hospital already works.

## 2. Why oncology, why Tier-2/3

- Oncology OPD visits are **repetitive and longitudinal**: chemo cycles, radiation reviews, symptom follow-ups. The same patient returns 6–20 times. This makes intake reuse, check-ins, and personalization dramatically more valuable than in general OPD.
- Rural oncology patients travel far (often 50–200 km), frequently with a **caregiver as the actual phone/app operator**. Design for the caregiver as a first-class user.
- Symptom reporting quality directly changes chemo decisions (e.g., grade of neuropathy, mucositis, fever between cycles). Structured intake is clinically material, not a convenience.

## 3. Personas

| Persona | Reality to design for |
|---|---|
| **Patient (55–70, rural)** | May be non-literate; feature phone or entry Android shared with family; speaks Hindi/Marathi/Telugu dialects; low trust in tech, high trust in voice and human tone |
| **Caregiver (25–45)** | Operates WhatsApp fluently; the real app installer; manages appointments, reports symptoms on patient's behalf |
| **Registration coordinator** | Manages a physical crowd; needs the system to be faster than paper, or they will abandon it |
| **Oncologist (sees 40–80 pts/day)** | 4–8 min per consult; will read a summary only if it is scannable in <20 seconds; will dictate only if it saves writing |
| **OPD nurse / floor staff** | Runs the queue board, manages downtime protocol |

## 4. End-to-end workflows

### 4.1 New patient — walk-in (kiosk path)
```
Arrive → Registration desk creates patient (name, phone, age, sex — 60s)
→ Kiosk token slip printed with QR → Patient/caregiver taps QR at kiosk (or staff assists)
→ Kiosk intake: voice chief complaint → NLP dept routing → dept question tree (tap + read-aloud)
→ AI reads back summary in patient's language → patient confirms → token assigned to doctor queue
→ Queue board shows token; WhatsApp message to caregiver with token + est. wait
→ Doctor opens patient: sees AI summary → consults → dictates plan
→ Dictation mapped to structured fields → digital prescription → printed + WhatsApp PDF
→ System auto-creates check-in schedule from treatment notes (e.g., chemo D+2, D+7)
```

### 4.2 Returning patient — pre-visit phone intake (Exotel path)
```
Appointment exists (D-1) → Outbound AI call in patient's language, evening slot
→ Conversational intake: "How have you been since the last cycle?" (context-aware: knows regimen, last dictation)
→ Summary attached to tomorrow's appointment → red-flag rules (fever + chemo <10 days ago → alert nurse queue)
→ On arrival: registration scans phone/ID → token issued instantly, intake already done
```

### 4.3 WhatsApp path (caregiver-driven)
```
Caregiver messages hospital WhatsApp number (or taps link from reminder)
→ Bot: buttons + voice notes both accepted → intake or appointment management
→ Voice note in → STT → same intake engine → confirmation as voice note back + text
→ Appointment booking/reschedule/cancel with slot buttons; confirmation via WhatsApp + SMS fallback
```

### 4.4 Inbound appointment call (Exotel)
```
Patient calls hospital line → AI answers in caller's language (detect from greeting, offer switch)
→ Intent: book / reschedule / cancel / "when is my appointment?" / talk to human
→ Books against real slot inventory → SMS + WhatsApp confirmation → human handoff on 2 failed turns
```

### 4.5 Post-treatment check-in (auto-created)
```
Doctor dictation saved → LLM extracts treatment events (e.g., "Cycle 3 FOLFOX today, next cycle in 21 days")
→ Check-in plan generated from protocol templates + dictation specifics, doctor sees & can edit one-tap
→ D+2 WhatsApp/voice call: "Any fever? Vomiting? Able to eat?" (CTCAE-lite graded questions)
→ Answers graded → green: logged; amber: nurse review queue; red: immediate call task + doctor alert
→ D+19: reminder for next cycle + booking link
```

## 5. Downtime Protocol (system down 1–2 hours)

Design principle: **the hospital ran on paper yesterday; downtime mode is "paper with a memory."**

1. **Local-first kiosk & queue board.** Kiosk and queue-board are PWAs with IndexedDB. If the server is unreachable, the kiosk keeps issuing tokens from a **pre-allocated offline token block** (e.g., server pre-assigns block 500–599 per kiosk daily) and stores intakes locally. Queue board keeps advancing tokens locally (nurse taps "next"). Everything syncs automatically on reconnect; conflicts resolve by timestamp, tokens never collide because blocks are pre-allocated.
2. **Degraded intake.** If LLM/STT APIs are down but server is up: kiosk falls back to the **rule-based question tree with pre-recorded voices** (no AI needed end-to-end). Summary becomes a template-rendered structured sheet instead of AI prose. This is why the "minimalistic versions" exist — they ARE the fallback tier, always maintained.
3. **Total blackout (power/network both).** Laminated **paper intake sheets** printed from the same question trees (one per department, in 4 languages) at registration; whiteboard queue with the same token series (staff use the next numbers from the printed daily token block sheet). On recovery, coordinator batch-enters paper sheets via a fast "downtime entry" screen (photo upload + OCR assist optional in later phase).
4. **Heartbeat + banner.** Every screen shows a subtle sync status. When offline >60s, screens switch to Downtime Mode automatically with an on-screen card telling staff exactly what still works.
5. **Recovery drill.** A `downtime-drill` admin button simulates outage; run monthly. Recovery is: reconnect → auto-sync → coordinator reviews "Downtime Reconciliation" list → done.

## 6. What we deliberately exclude from the pilot

- No EMR replacement; we export PDFs and (later) HL7/FHIR push. Integration is one-way out.
- No payments/billing.
- No diagnosis AI. Department routing and red-flag rules only, with human confirmation.
- iOS app deferred to Phase 2 (spec included so Android is built share-ready).

## 7. Success metrics (pilot, 8 weeks)

- ≥70% of OPD patients complete structured intake before consult
- Median intake time ≤4 min (kiosk), ≤6 min (phone)
- Doctor summary open-rate ≥80%; dictation used in ≥50% of consults
- Check-in response rate ≥60% at D+2; ≥1 red-flag escalation caught per week
- Queue wait-time estimate error ≤ ±15 min
