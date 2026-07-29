/* ============================================================
   CareCompass — prototype behaviour
   The home screen is not a dashboard. A deterministic rules layer
   picks one care context; the home renders only the next
   meaningful actions for that context.
   ============================================================ */

/* ---------------- icons ---------------- */
const I = (() => {
  const s = (d, extra = '') =>
    `<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8"
      stroke-linecap="round" stroke-linejoin="round" ${extra}>${d}</svg>`;
  return {
    home:    s('<path d="M4 10.5 12 4l8 6.5V20a1 1 0 0 1-1 1h-4v-6H9v6H5a1 1 0 0 1-1-1z"/>'),
    journey: s('<path d="M6 3v12a3 3 0 0 0 3 3h6"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="18" r="2"/><circle cx="6" cy="4" r="1.6"/>'),
    records: s('<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h4"/>'),
    meds:    s('<rect x="3" y="8.5" width="18" height="7" rx="3.5" transform="rotate(-40 12 12)"/><path d="M9 9l6 6"/>'),
    team:    s('<circle cx="9" cy="8" r="3"/><path d="M3 20a6 6 0 0 1 12 0"/><path d="M16 5.2a3 3 0 0 1 0 5.6M17.5 20a5.6 5.6 0 0 0-2-4"/>'),
    check:   s('<path d="M5 12.5 10 17.5 19 7"/>', 'stroke-width="2.6"'),
    chev:    s('<path d="M9 5l7 7-7 7"/>'),
    clock:   s('<circle cx="12" cy="12" r="9"/><path d="M12 7v5.2l3.2 2"/>'),
    drop:    s('<path d="M12 3.5c3.4 4 6 6.8 6 10a6 6 0 0 1-12 0c0-3.2 2.6-6 6-10z"/>'),
    alert:   s('<path d="M12 4 2.8 20h18.4z"/><path d="M12 10v4.2M12 17.4v.1"/>'),
    share:   s('<circle cx="6" cy="12" r="2.4"/><circle cx="17" cy="6" r="2.4"/><circle cx="17" cy="18" r="2.4"/><path d="M8.2 10.9 14.8 7.2M8.2 13.1l6.6 3.7"/>'),
    lock:    s('<rect x="4.5" y="10.5" width="15" height="10" rx="2.5"/><path d="M8 10.5V8a4 4 0 0 1 8 0v2.5"/>'),
    mic:     s('<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3"/>'),
    phone:   s('<path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a1 1 0 0 1-1 1A16 16 0 0 1 4 5a1 1 0 0 1 1-1z"/>'),
    plus:    s('<path d="M12 5v14M5 12h14"/>'),
    info:    s('<circle cx="12" cy="12" r="9"/><path d="M12 11v5.5M12 7.6v.1"/>'),
    scan:    s('<path d="M4 8V6a2 2 0 0 1 2-2h2M16 4h2a2 2 0 0 1 2 2v2M20 16v2a2 2 0 0 1-2 2h-2M8 20H6a2 2 0 0 1-2-2v-2"/><circle cx="12" cy="12" r="3"/>'),
    spark:   s('<path d="M12 3.5 13.9 9.3 19.5 11l-5.6 1.7L12 18.5l-1.9-5.8L4.5 11l5.6-1.7z"/>'),
    temp:    s('<path d="M10 13.5V6a2 2 0 1 1 4 0v7.5a4 4 0 1 1-4 0z"/>'),
    bell:    s('<path d="M6 9a6 6 0 1 1 12 0c0 5 2 6 2 6H4s2-1 2-6"/><path d="M10.5 19a1.8 1.8 0 0 0 3 0"/>'),
    car:     s('<path d="M4 15h16M6 15l1.5-5.2A2 2 0 0 1 9.4 8h5.2a2 2 0 0 1 1.9 1.8L18 15v3.5h-3V17H9v1.5H6z"/><circle cx="8.4" cy="15" r="0"/>'),
    doc:     s('<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4M9 12h6M9 16h6"/>'),
    heart:   s('<path d="M12 20s-7-4.4-7-9.2A3.9 3.9 0 0 1 12 8a3.9 3.9 0 0 1 7 2.8C19 15.6 12 20 12 20z"/>'),
  };
})();

/* ---------------- state ---------------- */
const S = {
  ctx: 'hospital',
  tab: 'home',
  prep: SEED.prepItems.map(p => ({ ...p })),
  questions: SEED.questions.map(q => ({ ...q })),
  slots: SEED.medSlots.map(s => ({ ...s, meds: s.meds.map(m => ({ ...m })) })),
  hydration: SEED.hydration.taken,
  picked: new Set(),
  caregivers: SEED.caregivers.map(c => ({ ...c, perms: { ...c.perms } })),
  audit: [...SEED.audit],
  symptom: { severity: null, zones: new Set(), logged: false },
  checkedIn: false,
};

const TABS = [
  { id: 'home',    label: 'Home',     icon: 'home' },
  { id: 'journey', label: 'Journey',  icon: 'journey' },
  { id: 'records', label: 'Records',  icon: 'records' },
  { id: 'meds',    label: 'Medicines',icon: 'meds' },
  { id: 'team',    label: 'Care Team',icon: 'team' },
];

/* ---------------- tiny helpers ---------------- */
const $  = sel => document.querySelector(sel);
const esc = str => String(str).replace(/[&<>"]/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;' }[c]));
const prepDone = () => S.prep.filter(p => p.done).length;

function toast(msg) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove('show'), 2400);
}

/* ---------------- sheet ---------------- */
function openSheet(title, html) {
  $('#sheetTitle').textContent = title;
  $('#sheetBody').innerHTML = html;
  $('#sheet').hidden = false;
  $('#scrim').hidden = false;
  $('#sheetClose').focus();
}
function closeSheet() { $('#sheet').hidden = true; $('#scrim').hidden = true; }

