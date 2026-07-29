/* ============================================================
   Good Days — seeded data
   The app is a forecast, not a record. Everything else the
   patient needs (intake, visit prep, records, medicines) hangs
   off the same time axis rather than becoming a tab.

   Three stages, because a person six days from their first
   infusion and a person two years past treatment are not
   the same user with the same app.
   ============================================================ */

const GD = {

patient:  { name:'Ananya', full:'Ananya Mehta', mrn:'APJ-2024-88412' },
oncologist:'Dr. Meera Sharma',
nurse:    'Sister Anjali George, day-care nurse',
hospital: 'Apollo Hospital, Jaipur',
helpline: { label:'24/7 chemotherapy helpline', number:'1800 200 4455' },

redFlags: [
  'Temperature of 100.4°F (38°C) or higher, even once',
  'Chills or shaking, with or without fever',
  'Vomiting that stops you keeping water down for 12 hours',
  'Breathlessness at rest, or chest pain',
],

stageOrder: ['newly','treatment','followup'],

/* ============================================================
   STAGE 1 — newly diagnosed, nothing has started
   No personal history yet, so the forecast cannot be personal.
   Saying so plainly is the whole trust proposition.
   ============================================================ */
stages: {

newly: {
  label:'Newly diagnosed', chip:'Before cycle 1 · starts in 6 days',
  lead:{
    eyebrow:'6 days before your first infusion',
    h1:['Nothing has started yet.','Here is what it will ask of you.'],
    hi:'Nothing',
    sub:'This is the shape of the three weeks after a first AC-T infusion for most people. After your own cycle 1, it stops being most people and becomes yours.',
  },
  track:{
    unit:'day', today:null, startsIn:6, personal:false,
    axis:'21 days after your first infusion',
    legend:['Typical for AC-T — not yours yet','After cycle 1, this line is drawn from your own days'],
    windows:[
      { from:12, to:17, kind:'good', label:'The good stretch', short:'Good',
        sub:'Most people get six usable days here' },
      { from:7,  to:10, kind:'risk', label:'The careful week', short:'Careful',
        sub:'Counts at their lowest — the risk you cannot feel' },
    ],
  },
  check:{
    title:'How are you right now — before any of it starts?',
    sub:'This one is different. It is your baseline: the version of you that treatment will be compared against. Without it, nobody can tell later what is the cancer, what is the chemotherapy, and what is just a bad week.',
    cta:'Set my baseline',
    done:'Baseline set.',
    doneSub:'From here, every check-in is measured against this. It is the single most useful thing you can give the next six months.',
  },
  visit:{
    when:'20 May', who:'Dr. Meera Sharma', what:'First infusion · day-care ward 4B',
    prepTitle:'Before your first infusion',
    intakeTitle:'Your intake for this visit',
    intakeNote:'Nothing is pre-filled yet — this is the one visit where the app cannot write it for you. After this, it can.',
    intake:[
      { k:'Other medicines you take', v:'Thyroxine 50 mcg, daily', src:'you' },
      { k:'Allergies', v:'Sulpha drugs — rash', src:'you' },
      { k:'Other conditions', v:'Hypothyroidism, 2019', src:'you' },
      { k:'Who is coming with you', v:'Rahul (husband)', src:'you' },
      { k:'Height and weight', v:'161 cm · 59.3 kg', src:'you' },
    ],
    questions:[
      'How will I know if it is working?',
      'What will I be able to keep doing — work, driving, cooking?',
      'Will I lose my hair, and when?',
      'What should my family watch for at home?',
    ],
    bring:[
      { t:'All previous reports, even old ones', s:'Biopsy, mammogram, blood work' },
      { t:'Your medicine box, as it is', s:'Photograph is fine — brands matter' },
      { t:'Someone to drive you home', s:'You should not drive after the first one' },
      { t:'Food you actually like', s:'The ward day is long and hospital food is not the day to test' },
    ],
  },
  meds:{
    mode:'list',
    title:'What you will be given',
    sub:'Nothing to take yet. This is so the first prescription is not a surprise.',
    rows:[
      { t:'Adriamycin + Cyclophosphamide', s:'The infusion itself · every 21 days', k:'infusion' },
      { t:'Ondansetron', s:'Anti-sickness · starts the same day, before you feel sick', k:'support' },
      { t:'Pantoprazole', s:'Protects your stomach · daily', k:'support' },
      { t:'A thermometer', s:'Not a medicine. The most important object you will own for six months.', k:'kit' },
    ],
  },
  plansTitle:'Can I still…',
  plansSub:'Ask now, while it is still easy to move things.',
  plans:[
    { id:'n1', i:14, when:'3 Jun', title:'Back at work, part time', who:'Your school · Grade 6 class',
      verdict:'careful',
      answer:'Plan for the second half of each cycle, not the first.',
      why:'Days 12 to 17 after each infusion are when most people function normally. The first week is not the week to promise anyone anything.',
      how:['Ask for a timetable built around days 12–18 of each cycle.',
           'Do not commit to the week after an infusion until you have been through one.',
           'Tell one colleague the real timetable, so someone can cover without a conversation.'] },
    { id:'n2', i:2, when:'22 May', title:'Riya’s birthday dinner', who:'Your daughter · turning 21',
      verdict:'no',
      answer:'Move it, and do not feel bad about it.',
      why:'Day 2 after an infusion is the heaviest day for almost everyone. Moving it by five days changes it completely.',
      how:['27 May is day 7 — you will feel well enough to enjoy it.',
           'Tell her now rather than cancelling on the day. It lands better.'] },
  ],
  recordsNote:'Get everything into one place now, before you need it in a hurry.',
},

/* ============================================================
   STAGE 2 — in treatment (the default; Ananya, cycle 3, day 2)
   ============================================================ */
treatment: {
  label:'In treatment', chip:'Cycle 3 of 6 · day 2 of 21',
  lead:{
    eyebrow:'Day 2 · 14 May',
    h1:['Tonight is the heaviest it gets.','Then it turns.'],
    hi:'heaviest',
    sub:'You start lifting on Saturday, and from 24 May six days belong to you. That is the week to plan something — not the week to rest.',
  },
  track:{
    unit:'day', today:2, startsIn:null, personal:true,
    axis:'this cycle, day by day',
    legend:['Forecast for this cycle','How your last cycle actually went','What you reported'],
    windows:[
      { from:12, to:17, kind:'good', label:'Your window', short:'Yours',
        sub:'24–29 May · six days that belong to you' },
      { from:7,  to:10, kind:'risk', label:'The careful week', short:'Careful',
        sub:'19–22 May · counts at their lowest' },
    ],
  },
  check:{
    title:'How heavy is it right now?',
    sub:'This is the only thing the app asks of you. It takes one tap — and it is what turns an average forecast into yours.',
    done:'Logged for today.',
    doneSub:'That is today done. Every check-in tightens the next three weeks, and writes itself into your intake for 2 June.',
  },
  visit:{
    when:'2 Jun', who:'Dr. Meera Sharma', what:'Cycle 4 · day-care ward 4B',
    before:{ when:'31 May', what:'Blood counts', why:'Two days ahead, so there is time to act if anything is low' },
    prepTitle:'Before you go',
    intakeTitle:'Your intake is already written',
    intakeNote:'Built from your 34 check-ins this cycle. Read it, correct anything wrong, and it reaches Dr. Sharma before you walk in — so the ten minutes are spent on decisions, not recall.',
    /* the payoff of the check-in loop: the patient reviews instead of remembering */
    intake:[
      { k:'Nausea', v:'Peaked at 8/10 on day 2, settled by day 5', src:'logs', detail:'Worse than cycle 2 by about a day.' },
      { k:'Tiredness', v:'Heavy days 1–4, near normal from day 12', src:'logs' },
      { k:'Mouth ulcers', v:'Returned day 6, lasted 4 days', src:'logs', detail:'Third cycle running.' },
      { k:'Doses missed', v:'None', src:'logs' },
      { k:'Weight', v:'58.2 kg · down 1.1 kg since cycle 2', src:'logs', flag:true },
      { k:'Temperature', v:'No reading above 99.1°F', src:'logs' },
      { k:'New symptoms', v:'Tingling in fingertips, from day 9', src:'logs', flag:true,
        detail:'You reported this twice. It is worth saying out loud — it changes what she watches for.' },
    ],
    questions:[
      'The tiredness after cycle 2 lasted six days. Is that expected?',
      'My mouth ulcers came back again. Should I change the mouthwash?',
      'Can I travel to Delhi for a wedding on 20 May?',
    ],
    bring:[
      { t:'The March CT film', s:'Radiology asked for it, for comparison' },
      { t:'Blood report from 31 May', s:'Already in your records — nothing to carry' , done:true },
      { t:'Fasting from 8 PM the night before', s:'For the blood work only' },
    ],
  },
  meds:{
    mode:'rhythm',
    title:'What your medicines ask of you, across the cycle',
    sub:'Not a list. A shape — so you can see which days need something from you and which do not.',
    tonight:[
      { id:'m4', name:'Capecitabine 500 mg', sub:'Two tablets, within 30 min of food', key:true },
      { id:'m5', name:'Ondansetron 8 mg', sub:'Only if nausea · up to twice a day', prn:true },
    ],
    bars:[
      { t:'Capecitabine', s:'Twice a day, with food', from:0, to:13, k:'key', id:'m4' },
      { t:'Ondansetron', s:'Anti-sickness — while it is heavy', from:0, to:5, k:'support', id:'m1' },
      { t:'Pantoprazole', s:'Every morning, whole cycle', from:0, to:20, k:'plain', id:'m2' },
      { t:'Temperature, twice a day', s:'Through the careful week', from:5, to:12, k:'watch' },
      { t:'Avoid crowded places', s:'While your counts are lowest', from:7, to:11, k:'watch' },
    ],
    marks:[
      { i:19, t:'Blood counts' },
      { i:20, t:'Cycle 4' },
    ],
    detail:{
      m4:{ name:'Capecitabine 500 mg',
        what:'A chemotherapy tablet you take at home, alongside your infusions.',
        taking:['Take within 30 minutes after food — never on an empty stomach.',
                'Swallow whole with water. Do not crush, split or chew.',
                'Morning and evening doses about 12 hours apart.'],
        missed:'If it has been less than 12 hours, take it now. Otherwise skip that dose and carry on. Never double up to catch up.',
        source:'Prescription dated 12 May 2026, signed by Dr. Meera Sharma.' },
      m1:{ name:'Ondansetron 8 mg',
        what:'Stops sickness before it starts. It works far better taken on schedule than taken in hope.',
        taking:['After food, three times a day for the first three days.',
                'Do not wait until you feel sick — that is too late for it to work well.'],
        missed:'Take it when you remember, unless the next dose is close.',
        source:'Prescription revised 12 May 2026, Dr. Meera Sharma.' },
      m2:{ name:'Pantoprazole 40 mg',
        what:'Protects your stomach lining from the rest of it.',
        taking:['One tablet before breakfast, every day of the cycle.'],
        missed:'Take it as soon as you remember, on any day.',
        source:'Prescription dated 14 April 2026, Dr. Meera Sharma.' },
    },
  },
  plansTitle:'Can I still…',
  plansSub:'Your life does not stop for the cycle. It works around it.',
  plans:[
    { id:'p1', i:8, when:'20 May', title:'Nikhil & Sara’s wedding, Delhi', who:'Family · 400 guests',
      verdict:'careful',
      answer:'Go. But this lands in your careful week, so go differently.',
      why:'Day 8 is the bottom of your counts. The danger at a wedding is four hundred people, not the dancing.',
      how:['Fly rather than take the overnight train — less shared air, less exhaustion.',
           'Skip the buffet. Ask for a plate from the kitchen, freshly cooked and hot.',
           'Go to the ceremony, skip the late reception. Leave before you are tired.',
           'Take your temperature that night and the next morning, without fail.'],
      escalate:'A fever of 100.4°F that week is an emergency — go to the nearest hospital, do not wait to fly home.' },
    { id:'p2', i:14, when:'26 May', title:'Riya’s college farewell', who:'Your daughter · Pune',
      verdict:'yes',
      answer:'Yes. This is the best day of your cycle — say yes without hedging.',
      why:'Day 14 was your strongest day in both cycle 1 and cycle 2. Counts recovered, sickness gone.',
      how:['Travel the day before rather than the morning of, so the day itself is hers.',
           'Nothing else needs planning around it. This one is simply a good day.'] },
  ],
  recordsNote:'Everything from this cycle lands here on its own. You only add what the hospital does not send.',
},

/* ============================================================
   STAGE 3 — living after treatment
   The cycle rhythm is gone. The rhythm now is scans, and the
   thing nobody builds for: the fortnight before one.
   ============================================================ */
followup: {
  label:'Living after', chip:'2 years clear · scans every 6 months',
  lead:{
    eyebrow:'Week of 1 July · 2 years and 3 months clear',
    h1:['Your scan is on 12 August.','The hard part is the fortnight before.'],
    hi:'fortnight before',
    sub:'Two years of your own logs say the same thing every time: you are fine until about three weeks out, and then you are not. It has a name — scanxiety — and it is the most predictable thing left in your year.',
  },
  track:{
    unit:'week', today:0, startsIn:null, personal:true,
    axis:'the next sixteen weeks',
    legend:['Forecast for the months ahead','How the last two scan cycles went','What you reported'],
    windows:[
      { from:4, to:7, kind:'risk', label:'The wait', short:'The wait',
        sub:'The three weeks before a scan, and the days waiting for the result' },
      { from:9, to:15, kind:'good', label:'Clear months', short:'Clear',
        sub:'After results, until it starts again' },
    ],
  },
  check:{
    title:'How is it, this month?',
    sub:'Once a month is enough now. It is what turns "I think I was worse before the last scan" into something you can actually see.',
    done:'Logged for July.',
    doneSub:'Two years of these are why the app can tell you the wait is coming before you feel it.',
  },
  visit:{
    when:'12 Aug', who:'Dr. Meera Sharma', what:'Six-month review · CT and consultation',
    before:{ when:'10 Aug', what:'CT chest and abdomen', why:'Results usually come back the same week' },
    prepTitle:'Before the review',
    intakeTitle:'Your intake is already written',
    intakeNote:'Built from twelve monthly check-ins. Survivorship visits are short and easy to waste — this makes sure the things that actually bother you get said.',
    intake:[
      { k:'Energy', v:'Back to normal, except the week before a scan', src:'logs' },
      { k:'Joint pain', v:'Present most mornings, 3/10', src:'logs', flag:true,
        detail:'Reported in 9 of 12 months. Common on hormone therapy and worth raising properly.' },
      { k:'Hot flushes', v:'4–6 a day, disturbing sleep twice a week', src:'logs', flag:true },
      { k:'Mood', v:'Low in the fortnight before each scan, recovers after', src:'logs' },
      { k:'Tablet taken', v:'Letrozole, 358 of 365 days', src:'logs' },
      { k:'Weight', v:'61.4 kg · stable' , src:'logs' },
    ],
    questions:[
      'The joint pain in the mornings — is this the letrozole, and is there anything for it?',
      'How long do I stay on this tablet, and what happens when I stop?',
      'Should my daughter be tested?',
    ],
    bring:[
      { t:'Nothing to carry', s:'The CT goes straight into your records from radiology', done:true },
      { t:'A list of what has changed since February', s:'Already written below' , done:true },
      { t:'Someone with you, if the wait is hard', s:'Most people bring someone to results visits' },
    ],
  },
  meds:{
    mode:'list',
    title:'What you are on now',
    sub:'One tablet, for years. The difficulty is not the taking — it is the side effects nobody warned you would last this long.',
    rows:[
      { t:'Letrozole 2.5 mg', s:'One tablet daily · until March 2031', k:'key' },
      { t:'Calcium + Vitamin D3', s:'Daily · protects your bones on letrozole', k:'support' },
      { t:'Bone density scan', s:'Every 2 years · next in March 2029', k:'kit' },
    ],
  },
  plansTitle:'Can I still…',
  plansSub:'Almost always yes now. The answer is worth having in writing anyway.',
  plans:[
    { id:'f1', i:6, when:'12 Aug', title:'Trek in Himachal, 4 days', who:'With Rahul · booked',
      verdict:'careful',
      answer:'Go — but not that week. Move it after the results.',
      why:'It falls on scan week. Every one of your last four scan weeks shows the same dip, and you would spend the trek waiting for a phone call.',
      how:['Late August is clear in your forecast and the weather is better.',
           'If the booking cannot move, ask for the result by phone rather than waiting for the visit.'] },
    { id:'f2', i:12, when:'23 Sep', title:'Riya’s wedding', who:'Your daughter',
      verdict:'yes',
      answer:'Yes. Nothing in the forecast touches it.',
      why:'Well clear of the scan cycle, and your energy has been steady for eleven months.',
      how:['No adaptations needed. This is simply a day in your life.'] },
  ],
  recordsNote:'Two years of records, still in one place — and still yours to send anywhere.',
},

}, /* end stages */

/* ============================================================
   TRACK SERIES (burden per point, per stage)
   ============================================================ */
series: {
  newly: {
    forecast:[3.0,5.2,7.4,6.8,5.4,4.2,3.4,3.0,3.0,3.1,2.9,2.3,1.8,1.4,1.2,1.2,1.5,1.8,2.2,2.8,3.4],
    last:null, logged:[],
    labels:[['20','Tue'],['21','Wed'],['22','Thu'],['23','Fri'],['24','Sat'],['25','Sun'],['26','Mon'],
            ['27','Tue'],['28','Wed'],['29','Thu'],['30','Fri'],['31','Sat'],['1 Jun','Sun'],['2','Mon'],
            ['3','Tue'],['4','Wed'],['5','Thu'],['6','Fri'],['7','Sat'],['8','Sun'],['9','Mon']],
    names:['Steady','Tender','Heavy','Heavy','Tender','Lifting','Lifting','Guarded','Guarded','Guarded',
           'Guarded','Lifting','Good','Good','Good','Good','Good','Good','Lifting','Steady','Steady'],
    risks:[0,0,0,1,1,1,2,3,3,3,3,2,1,1,0,0,0,0,0,0,0],
    lines:[
      'Infusion day. The steroids hold you up — this is not how the week will feel.',
      'It settles in. Eat what you can, while you can.',
      'The heaviest day for most people. It is also the worst of it.',
      'Still heavy, but past the peak.',
      'Appetite starts to come back. Small and often beats big and hopeful.',
      'Most people notice the turn today.',
      'Most of the sickness is behind you. The quiet risk is just ahead.',
      'Counts start to fall. You will feel fine — that is exactly why this matters.',
      'Lowest defences. The risk is crowds and infection, never effort.',
      'Still the careful week. A fever now is an emergency, not a wait-and-see.',
      'Counts bottom out and begin to climb.',
      'Defences returning. The week opens up.',
      'A good day for most people.',
      'About as close to yourself as the cycle gets.',
      'The best day of the cycle, typically.',
      'Still good. Energy holding.',
      'Good enough for a full day out.',
      'The last of the easy days.',
      'A little tiredness returning.',
      'Blood counts tomorrow.',
      'The day before the next one. Dreading it is normal.',
    ],
  },
  treatment: {
    forecast:[3.0,5.2,7.4,6.8,5.4,4.2,3.4,3.0,3.0,3.1,2.9,2.3,1.8,1.4,1.2,1.2,1.5,1.8,2.2,2.8,3.4],
    last:[3.4,5.6,7.8,7.0,5.6,4.0,3.4,3.1,3.0,3.2,3.0,2.5,2.0,1.6,1.3,1.3,1.6,1.9,2.4,3.0,3.6],
    logged:[{i:0,burden:3.2},{i:1,burden:5.5}],
    labels:[['12','Mon'],['13','Tue'],['14','Wed'],['15','Thu'],['16','Fri'],['17','Sat'],['18','Sun'],
            ['19','Mon'],['20','Tue'],['21','Wed'],['22','Thu'],['23','Fri'],['24','Sat'],['25','Sun'],
            ['26','Mon'],['27','Tue'],['28','Wed'],['29','Thu'],['30','Fri'],['31','Sat'],['1 Jun','Sun']],
    names:['Steady','Tender','Heavy','Heavy','Tender','Lifting','Lifting','Guarded','Guarded','Guarded',
           'Guarded','Lifting','Yours','Yours','Yours','Yours','Yours','Yours','Lifting','Steady','Steady'],
    risks:[0,0,0,1,1,1,2,3,3,3,3,2,1,1,0,0,0,0,0,0,0],
    lines:[
      'Infusion day. The steroids hold you up — this is not how the week will feel.',
      'It settles in. Eat what you can, while you can.',
      'The heaviest day of the cycle. Tonight is the worst of it — and it is the worst of it.',
      'Still heavy, but you are past the peak now. It only goes one way from here.',
      'Appetite starts to come back. Small and often beats big and hopeful.',
      'You will notice the difference today. Both your last cycles turned here.',
      'Most of the sickness is behind you. The quiet risk is just ahead.',
      'Your counts start to fall. You will feel fine — that is exactly why this matters.',
      'Lowest defences of the cycle. The risk is crowds and infection, never effort.',
      'Still the careful week. A fever today is an emergency, not a wait-and-see.',
      'Counts bottom out and begin to climb. Nearly through the narrow part.',
      'Defences returning. The week opens up from here.',
      'A good day. This is one to spend, not to save.',
      'About as close to yourself as this cycle gets.',
      'Your best day of the cycle. Both previous cycles agree on this.',
      'Still yours. Energy holding steady.',
      'Good enough for a full day out, if you want one.',
      'The last of the easy days. Make the plans you have been putting off.',
      'A little tiredness returning. Nothing that should stop you.',
      'Blood counts tomorrow. An early night is worth more than it sounds.',
      'The day before. Dreading it is normal, and it does not mean it will be worse.',
    ],
  },
  followup: {
    forecast:[1.0,0.9,1.1,1.6,2.8,4.2,5.6,6.2,2.0,1.1,0.9,0.9,1.0,1.0,1.1,1.2],
    last:[1.2,1.0,1.2,1.8,3.0,4.6,5.8,6.0,2.2,1.2,1.0,1.0,1.1,1.1,1.2,1.3],
    logged:[{i:0,burden:1.1}],
    labels:[['1','Jul'],['8','Jul'],['15','Jul'],['22','Jul'],['29','Jul'],['5','Aug'],
            ['12','Aug'],['19','Aug'],['26','Aug'],['2','Sep'],['9','Sep'],['16','Sep'],
            ['23','Sep'],['30','Sep'],['7','Oct'],['14','Oct']],
    names:['Clear','Clear','Clear','Stirring','Rising','Rising','Scan week','The wait',
           'Clear','Yours','Yours','Yours','Yours','Yours','Yours','Yours'],
    risks:[0,0,0,0,1,1,2,2,0,0,0,0,0,0,0,0],
    lines:[
      'A normal week. This is what most of your year looks like now.',
      'Nothing due. Nothing to do.',
      'Still clear. The scan is four weeks out and not yet in your head.',
      'This is usually where it starts — a low hum rather than a thought.',
      'Sleep gets worse from about here. It did before both previous scans.',
      'Two weeks out. Most people find this the hardest stretch, not the scan itself.',
      'Scan on 12 August. The day itself is usually easier than the week before it.',
      'Waiting for the result. Historically your hardest week of the year — and it is nine days long.',
      'Results. Every previous one has been clear, which does not make this week easy.',
      'It lifts quickly once you know. It did both times before.',
      'Back to a normal month.',
      'Nothing due.',
      'Riya’s wedding week. Nothing in the forecast touches it.',
      'A normal month.',
      'A normal month.',
      'The next scan is six months out.',
    ],
  },
},

/* ============================================================
   RECORDS — shared across stages, tagged by which stage has them
   ============================================================ */
recordGroups:[
  { g:'Imaging', items:[
    { t:'CT chest', d:'5 May 2026', src:'Apollo Radiology', auto:true, stages:['treatment','followup'] },
    { t:'Mammogram', d:'10 Mar 2026', src:'Apollo Radiology', auto:true, stages:['newly','treatment','followup'] },
    { t:'CT chest & abdomen', d:'14 Feb 2028', src:'Apollo Radiology', auto:true, stages:['followup'] },
  ]},
  { g:'Pathology', items:[
    { t:'Core biopsy report', d:'16 Mar 2026', src:'Apollo Lab', auto:true, stages:['newly','treatment','followup'] },
    { t:'HER2 / hormone receptor', d:'18 Mar 2026', src:'Apollo Lab', auto:true, stages:['treatment','followup'] },
    { t:'Surgical histopathology', d:'2 Apr 2026', src:'Apollo Lab', auto:true, stages:['treatment','followup'] },
  ]},
  { g:'Blood work', items:[
    { t:'Blood counts (CBC)', d:'Today, 08:40', src:'Apollo Lab', auto:true, fresh:true, stages:['treatment'] },
    { t:'Baseline bloods', d:'12 May 2026', src:'Apollo Lab', auto:true, stages:['newly','treatment'] },
    { t:'Annual bloods', d:'14 Feb 2028', src:'Apollo Lab', auto:true, stages:['followup'] },
  ]},
  { g:'Prescriptions & summaries', items:[
    { t:'Prescription — cycle 3', d:'12 May 2026', src:'Dr. M. Sharma', auto:true, stages:['treatment'] },
    { t:'Discharge summary — cycle 3', d:'12 May 2026', src:'Ward 4B', auto:true, stages:['treatment'] },
    { t:'Survivorship care plan', d:'8 Aug 2026', src:'Dr. M. Sharma', auto:true, stages:['followup'] },
  ]},
  { g:'Yours', items:[
    { t:'Insurance policy', d:'Added by you', src:'Photo · 2 pages', auto:false, stages:['newly','treatment','followup'] },
    { t:'Old thyroid reports', d:'Added by you', src:'WhatsApp · 3 files', auto:false, stages:['newly','treatment','followup'] },
  ]},
],

/* the app knows what a complete breast-cancer file looks like */
gaps:{
  newly:[{ t:'Your HER2 and hormone receptor result', s:'Not here yet — it decides the whole treatment plan. Ask for it at the first visit.' },
         { t:'A photograph of your medicine box', s:'Brands matter more than names. Thirty seconds now saves a phone call later.' }],
  treatment:[{ t:'Echo / heart scan before cycle 4', s:'Standard on this regimen. Not in your records — worth asking on 2 June.' }],
  followup:[{ t:'Bone density scan', s:'Due every 2 years on letrozole. Your last was March 2027.' }],
},

shareWith:[
  { t:'Dr. Arjun Patel', s:'Surgical oncologist · in your care team', k:'doctor' },
  { t:'Rahul Mehta', s:'Husband · caregiver access', k:'family' },
  { t:'A doctor not on this list', s:'They get a link, never your login', k:'other' },
],

activeShares:[
  { t:'Dr. Kapoor — second opinion', s:'4 documents · expires in 9 days · opened twice' },
],

addWays:[
  { t:'Scan with the camera', s:'Straightens and crops the page for you', k:'scan' },
  { t:'Forward from WhatsApp', s:'Where most reports actually arrive', k:'wa' },
  { t:'Upload a file', s:'PDF, photo, anything' , k:'file' },
  { t:'Ask the hospital to send it', s:'They post it straight into your records', k:'ask' },
],

provenance:[
  'Your own check-ins — the count grows every day you use it.',
  'Dr. Meera Sharma’s instructions for this regimen.',
  'The published timing for AC-T: sickness days 1–4, counts lowest days 7–10.',
],
};
