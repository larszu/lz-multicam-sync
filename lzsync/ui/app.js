/* LZ Multicam Sync – window logic. Talks to Python via window.pywebview.api. */
const $ = (id) => document.getElementById(id);
const nf = (d) => new Intl.NumberFormat('de-DE', { minimumFractionDigits: d, maximumFractionDigits: d });
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
};

const state = { source: null, log: [], output: null, pairs: [0, 0], edges: 0 };
const timeline = new Timeline($('timeline'));
const WEIGHT = { decode: [0, 20], pairs: [20, 75], fine: [75, 96] };
function bar(stage, done, total) {
  const [a, b] = WEIGHT[stage];
  $('prog-bar').style.width = `${a + (b - a) * (total ? done / total : 1)}%`;
}
const api = () => window.pywebview && window.pywebview.api;

function show(view) {
  for (const v of ['empty', 'running', 'result']) $('view-' + v).hidden = v !== view;
}

function setTheme(choice) {
  if (choice === 'system') document.documentElement.removeAttribute('data-theme');
  else document.documentElement.setAttribute('data-theme', choice);
  for (const b of document.querySelectorAll('[data-theme-choice]')) {
    b.setAttribute('aria-checked', String(b.dataset.themeChoice === choice));
  }
  store.set('lzsync-theme', choice);
  if (window.Timeline && typeof timeline !== 'undefined') requestAnimationFrame(() => timeline.readColors());
}

function setSource(path) {
  state.source = path || null;
  $('source-label').textContent = path ? path.split(/[\\/]/).pop() : 'Keine Quelle gewählt';
  $('drop-hint').textContent = path || 'FCPXML, .fcpxmld oder Medienordner hierher ziehen';
  $('drop').classList.toggle('has', !!path);
  $('run').disabled = !path;
}

async function pick(kind) {
  if (!api()) return;
  const p = kind === 'folder' ? await api().pick_folder() : await api().pick_file();
  if (p) setSource(p);
}

function td(text, cls) {
  const c = document.createElement('td');
  if (cls) c.className = cls;
  c.textContent = text;
  return c;
}

const fmtPpm = (v) => (v == null ? '–' : (v >= 0 ? '+' : '−') + nf(1).format(Math.abs(v)) + ' ppm');
const fmtOff = (v) => (v == null ? '–' : (v >= 0 ? '+' : '−') + nf(3).format(Math.abs(v)) + ' s');
const TC = { 'free-run': 'Free-Run', 'rec-run': 'Rec-Run', none: 'ohne' };

const app = {
  log(line) {
    state.log.push(line);
    const li = document.createElement('li');
    li.textContent = line.trim();
    const ol = $('live-log');
    ol.append(li);
    while (ol.children.length > 6) ol.firstElementChild.remove();  // the timeline stays in view
  },
  dropped(path) { setSource(path); },
  progress({ type, data }) {
    const st = $('tl-stage');
    if (type === 'layout') {
      $('tl-block').hidden = false;
      timeline.layout(data);
      const n = data.clips.length, d = new Set(data.clips.map((c) => c.dev)).size;
      st.textContent = `${n} Clips · ${d} Geräte · nach Timecode geordnet`;
      $('prog-bar').style.width = '2%';
    } else if (type === 'decode') {
      st.textContent = `Audio lesen ${data.done + 1} / ${data.total}`;
      bar('decode', data.done, data.total);
    } else if (type === 'pairs') {
      state.pairs = [data.done, data.total];
      st.textContent = `Abgleich ${nf(0).format(data.done)} / ${nf(0).format(data.total)} Paare · ${state.edges} Treffer`;
      bar('pairs', data.done, data.total);
    } else if (type === 'edge') {
      state.edges += 1;
      timeline.edge(data);
      st.textContent = `Abgleich ${nf(0).format(state.pairs[0])} / ${nf(0).format(state.pairs[1])} Paare · ${state.edges} Treffer`;
    } else if (type === 'fine') {
      st.textContent = `Feinabgleich ${data.done + 1} / ${data.total}`;
      bar('fine', data.done, data.total);
    } else if (type === 'final') {
      timeline.final(data);
      st.textContent = `Gelöst · Referenzuhr ${data.reference}`;
      $('prog-bar').style.width = '100%';
    }
  },
  failed(msg) {
    finish();
    show('result');
    for (const id of ['stats', 'output-row', 'missing']) $(id).hidden = true;
    $('error').hidden = false;
    $('error-text').textContent = msg;
    renderTables({ devices: [], review: [], warnings: [] });
  },
  done(d) {
    finish();
    show('result');
    $('error').hidden = true;
    $('stats').hidden = false;
    $('output-row').hidden = false;
    $('missing').hidden = !d.missing;
    if (d.missing) {
      $('missing-title').textContent = `${d.missing} von ${d.total} Mediendateien nicht gefunden`;
      $('missing-text').textContent = 'Ohne Medien kein Audio-Abgleich. Laufwerk anschließen oder „Pfad ersetzen“ ausfüllen und erneut synchronisieren.';
    }
    $('stat-placed').textContent = `${d.placed} / ${d.total}`;
    $('stat-ref').textContent = d.reference || '–';
    $('stat-review').textContent = String(d.review.length);
    state.output = d.output_xml || d.output;
    $('output-path').textContent = d.output_xml
      ? `${d.output_xml.split(/[\\/]/).pop()} – Resolve, Premiere · ${d.output.split(/[\\/]/).pop()} – Final Cut Pro`
      : d.output;
    renderTables(d);
  },
};
window.app = app;