/* ============================================================
   COMPONENTS
   ============================================================ */

/* the signature element: a segmented cycle ring */
function cycleRing() {
  const { cycle, cycles } = SEED.treatment;
  const r = 62, C = 2 * Math.PI * r, gap = 7;
  const seg = C / cycles;
  const arcs = Array.from({ length: cycles }, (_, i) => {
    const done = i < cycle - 1, now = i === cycle - 1;
    const color = done ? '#E2901F' : now ? '#FFFFFF' : 'rgba(255,255,255,.22)';
    return `<circle cx="77" cy="77" r="${r}" fill="none" stroke="${color}" stroke-width="${now ? 11 : 8}"
      stroke-dasharray="${seg - gap} ${C - seg + gap}" stroke-dashoffset="${-i * seg}" stroke-linecap="round"/>`;
  }).join('');
  return `
    <div>
      <div class="ring-wrap">
        <svg viewBox="0 0 154 154" width="154" height="154" role="img"
             aria-label="Cycle ${cycle} of ${cycles} chemotherapy">${arcs}</svg>
        <span class="ring-num"><b>${cycle}<span style="opacity:.45;font-size:22px">/${cycles}</span></b><span>Cycles</span></span>
      </div>
      <div class="ring-legend">
        <i><b style="background:#E2901F"></b>Done</i>
        <i><b style="background:#fff"></b>Current</i>
        <i><b style="background:rgba(255,255,255,.3)"></b>To come</i>
      </div>
    </div>`;
}

function pageHead(title, sub) {
  return `<header class="pagehead"><div><h1>${title}</h1><p>${sub}</p></div></header>`;
}

function sourceNote(text) {
  return `<div class="source">${I.lock}<span>${text}</span></div>`;
}

/* ============================================================
   HOME — one contextual surface, three states
   ============================================================ */

function viewHome() {
  const c = SEED.contexts[S.ctx];
  const hero = `
    <section class="hero">
      <div class="hero-grid">
        <div>
          <span class="hero-eyebrow"><span class="pulse"></span>${esc(c.eyebrow)}</span>
          <h1>${esc(c.headline)}</h1>
          <p class="lede">${esc(c.lede)}</p>
          ${heroAction()}
        </div>
        ${cycleRing()}
      </div>
    </section>`;

  const body = S.ctx === 'hospital' ? homeHospital()
             : S.ctx === 'discharged' ? homeDischarged()
             : homeHome();

  return `
    <p class="eyebrow" style="margin-bottom:6px">${esc(c.greeting)}</p>
    ${hero}
    <div style="margin-top:12px">
      <button class="btn ghost sm" data-act="why">${I.info} Why am I seeing this?</button>
    </div>
    ${body}`;
}

function heroAction() {
  if (S.ctx === 'hospital') {
    const a = SEED.appointment;
    return `
      <div class="now">
        <span class="now-time">${a.time}</span>
        <span class="now-main"><b>${esc(a.doctor)} · ${esc(a.role)}</b><span>${esc(a.place)}</span></span>
      </div>
      <div class="btn-row">
        ${S.checkedIn
          ? `<span class="chip onhero">${I.check} Checked in · you are 4th in queue</span>`
          : `<button class="btn onhero" data-act="checkin">Check in now</button>`}
        <button class="btn ghosthero" data-act="questions">My questions (${S.questions.length})</button>
      </div>`;
  }
  if (S.ctx === 'discharged') {
    const t = SEED.discharge.transport;
    return `
      <div class="now">
        <span class="now-time">${t.when}</span>
        <span class="now-main"><b>${esc(t.what)}</b><span>${esc(t.who)}</span></span>
      </div>
      <div class="btn-row">
        <button class="btn onhero" data-act="doc:d4">Open discharge summary</button>
        <button class="btn ghosthero" data-act="tab:meds">What changed in my medicines</button>
      </div>`;
  }
  const pct = Math.min(100, Math.round((S.hydration / SEED.hydration.goal) * 100));
  return `
    <div class="now" style="display:block">
      <div class="row" style="justify-content:space-between;margin-bottom:8px">
        <span class="now-main"><b>Water today</b><span>${S.hydration.toFixed(1)} of ${SEED.hydration.goal} litres</span></span>
        <button class="btn onhero sm" data-act="water">${I.plus} 250 ml</button>
      </div>
      <div class="meter onhero"><i style="width:${pct}%"></i></div>
    </div>
    <div class="btn-row">
      <button class="btn onhero" data-act="symptom">How are you feeling?</button>
      <button class="btn ghosthero" data-act="tab:team">Contact care team</button>
    </div>`;
}

