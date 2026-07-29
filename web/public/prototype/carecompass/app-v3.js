/* ============================================================
   Good Days (v3)
   The bet: patients don't need another filing cabinet. They need
   to see FORWARD — what today will feel like, when it lifts, and
   whether what they feel right now is inside the expected shape.

     you report → sharper forecast → you can plan a life
                → so you keep reporting

   Everything else the patient needs — intake, visit prep,
   records, medicines — hangs off the same time axis instead of
   becoming a tab. The intake is the clearest payoff: the app
   already holds the check-ins, so the form is already written
   and the patient reviews rather than recalls.
   ============================================================ */

const S3 = {
  stage: 'treatment',
  sel: 0,
  logged: [],
  todayBurden: null,
  openPlan: null,
  askOut: null,
  intakeOk: new Set(),
  intakeSent: false,
  openMed: null,
  shareFor: null,
  addOpen: false,
};

const $  = s => document.querySelector(s);
const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;' }[c]));

const stage  = () => GD.stages[S3.stage];
const series = () => GD.series[S3.stage];
const N      = () => series().forecast.length;
const point  = i => {
  const s = series(), n = Math.max(0, Math.min(N() - 1, i));
  return { i:n, burden:s.forecast[n], name:s.names[n], risk:s.risks[n],
           line:s.lines[n], lab:s.labels[n] };
};

function toast(m) {
  const t = $('#toast'); t.textContent = m; t.classList.add('show');
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove('show'), 2600);
}

const IC = {
  scan:'<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M4 8V6a2 2 0 0 1 2-2h2M16 4h2a2 2 0 0 1 2 2v2M20 16v2a2 2 0 0 1-2 2h-2M8 20H6a2 2 0 0 1-2-2v-2"/><circle cx="12" cy="12" r="3"/></svg>',
  wa:'<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M20 12a8 8 0 1 1-3.2-6.4"/><path d="M4 20l1.4-3.6"/><path d="M9 9.5c.6 3 2.5 4.9 5.5 5.5l1-1.6 2 .9v1.7c-4.4.6-8.5-3.5-7.9-7.9h1.7l.9 2z"/></svg>',
  file:'<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/><path d="M12 11v6M9 14h6"/></svg>',
  ask:'<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M4 5h16v11H8l-4 4z"/><path d="M12 8v3M12 13.2v.1"/></svg>',
  doc:'<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4M9 12h6M9 16h4"/></svg>',
  share:'<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="6" cy="12" r="2.2"/><circle cx="17" cy="6" r="2.2"/><circle cx="17" cy="18" r="2.2"/><path d="M8 11 15 7.2M8 13.1l7 3.6"/></svg>',
  tick:'<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5 10 17.5 19 7"/></svg>',
  mic:'<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3"/></svg>',
};

/* ============================================================
   TERRAIN — the signature
   ============================================================ */
const W = 900, PL = 30, PR = 30, PT = 34, PB = 56;
let H = 300;
const narrow = () => innerWidth < 760;
const X = i => PL + i * ((W - PL - PR) / (N() - 1));
const Y = b => (H - PB) - (b / 10) * (H - PB - PT);