function renderTables(d) {
  const dev = $('devices');
  dev.replaceChildren();
  for (const r of d.devices) {
    const tr = document.createElement('tr');
    if (r.reference) tr.className = 'ref';
    const name = td(r.name);
    if (r.reference) { const t = document.createElement('span'); t.className = 'tag'; t.textContent = 'Referenz'; name.append(t); }
    if (!r.video) { const t = document.createElement('span'); t.className = 'tag'; t.textContent = 'Audio'; name.append(t); }
    tr.append(name, td(TC[r.tc] || r.tc, 'dim'), td(fmtPpm(r.ppm), 'num'), td(fmtOff(r.tc_offset), 'num'),
      td(r.clips, 'num'), td(r.audio, 'num'), td(r.timecode, 'num'), td(r.chronology, 'num'),
      td(r.unplaced, 'num' + (r.unplaced ? '' : ' dim')));
    dev.append(tr);
  }
  const rv = $('review');
  rv.replaceChildren();
  for (const r of d.review) {
    const tr = document.createElement('tr');
    tr.append(td(`${r.device} / ${r.name}`), td(r.method, 'dim'),
      td(nf(0).format(r.confidence * 100) + ' %', 'num'), td(r.note, 'dim'));
    rv.append(tr);
  }
  $('review-block').hidden = !d.review.length;
  const w = $('warnings');
  w.replaceChildren(...d.warnings.map((t) => Object.assign(document.createElement('li'), { textContent: t })));
  $('warn-block').hidden = !d.warnings.length;
  $('final-log').replaceChildren(...state.log.map((t) => Object.assign(document.createElement('li'), { textContent: t.trim() })));
}

function finish() {
  const b = $('run');
  b.classList.remove('busy');
  b.textContent = 'Synchronisieren';
  b.disabled = !state.source;
}

async function run() {
  if (!state.source || !api()) return;
  state.log = []; state.edges = 0; state.pairs = [0, 0];
  $('live-log').replaceChildren();
  $('prog-bar').style.width = '0';
  const b = $('run');
  b.classList.add('busy');
  b.textContent = 'Synchronisiert …';
  b.disabled = true;
  show('running');
  await api().run({
    source: state.source,
    reference: $('reference').value,
    jammed: $('jammed').value,
    remap: $('remap').value,
    firstChannel: $('first-channel').checked,
    noAudio: $('no-audio').checked,
  });
}

/* wiring */
for (const b of document.querySelectorAll('[data-theme-choice]')) b.addEventListener('click', () => setTheme(b.dataset.themeChoice));
setTheme(store.get('lzsync-theme') || 'system');
$('pick-file').addEventListener('click', () => pick('file'));
$('empty-pick').addEventListener('click', () => pick('file'));
$('pick-folder').addEventListener('click', () => pick('folder'));
$('drop').addEventListener('click', () => pick('file'));
$('drop').addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick('file'); } });
$('run').addEventListener('click', run);
$('reveal').addEventListener('click', () => state.output && api() && api().reveal(state.output));
for (const ev of ['dragenter', 'dragover']) document.addEventListener(ev, (e) => { e.preventDefault(); $('drop').classList.add('over'); });
for (const ev of ['dragleave', 'drop']) document.addEventListener(ev, (e) => { e.preventDefault(); $('drop').classList.remove('over'); });

window.addEventListener('pywebviewready', async () => {
  $('version').textContent = 'v' + (await api().version());
});

/* Vorschau ohne Python (Screenshots, Gestaltung): index.html?demo=result */
const qp = new URLSearchParams(location.search);
if (qp.get('theme')) setTheme(qp.get('theme'));
function demoOverview() {
  const dev = (name, tc, ppm, off, clips, audio, t, c, u, extra = {}) =>
    ({ name, tc, ppm, tc_offset: off, clips, audio, timecode: t, chronology: c, unplaced: u, video: true, reference: false, ...extra });
  state.log = ['76 Clips, 9 Geräte', '  Audio dekodieren / Hüllkurven …', '  Grobabgleich: 719 Paare', '  263 Audio-Treffer, Feinabgleich …', '  Suchfenster: 2 Clip(s) ohne eigenen Treffer exakt nachgezogen'];
  return ({
    total: 76, placed: 75, reference: 'TENTACLE_1', missing: 0,
    output: '/Volumes/MEDIA/DEMO/Multicam-Dreh - lzsync.fcpxml',
    output_xml: '/Volumes/MEDIA/DEMO/Multicam-Dreh - lzsync.xml',
    devices: [
      dev('A7IV', 'rec-run', 5.0, null, 4, 4, 0, 0, 0),
      dev('FX3_A', 'rec-run', 12.0, null, 16, 16, 0, 0, 0),
      dev('FX3_B', 'rec-run', -9.0, null, 20, 20, 0, 0, 0),
      dev('FX3_C', 'free-run', 20.0, -85.37, 12, 12, 0, 0, 0),
      dev('FX3_D', 'free-run', 25.0, -48900.076, 15, 15, 0, 0, 0),
      dev('INSTA_01', 'free-run', -40.0, 16000.069, 6, 5, 1, 0, 0),
      dev('OTHER', 'rec-run', null, null, 1, 0, 0, 0, 1),
      dev('TENTACLE_1', 'free-run', 0, 0, 1, 1, 0, 0, 0, { video: false, reference: true }),
      dev('TENTACLE_2', 'free-run', 0, -0.017, 1, 1, 0, 0, 0, { video: false }),
    ],
    review: [{ device: 'OTHER', name: 'OTHER_0001.wav', method: 'offen', confidence: 0, note: 'kein Audio-Treffer' }],
    warnings: [],
  });
}
if (qp.get('demo') === '1' || qp.get('demo') === 'result') {
  $('version').textContent = 'v0.4.0';
  setSource('/Volumes/MEDIA/DEMO/Multicam-Dreh.fcpxmld');
  app.done(demoOverview());
}