/* ---- context A: at hospital ---- */
function homeHospital() {
  const a = SEED.appointment;
  const done = prepDone(), total = S.prep.length;

  const seq = a.sequence.map(s => {
    const cls = s.state === 'done' ? '' : s.state === 'now' ? 'warm' : 'neutral';
    const badge = s.state === 'done' ? '<span class="chip">Done</span>'
                : s.state === 'now'  ? '<span class="chip warn">Now</span>'
                : s.state === 'next' ? '<span class="chip clay">Next</span>' : '';
    return `<li class="li">
      <span class="li-icon ${cls}">${s.state === 'done' ? I.check : I.clock}</span>
      <span class="li-main"><b>${esc(s.label)}</b><span>${esc(s.t)} · ${esc(s.note)}</span></span>
      <span class="li-right">${badge}</span>
    </li>`;
  }).join('');

  const prep = S.prep.map(p => `
    <button class="check ${p.done ? 'done' : ''}" data-act="prep:${p.id}">
      <span class="box">${I.check}</span>
      <span><b>${esc(p.title)}</b><span>${esc(p.sub)}</span></span>
    </button>`).join('');

  return `
    <div class="cols" style="margin-top:var(--sp3)">
      <div class="stack-lg">
        <section class="card">
          <div class="card-head"><span class="card-icon">${I.clock}</span><h3>Your visit, in order</h3>
            <span class="spacer"></span><span class="tiny muted">Updated 2 min ago</span></div>
          <ul class="list">${seq}</ul>
        </section>

        <section class="card">
          <div class="card-head"><span class="card-icon warm">${I.spark}</span>
            <h3>Before you go in</h3><span class="spacer"></span>
            <span class="chip ${done === total ? '' : 'warn'}">${done} of ${total} ready</span></div>
          <div class="meter warm" style="margin-bottom:14px"><i style="width:${(done / total) * 100}%"></i></div>
          ${prep}
          ${done === total
            ? `<div class="card tint" style="margin-top:14px;padding:14px">
                 <b style="font-size:14.5px">You’re fully prepared.</b>
                 <p class="tiny muted" style="margin-top:4px">Nothing else is needed before the consultation. Rest until you’re called.</p>
               </div>` : ''}
        </section>
      </div>

      <div class="stack-lg">
        <section class="card sand">
          <div class="card-head"><span class="card-icon clay">${I.mic}</span><h3>Questions for Dr. Sharma</h3></div>
          <p class="tiny muted" style="margin-bottom:12px">Say them out loud, or type. They appear on your doctor’s screen too.</p>
          ${S.questions.map(q => `<div class="check" style="background:#fff"><span class="box" style="border:0;color:var(--clay)">${I.chev}</span><span><b style="font-weight:600;font-size:14px">${esc(q.text)}</b></span></div>`).join('')}
          <div class="btn-row"><button class="btn warm block" data-act="askq">${I.mic} Add a question by voice</button></div>
        </section>

        <section class="card">
          <div class="card-head"><span class="card-icon">${I.doc}</span><h3>Fresh result</h3></div>
          <button class="li" data-act="doc:d3" style="border:0;width:100%;background:transparent">
            <span class="li-icon">${I.doc}</span>
            <span class="li-main"><b>Blood counts (CBC)</b><span>Reported today 08:40</span></span>
            <span class="li-right"><span class="chip">Acceptable</span>${I.chev}</span>
          </button>
        </section>
      </div>
    </div>`;
}

/* ---- context B: recently discharged ---- */
function homeDischarged() {
  const d = SEED.discharge;
  const changes = d.changes.map(c => `
    <li class="li">
      <span class="li-icon ${c.kind === 'stop' ? 'danger' : 'warm'}">${c.kind === 'stop' ? I.alert : I.meds}</span>
      <span class="li-main"><b>${esc(c.name)}</b><span>${esc(c.change)}</span></span>
      <span class="li-right"><span class="chip ${c.kind === 'stop' ? 'danger' : 'warn'}">${c.kind === 'stop' ? 'Stopped' : 'Changed'}</span></span>
    </li>`).join('');

  const instr = d.instructions.map(x => `
    <li class="li">
      <span class="li-icon">${I.check}</span>
      <span class="li-main"><b>${esc(x.t)}</b><span>${esc(x.why)}</span></span>
    </li>`).join('');

  return `
    <div class="cols" style="margin-top:var(--sp3)">
      <div class="stack-lg">
        <section class="card">
          <div class="card-head"><span class="card-icon warm">${I.meds}</span><h3>What changed in your medicines</h3>
            <span class="spacer"></span><span class="chip warn">2 changes</span></div>
          <ul class="list">${changes}</ul>
          ${sourceNote('Taken from the prescription revised today by Dr. Meera Sharma. CareCompass never changes a dose — it only explains one.')}
          <div class="btn-row"><button class="btn primary" data-act="tab:meds">See today’s full schedule</button></div>
        </section>

        <section class="card">
          <div class="card-head"><span class="card-icon">${I.check}</span><h3>For the next four days</h3></div>
          <ul class="list">${instr}</ul>
        </section>
      </div>

      <div class="stack-lg">
        <div class="redflag">
          <h4>${I.alert} Call the hospital straight away if</h4>
          <ul>${SEED.redFlags.slice(0, 4).map(f => `<li>${esc(f)}</li>`).join('')}</ul>
          <div class="btn-row">
            <button class="btn danger block" data-act="call">${I.phone} ${esc(SEED.helpline.number)}</button>
          </div>
          <p class="tiny" style="color:#7E3232;margin-top:8px">${esc(SEED.helpline.label)}. Do not wait until morning.</p>
        </div>

        <section class="card">
          <div class="card-head"><span class="card-icon clay">${I.car}</span><h3>Getting home</h3></div>
          <p style="font-size:14.5px"><b>${esc(d.transport.when)}</b> — ${esc(d.transport.what)}</p>
          <p class="tiny muted" style="margin-top:6px">${esc(d.transport.who)}</p>
        </section>

        <section class="card">
          <div class="card-head"><span class="card-icon">${I.doc}</span><h3>Papers from today</h3></div>
          <button class="li" data-act="doc:d4" style="border:0;width:100%;background:transparent">
            <span class="li-icon">${I.doc}</span>
            <span class="li-main"><b>Discharge summary — Cycle 3</b><span>Signed by Dr. Meera Sharma</span></span>
            <span class="li-right">${I.chev}</span>
          </button>
        </section>
      </div>
    </div>`;
}

