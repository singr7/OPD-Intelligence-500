/* ============================================================
   CareCompass — seeded demonstration data
   Shaped to the canonical patient model so the prototype maps
   1:1 onto FHIR-ish resources later:
     patient · treatmentPlan · events · appointments · prepItems
     prescriptions · doses · documents · symptoms · questions
     careTeam · caregivers · shareBundles · audit
   Nothing here is a real medical record.
   ============================================================ */

const SEED = {

  patient: {
    id: 'pt_ananya',
    name: 'Ananya Mehta',
    initials: 'AM',
    age: 42,
    diagnosis: 'Invasive ductal carcinoma, left breast',
    short: 'Breast cancer · Stage II',
    stage: 'Stage II (T2 N1 M0)',
    phase: 'Neoadjuvant chemotherapy',
    hospital: 'Apollo Hospital, Jaipur',
    mrn: 'APJ-2024-88412',
  },

  treatment: {
    regimen: 'AC-T (Adriamycin + Cyclophosphamide, then Taxol)',
    cycle: 3,
    cycles: 6,
    lastSession: '12 May 2026',
    nextSession: '2 Jun 2026',
    oncologist: 'Dr. Meera Sharma',
    // day 2 post-chemo — drives the "at home" recovery context
    daysSinceSession: 2,
  },

  /* ---- context definitions: a deterministic rules layer picks one ---- */
  contexts: {
    hospital: {
      id: 'hospital',
      label: 'At Apollo Hospital',
      eyebrow: 'You are at Apollo Hospital',
      greeting: 'Good morning, Ananya.',
      headline: 'Your consultation is the next thing that matters.',
      lede: 'Room 204, Oncology OPD. Check in first — your blood report is already back and Dr. Sharma has seen it.',
      // the signals a production context engine would have used
      signals: [
        'Geofence match: Apollo Hospital campus (consented)',
        'Appointment today 11:30 with Dr. Meera Sharma',
        'Check-in status: not yet checked in',
        '3 of 4 consultation-prep items complete',
      ],
    },
    discharged: {
      id: 'discharged',
      label: 'Recently discharged',
      eyebrow: 'Discharged 1 hour ago',
      greeting: 'You’re on your way home, Ananya.',
      headline: 'Everything from today, in the order you’ll need it.',
      lede: 'Discharge summary is ready, two medicines changed, and your transport is booked for 4:10 PM.',
      signals: [
        'Discharge event recorded 1h ago (Ward 4B)',
        'Prescription revised — 2 medicines changed',
        'Discharge summary signed by Dr. Meera Sharma',
        'No red-flag symptoms reported in last 24h',
      ],
    },
    home: {
      id: 'home',
      label: 'At home, recovering',
      eyebrow: 'Day 2 after chemotherapy',
      greeting: 'Good evening, Ananya.',
      headline: 'Day 2 is usually the heaviest. Rest is the plan.',
      lede: 'Nausea peaks around now and settles by day 4. Hydration and your evening medicines are what matter tonight.',
      signals: [
        'No appointment today · location: home',
        'Chemotherapy session 2 days ago (Cycle 3)',
        'Symptom log: nausea reported this morning',
        'Evening medication window opens 8:00 PM',
      ],
    },
  },

  /* ---- appointments & consultation preparation ---- */
  appointment: {
    time: '11:30 AM',
    doctor: 'Dr. Meera Sharma',
    role: 'Medical Oncologist',
    place: 'Room 204 · Oncology OPD',
    sequence: [
      { t: '10:45', label: 'Registration & check-in', state: 'now', note: 'Counter 2, ground floor' },
      { t: '11:00', label: 'Blood test review', state: 'done', note: 'Reported 08:40 — counts acceptable' },
      { t: '11:30', label: 'Consultation with Dr. Sharma', state: 'next', note: 'Room 204' },
      { t: '02:00', label: 'CT chest', state: 'later', note: 'Radiology, basement · fasting not required' },
    ],
  },

  prepItems: [
    { id: 'p1', title: 'Bring the previous CT film', sub: 'Radiology asked for the March scan for comparison', done: true },
    { id: 'p2', title: 'Note down your questions', sub: '3 questions saved — tap to review before you go in', done: true },
    { id: 'p3', title: 'Log this week’s side effects', sub: 'Dr. Sharma reviews this before deciding the next dose', done: true },
    { id: 'p4', title: 'Fasting since 8:00 PM last night', sub: 'Required only for today’s blood work', done: false },
  ],

  questions: [
    { id: 'q1', text: 'The tiredness after cycle 2 lasted six days. Is that expected?', asked: false },
    { id: 'q2', text: 'Can I travel to Delhi for a wedding between cycles?', asked: false },
    { id: 'q3', text: 'My mouth ulcers came back. Should I change the mouthwash?', asked: false },
  ],

  /* ---- treatment journey ---- */
  journey: [
    { id: 'j1', date: '16 Mar 2026', title: 'Diagnosis confirmed', state: 'done',
      body: 'Core biopsy confirmed invasive ductal carcinoma, ER+/PR+, HER2-negative.' },
    { id: 'j2', date: '28 Mar 2026', title: 'Surgery — lumpectomy', state: 'done',
      body: 'Left breast wide local excision with sentinel node biopsy. 1 of 3 nodes involved.' },
    { id: 'j3', date: '14 Apr 2026', title: 'Chemotherapy started — Cycle 1', state: 'done',
      body: 'AC-T regimen planned over 6 cycles, one every 21 days.' },
    { id: 'j4', date: '12 May 2026', title: 'Cycle 3 of 6', state: 'now',
      body: 'Given at day-care ward 4B. Dose unchanged. Next cycle due 2 June.' },
    { id: 'j5', date: '2 Jun 2026', title: 'Cycle 4 of 6', state: 'future',
      body: 'Blood counts to be checked 2 days before.' },
    { id: 'j6', date: 'Jul 2026', title: 'Radiation therapy', state: 'future',
      body: 'Planned after chemotherapy completes. 15 sessions expected.' },
    { id: 'j7', date: 'Aug 2026 onward', title: 'Hormone therapy & follow-up', state: 'future',
      body: 'Daily tablet for 5 years, with review every 3 months.' },
  ],

  /* ---- medications ---- */
  medSlots: [
    { time: '8:00', part: 'Morning', meds: [
      { id: 'm1', name: 'Ondansetron 8 mg', sub: 'After food · for nausea', taken: true },
      { id: 'm2', name: 'Pantoprazole 40 mg', sub: 'Before breakfast · stomach protection', taken: true },
    ]},
    { time: '2:00', part: 'Afternoon', meds: [
      { id: 'm3', name: 'Calcium + Vitamin D3', sub: 'After lunch', taken: false },
    ]},
    { time: '8:00', part: 'Evening', meds: [
      { id: 'm4', name: 'Capecitabine 500 mg', sub: 'Two tablets, within 30 min of food', taken: false, key: true },
      { id: 'm5', name: 'Ondansetron 8 mg', sub: 'Only if nausea · up to twice a day', taken: false, prn: true },
    ]},
  ],

  medDetail: {
    m4: {
      name: 'Capecitabine 500 mg',
      what: 'A chemotherapy tablet used in breast and colorectal cancer. It is taken at home, in cycles, alongside your infusions.',
      how: 'It slows down or stops the growth of cancer cells by interfering with the way they copy themselves.',
      taking: [
        'Take within 30 minutes after food — never on an empty stomach.',
        'Swallow whole with water. Do not crush, split or chew.',
        'Morning and evening doses should be about 12 hours apart.',
      ],
      missed: 'If it has been less than 12 hours, take it now. Otherwise skip that dose and continue as usual. Never double the dose to catch up.',
      watch: ['Sore or peeling palms and soles', 'Mouth ulcers that stop you eating', 'Loose motions more than 4 times a day'],
      source: 'Prescription dated 12 May 2026, signed by Dr. Meera Sharma (Apollo Hospital, Jaipur).',
    },
  },

  /* ---- documents ---- */
  documents: [
    { id: 'd1', title: 'CT chest', sub: '5 May 2026 · Radiology', kind: 'scan', tag: 'Imaging' },
    { id: 'd2', title: 'Histopathology report', sub: '18 Mar 2026 · Lab', kind: 'paper', tag: 'Pathology' },
    { id: 'd3', title: 'Blood counts (CBC)', sub: 'Today, 08:40 · Lab', kind: 'paper', tag: 'Laboratory', fresh: true },
    { id: 'd4', title: 'Discharge summary — Cycle 3', sub: '12 May 2026 · Ward 4B', kind: 'paper', tag: 'Summary' },
    { id: 'd5', title: 'Mammogram', sub: '10 Mar 2026 · Radiology', kind: 'scan', tag: 'Imaging' },
    { id: 'd6', title: 'Surgery notes — lumpectomy', sub: '28 Mar 2026 · Theatre', kind: 'paper', tag: 'Procedure' },
  ],

  reportSummary: {
    docId: 'd1',
    title: 'CT chest — 5 May 2026',
    draft: true,
    lines: [
      'No new suspicious nodules in either lung field.',
      'Post-surgical changes in the left breast, as expected after March surgery.',
      'Lymph nodes unchanged in size compared with the March scan.',
    ],
    caveat: 'This plain-language summary is generated from the radiologist’s report and is marked draft until Dr. Sharma confirms it. The signed report is the record.',
    source: 'Radiology report, Apollo Hospital, reported by Dr. R. Nair on 5 May 2026.',
  },

  /* ---- discharge context content ---- */
  discharge: {
    changes: [
      { name: 'Ondansetron 8 mg', change: 'Increased to three times a day for 3 days', kind: 'up' },
      { name: 'Diclofenac', change: 'Stopped — can irritate the stomach with this cycle', kind: 'stop' },
    ],
    instructions: [
      { t: 'Drink 2.5–3 litres of water daily for the next 4 days', why: 'Helps clear the drugs and protects the kidneys' },
      { t: 'Check your temperature twice a day', why: 'Fever is the one thing we must catch early' },
      { t: 'Avoid crowded places until 26 May', why: 'Your infection-fighting counts dip around day 7–10' },
    ],
    transport: { when: '4:10 PM', what: 'Hospital cab booked to Vaishali Nagar', who: 'Rahul is meeting you at the porch' },
  },

  redFlags: [
    'Temperature of 100.4°F (38°C) or higher, even once',
    'Chills or shaking, with or without fever',
    'Bleeding or bruising that appears on its own',
    'Vomiting that stops you keeping water down for 12 hours',
    'Breathlessness at rest, or chest pain',
  ],

  /* ---- recovery / home context ---- */
  hydration: { taken: 1.2, goal: 2.5 },

  symptomHistory: [
    { day: 'Day 1', nausea: 3, fatigue: 4 },
    { day: 'Day 2', nausea: 3, fatigue: 4 },
  ],

  /* ---- care team & caregiver access ---- */
  careTeam: [
    { id: 'c1', name: 'Dr. Meera Sharma', role: 'Medical Oncologist · Apollo', initials: 'MS', kind: 'doctor',
      note: 'Replies on weekdays, usually within a day' },
    { id: 'c2', name: 'Sister Anjali George', role: 'Chemotherapy day-care nurse', initials: 'AG', kind: 'nurse',
      note: 'Best person for side-effect questions', phone: true },
    { id: 'c3', name: 'Dr. Arjun Patel', role: 'Surgical Oncologist', initials: 'AP', kind: 'doctor',
      note: 'Follow-up after radiation' },
  ],

  caregivers: [
    { id: 'g1', name: 'Rahul Mehta', rel: 'Spouse', initials: 'RM',
      perms: { records: true, appointments: true, medicines: true, symptoms: true, share: true } },
    { id: 'g2', name: 'Riya Mehta', rel: 'Daughter · lives in Pune', initials: 'RM2',
      perms: { records: false, appointments: true, medicines: true, symptoms: false, share: false } },
  ],

  permLabels: {
    records: 'View reports & scans',
    appointments: 'See appointments',
    medicines: 'See & tick medicines',
    symptoms: 'See symptom log',
    share: 'Share records outside',
  },

  audit: [
    { when: 'Today 09:12', what: 'Rahul Mehta viewed “Blood counts (CBC)”' },
    { when: 'Yesterday', what: 'Dr. Meera Sharma opened your symptom log' },
    { when: '12 May', what: 'Discharge summary added by Apollo Hospital' },
    { when: '10 May', what: 'Share link to Dr. Kapoor expired automatically' },
  ],

  helpline: { label: '24/7 chemotherapy helpline', number: '1800 200 4455' },
};