function smooth(pts) {
  if (pts.length < 2) return '';
  let d = `M${pts[0][0].toFixed(1)},${pts[0][1].toFixed(1)}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] || pts[i], p1 = pts[i], p2 = pts[i + 1], p3 = pts[i + 2] || p2;
    d += `C${(p1[0] + (p2[0] - p0[0]) / 6).toFixed(1)},${(p1[1] + (p2[1] - p0[1]) / 6).toFixed(1)} ` +
         `${(p2[0] - (p3[0] - p1[0]) / 6).toFixed(1)},${(p2[1] - (p3[1] - p1[1]) / 6).toFixed(1)} ` +
         `${p2[0].toFixed(1)},${p2[1].toFixed(1)}`;
  }
  return d;
}

function terrainSvg() {
  const st = stage().track, s = series();
  const pts   = s.forecast.map((b, i) => [X(i), Y(b)]);
  const upper = s.forecast.map((b, i) => [X(i), Y(Math.min(10, b + 1.1))]);
  const lower = s.forecast.map((b, i) => [X(i), Y(Math.max(0, b - 1.1))]);

  const area = `${smooth(pts)} L${X(N() - 1)},${H - PB} L${X(0)},${H - PB} Z`;
  const band = `${smooth(upper)} ${smooth([...lower].reverse()).replace('M', 'L')} Z`;

  const wins = st.windows.map(w => {
    const x1 = X(w.from - .5), x2 = X(w.to + .5);
    const good = w.kind === 'good';
    return `<rect x="${x1}" y="${PT - 12}" width="${x2 - x1}" height="${H - PB - PT + 12}"
      fill="${good ? 'rgba(111,216,176,.10)' : 'rgba(242,169,59,.09)'}"
      stroke="${good ? 'rgba(111,216,176,.30)' : 'rgba(242,169,59,.30)'}"
      stroke-dasharray="3 4" rx="10"/>`;
  }).join('');

  const lastLine = s.last
    ? `<path d="${smooth(s.last.map((b, i) => [X(i), Y(b)]))}" fill="none"
         stroke="rgba(157,191,182,.5)" stroke-width="1.6" stroke-dasharray="4 5"/>` : '';

  const dots = S3.logged.map(l =>
    `<circle cx="${X(l.i)}" cy="${Y(l.burden)}" r="5.5" fill="#fff" stroke="#071614" stroke-width="2.5"/>`).join('');

  const t = st.today;
  const todayMark = t === null ? '' : `
    <line x1="${X(t)}" y1="${PT - 12}" x2="${X(t)}" y2="${H - PB}" stroke="rgba(255,255,255,.35)"
      stroke-width="1.5" stroke-dasharray="2 4"/>
    <circle cx="${X(t)}" cy="${Y(point(t).burden)}" r="7" fill="#fff" filter="url(#glow)"/>`;

  // before treatment there is no "today" on this axis — the line has not started
  const startMark = st.startsIn === null ? '' : `
    <line x1="${X(0)}" y1="${PT - 12}" x2="${X(0)}" y2="${H - PB}"
      stroke="rgba(242,169,59,.7)" stroke-width="2"/>`;

  const sd = point(S3.sel);
  return `
  <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Forecast of how each ${st.unit} ahead is likely to feel">
    <defs>
      <linearGradient id="terr" x1="0" y1="${PT}" x2="0" y2="${H - PB}" gradientUnits="userSpaceOnUse">
        <stop offset="0"   stop-color="#F2A93B" stop-opacity=".92"/>
        <stop offset=".42" stop-color="#E8B45F" stop-opacity=".38"/>
        <stop offset="1"   stop-color="#6FD8B0" stop-opacity=".10"/>
      </linearGradient>
      <linearGradient id="terrLine" x1="0" y1="${PT}" x2="0" y2="${H - PB}" gradientUnits="userSpaceOnUse">
        <stop offset="0" stop-color="#FFB63F"/><stop offset=".34" stop-color="#F2A93B"/>
        <stop offset=".62" stop-color="#A8DCA6"/><stop offset="1" stop-color="#6FD8B0"/>
      </linearGradient>
      <filter id="glow" x="-50%" y="-50%" width="200%" height="200%">
        <feGaussianBlur stdDeviation="6" result="b"/>
        <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
      </filter>
    </defs>
    ${wins}
    <path d="${band}" fill="rgba(255,255,255,.05)"/>
    ${lastLine}
    <path d="${area}" fill="url(#terr)"/>
    <path d="${smooth(pts)}" fill="none" stroke="url(#terrLine)" stroke-width="3"
          stroke-linecap="round" filter="url(#glow)"/>
    ${dots}${todayMark}${startMark}
    <line x1="${X(S3.sel)}" y1="${PT - 12}" x2="${X(S3.sel)}" y2="${H - PB}"
      stroke="rgba(255,255,255,.75)" stroke-width="1.5"/>
    <circle cx="${X(S3.sel)}" cy="${Y(sd.burden)}" r="10" fill="none" stroke="#fff" stroke-width="2"/>
  </svg>`;
}

function terrainLabels() {
  const st = stage().track, s = series(), last = N() - 1;
  const step = narrow() ? 6 : 3;
  const rank = i => i === st.today ? 4 : i === S3.sel ? 3 : i === last ? 2 : i === 0 ? 1 : 0;
  const minGap = narrow() ? 3 : 2;

  const cand = new Set([0, last, S3.sel]);
  if (st.today !== null) cand.add(st.today);
  s.forecast.forEach((_, i) => { if (i % step === 0) cand.add(i); });

  const keep = [];
  [...cand].sort((a, b) => a - b).forEach(i => {
    const prev = keep[keep.length - 1];
    if (prev !== undefined && i - prev < minGap) {
      if (rank(i) > rank(prev)) keep[keep.length - 1] = i;
      return;
    }
    keep.push(i);
  });

  const labs = keep.map(i => {
    const shift = i === 0 ? 'translateX(0)' : i === last ? 'translateX(-100%)' : 'translateX(-50%)';
    const sub = i === st.today ? 'today' : (st.startsIn !== null && i === 0 ? 'starts' : s.labels[i][1]);
    return `<span class="t-lab ${i === S3.sel ? 'on' : ''}"
      style="left:${(X(i) / W) * 100}%;transform:${shift}">
      <b>${esc(s.labels[i][0])}</b>${esc(sub)}</span>`;
  }).join('');

  const wins = st.windows.map((w, k) => `
    <span class="t-win ${w.kind}"
      style="left:${((X(w.from) + X(w.to)) / 2 / W) * 100}%;top:${narrow() && k === 1 ? 42 : 12}px">
      ${esc(narrow() ? w.short : w.label)}</span>`).join('');

  const sd = point(S3.sel);
  return `<div class="t-labels">${labs}</div>${wins}
    <span class="t-chip" style="left:${(X(S3.sel) / W) * 100}%;top:${(Y(sd.burden) / H) * 100}%">
      ${esc(sd.name)}</span>`;
}

function drawTerrain() {
  H = narrow() ? 430 : 300;
  const st = stage().track;
  $('#tTitle').textContent = st.axis.charAt(0).toUpperCase() + st.axis.slice(1);
  $('#tHint').textContent = st.personal
    ? `Drag across the ${st.unit}s. Every one of them has a name.`
    : 'Drag across the days. This is the typical shape — not yet yours.';
  $('#terrain').innerHTML = terrainSvg() + terrainLabels();
  $('#tLegend').innerHTML = st.legend.map((l, i) =>
    `<span><i class="${['fore','last','you'][i] || 'fore'}"></i>${esc(l)}</span>`).join('') +
    `<span>Higher means the ${st.unit} takes more from you.</span>`;
  drawRhythm();
}

/* ============================================================
   RHYTHM — medicines on the same axis as the terrain.
   Not a list: a shape, so you can see which days ask something
   of you and which do not.
   ============================================================ */
function drawRhythm() {
  const m = stage().meds;
  if (m.mode !== 'rhythm') { $('#rhythm').innerHTML = ''; return; }
  const st = stage().track;

  // full-width tracks, so a bar's left edge lands on exactly the same day as
  // the terrain above it. The label rides inside the bar, Gantt-style.
  const bars = m.bars.map(b => {
    const l = (X(b.from - .5) / W) * 100, r = (X(b.to + .5) / W) * 100;
    const wide = r - l > 26;
    return `<div class="ry-row">
      <span class="ry-track">
        <button class="ry-bar ${b.k}" style="left:${l}%;width:${r - l}%"
          ${b.id ? `data-a="med:${b.id}"` : ''} title="${esc(b.t)} — ${esc(b.s)}">
          <b>${esc(b.t)}</b>${wide ? `<span>${esc(b.s)}</span>` : ''}
        </button>
      </span></div>`;
  }).join('');

  // staggered, because these cluster at the end of a cycle and would collide
  const marks = m.marks.map((k, i) => {
    const atEnd = k.i > N() - 4;
    return `<span class="ry-mark ${atEnd ? 'end' : ''}" style="left:${(X(k.i) / W) * 100}%;top:${i % 2 ? 22 : 2}px">
      ${esc(k.t)}</span>`;
  }).join('');

  $('#rhythm').innerHTML = `
    <div class="ry-head"><h3>${esc(m.title)}</h3><p>${esc(m.sub)}</p></div>
    <div class="ry-body">
      ${st.today === null ? '' : `<span class="ry-today" style="left:${(X(st.today) / W) * 100}%"></span>`}
      ${bars}
      <div class="ry-marks">${marks}</div>
    </div>
    ${S3.openMed ? medPanel(S3.openMed) : ''}
    <div class="ry-tonight">
      <b>Tonight, 8:00 PM</b>
      ${m.tonight.map(t => `<span class="pill ${t.key ? 'amber' : ''}">${esc(t.name)}</span>`).join('')}
      <span class="tiny">Tap any bar above to understand what it is for.</span>
    </div>`;
}

function medPanel(id) {
  const d = stage().meds.detail[id];
  if (!d) return '';
  return `
    <div class="medbox">
      <div class="medbox-h"><h4>${esc(d.name)}</h4>
        <button class="x" data-a="medclose" aria-label="Close">✕</button></div>
      <p>${esc(d.what)}</p>
      <ul>${d.taking.map(t => `<li>${esc(t)}</li>`).join('')}</ul>
      <p class="miss"><b>If you miss a dose.</b> ${esc(d.missed)}</p>
      <p class="src">${esc(d.source)} Good Days explains a dose — it never changes one.</p>
    </div>`;
}

/* ============================================================
   LEAD / READOUT
   ============================================================ */
function drawLead() {
  const L = stage().lead;
  $('#barCycle').textContent = stage().chip;
  const h1 = L.h1.map(part => part.replace(L.hi, `<span class="hi">${esc(L.hi)}</span>`)).join('<br>');
  $('#lead').innerHTML = `
    <span class="eyebrow"><i></i>${esc(L.eyebrow)}</span>
    <h1>${h1}</h1>
    <p class="sub">${esc(L.sub)}</p>`;
}

/* "Wed 14" for a day, "Week of 12 Aug" for a week */
const whenText = x => stage().track.unit === 'week'
  ? `Week of ${x.lab[0]} ${x.lab[1]}`
  : `${x.lab[1]} ${x.lab[0]}`;

function drawReadout() {
  const x = point(S3.sel), st = stage().track;
  const isToday = x.i === st.today, isPast = st.today !== null && x.i < st.today;
  const riskWords = ['Normal', 'Slightly low', 'Low', 'At their lowest'][x.risk];
  const good = ['Yours', 'Good', 'Clear'].includes(x.name);

  $('#readout').innerHTML = `
    <div class="card r-main">
      <div class="r-top">
        <span class="r-name ${x.name.toLowerCase().replace(/[^a-z]/g, '')}">${esc(x.name)}</span>
        <span class="r-when">${esc(whenText(x))}${isToday ? ' · today' : ''}</span>
      </div>
      <p class="r-line">${esc(x.line)}</p>
      <div class="r-meta">
        ${isPast ? '<span class="pill">Already behind you</span>' : ''}
        ${x.risk >= 3 ? '<span class="pill rose">Infection risk — highest of the cycle</span>' : ''}
        ${good ? '<span class="pill mint">Good day — spend it</span>' : ''}
        ${x.burden >= 6.5 ? '<span class="pill amber">Plan nothing</span>' : ''}
      </div>
    </div>
    <div class="card gauge">
      <h3>What is actually happening</h3>
      <div class="gauge-row"><span>How you feel</span>
        <span class="gauge-bar"><i class="b" style="width:${x.burden * 10}%"></i></span></div>
      <div class="gauge-row"><span>Your defences</span>
        <span class="gauge-bar"><i class="r" style="width:${25 + x.risk * 25}%"></i></span></div>
      <p class="gauge-note">Counts: <b>${riskWords.toLowerCase()}</b>.
        ${x.risk >= 3
          ? 'You will feel completely normal this week. That is the trap — the risk is invisible, so it gets ignored.'
          : 'These two rarely move together, which is why feeling fine is not the same as being safe.'}</p>
    </div>`;
}

/* ============================================================
   CHECK-IN
   ============================================================ */
const FACES = [
  { f:'🙂', l:'Light',      b:1.5 },
  { f:'😐', l:'Manageable', b:3.5 },
  { f:'😣', l:'Heavy',      b:5.5 },
  { f:'😖', l:'Very heavy', b:7.5 },
  { f:'😭', l:'Unbearable', b:9.5 },
];

function drawCheck() {
  const c = stage().check, st = stage().track;
  const b = S3.todayBurden;
  const at = st.today === null ? null : point(st.today).burden;
  const lo = at === null ? null : at - 1.1, hi = at === null ? null : at + 1.1;
  let verdict = '';

  if (b !== null) {
    if (at === null) {
      verdict = `
        <div class="verdict inside">
          <h3>Baseline set.</h3>
          <p>There is nothing to compare it against yet, and that is exactly the point.
             From your first infusion onward, every check-in is measured against this one.</p>
          <p class="fine">This is the single most useful thing you can give the next six months,
             and it takes one tap before anything has even started.</p>
        </div>`;
    } else if (b > hi) {
      verdict = `
        <div class="verdict outside">
          <h3>This is heavier than your own last two cycles at this point.</h3>
          <p>Not dangerous by itself — but it is a real change, and it is the kind of thing
             your team would rather hear on a Wednesday than on a Sunday night.</p>
          <p class="fine">Sent to ${esc(GD.nurse)} with your cycle 1 and 2 logs alongside it,
             so she can see the difference rather than take your word for how bad it is.</p>
          <div class="btns">
            <button class="btn rose" data-a="call">Call the helpline now</button>
            <button class="btn" data-a="flags">What counts as an emergency</button>
          </div>
        </div>`;
    } else if (b >= lo) {
      verdict = `
        <div class="verdict inside">
          <h3>This is exactly the shape we expected.</h3>
          <p>You are sitting inside the range your own history drew. Nothing here needs a
             phone call, and it eases from tomorrow afternoon.</p>
          <p class="fine">Knowing that this is normal is not a small thing. It is most of what
             people ring the helpline at 3am to find out.</p>
        </div>`;
    } else {
      verdict = `
        <div class="verdict inside">
          <h3>Lighter than your last two cycles at this point.</h3>
          <p>Worth noticing. The anti-sickness change made on 12 May is the most likely reason,
             and that is worth telling Dr. Sharma at the next visit.</p>
          <p class="fine">Your forecast has shifted down slightly for the next four days.</p>
        </div>`;
    }
  }

  $('#check').innerHTML = `
    <div class="c-card">
      <h2>${b === null ? esc(c.title) : esc(c.done)}</h2>
      <p>${b === null ? esc(c.sub) : esc(c.doneSub)}</p>
      <div class="faces">
        ${FACES.map((f, i) => `
          <button data-a="log:${i}" class="${b === f.b ? 'on' : ''}" aria-pressed="${b === f.b}">
            <span class="f">${f.f}</span>${f.l}</button>`).join('')}
      </div>
      ${verdict}
    </div>`;
}

/* ============================================================
   NEXT VISIT — intake written from the check-ins, plus prep
   The payoff of the whole loop: the patient reviews rather
   than recalls, and the doctor gets structure instead of "fine".
   ============================================================ */
function drawVisit() {
  const v = stage().visit;
  const confirmed = S3.intakeOk.size;
  const total = v.intake.length;

  const rows = v.intake.map((r, i) => {
    const ok = S3.intakeOk.has(i);
    return `
      <div class="ix ${ok ? 'ok' : ''} ${r.flag ? 'flag' : ''}">
        <button class="ix-tick" data-a="ix:${i}" aria-pressed="${ok}"
          aria-label="Confirm ${esc(r.k)}">${IC.tick}</button>
        <span class="ix-t">
          <b>${esc(r.k)}</b>
          <span class="ix-v">${esc(r.v)}</span>
          ${r.detail ? `<span class="ix-d">${esc(r.detail)}</span>` : ''}
        </span>
        ${r.flag ? '<span class="pill amber">Worth raising</span>' : ''}
        <button class="ix-fix" data-a="fix:${i}">Change</button>
      </div>`;
  }).join('');

  $('#visit').innerHTML = `
    <div class="s-head">
      <h2>Your next visit</h2>
      <p>${esc(v.who)} · ${esc(v.what)}${v.before ? ` — with ${esc(v.before.what.toLowerCase())} on ${esc(v.before.when)} first` : ''}</p>
    </div>

    <div class="v-grid">
      <div class="card v-intake">
        <div class="v-h">
          <h3>${esc(v.intakeTitle)}</h3>
          <span class="pill ${v.intake[0].src === 'logs' ? 'mint' : ''}">
            ${v.intake[0].src === 'logs' ? 'From your check-ins' : 'You fill this once'}</span>
        </div>
        <p class="v-note">${esc(v.intakeNote)}</p>
        <p class="ix-how">Tick what is right. Change what is not.</p>
        <div class="ix-list">${rows}</div>
        <div class="v-foot">
          <span class="tiny">${confirmed} of ${total} confirmed</span>
          <button class="btn ${S3.intakeSent ? '' : 'mint'}" data-a="send"
            ${S3.intakeSent ? 'disabled' : ''}>
            ${S3.intakeSent ? 'Sent — she will read it before you arrive' : `Send to ${esc(v.who)}`}</button>
        </div>
      </div>

      <div class="v-side">
        <div class="card">
          <h3>Questions you saved</h3>
          <ul class="qlist">${v.questions.map(q => `<li>${esc(q)}</li>`).join('')}</ul>
          <button class="btn sm" data-a="askq">${IC.mic} Add one by voice</button>
        </div>
        <div class="card">
          <h3>${esc(v.prepTitle)}</h3>
          <ul class="blist">
            ${v.bring.map(b => `<li class="${b.done ? 'done' : ''}">
              <i>${b.done ? IC.tick : ''}</i><span><b>${esc(b.t)}</b><span>${esc(b.s)}</span></span></li>`).join('')}
          </ul>
        </div>
      </div>
    </div>`;
}

/* ============================================================
   RECORDS — organised, addable, sendable, and honest about
   what is missing
   ============================================================ */
function drawRecords() {
  const groups = GD.recordGroups
    .map(g => ({ ...g, items: g.items.filter(i => i.stages.includes(S3.stage)) }))
    .filter(g => g.items.length);
  const count = groups.reduce((n, g) => n + g.items.length, 0);
  const gaps = GD.gaps[S3.stage] || [];

  $('#records').innerHTML = `
    <div class="s-head">
      <h2>Everything, in one place</h2>
      <p>${esc(stage().recordsNote)}</p>
    </div>

    <div class="rc-bar">
      <span class="tiny">${count} documents · ${groups.length} groups</span>
      <span class="sp"></span>
      <button class="btn sm ${S3.addOpen ? 'mint' : ''}" data-a="add">+ Add a report</button>
      <button class="btn sm" data-a="share:all">${IC.share} Share</button>
    </div>

    ${S3.addOpen ? `
      <div class="addbox">
        ${GD.addWays.map(w => `
          <button class="addway" data-a="added:${esc(w.t)}">
            <i>${IC[w.k]}</i><span><b>${esc(w.t)}</b><span>${esc(w.s)}</span></span></button>`).join('')}
      </div>` : ''}

    ${S3.shareFor ? shareBox() : ''}

    <div class="rc-groups">
      ${groups.map(g => `
        <div class="rc-g">
          <h4>${esc(g.g)} <span>${g.items.length}</span></h4>
          ${g.items.map(i => `
            <div class="rc-i">
              <i class="rc-ic">${IC.doc}</i>
              <span class="rc-t"><b>${esc(i.t)}</b><span>${esc(i.d)} · ${esc(i.src)}</span></span>
              ${i.fresh ? '<span class="pill mint">New</span>' : ''}
              ${i.auto ? '' : '<span class="pill">Yours</span>'}
              <button class="rc-s" data-a="share:${esc(i.t)}" aria-label="Share ${esc(i.t)}">${IC.share}</button>
            </div>`).join('')}
        </div>`).join('')}
    </div>

    ${gaps.length ? `
      <div class="gapbox">
        <h4>What is not here yet</h4>
        <p class="tiny">A complete file for your diagnosis usually includes these. Nobody else is
          checking, so the app does.</p>
        ${gaps.map(g => `<div class="gap"><b>${esc(g.t)}</b><span>${esc(g.s)}</span></div>`).join('')}
      </div>` : ''}

    ${GD.activeShares.length ? `
      <div class="rc-active">
        <h4>Open right now</h4>
        ${GD.activeShares.map(a => `
          <div class="rc-i"><i class="rc-ic">${IC.share}</i>
            <span class="rc-t"><b>${esc(a.t)}</b><span>${esc(a.s)}</span></span>
            <button class="btn sm" data-a="revoke">Revoke</button></div>`).join('')}
      </div>` : ''}`;
}

function shareBox() {
  return `
    <div class="sharebox">
      <div class="v-h"><h3>Send ${S3.shareFor === 'all' ? 'your records' : `“${esc(S3.shareFor)}”`}</h3>
        <button class="x" data-a="shareclose" aria-label="Close">✕</button></div>
      <div class="who">
        ${GD.shareWith.map(w => `
          <button class="whobtn" data-a="sent:${esc(w.t)}">
            <b>${esc(w.t)}</b><span>${esc(w.s)}</span></button>`).join('')}
      </div>
      <ul class="rules">
        <li>Expires in 14 days, by itself</li>
        <li>Watermarked with their name</li>
        <li>You are told each time it is opened</li>
        <li>Revocable at any moment, from here</li>
      </ul>
      <p class="src">A sealed copy is sent. The originals never leave your record, and consent is
        logged with a timestamp.</p>
    </div>`;
}

/* ============================================================
   PLANS
   ============================================================ */
function drawPlans() {
  const st = stage();
  $('#plans').innerHTML = `
    <div class="s-head"><h2>${esc(st.plansTitle)}</h2><p>${esc(st.plansSub)}</p></div>
    <div id="planList">${st.plans.map(p => {
      const x = point(p.i), open = S3.openPlan === p.id;
      return `
        <article class="plan ${open ? 'open' : ''}">
          <button class="plan-head" data-a="plan:${p.id}" aria-expanded="${open}">
            <span class="plan-day"><b>${esc(p.when.split(' ')[0])}</b><span>${esc(p.when.split(' ')[1] || '')}</span></span>
            <span class="plan-t"><b>${esc(p.title)}</b><span>${esc(p.who)} · ${esc(x.name)}</span></span>
            <span class="stamp ${p.verdict}">${p.verdict === 'yes' ? 'Go' : p.verdict === 'careful' ? 'Go carefully' : 'Move it'}</span>
          </button>
          <div class="plan-body"><div><div class="plan-in">
            <p class="answer">${esc(p.answer)}</p>
            <p class="because">${esc(p.why)}</p>
            <div class="how">${p.how.map((h, i) => `<div><i>${i + 1}</i><span>${esc(h)}</span></div>`).join('')}</div>
            ${p.escalate ? `<p class="warn">${esc(p.escalate)}</p>` : ''}
          </div></div></div>
        </article>`;
    }).join('')}</div>
    <div class="ask" id="ask">
      <h3>Thinking about another ${stage().track.unit}?</h3>
      <p>Ask before you commit, not after you cancel.</p>
      <div class="ask-row">
        ${askable().map(i => `<button data-a="ask:${i}">${esc(series().labels[i][0])} ${esc(series().labels[i][1])}</button>`).join('')}
      </div>
      <div class="ask-out">${S3.askOut !== null ? answerFor(S3.askOut) : ''}</div>
    </div>`;
}

const askable = () => {
  const n = N(), t = stage().track.today ?? 0;
  return [t + 3, t + 7, t + 11, t + 15].filter(i => i < n);
};

function answerFor(i) {
  const x = point(i);
  const s = series();
  const best = s.forecast
    .map((b, k) => ({ b, k }))
    .filter(o => Math.abs(o.k - i) <= 4 && o.b < 2.0)
    .sort((a, b) => a.b - b.b)[0];

  const map = {
    Yours:['yes','Yes — and this is one to spend, not save.'],
    Good: ['yes','Yes. A good day, most likely.'],
    Clear:['yes','Yes. Nothing in the forecast touches it.'],
    Lifting:['yes','Yes, with a quiet evening after it.'],
    Steady:['careful','Yes, if you keep it short.'],
    Stirring:['careful','Yes — though this is where the waiting usually starts.'],
    Rising:['careful','Yes, but expect to be distracted.'],
    Guarded:['careful','Only if you can keep away from crowds.'],
    Tender:['no','Not this one. You would spend it enduring rather than being there.'],
    Heavy:['no','No. Move it — two days later is a completely different day.'],
    'Scan week':['no','Not that week. You would spend it waiting for a phone call.'],
    'The wait':['no','Not that week. It is historically your hardest of the year.'],
  };
  const [verdict, line] = map[x.name] || ['careful', 'Probably — check with your team first.'];

  const extra = verdict === 'careful' && x.risk >= 3
    ? 'Your counts are at their lowest, so the risk is other people, not effort. Small rooms over big ones, and take your temperature that night.'
    : verdict === 'no' && best
      ? `${esc(s.labels[best.k][0])} is <b>${esc(s.names[best.k].toLowerCase())}</b> — that is the day to ask for instead.`
      : esc(x.line);

  return `
    <div class="verdict ${verdict === 'no' ? 'outside' : 'inside'}">
      <h3>${esc(whenText(x))} · ${esc(x.name)}</h3>
      <p style="font-family:var(--serif);font-size:19px;margin-bottom:8px">${esc(line)}</p>
      <p class="fine">${extra}</p>
    </div>`;
}

/* ============================================================
   WHY
   ============================================================ */
function drawWhy() {
  const personal = stage().track.personal;
  $('#why').innerHTML = `
    <h2>How it knows</h2>
    <ul>${GD.provenance.map(p => `<li>${esc(p)}</li>`).join('')}</ul>
    <p class="caveat">${personal
      ? 'This forecasts how days are likely to <i>feel</i>, from your own history and the known timing of this regimen.'
      : 'Right now this is the typical shape for this regimen, not yours. It becomes yours after your first cycle — the app says so rather than pretending otherwise.'}
      It says nothing about whether treatment is working, it never changes a dose, and when your
      reports fall outside the expected shape it does one thing only: it puts a human being in the loop.</p>`;
}

function drawStages() {
  $('#stageSeg').innerHTML = GD.stageOrder.map(k =>
    `<button data-a="stage:${k}" class="${S3.stage === k ? 'on' : ''}">${esc(GD.stages[k].label)}</button>`).join('');
}

/* ============================================================
   interaction
   ============================================================ */
function select(i) {
  const n = Math.max(0, Math.min(N() - 1, Math.round(i)));
  if (n === S3.sel) return;
  S3.sel = n; drawTerrain(); drawReadout();
}

function pointToIndex(ev) {
  const r = $('#terrain').getBoundingClientRect();
  return (((ev.clientX - r.left) / r.width) * W - PL) / ((W - PL - PR) / (N() - 1));
}

let dragging = false;
$('#terrain').addEventListener('pointerdown', e => {
  dragging = true; $('#terrain').setPointerCapture(e.pointerId); select(pointToIndex(e));
});
$('#terrain').addEventListener('pointermove', e => { if (dragging) select(pointToIndex(e)); });
addEventListener('pointerup', () => { dragging = false; });

addEventListener('keydown', e => {
  if (e.key === 'ArrowLeft')  { select(S3.sel - 1); e.preventDefault(); }
  if (e.key === 'ArrowRight') { select(S3.sel + 1); e.preventDefault(); }
  if (e.key === 'Escape') { $('#sosPanel').hidden = true; }
});

let rz; addEventListener('resize', () => { clearTimeout(rz); rz = setTimeout(drawTerrain, 160); });

document.addEventListener('click', e => {
  const b = e.target.closest('[data-a]');
  if (b) {
    const raw = b.getAttribute('data-a');
    const k = raw.slice(0, raw.indexOf(':') === -1 ? raw.length : raw.indexOf(':'));
    const v = raw.indexOf(':') === -1 ? '' : raw.slice(raw.indexOf(':') + 1);

    switch (k) {
      case 'stage':   setStage(v); return;
      case 'log': {
        const f = FACES[+v];
        S3.todayBurden = f.b;
        const t = stage().track.today;
        if (t !== null) S3.logged = S3.logged.filter(l => l.i !== t).concat({ i: t, burden: f.b });
        drawCheck(); drawTerrain();
        toast(stage().track.personal ? 'Logged — your forecast just got sharper' : 'Baseline saved');
        return;
      }
      case 'ix':
        S3.intakeOk.has(+v) ? S3.intakeOk.delete(+v) : S3.intakeOk.add(+v);
        drawVisit(); return;
      case 'fix':  toast('Prototype — you would correct the line here'); return;
      case 'send':
        S3.intakeSent = true; drawVisit();
        toast(`Sent — ${GD.oncologist} sees it before you walk in`); return;
      case 'askq': toast('Prototype — a voice note would be added here'); return;
      case 'add':  S3.addOpen = !S3.addOpen; drawRecords(); return;
      case 'added':
        S3.addOpen = false; drawRecords(); toast(`${v} — added to your records`); return;
      case 'share': S3.shareFor = v; drawRecords();
        $('.sharebox')?.scrollIntoView({ block:'nearest', behavior:'smooth' }); return;
      case 'shareclose': S3.shareFor = null; drawRecords(); return;
      case 'sent': S3.shareFor = null; drawRecords();
        toast(`Sent to ${v} — expires in 14 days`); return;
      case 'revoke': toast('Access revoked — the link is dead as of now'); return;
      case 'med': S3.openMed = S3.openMed === v ? null : v; drawRhythm(); return;
      case 'medclose': S3.openMed = null; drawRhythm(); return;
      case 'plan': S3.openPlan = S3.openPlan === v ? null : v; drawPlans(); return;
      case 'ask':  S3.askOut = +v; drawPlans();
        $('.ask-out')?.scrollIntoView({ block:'nearest', behavior:'smooth' }); return;
      case 'call': toast('Prototype — a real call would start here'); return;
      case 'flags': openSos(); return;
    }
  }

  if (e.target.closest('#sosBtn')) { $('#sosPanel').hidden ? openSos() : closeSos(); return; }
  if (!e.target.closest('.sos')) closeSos();
});

function openSos() {
  $('#sosPanel').hidden = false;
  $('#sosBtn').setAttribute('aria-expanded', 'true');
  $('#sosPanel').innerHTML = `
    <h3>Ring straight away if any of these</h3>
    <ul>${GD.redFlags.map(f => `<li>${esc(f)}</li>`).join('')}</ul>
    <button class="btn rose" style="width:100%" data-a="call">${esc(GD.helpline.number)}</button>
    <p style="font-size:12px;color:var(--soft);margin-top:12px">${esc(GD.helpline.label)}.
      During the careful week, a fever is an emergency, not a wait-and-see.</p>`;
}
function closeSos() { $('#sosPanel').hidden = true; $('#sosBtn').setAttribute('aria-expanded', 'false'); }

/* ============================================================
   stage switching — the app is not the same product at every
   point in the journey, and pretending otherwise is the flaw
   in most of these apps
   ============================================================ */
function setStage(k) {
  S3.stage = k;
  const st = stage().track;
  S3.sel = st.today ?? 0;
  S3.logged = series().logged.map(l => ({ ...l }));
  S3.todayBurden = null; S3.openPlan = null; S3.askOut = null;
  S3.intakeOk = new Set(); S3.intakeSent = false;
  S3.openMed = null; S3.shareFor = null; S3.addOpen = false;
  drawAll();
  scrollTo({ top: 0, behavior: 'smooth' });
}

function drawAll() {
  drawStages(); drawLead(); drawTerrain(); drawReadout();
  drawCheck(); drawVisit(); drawRecords(); drawPlans(); drawWhy();
}

setStage('treatment');