/* ---- context C: at home, recovering ---- */
function homeHome() {
  const evening = S.slots[2].meds.filter(m => !m.taken).length;
  return `
    <div class="cols" style="margin-top:var(--sp3)">
      <div class="stack-lg">
        <section class="card">
          <div class="card-head"><span class="card-icon clay">${I.heart}</span><h3>How are you feeling right now?</h3></div>
          <p class="tiny muted" style="margin-bottom:14px">One tap. Dr. Sharma sees this before your next cycle — it is how the dose gets adjusted.</p>
          <div class="faces">
            ${[['😀','Great',1],['🙂','Okay',2],['😐','Low',3],['😣','Rough',4],['😖','Bad',5]]
              .map(([f, l, v]) => `<button data-act="sev:${v}" class="${S.symptom.severity === v ? 'on' : ''}"><span class="f">${f}</span>${l}</button>`).join('')}
          </div>

          <div class="sectionhead"><h3>Where is it bothering you?</h3></div>
          <div class="bodymap">
            ${bodyMapSvg()}
            <div>
              <div class="zonelist">
                ${['Head','Mouth','Chest','Stomach','Hands','Feet'].map(z =>
                  `<button class="btn sm ${S.symptom.zones.has(z) ? 'primary' : 'ghost'}" data-act="zone:${z}">${z}</button>`).join('')}
              </div>
              <p class="tiny muted" style="margin-top:12px">Tap the figure or the words — whichever is easier.</p>
              <div class="btn-row">
                <button class="btn primary" data-act="savesym">Save today’s check-in</button>
                <button class="btn ghost">${I.mic} Speak instead</button>
              </div>
            </div>
          </div>
        </section>

        <section class="card sand">
          <div class="card-head"><span class="card-icon clay">${I.spark}</span><h3>What day 2 usually looks like</h3></div>
          <p style="font-size:14.5px;line-height:1.6">Nausea and tiredness are heaviest on days 2 and 3, then ease off by day 4 or 5.
            Your counts dip around day 7 to 10 — that is when to avoid crowds. This pattern matched your last two cycles.</p>
          ${sourceNote('Based on Dr. Sharma’s post-cycle instructions and your own logs from cycles 1 and 2. Not a prediction — a pattern.')}
        </section>
      </div>

      <div class="stack-lg">
        <section class="card">
          <div class="card-head"><span class="card-icon warm">${I.meds}</span><h3>Tonight’s medicines</h3>
            <span class="spacer"></span><span class="chip warn">${evening} due at 8 PM</span></div>
          ${S.slots[2].meds.map(m => medPill(m)).join('')}
          <div class="btn-row"><button class="btn ghost block" data-act="tab:meds">See the full day</button></div>
        </section>

        <section class="card">
          <div class="card-head"><span class="card-icon">${I.bell}</span><h3>Coming up</h3></div>
          <ul class="list">
            <li class="li"><span class="li-icon neutral">${I.temp}</span>
              <span class="li-main"><b>Temperature check</b><span>Twice a day until 19 May</span></span></li>
            <li class="li"><span class="li-icon neutral">${I.doc}</span>
              <span class="li-main"><b>Blood counts before Cycle 4</b><span>31 May · two days before the cycle</span></span></li>
            <li class="li"><span class="li-icon">${I.journey}</span>
              <span class="li-main"><b>Cycle 4 of 6</b><span>2 June · day-care ward 4B</span></span></li>
          </ul>
        </section>

        <div class="redflag">
          <h4>${I.alert} Don’t wait — call if</h4>
          <ul>${SEED.redFlags.slice(0, 3).map(f => `<li>${esc(f)}</li>`).join('')}</ul>
          <div class="btn-row"><button class="btn danger block" data-act="call">${I.phone} Call helpline</button></div>
        </div>
      </div>
    </div>`;
}

function bodyMapSvg() {
  const z = n => S.symptom.zones.has(n) ? 'zone on' : 'zone';
  return `<svg viewBox="0 0 100 210" aria-label="Body map">
    <circle class="${z('Head')}" data-act="zone:Head" cx="50" cy="20" r="15"/>
    <rect class="${z('Mouth')}" data-act="zone:Mouth" x="42" y="24" width="16" height="7" rx="3.5"/>
    <rect class="${z('Chest')}" data-act="zone:Chest" x="30" y="42" width="40" height="34" rx="10"/>
    <rect class="${z('Stomach')}" data-act="zone:Stomach" x="32" y="79" width="36" height="30" rx="10"/>
    <rect class="${z('Hands')}" data-act="zone:Hands" x="6" y="60" width="17" height="52" rx="8.5"/>
    <rect class="${z('Hands')}" data-act="zone:Hands" x="77" y="60" width="17" height="52" rx="8.5"/>
    <rect class="${z('Feet')}" data-act="zone:Feet" x="30" y="113" width="17" height="90" rx="8.5"/>
    <rect class="${z('Feet')}" data-act="zone:Feet" x="53" y="113" width="17" height="90" rx="8.5"/>
  </svg>`;
}

/* ============================================================
   JOURNEY
   ============================================================ */
