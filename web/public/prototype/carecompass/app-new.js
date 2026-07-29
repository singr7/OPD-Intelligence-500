/* ============================================================
   Passage (v2) — one river of moments.
   Paradigm notes:
   · No tabs, no pages, no modals. Everything opens in place.
   · Time is the only axis. Past above, now at rest, ahead below.
   · Lenses change what the river SHOWS, never where you are.
   · Access events, doses, scans and milestones are all one type:
     a moment. That is what makes the whole model fall out of
     a single idea.
   Reuses seed.js unchanged, so v1 and v2 show the same patient.
   ============================================================ */

const ico = (d, w = 18) =>
  `<svg viewBox="0 0 24 24" width="${w}" height="${w}" fill="none" stroke="currentColor"
    stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
const IC = {
  chev:  ico('<path d="M6 9l6 6 6-6"/>', 15),
  check: ico('<path d="M5 12.5 10 17.5 19 7"/>'),
  lock:  ico('<rect x="4.5" y="10.5" width="15" height="10" rx="2.5"/><path d="M8 10.5V8a4 4 0 0 1 8 0v2.5"/>', 15),
  drop:  ico('<path d="M12 3.5c3.4 4 6 6.8 6 10a6 6 0 0 1-12 0c0-3.2 2.6-6 6-10z"/>'),
  pill:  ico('<rect x="3" y="8.5" width="18" height="7" rx="3.5" transform="rotate(-40 12 12)"/><path d="M9 9l6 6"/>'),
  doc:   ico('<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4M9 12h6M9 16h6"/>'),
  scan:  ico('<path d="M4 8V6a2 2 0 0 1 2-2h2M16 4h2a2 2 0 0 1 2 2v2M20 16v2a2 2 0 0 1-2 2h-2M8 20H6a2 2 0 0 1-2-2v-2"/><circle cx="12" cy="12" r="3"/>'),
  eye:   ico('<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="2.8"/>'),
  temp:  ico('<path d="M10 13.5V6a2 2 0 1 1 4 0v7.5a4 4 0 1 1-4 0z"/>'),
  heart: ico('<path d="M12 20s-7-4.4-7-9.2A3.9 3.9 0 0 1 12 8a3.9 3.9 0 0 1 7 2.8C19 15.6 12 20 12 20z"/>'),
  phone: ico('<path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a1 1 0 0 1-1 1A16 16 0 0 1 4 5a1 1 0 0 1 1-1z"/>'),
  share: ico('<circle cx="6" cy="12" r="2.2"/><circle cx="17" cy="6" r="2.2"/><circle cx="17" cy="18" r="2.2"/><path d="M8 11 15 7.2M8 13.1l7 3.6"/>'),
  mic:   ico('<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3"/>'),
  spark: ico('<path d="M12 3.5 13.9 9.3 19.5 11l-5.6 1.7L12 18.5l-1.9-5.8L4.5 11l5.6-1.7z"/>'),
  step:  ico('<path d="M6 3v12a3 3 0 0 0 3 3h6"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="18" r="2"/>'),
};

/* ---------------- state ---------------- */
const V = {
  lens: 'all',
  severity: null,
  water: SEED.hydration.taken,
  meds: SEED.medSlots[2].meds.map(m => ({ ...m })),   // tonight's doses
  perms: { ...SEED.caregivers[0].perms },
  shareOpen: false,
  questions: SEED.questions.map(q => ({ ...q })),
};

const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;' }[c]));
const $ = s => document.querySelector(s);

function toast(msg) {
  const t = $('#toast'); t.textContent = msg; t.classList.add('show');
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove('show'), 2300);
}

/* ---------------- reusable fragments ---------------- */
const from = txt => `<p class="from">${IC.lock}<span>${esc(txt)}</span></p>`;
const rows = items => `<div class="rows">${items.map(r => `
  <div><span class="k ${r.k || ''}">${r.i || IC.check}</span>
    <span><b>${r.t}</b>${r.s ? `<span>${r.s}</span>` : ''}</span>
    ${r.right || ''}</div>`).join('')}</div>`;

function shareBlock(docTitle) {
  return `
    <div class="panel">
      <h3>Send for a second opinion</h3>
      ${rows([
        { i: IC.doc,   t: esc(docTitle), s: 'A sealed, watermarked copy — the original never leaves your record' },
        { i: IC.eye,   t: 'Dr. Arjun Patel', s: 'Surgical Oncologist · already in your care circle' },
        { i: IC.lock,  t: 'Expires in 14 days', s: 'The link stops working by itself', k: 'plain' },
      ])}
      <div class="btns" style="margin-top:14px">
        <button class="btn green" data-a="send">${IC.share} Send it</button>
        <button class="btn" data-a="unshare">Not now</button>
      </div>
    </div>
    ${from('Consent is recorded with your name and a timestamp, and appears in your care circle history. You can revoke it at any time.')}`;
}

/* ---------------- the now moment ---------------- */
function nowCard() {
  const c = SEED.contexts.home;
  const pct = Math.round((V.water / SEED.hydration.goal) * 100);
  const due = V.meds.filter(m => !m.taken).length;

  return `
    <span class="now-eyebrow"><i></i>Day ${SEED.treatment.daysSinceSession} after chemotherapy</span>
    <h1>${esc(c.headline)}</h1>
    <p class="lede">${esc(c.lede)}</p>

    <div class="now-acts">
      <div class="act" style="display:block">
        <b style="font-size:14.5px">How is today, really?</b>
        <span style="display:block;font-size:12.5px;color:#B4D9CD;margin-top:2px">
          Dr. Sharma reads this before deciding your next dose.</span>
        <div class="faces">
          ${[['😀','Good',1],['🙂','Okay',2],['😐','Low',3],['😣','Rough',4],['😖','Bad',5]].map(([f, l, v]) =>
            `<button data-a="sev:${v}" class="${V.severity === v ? 'on' : ''}"
               aria-pressed="${V.severity === v}"><span class="f">${f}</span>${l}</button>`).join('')}
        </div>
      </div>

      <div class="act" style="display:block">
        <div style="display:flex;align-items:center;gap:12px">
          <span class="act-k">${IC.drop}</span>
          <span class="act-t"><b>Water today</b><span>${V.water.toFixed(2).replace(/0$/, '')} of ${SEED.hydration.goal} litres</span></span>
          <button class="btn" data-a="water"
            style="min-height:34px;padding:0 14px;color:#08453A;background:#fff;border-color:#fff">+ 250 ml</button>
        </div>
        <div class="sip"><span class="bar"><i style="width:${Math.min(100, pct)}%"></i></span><b>${Math.min(100, pct)}%</b></div>
      </div>

      ${V.meds.map(m => `
        <button class="act ${m.taken ? 'done' : ''}" data-a="tick:${m.id}">
          <span class="act-k">${m.taken ? IC.check : IC.pill}</span>
          <span class="act-t"><b>${esc(m.name)}</b><span>${esc(m.sub)}</span></span>
          <span class="act-x">${m.taken ? 'TAKEN' : '8:00 PM'}</span>
        </button>`).join('')}
    </div>

    <p style="position:relative;margin-top:16px;font-size:12.5px;color:#9CCDBD">
      ${due ? `${due} thing${due > 1 ? 's' : ''} left tonight. Nothing else is asked of you today.`
            : 'Everything for today is done. Rest is the whole plan now.'}</p>`;
}

/* ---------------- the river ---------------- */
function moments() {
  const d = SEED.discharge, t = SEED.treatment;

  return [
    /* ---------- what is behind ---------- */
    { when: '16 March', title: 'The day it was named', phase: 'past', lens: ['treatment'],
      note: SEED.journey[0].body,
      detail: () => `${rows([
        { t: 'Invasive ductal carcinoma, left breast', s: 'ER+ / PR+ · HER2-negative' },
        { t: 'Stage II (T2 N1 M0)', s: 'One of three sampled nodes involved' },
      ])}${from('Core biopsy report, Apollo Hospital, 16 March 2026.')}` },

    { when: '28 March', title: 'Surgery — lumpectomy', phase: 'past', lens: ['treatment'],
      note: SEED.journey[1].body,
      detail: () => rows([{ i: IC.doc, t: 'Surgery notes', s: '28 Mar 2026 · Theatre', k: 'plain' }]) },

    { when: '14 April', title: 'Chemotherapy began', phase: 'past', lens: ['treatment', 'meds'],
      note: `${t.regimen} — six cycles, one every 21 days.`,
      detail: () => `${rows([
        { i: IC.step, t: 'Six cycles planned', s: 'Cycles 1, 2 and 3 are behind you' },
        { i: IC.pill, t: 'Given by infusion at the day-care ward', s: 'With tablets to take at home between cycles' },
      ])}${from('Treatment plan recorded by Dr. Meera Sharma, 14 April 2026.')}` },

    { when: '5 May', title: 'CT chest', phase: 'past', lens: ['reports'],
      note: 'Read by Dr. R. Nair. Nothing new appeared.',
      tags: [{ t: 'Imaging', c: '' }, { t: 'Draft summary', c: 'draft' }],
      detail: () => `
        <div class="panel">
          <h3>In plain language <span class="tag draft" style="margin-left:6px">not yet confirmed</span></h3>
          ${rows(SEED.reportSummary.lines.map(l => ({ t: esc(l) })))}
          <p style="font-size:12.5px;color:var(--ink-2);margin-top:12px">${esc(SEED.reportSummary.caveat)}</p>
        </div>
        ${from(SEED.reportSummary.source)}
        <div class="btns">
          <button class="btn" data-a="scan">${IC.scan} Open the scan</button>
          <button class="btn" data-a="share">${IC.share} Second opinion</button>
        </div>
        <div id="shareSlot">${V.shareOpen ? shareBlock('CT chest — 5 May 2026') : ''}</div>` },

    { when: '10 May', title: 'A share link expired on its own', phase: 'past', lens: ['people'],
      note: 'Dr. Kapoor’s access to your records ended automatically, as set.',
      detail: () => from('Every share you create expires by itself. Nothing stays open unless you say so.') },

    { when: '12 May', title: 'Cycle 3 of 6', phase: 'past', lens: ['treatment', 'meds'],
      note: 'Given at day-care ward 4B. Dose unchanged — your counts held up.',
      tags: [{ t: 'Halfway', c: 'gold' }],
      detail: () => `
        <div class="panel">
          <h3>What changed in your medicines that day</h3>
          ${rows(d.changes.map(c => ({
            i: IC.pill, k: c.kind === 'stop' ? 'red' : 'gold',
            t: esc(c.name), s: esc(c.change),
          })))}
        </div>
        ${from('Taken from the prescription revised by Dr. Meera Sharma. Passage explains a dose. It never changes one.')}
        ${rows([{ i: IC.doc, t: 'Discharge summary — Cycle 3', s: 'Signed by Dr. Meera Sharma', k: 'plain' }])}` },

    { when: 'Today, 08:40', title: 'Blood counts came back', phase: 'past', lens: ['reports'],
      note: 'Acceptable. This is what clears you for Cycle 4.',
      tags: [{ t: 'Acceptable', c: 'green' }],
      detail: () => from('Laboratory report, Apollo Hospital, reported today at 08:40.') },

    { when: 'Today, 09:12', title: 'Rahul looked at your blood counts', phase: 'past', lens: ['people'],
      note: 'He has the access you gave him. You can change it in a tap.',
      detail: () => `
        <div class="panel">
          <h3>What Rahul can see</h3>
          <div class="rows">
            ${Object.keys(SEED.permLabels).map(k => `
              <div><span><b>${esc(SEED.permLabels[k])}</b></span>
                <button class="toggle ${V.perms[k] ? 'on' : ''}" data-a="perm:${k}"
                  role="switch" aria-checked="${V.perms[k]}"
                  aria-label="${esc(SEED.permLabels[k])} for Rahul Mehta"><i></i></button></div>`).join('')}
          </div>
        </div>
        ${from('Every view is written to a history that cannot be edited or deleted — including your own.')}` },

    /* ---------- the present ---------- */
    { id: 'now', phase: 'now', lens: ['treatment', 'body', 'meds', 'reports', 'people'] },

    { when: 'What usually happens', title: 'Day 2 and 3 are the heaviest', phase: 'now', lens: ['body'],
      note: 'Then it eases. Your counts dip around day 7 to 10 — that is the week to avoid crowds.',
      detail: () => `${rows([
        { i: IC.heart, t: 'This matched your last two cycles', s: 'Nausea peaked on day 2, gone by day 5' },
        { i: IC.temp,  t: 'Days 7–10 are the careful ones', s: 'Not because you will be ill — because you are less defended' },
      ])}${from('Dr. Sharma’s post-cycle instructions, and your own logs from cycles 1 and 2. A pattern, not a prediction.')}` },

    /* ---------- what is ahead ---------- */
    { when: 'Tonight, 8:00 PM', title: 'Two tablets, after food', phase: 'future', lens: ['meds'],
      note: 'Capecitabine is the one that matters. Never on an empty stomach.',
      detail: () => `
        <div class="panel">
          <h3>Capecitabine 500 mg</h3>
          <p style="font-size:14px;color:var(--ink-2);margin-bottom:12px">${esc(SEED.medDetail.m4.what)}</p>
          ${rows(SEED.medDetail.m4.taking.map(x => ({ t: esc(x) })))}
        </div>
        <div class="panel" style="background:#F7F1E4;border-color:#EADFC8">
          <h3>If you miss a dose</h3>
          <p style="font-size:14px">${esc(SEED.medDetail.m4.missed)}</p>
        </div>
        ${from(SEED.medDetail.m4.source)}` },

    { when: 'Twice a day, until 19 May', title: 'Take your temperature', phase: 'future', lens: ['body'],
      note: 'Fever is the one thing that must be caught early. Everything else can wait for morning.',
      detail: () => `${rows([
        { i: IC.temp, t: '100.4°F (38°C) or higher — even once', s: 'Ring the helpline straight away, day or night', k: 'red' },
      ])}<div class="btns"><button class="btn red" data-a="call">${IC.phone} ${SEED.helpline.number}</button></div>` },

    { when: 'Until 26 May', title: 'Skip the crowded places', phase: 'future', lens: ['body'],
      note: 'Not forever — just the fortnight when your counts are lowest.' },

    { when: '31 May', title: 'Blood counts before Cycle 4', phase: 'future', lens: ['reports'],
      note: 'Two days ahead, so there is time to act if anything is low.' },

    { when: '2 June', title: 'Cycle 4 of 6', phase: 'future', lens: ['treatment'],
      note: 'Day-care ward 4B. After this one, two remain.',
      tags: [{ t: 'Next session', c: 'green' }],
      detail: () => `
        <div class="panel">
          <h3>Things you wanted to ask</h3>
          ${rows(V.questions.map(q => ({ i: IC.mic, t: esc(q.text), k: 'plain' })))}
          <div class="btns" style="margin-top:14px">
            <button class="btn" data-a="ask">${IC.mic} Add one by voice</button>
          </div>
        </div>
        <p style="font-size:12.5px;color:var(--ink-2)">These travel with you — they appear on Dr. Sharma’s screen
          when your consultation opens, so nothing is lost in the room.</p>` },

    { when: 'July', title: 'Radiation therapy', phase: 'future', lens: ['treatment'],
      note: 'About 15 sessions, once chemotherapy is finished.' },

    { when: 'August, then five years', title: 'A tablet a day, and reviews every three months', phase: 'future',
      lens: ['treatment'], note: 'The long, quiet part. Most of your life goes back to being your life.' },
  ];
}

/* ---------------- render ---------------- */
const LENSES = [
  { id: 'all', label: 'Everything' },
  { id: 'treatment', label: 'Treatment' },
  { id: 'body', label: 'My body' },
  { id: 'meds', label: 'Medicines' },
  { id: 'reports', label: 'Reports' },
  { id: 'people', label: 'Care circle' },
];

let MOMENTS = [];

function momentHtml(m, i) {
  if (m.id === 'now') {
    return `<article class="moment now" data-i="${i}" data-phase="now" data-lens="${m.lens.join(' ')}">
      <div class="now-card" id="nowCard">${nowCard()}</div></article>`;
  }
  const tags = (m.tags || []).map(t => `<span class="tag ${t.c || ''}">${esc(t.t)}</span>`).join('');
  const hasDetail = typeof m.detail === 'function';
  return `
    <article class="moment" data-i="${i}" data-phase="${m.phase}" data-lens="${m.lens.join(' ')}">
      <span class="m-when">${esc(m.when)}</span>
      <${hasDetail ? 'button' : 'div'} class="m-head" ${hasDetail ? `data-a="open:${i}" aria-expanded="false"` : ''}>
        <h2 class="m-title">${esc(m.title)}</h2>
        ${m.note ? `<p class="m-note">${esc(m.note)}</p>` : ''}
        ${tags ? `<span class="m-tags">${tags}</span>` : ''}
        ${hasDetail ? `<span class="m-more">More ${IC.chev}</span>` : ''}
      </${hasDetail ? 'button' : 'div'}>
      ${hasDetail ? `<div class="m-detail"><div class="m-inner"><div class="m-body" data-body="${i}">${m.detail()}</div></div></div>` : ''}
    </article>`;
}

function render() {
  MOMENTS = moments();
  $('#river').innerHTML = MOMENTS.map(momentHtml).join('');
  $('#lenses').innerHTML = LENSES.map(l =>
    `<button data-a="lens:${l.id}" class="${V.lens === l.id ? 'on' : ''}">${l.label}</button>`).join('');
  applyLens();
  observe();
}

function applyLens() {
  document.querySelectorAll('.moment').forEach(el => {
    const show = V.lens === 'all' || el.dataset.lens.split(' ').includes(V.lens);
    el.dataset.hidden = show ? '0' : '1';
  });
}

/* ---------------- scroll-linked focus ---------------- */
let io;
function observe() {
  if (io) io.disconnect();
  io = new IntersectionObserver(entries => {
    entries.forEach(e => e.target.classList.toggle('is-focus', e.isIntersecting));
    const focused = document.querySelector('.moment.is-focus');
    if (!focused) return;
    const m = MOMENTS[+focused.dataset.i];
    document.body.dataset.phase = focused.dataset.phase;
    $('#cDate').textContent = m.id === 'now' ? 'Today' : (m.when || 'Today');
    $('#cPhase').textContent = focused.dataset.phase === 'past' ? 'behind'
                             : focused.dataset.phase === 'future' ? 'ahead' : 'now';
  }, { rootMargin: '-45% 0px -45% 0px', threshold: 0 });
  document.querySelectorAll('.moment').forEach(el => io.observe(el));

  // magnet appears whenever the present is off-screen
  const nowEl = document.querySelector('.moment.now');
  if (magnetIo) magnetIo.disconnect();
  magnetIo = new IntersectionObserver(([e]) => {
    const mg = $('#magnet');
    mg.hidden = e.isIntersecting;
    requestAnimationFrame(() => mg.classList.toggle('show', !e.isIntersecting));
    $('#magnetLabel').textContent =
      e.boundingClientRect.top > 0 ? 'Back to now ↓' : 'Back to now ↑';
  }, { threshold: 0 });
  if (nowEl) magnetIo.observe(nowEl);
}
let magnetIo;

function toNow(behavior = 'smooth') {
  const el = document.querySelector('.moment.now');
  if (el) el.scrollIntoView({ block: 'center', behavior });
}

/* ---------------- actions ---------------- */
function refreshNow() { const n = $('#nowCard'); if (n) n.innerHTML = nowCard(); }
function refreshBody(i) {
  const b = document.querySelector(`[data-body="${i}"]`);
  if (b) b.innerHTML = MOMENTS[i].detail();
}
const indexOfMomentContaining = el => {
  const a = el.closest('.moment'); return a ? +a.dataset.i : -1;
};

document.addEventListener('click', e => {
  const btn = e.target.closest('[data-a]');
  if (btn) {
    const [k, v] = btn.getAttribute('data-a').split(':');

    if (k === 'lens') {
      V.lens = v; render();
      // keep the present in view when the river re-forms
      const stillThere = V.lens === 'all' || document.querySelector('.moment.now[data-hidden="0"]');
      if (stillThere) toNow('auto');
      return;
    }

    if (k === 'open') {
      const el = document.querySelector(`.moment[data-i="${v}"]`);
      const open = el.classList.toggle('is-open');
      const head = el.querySelector('.m-head');
      if (head.tagName === 'BUTTON') head.setAttribute('aria-expanded', String(open));
      head.querySelector('.m-more').firstChild.textContent = open ? 'Less ' : 'More ';
      if (open) setTimeout(() => {
        const r = el.getBoundingClientRect();
        if (r.bottom > innerHeight - 40) el.scrollIntoView({ block: 'center', behavior: 'smooth' });
      }, 520);
      return;
    }

    if (k === 'sev') { V.severity = +v; refreshNow(); toast('Logged — your team sees this before Cycle 4'); return; }
    if (k === 'water') {
      V.water = Math.min(SEED.hydration.goal, +(V.water + 0.25).toFixed(2));
      refreshNow();
      if (V.water >= SEED.hydration.goal) toast('That is the full target for today');
      return;
    }
    if (k === 'tick') {
      const m = V.meds.find(x => x.id === v); m.taken = !m.taken; refreshNow();
      if (m.taken) toast(`${m.name} — marked as taken`);
      return;
    }
    if (k === 'perm') {
      V.perms[v] = !V.perms[v];
      refreshBody(indexOfMomentContaining(btn));
      toast(`Rahul: ${SEED.permLabels[v].toLowerCase()} ${V.perms[v] ? 'on' : 'off'}`);
      return;
    }
    if (k === 'share' || k === 'unshare') {
      V.shareOpen = k === 'share';
      refreshBody(indexOfMomentContaining(btn));
      return;
    }
    if (k === 'send') {
      V.shareOpen = false; refreshBody(indexOfMomentContaining(btn));
      toast('Sent to Dr. Patel — expires in 14 days');
      return;
    }
    if (k === 'ask') {
      const more = [
        'Will I lose my hair again in the next cycle?',
        'Is it safe to take my thyroid tablet on chemo days?',
        'Can I get the flu vaccine during treatment?',
      ];
      V.questions.push({ id: 'q' + (V.questions.length + 1), text: more[(V.questions.length - 3) % more.length] });
      refreshBody(indexOfMomentContaining(btn));
      toast('Saved for 2 June');
      return;
    }
    if (k === 'scan') { toast('Prototype — the scan viewer would open here'); return; }
    if (k === 'call') { toast('Prototype — a real call would start here'); return; }
  }

  if (e.target.closest('#magnet')) { toNow(); return; }

  if (e.target.closest('#sosBtn')) {
    const p = $('#sosPanel'), b = $('#sosBtn'), open = p.hidden;
    p.hidden = !open;
    b.setAttribute('aria-expanded', String(open));
    if (open) p.innerHTML = `
      <h3>Ring the helpline if any of these</h3>
      <ul>${SEED.redFlags.map(f => `<li>${esc(f)}</li>`).join('')}</ul>
      <button class="btn red wide" data-a="call">${IC.phone} ${SEED.helpline.number}</button>
      <p style="font-size:12px;color:var(--ink-2);margin-top:10px">
        ${esc(SEED.helpline.label)} — any hour. Do not wait until morning.</p>`;
    return;
  }
  if (!e.target.closest('.sos')) { $('#sosPanel').hidden = true; $('#sosBtn').setAttribute('aria-expanded', 'false'); }
});

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') { $('#sosPanel').hidden = true; }
  if (e.key === 'n' && !e.metaKey && !e.ctrlKey) toNow();
});

/* ---------------- go ---------------- */
render();
toNow('auto');
addEventListener('load', () => toNow('auto'));