/* Ablauf ohne Python vorspielen: index.html?demo=sim (Gestaltung, Aufnahmen) */
if (qp.get('demo') === 'sim') {
  let seed = 7;
  const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
  const T = 7200, clips = [], islands = [], truth = new Map();
  const devs = [['A7IV', 'rec', 4], ['FX3_A', 'rec', 16], ['FX3_B', 'rec', 20], ['FX3_C', 'free', 12],
    ['FX3_D', 'free', 15], ['INSTA_01', 'free', 6], ['OTHER', 'rec', 1], ['TENTACLE_1', 'free', 1], ['TENTACLE_2', 'free', 1]];
  let k = 0;
  for (const [dev, mode, n] of devs) {
    const video = !dev.startsWith('TENTACLE');
    let t = rnd() * 120;
    const isl = `${dev}#0`;
    if (mode === 'free') islands.push({ id: isl, dev, length: 0 });
    for (let i = 0; i < n; i++) {
      const dur = dev === 'OTHER' ? 120 : n === 1 ? T - t - 30 : Math.min(T - t - 10, 60 + rnd() * (T / n) * 0.9);
      if (dur < 5) break;
      const name = `${dev}_${String(i + 1).padStart(4, '0')}`;
      const id = mode === 'free' ? isl : `${dev}/${name}`;
      if (mode !== 'free') islands.push({ id, dev, length: dur });
      const first = clips.find((c) => c.island === id);
      clips.push({ k: k++, dev, name, dur, island: id, rel: first ? t - truth.get(first.k) : 0, video });
      truth.set(k - 1, t);
      t += dur + 10 + rnd() * 120;
    }
    if (mode === 'free') islands.find((i) => i.id === isl).length = t;
  }
  const start = (id) => Math.min(...clips.filter((c) => c.island === id).map((c) => truth.get(c.k)));
  const anchor = 'TENTACLE_1#0';
  const others = islands.map((i) => i.id)
    .filter((id) => id !== anchor && !id.startsWith('OTHER')).sort(() => rnd() - 0.5);
  const ev = [['layout', { clips, islands, modes: {} }]];
  for (let i = 0; i < islands.length; i++) ev.push(['decode', { done: i, total: islands.length }]);
  const total = 719;
  others.forEach((id, i) => {
    const via = i > 3 && rnd() < 0.4 ? others[Math.floor(rnd() * i)] : anchor;
    ev.push(['pairs', { done: Math.round(((i + 1) / others.length) * total * 0.95), total }]);
    ev.push(['edge', { a: via, b: id, lag: start(id) - start(via), z: 20 }]);
  });
  ev.push(['pairs', { done: total, total }]);
  for (let i = 0; i < 20; i++) ev.push(['fine', { done: i * 13, total: 263 }]);
  ev.push(['final', { reference: 'TENTACLE_1', clips: clips.map((c) => ({
    k: c.k, start: c.dev === 'OTHER' ? null : truth.get(c.k), group: c.dev === 'OTHER' ? 1 : 0,
    method: c.dev === 'OTHER' ? 'unplaced' : c.dur < 20 ? 'chronology' : 'audio' })) }]);
  setSource('/Volumes/MEDIA/DEMO/Multicam-Dreh.fcpxmld');
  $('version').textContent = 'v0.4.0';
  show('running');
  let i = 0;
  const speed = Number(qp.get('speed') || 1);
  const tick = () => {
    if (i >= ev.length) { setTimeout(() => app.done(demoOverview()), 900 / speed); return; }
    const [type, data] = ev[i++];
    app.progress({ type, data });
    if (type === 'edge') app.log(`  Treffer ${data.a} ↔ ${data.b}: ${data.lag >= 0 ? '+' : '−'}${nf(2).format(Math.abs(data.lag))} s`);
    setTimeout(tick, (type === 'layout' ? 1600 : type === 'edge' ? 160 : 40) / speed);
  };
  setTimeout(tick, 300);
}