function viewJourney() {
  const t = SEED.treatment;
  const items = SEED.journey.map(j => {
    const dot = j.state === 'now' ? 'now' : j.state === 'future' ? 'future' : '';
    const icon = j.state === 'done' ? I.check : j.state === 'now' ? I.spark : '';
    return `<article class="tl ${j.state === 'now' ? 'is-now' : ''}">
      <span class="tl-dot ${dot}">${icon}</span>
      <time>${esc(j.date)}</time>
      <b>${esc(j.title)}</b>
      <p>${esc(j.body)}</p>
      ${j.state === 'now' ? `<div class="btn-row"><button class="btn sm ghost" data-act="doc:d4">Discharge summary</button><button class="btn sm ghost" data-act="tab:meds">Medicines from this cycle</button></div>` : ''}
    </article>`;
  }).join('');

  return `
    ${pageHead('Your treatment journey', `${esc(SEED.patient.diagnosis)} · ${esc(SEED.patient.stage)}`)}
    <div class="cols">
      <section class="card"><div class="timeline">${items}</div></section>
      <div class="stack-lg">
        <section class="card tint">
          <div class="card-head"><h3>Where you are</h3></div>
          <p class="display" style="font-size:30px;margin-bottom:6px">Halfway through chemotherapy.</p>
          <p class="tiny muted">Three cycles done, three to go. After that comes radiation, then a daily tablet with three-monthly reviews.</p>
        </section>
        <div class="stats">
          <div class="stat"><b>${t.cycle}/${t.cycles}</b><span>Cycles completed</span></div>
          <div class="stat"><b>21</b><span>Days between cycles</span></div>
          <div class="stat"><b>2 Jun</b><span>Next session</span></div>
        </div>
        <section class="card">
          <div class="card-head"><span class="card-icon">${I.info}</span><h3>Your regimen</h3></div>
          <p style="font-size:14.5px"><b>${esc(t.regimen)}</b></p>
          <p class="tiny muted" style="margin-top:8px">Under ${esc(t.oncologist)}, Apollo Hospital, Jaipur.</p>
          ${sourceNote('Plan as recorded in your treatment chart on 14 April 2026.')}
        </section>
      </div>
    </div>`;
}

/* ============================================================
   RECORDS  (+ second-opinion bundle)
   ============================================================ */
function viewRecords() {
  const docs = SEED.documents.map(d => `
    <button class="doc ${S.picked.has(d.id) ? 'picked' : ''}" data-act="doc:${d.id}">
      <span class="thumb ${d.kind}">
        ${d.kind === 'scan' ? I.scan : I.doc}
        <span class="pick" data-act="pick:${d.id}" role="checkbox" aria-checked="${S.picked.has(d.id)}">${I.check}</span>
      </span>
      <span class="meta">
        <b>${esc(d.title)}</b><span>${esc(d.sub)}</span>
        <span class="row" style="margin-top:8px">
          <span class="chip neutral">${esc(d.tag)}</span>${d.fresh ? '<span class="chip">New</span>' : ''}
        </span>
      </span>
    </button>`).join('');

  const n = S.picked.size;
  return `
    ${pageHead('Records & imaging', 'Every report in one place — and a safe way to send them onward.')}
    <div class="sectionhead" style="margin-top:0"><h3>All documents</h3>
      <span class="muted">${SEED.documents.length} items</span><span class="spacer"></span>
      <span class="tiny muted">Tap the tick to add to a share bundle</span></div>
    <div class="docgrid">${docs}</div>

    ${n ? `<div class="selbar">
      <b>${n} document${n > 1 ? 's' : ''} selected</b>
      <span class="spacer"></span>
      <button class="btn sm ghosthero" data-act="clearpick">Clear</button>
      <button class="btn sm warm" data-act="bundle">${I.share} Build second-opinion bundle</button>
    </div>` : ''}

    <div class="cols" style="margin-top:var(--sp4)">
      <section class="card">
        <div class="card-head"><span class="card-icon clay">${I.spark}</span><h3>${esc(SEED.reportSummary.title)}</h3>
          <span class="spacer"></span><span class="chip draft">Draft · not yet confirmed</span></div>
        <ul class="list">${SEED.reportSummary.lines.map(l => `
          <li class="li"><span class="li-icon neutral">${I.check}</span><span class="li-main"><b style="font-weight:550">${esc(l)}</b></span></li>`).join('')}</ul>
        <p class="tiny muted" style="margin:12px 0">${esc(SEED.reportSummary.caveat)}</p>
        ${sourceNote(SEED.reportSummary.source)}
        <div class="btn-row"><button class="btn ghost" data-act="doc:d1">Open the signed report</button></div>
      </section>
      <section class="card">
        <div class="card-head"><span class="card-icon">${I.lock}</span><h3>Who has looked at what</h3></div>
        <div class="audit">${S.audit.map(a => `<div><time>${esc(a.when)}</time><span>${esc(a.what)}</span></div>`).join('')}</div>
        <p class="tiny muted" style="margin-top:14px">Every view is recorded and cannot be edited or deleted.</p>
      </section>
    </div>`;
}

function bundleSheet() {
  const picked = SEED.documents.filter(d => S.picked.has(d.id));
  return `
    <p class="muted" style="font-size:14.5px">A bundle is a sealed, watermarked copy that expires on its own. The originals never leave your record.</p>
    <section class="card flat" style="padding:16px">
      <div class="card-head"><h3>Including</h3><span class="spacer"></span><span class="chip">${picked.length} items</span></div>
      <ul class="list">${picked.map(d => `<li class="li"><span class="li-icon neutral">${d.kind === 'scan' ? I.scan : I.doc}</span>
        <span class="li-main"><b>${esc(d.title)}</b><span>${esc(d.sub)}</span></span></li>`).join('')}</ul>
    </section>
    <section class="card flat" style="padding:16px">
      <div class="card-head"><h3>Send to</h3></div>
      <div class="person"><span class="avatar">AP</span>
        <span class="li-main"><b>Dr. Arjun Patel</b><span>Surgical Oncologist · already in your care team</span></span>
        <span class="toggle on"><i></i></span></div>
      <div class="person"><span class="avatar" style="background:#EEF2F0;color:var(--ink-soft)">+</span>
        <span class="li-main"><b>Add a specialist by name or email</b><span>They get a link, not your login</span></span></div>
    </section>
    <section class="card flat" style="padding:16px">
      <div class="card-head"><h3>Rules on this bundle</h3></div>
      <ul class="list">
        <li class="li"><span class="li-icon">${I.clock}</span><span class="li-main"><b>Expires in 14 days</b><span>The link stops working by itself</span></span><span class="toggle on"><i></i></span></li>
        <li class="li"><span class="li-icon">${I.lock}</span><span class="li-main"><b>Watermarked with the recipient’s name</b><span>Discourages onward forwarding</span></span><span class="toggle on"><i></i></span></li>
        <li class="li"><span class="li-icon">${I.bell}</span><span class="li-main"><b>Tell me when it is opened</b><span>You’ll get a notification each time</span></span><span class="toggle on"><i></i></span></li>
      </ul>
    </section>
    ${sourceNote('Consent is recorded against your name, with a timestamp, and is shown in the access history. You can revoke this bundle at any time.')}
    <button class="btn primary block" data-act="sendbundle">Create bundle & share</button>`;
}

