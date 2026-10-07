/* Live timeline: real clips per device, moving to where the engine puts them.
   Phases: input order → timecode islands → audio matches (provisional) → solver result. */
(function () {
  const H_LANE = 22, GAP = 4, LABEL = 128, PAD = 12;

  class Timeline {
    constructor(canvas, meta) {
      this.cv = canvas; this.meta = meta; this.ctx = canvas.getContext('2d');
      this.clips = []; this.islands = new Map(); this.lanes = []; this.edges = [];
      this.flashes = []; this.view = { a: 0, b: 1, ta: 0, tb: 1 };
      this.phase = 'idle'; this.anchor = null;
      this.reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
      this.readColors();
      new ResizeObserver(() => this.resize()).observe(canvas);
      matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => this.readColors());
      const loop = () => { this.step(); this.draw(); requestAnimationFrame(loop); };
      requestAnimationFrame(loop);
    }

    readColors() {
      const probe = document.createElement('span');
      document.body.append(probe);
      const get = (v) => { probe.style.color = `var(${v})`; return getComputedStyle(probe).color; };
      this.c = {
        bg: get('--surface-0'), lane: get('--surface-1'), line: get('--line-1'), line2: get('--line-2'),
        text: get('--text-muted'), strong: get('--text-strong'), accent: get('--accent'),
        steel: get('--stahlblau'), line3: get('--line-3'),
      };
      probe.remove();
    }

    resize() {
      const r = this.cv.getBoundingClientRect(), d = devicePixelRatio || 1;
      this.cv.width = Math.round(r.width * d); this.cv.height = Math.round(r.height * d);
      this.ctx.setTransform(d, 0, 0, d, 0, 0); this.w = r.width; this.h = r.height;
    }

    /* ---- engine events ---- */
    layout(d) {
      this.phase = 'input'; this.edges = []; this.anchor = null;
      const order = (a, b) => a.localeCompare(b, 'de', { numeric: true });
      const devs = [...new Set(d.clips.map((c) => c.dev))].sort(order);
      const video = devs.filter((v) => d.clips.some((c) => c.dev === v && c.video));
      this.lanes = [...video, ...devs.filter((v) => !video.includes(v))];
      this.islands = new Map(d.islands.map((i) => [i.id, { ...i, pos: null }]));
      this.clips = d.clips.map((c) => ({ ...c, x: 0, tx: 0, state: 'pending', alpha: 0, talpha: 1 }));
      // input order: files of one device back to back, as in an unsynced project
      for (const dev of this.lanes) {
        let t = 0;
        for (const c of this.clips.filter((c) => c.dev === dev).sort((a, b) => order(a.name, b.name))) {
          c.x = c.tx = t; t += c.dur + 2;
        }
      }
      this.fit(true);
      setTimeout(() => this.islandLayout(), this.reduced ? 0 : 700);
      this.resizeHeight();
    }

    islandLayout() {
      if (this.phase !== 'input') return;
      this.phase = 'islands';
      // timecode islands: inside an island the order is known, islands end to end per device
      for (const dev of this.lanes) {
        let t = 0;
        for (const isl of [...this.islands.values()].filter((i) => i.dev === dev)) {
          isl.wait = t; t += isl.length + 30;
        }
      }
      this.place();
    }

    edge(e) {
      if (this.phase === 'input') this.islandLayout();
      this.phase = 'audio';
      this.edges.push(e);
      if (!this.anchor) this.anchor = e.a;
      this.flashes.push({ a: e.a, b: e.b, t: performance.now() });
      this.place();
    }

    final(d) {
      this.phase = 'final';
      const byK = new Map(d.clips.map((c) => [c.k, c]));
      const main = d.clips.filter((c) => c.start != null && c.group === 0);
      const t0 = Math.min(...main.map((c) => c.start));
      let end = Math.max(...main.map((c) => c.start - t0 + this.dur(c.k)));
      const tail = new Map();
      for (const c of this.clips) {
        const r = byK.get(c.k); if (!r) continue;
        c.state = r.group !== 0 && r.start != null ? 'group' : r.method;
        if (r.start != null && r.group === 0) c.tx = r.start - t0;
        else {  // behind the timeline, like the FCPXML export
          const x = tail.get(c.dev) ?? end + 30; c.tx = x; tail.set(c.dev, x + c.dur + 2);
        }
        c.talpha = r.method === 'unplaced' ? 0.45 : 1;
      }
      this.fit();
    }

    dur(k) { const c = this.clips.find((c) => c.k === k); return c ? c.dur : 0; }

    /* provisional positions from the audio matches found so far (BFS over lags) */
    place() {
      for (const i of this.islands.values()) i.pos = null;
      if (this.anchor) {
        const adj = new Map();
        for (const e of this.edges) {
          (adj.get(e.a) || adj.set(e.a, []).get(e.a)).push([e.b, e.lag]);
          (adj.get(e.b) || adj.set(e.b, []).get(e.b)).push([e.a, -e.lag]);
        }
        this.islands.get(this.anchor).pos = 0;
        const q = [this.anchor];
        while (q.length) {
          const a = q.shift(), pa = this.islands.get(a).pos;
          for (const [b, lag] of adj.get(a) || []) {
            const ib = this.islands.get(b);
            if (ib.pos == null) { ib.pos = pa + lag; q.push(b); }
          }
        }
      }
      const placed = [...this.islands.values()].filter((i) => i.pos != null);
      const right = placed.length ? Math.max(...placed.map((i) => i.pos + i.length)) + 60 : 0;
      for (const c of this.clips) {
        const i = this.islands.get(c.island);
        if (i.pos != null) { c.tx = i.pos + c.rel; c.state = 'matched'; c.talpha = 1; }
        else { c.tx = right + (i.wait || 0) + c.rel; c.state = 'pending'; c.talpha = 1; }
      }
      this.fit();
    }

    fit(snap) {
      if (!this.clips.length) return;
      const a = Math.min(...this.clips.map((c) => c.tx)), b = Math.max(...this.clips.map((c) => c.tx + c.dur));
      const pad = (b - a) * 0.02 || 1;
      this.view.ta = a - pad; this.view.tb = b + pad;
      if (snap || this.reduced) { this.view.a = this.view.ta; this.view.b = this.view.tb; }
    }

    resizeHeight() {
      this.cv.style.height = `${PAD * 2 + this.lanes.length * (H_LANE + GAP)}px`;
    }

    /* ---- animation ---- */
    step() {
      const k = this.reduced ? 1 : 0.14;
      for (const c of this.clips) {
        c.x += (c.tx - c.x) * k; c.alpha += (c.talpha - c.alpha) * k;
      }
      this.view.a += (this.view.ta - this.view.a) * k; this.view.b += (this.view.tb - this.view.b) * k;
    }

    draw() {
      const { ctx, w, h, c } = this;
      if (!w) return;
      ctx.clearRect(0, 0, w, h);
      const x0 = LABEL, x1 = w - PAD, sx = (x1 - x0) / (this.view.b - this.view.a || 1);
      const X = (t) => x0 + (t - this.view.a) * sx;
      const Y = (dev) => PAD + this.lanes.indexOf(dev) * (H_LANE + GAP);
      ctx.font = '600 11px "Public Sans Variable", system-ui, sans-serif';
      ctx.textBaseline = 'middle';
      for (const dev of this.lanes) {
        const y = Y(dev);
        ctx.fillStyle = c.lane; ctx.fillRect(x0, y, x1 - x0, H_LANE);
        ctx.fillStyle = c.text; ctx.fillText(dev, 0, y + H_LANE / 2, LABEL - 10);
      }
      for (const cl of this.clips) {
        const y = Y(cl.dev), xa = X(cl.x), wd = Math.max(1.5, cl.dur * sx);
        if (xa > x1 || xa + wd < x0) continue;
        ctx.globalAlpha = Math.max(0, Math.min(1, cl.alpha));
        const fill = { pending: c.line3, matched: c.steel, audio: c.accent, timecode: c.steel,
          chronology: c.steel, group: c.line3, unplaced: c.line3 }[cl.state] || c.line3;
        ctx.fillStyle = fill;
        ctx.fillRect(xa, y + 3, wd > 4 ? wd - 1 : wd, H_LANE - 6);  // 1 px gap keeps neighbouring files apart
        if (cl.state === 'chronology' || cl.state === 'unplaced' || cl.state === 'group') {
          ctx.strokeStyle = c.strong; ctx.lineWidth = 1; ctx.strokeRect(xa + 0.5, y + 3.5, wd - 1, H_LANE - 7);
        }
      }
      ctx.globalAlpha = 1;
      // a new audio match briefly links the two lanes
      const now = performance.now();
      this.flashes = this.flashes.filter((f) => now - f.t < 700);
      for (const f of this.flashes) {
        const ca = this.clips.find((x) => x.island === f.a), cb = this.clips.find((x) => x.island === f.b);
        if (!ca || !cb) continue;
        const xb = X(cb.x), ya = Y(ca.dev) + H_LANE / 2, yb = Y(cb.dev) + H_LANE / 2;
        ctx.globalAlpha = 1 - (now - f.t) / 700;
        ctx.strokeStyle = c.strong; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(xb, ya); ctx.lineTo(xb, yb); ctx.stroke();
      }
      ctx.globalAlpha = 1;
    }
  }
  window.Timeline = Timeline;
})();