/* ============================================================
   MEDICINES
   ============================================================ */
function medPill(m) {
  return `<button class="pill ${m.taken ? 'taken' : ''}" data-act="med:${m.id}">
    <span class="tick" data-act="tick:${m.id}">${I.check}</span>
    <span class="li-main"><b>${esc(m.name)}</b><span>${esc(m.sub)}</span></span>
    <span class="li-right">${m.key ? '<span class="chip warn">Key</span>' : m.prn ? '<span class="chip neutral">If needed</span>' : ''}${I.chev}</span>
  </button>`;
}

function viewMeds() {
  const all = S.slots.flatMap(s => s.meds);
  const taken = all.filter(m => m.taken).length;
  const slots = S.slots.map(s => `
    <div class="slot">
      <div class="slot-time"><b>${s.time}</b><span>${esc(s.part)}</span></div>
      <div class="slot-body">${s.meds.map(medPill).join('')}</div>
    </div>`).join('');

  return `
    ${pageHead('Medicines', 'Tap any medicine to understand it — in plain language, tied to the prescription it came from.')}
    <div class="cols">
      <section class="card">
        <div class="card-head"><span class="card-icon warm">${I.meds}</span><h3>Today, 14 May</h3>
          <span class="spacer"></span><span class="chip ${taken === all.length ? '' : 'warn'}">${taken} of ${all.length} taken</span></div>
        <div class="meter warm" style="margin-bottom:var(--sp3)"><i style="width:${(taken / all.length) * 100}%"></i></div>
        ${slots}
      </section>
      <div class="stack-lg">
        <section class="card tint">
          <div class="card-head"><span class="card-icon">${I.lock}</span><h3>What this app will never do</h3></div>
          <p style="font-size:14.5px;line-height:1.6">CareCompass explains your medicines. It never changes a dose, never suggests stopping one, and never adds a new medicine. Only your doctor does that.</p>
        </section>
        <section class="card">
          <div class="card-head"><span class="card-icon">${I.doc}</span><h3>Current prescription</h3></div>
          <button class="li" style="border:0;width:100%;background:transparent" data-act="doc:d4">
            <span class="li-icon neutral">${I.doc}</span>
            <span class="li-main"><b>Prescription — 12 May 2026</b><span>Dr. Meera Sharma · Apollo Hospital</span></span>
            <span class="li-right">${I.chev}</span>
          </button>
        </section>
        <section class="card">
          <div class="card-head"><span class="card-icon danger">${I.alert}</span><h3>Missed a dose?</h3></div>
          <p style="font-size:14.5px">Don’t guess and don’t double up. Tap the medicine and read the “If you miss a dose” note, or ring the day-care nurse.</p>
          <div class="btn-row"><button class="btn ghost" data-act="call">${I.phone} Call Sister Anjali</button></div>
        </section>
      </div>
    </div>`;
}

function medSheet(id) {
  const d = SEED.medDetail[id];
  if (!d) {
    const m = S.slots.flatMap(s => s.meds).find(x => x.id === id);
    return `<p class="muted">${esc(m ? m.sub : '')}</p>
      <p style="font-size:14.5px">A plain-language explanation for this medicine would appear here, written from the prescription it came from.</p>`;
  }
  return `
    <div class="row"><span class="chip warn">Chemotherapy tablet</span><span class="chip neutral">Twice a day</span></div>
    <section class="card flat" style="padding:16px">
      <div class="card-head"><h3>What is it for?</h3></div><p style="font-size:14.5px">${esc(d.what)}</p>
    </section>
    <section class="card flat" style="padding:16px">
      <div class="card-head"><h3>How does it work?</h3></div><p style="font-size:14.5px">${esc(d.how)}</p>
    </section>
    <section class="card flat" style="padding:16px">
      <div class="card-head"><h3>How to take it</h3></div>
      <ul class="list">${d.taking.map(t => `<li class="li"><span class="li-icon">${I.check}</span><span class="li-main"><b style="font-weight:550">${esc(t)}</b></span></li>`).join('')}</ul>
    </section>
    <div class="card sand" style="padding:16px">
      <b style="font-size:14.5px">If you miss a dose</b>
      <p style="font-size:14.5px;margin-top:6px">${esc(d.missed)}</p>
    </div>
    <div class="redflag">
      <h4>${I.alert} Tell your team if you notice</h4>
      <ul>${d.watch.map(w => `<li>${esc(w)}</li>`).join('')}</ul>
    </div>
    ${sourceNote(d.source)}
    <div class="btn-row"><button class="btn ghost block" data-act="call">${I.phone} Ask the day-care nurse</button></div>`;
}

/* ============================================================
   CARE TEAM & CAREGIVERS
   ============================================================ */
function viewTeam() {
  const team = SEED.careTeam.map(c => `
    <div class="person">
      <span class="avatar">${esc(c.initials)}</span>
      <span class="li-main"><b>${esc(c.name)}</b><span>${esc(c.role)}</span>
        <span class="tiny muted" style="display:block;margin-top:4px">${esc(c.note)}</span></span>
      <span class="li-right">${c.phone ? `<button class="btn sm ghost" data-act="call">${I.phone}</button>` : ''}</span>
    </div>`).join('');

  const givers = S.caregivers.map(g => `
    <section class="card" style="padding:18px">
      <div class="row" style="margin-bottom:12px">
        <span class="avatar">${esc(g.initials.slice(0, 2))}</span>
        <span class="li-main"><b>${esc(g.name)}</b><span>${esc(g.rel)}</span></span>
      </div>
      <ul class="list">
        ${Object.keys(SEED.permLabels).map(k => `
          <li class="li" style="padding:10px 0">
            <span class="li-main"><b style="font-weight:550;font-size:14px">${esc(SEED.permLabels[k])}</b></span>
            <button class="toggle ${g.perms[k] ? 'on' : ''}" data-act="perm:${g.id}:${k}"
              role="switch" aria-checked="${g.perms[k]}" aria-label="${esc(SEED.permLabels[k])} for ${esc(g.name)}"><i></i></button>
          </li>`).join('')}
      </ul>
    </section>`).join('');

  return `
    ${pageHead('Your care team', 'The people treating you, and the people you have let in.')}
    <div class="cols">
      <div class="stack-lg">
        <section class="card">
          <div class="card-head"><span class="card-icon">${I.team}</span><h3>Clinical team</h3></div>
          ${team}
        </section>
        <div class="redflag" style="background:var(--danger-soft)">
          <h4>${I.phone} ${esc(SEED.helpline.label)}</h4>
          <p class="tiny" style="color:#7E3232;margin-top:6px">For fever, uncontrolled vomiting or bleeding — any hour, any day.</p>
          <div class="btn-row"><button class="btn danger block" data-act="call">${esc(SEED.helpline.number)}</button></div>
        </div>
      </div>
      <div class="stack-lg">
        <div class="sectionhead" style="margin:0"><h3>Family access</h3><span class="spacer"></span>
          <span class="tiny muted">You control every line</span></div>
        ${givers}
        <section class="card">
          <div class="card-head"><span class="card-icon">${I.lock}</span><h3>Access history</h3></div>
          <div class="audit">${S.audit.slice(0, 4).map(a => `<div><time>${esc(a.when)}</time><span>${esc(a.what)}</span></div>`).join('')}</div>
        </section>
      </div>
    </div>`;
}

/* ============================================================
   RENDER
   ============================================================ */
function render(animate) {
  const view = $('#view');
  view.innerHTML = ({ home: viewHome, journey: viewJourney, records: viewRecords, meds: viewMeds, team: viewTeam })[S.tab]();
  if (animate) { view.classList.remove('swap'); void view.offsetWidth; view.classList.add('swap'); }

  $('#railNav').innerHTML = TABS.map(t => `<li><button data-act="tab:${t.id}" class="${S.tab === t.id ? 'on' : ''}">
    ${I[t.icon]}<span>${t.label}</span>${t.id === 'home' && prepDone() < S.prep.length ? '<span class="dot"></span>' : ''}</button></li>`).join('');

  $('#tabbar').innerHTML = TABS.map(t => `<button data-act="tab:${t.id}" class="${S.tab === t.id ? 'on' : ''}"
    aria-current="${S.tab === t.id}"><span class="ic">${I[t.icon]}</span>${t.label}</button>`).join('');

  $('#railName').textContent = SEED.patient.name;
  $('#railSub').textContent = `${SEED.patient.short} · Cycle ${SEED.treatment.cycle} of ${SEED.treatment.cycles}`;
}

/* ============================================================
   ACTIONS
   ============================================================ */
function act(name) {
  const [k, a, b] = name.split(':');

  switch (k) {
    case 'tab':
      S.tab = a; render(true); $('#view').scrollTop = 0; window.scrollTo({ top: 0, behavior: 'smooth' }); break;

    case 'why': {
      const c = SEED.contexts[S.ctx];
      openSheet('Why this screen looks like this', `
        <p class="muted" style="font-size:14.5px">Your home screen changes with your situation. Right now it is set to
          <b style="color:var(--ink)">“${esc(c.label)}”</b>, because of these signals:</p>
        <ul class="list card flat" style="padding:8px 16px">
          ${c.signals.map(s => `<li class="li"><span class="li-icon neutral">${I.check}</span><span class="li-main"><b style="font-weight:550;font-size:14px">${esc(s)}</b></span></li>`).join('')}
        </ul>
        ${sourceNote('A fixed set of rules picks the context — not a guess by an AI. Location is used only because you allowed it, and can be switched off without losing anything else.')}
        <button class="btn ghost block" data-act="closesheet">Got it</button>`);
      break;
    }

    case 'checkin': S.checkedIn = true; render(); toast('Checked in — you are 4th in the queue'); break;

    case 'prep': {
      const p = S.prep.find(x => x.id === a); p.done = !p.done; render();
      if (prepDone() === S.prep.length) toast('All set for your consultation');
      break;
    }

    case 'questions':
      openSheet('Questions for Dr. Sharma', `
        <p class="muted" style="font-size:14.5px">These appear on the doctor’s screen when your consultation opens, so nothing gets forgotten in the room.</p>
        ${S.questions.map(q => `<div class="card flat" style="padding:14px"><b style="font-size:14.5px;font-weight:600">${esc(q.text)}</b></div>`).join('')}
        <button class="btn warm block" data-act="askq">${I.mic} Add another by voice</button>`);
      break;

    case 'askq': {
      const extra = [
        'Will I lose my hair again in the next cycle?',
        'Is it safe to take my thyroid tablet on chemo days?',
        'Can I get the flu vaccine during treatment?',
      ];
      S.questions.push({ id: 'q' + (S.questions.length + 1), text: extra[(S.questions.length - 3) % extra.length] });
      closeSheet(); render(); toast('Question added — Dr. Sharma will see it');
      break;
    }

    case 'water':
      S.hydration = Math.min(SEED.hydration.goal, +(S.hydration + 0.25).toFixed(2));
      render();
      if (S.hydration >= SEED.hydration.goal) toast('Full target reached — well done');
      break;

    case 'sev': S.symptom.severity = +a; render(); break;

    case 'zone':
      S.symptom.zones.has(a) ? S.symptom.zones.delete(a) : S.symptom.zones.add(a);
      render(); break;

    case 'savesym':
      if (!S.symptom.severity) { toast('Pick how you’re feeling first'); break; }
      S.audit.unshift({ when: 'Just now', what: 'You logged a symptom check-in' });
      toast('Saved — your team can see this before Cycle 4');
      break;

    case 'symptom': S.tab = 'home'; render(); toast('Scroll down to the feelings check-in'); break;

    case 'med': openSheet(
      (S.slots.flatMap(s => s.meds).find(m => m.id === a) || {}).name || 'Medicine', medSheet(a)); break;

    case 'tick': {
      const m = S.slots.flatMap(s => s.meds).find(x => x.id === a);
      m.taken = !m.taken; render(); if (m.taken) toast(`${m.name} marked as taken`);
      break;
    }

    case 'doc': {
      const d = SEED.documents.find(x => x.id === a);
      if (!d) break;
      openSheet(d.title, `
        <div class="row"><span class="chip neutral">${esc(d.tag)}</span><span class="chip">${esc(d.sub)}</span></div>
        <div class="card flat" style="padding:0;overflow:hidden">
          <div class="thumb ${d.kind}" style="height:220px;display:grid;place-items:center;color:#8FB0A7;
            background:${d.kind === 'scan' ? 'radial-gradient(circle at 50% 45%,#2E4A43,#0E1A17 70%)' : 'repeating-linear-gradient(180deg,#F7F6F1 0 9px,#EFEDE4 9px 10px)'}">
            ${d.kind === 'scan' ? I.scan : I.doc}
          </div>
        </div>
        ${a === 'd1' ? `<div class="card sand" style="padding:16px">
            <div class="row" style="margin-bottom:8px"><b style="font-size:14.5px">In plain language</b>
              <span class="chip draft">Draft</span></div>
            <ul style="display:flex;flex-direction:column;gap:6px">
              ${SEED.reportSummary.lines.map(l => `<li style="font-size:14px">• ${esc(l)}</li>`).join('')}</ul>
          </div>` : ''}
        ${sourceNote('Original document, stored encrypted. Any copy you share is watermarked and expires.')}
        <div class="btn-row">
          <button class="btn primary" data-act="pick:${d.id}">Add to share bundle</button>
          <button class="btn ghost" data-act="closesheet">Close</button>
        </div>`);
      break;
    }

    case 'pick':
      S.picked.has(a) ? S.picked.delete(a) : S.picked.add(a);
      closeSheet(); S.tab = 'records'; render();
      toast(S.picked.has(a) ? 'Added to bundle' : 'Removed from bundle');
      break;

    case 'clearpick': S.picked.clear(); render(); break;

    case 'bundle': openSheet('Second-opinion bundle', bundleSheet()); break;

    case 'sendbundle': {
      const n = S.picked.size;
      S.audit.unshift({ when: 'Just now', what: `You shared a ${n}-document bundle with Dr. Arjun Patel (expires in 14 days)` });
      S.picked.clear(); closeSheet(); render();
      toast('Bundle sent — expires in 14 days');
      break;
    }

    case 'perm': {
      const g = S.caregivers.find(x => x.id === a);
      g.perms[b] = !g.perms[b];
      S.audit.unshift({ when: 'Just now', what: `${g.perms[b] ? 'Granted' : 'Removed'} “${SEED.permLabels[b]}” for ${g.name}` });
      render(); toast(`${g.name}: ${SEED.permLabels[b].toLowerCase()} ${g.perms[b] ? 'on' : 'off'}`);
      break;
    }

    case 'call': toast('Prototype — a real call would start here'); break;

    case 'closesheet': closeSheet(); break;
  }
}

/* ---------------- wiring ---------------- */
document.addEventListener('click', e => {
  const target = e.target.closest('[data-act]');
  if (target) {
    e.preventDefault();
    e.stopPropagation();
    act(target.getAttribute('data-act'));
    return;
  }
  const dev = e.target.closest('[data-device]');
  if (dev) {
    document.querySelectorAll('[data-device]').forEach(b => b.classList.toggle('on', b === dev));
    document.body.classList.toggle('phone', dev.dataset.device === 'phone');
    return;
  }
  const ctx = e.target.closest('[data-ctx]');
  if (ctx) {
    document.querySelectorAll('[data-ctx]').forEach(b => b.classList.toggle('on', b === ctx));
    S.ctx = ctx.dataset.ctx; S.tab = 'home'; render(true);
    return;
  }
  if (e.target.id === 'scrim' || e.target.closest('#sheetClose')) closeSheet();
});

document.addEventListener('keydown', e => { if (e.key === 'Escape') closeSheet(); });

render();
