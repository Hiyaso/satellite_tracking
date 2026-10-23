/* Generated track: pass selection, summary, table, pass list, sky + timeline plots, exports. */

/* ---------------- pass selection ---------------- */
/* Choosing a pass narrows every view to that pass. It filters the samples
   already in hand rather than asking the server again, so switching between
   passes is instant and cannot disagree with the generated data. */

let selectedPass = null;   // index into lastResult.passes, or null for all

/** [from, to] in epoch ms. A pass clipped by the window edge has an open end. */
function passWindow(pass) {
  const at = (event) => (event ? Date.parse(event.time) : null);
  return [at(pass.rise) ?? -Infinity, at(pass.set) ?? Infinity];
}

/** The samples every view should draw: the whole window, or one pass of it. */
function activeSamples() {
  if (!lastResult) return [];
  if (selectedPass === null || !lastResult.passes[selectedPass]) return lastResult.samples;
  const [from, to] = passWindow(lastResult.passes[selectedPass]);
  return lastResult.samples.filter(s => {
    const t = Date.parse(s.time);
    return t >= from && t <= to;
  });
}

/** Mirrors the server's _summarize so a selected pass gets its own statistics. */
function summarise(samples) {
  if (!samples.length) return { samples: 0, visible_samples: 0, violations: 0 };
  const visible = samples.filter(s => s.visible);
  const values = (key, from) => from.map(s => s[key]).filter(Number.isFinite);
  const max = (key, from) => { const v = values(key, from); return v.length ? Math.max(...v) : null; };
  const min = (key, from) => { const v = values(key, from); return v.length ? Math.min(...v) : null; };
  // Rates are peaked over the tracked samples only, matching the server: while
  // the antenna is parked below the mask its rates are not a slew of the target.
  const tracked = visible.length ? visible : samples;
  return {
    samples: samples.length,
    visible_samples: visible.length,
    visible_percent: Math.round(1000 * visible.length / samples.length) / 10,
    max_elevation_deg: max('elevation_deg', samples),
    min_range_km: min('range_km', samples),
    peak_az_rate_dps: max('az_rate_dps', tracked),
    peak_el_rate_dps: max('el_rate_dps', tracked),
    violations: samples.filter(s => s.flags.some(f => f !== 'BELOW_MASK')).length,
  };
}

/** Redraw everything that depends on which pass is chosen. */
function applyPassSelection() {
  if (!lastResult) return;
  const samples = activeSamples();
  const bar = $('filter-bar');

  if (selectedPass === null || !lastResult?.passes[selectedPass]) {
    bar.hidden = true;
    renderSummary(lastResult, lastResult.summary);
  } else {
    const p = lastResult.passes[selectedPass];
    const span = `${utcStamp(p.rise?.time)} → ${(p.set?.time || '').slice(11, 19) || '—'} UTC`;
    bar.hidden = false;
    bar.innerHTML = `<span>Showing <b>pass ${selectedPass + 1}</b> of ${lastResult.passes.length} · ${span}` +
      ` · ${samples.length} samples</span><button type="button" class="btn-small" id="show-all">Show all</button>`;
    $('show-all').addEventListener('click', () => selectPass(null));
    renderSummary(lastResult, summarise(samples));
  }

  renderTable(samples);
  if (selectedPass !== null) $('table-note').textContent += ` · pass ${selectedPass + 1} only`;
  drawSky(samples, lastResult.acu.min_elevation_deg);
  drawTimeline(samples, lastResult.acu);
  renderPasses(lastResult.passes);   // re-rendered so the cards show what is chosen

  // A pass shorter than the sample step falls between samples, so say so rather
  // than leaving the plots mysteriously blank.
  if (selectedPass !== null && !samples.length) {
    showMsg($('track-msg'), 'This pass is shorter than the sample step — nothing was sampled inside it. Reduce the step and generate again.', 'info');
  } else {
    showMsg($('track-msg'), '');
  }
}

function selectPass(index) {
  // Clicking the chosen pass again clears the filter, so the list is a toggle.
  selectedPass = (index === selectedPass) ? null : index;
  applyPassSelection();
}

/* ---------------- rendering ---------------- */

function renderSummary(result, summary) {
  const s = summary, sat = result.satellite;
  const cells = [
    ['Satellite', sat.name || '—'],
    ['Samples', s.samples ?? 0],
    ['Above mask', `${s.visible_samples ?? 0} (${s.visible_percent ?? 0}%)`],
    ['Max elevation', fmt(s.max_elevation_deg, 2) + '°'],
    ['Min range', fmt(s.min_range_km, 0) + ' km'],
    ['Peak az rate', fmt(s.peak_az_rate_dps, 3) + ' °/s'],
    ['Peak el rate', fmt(s.peak_el_rate_dps, 3) + ' °/s'],
    ['Limit hits', s.violations ?? 0],
  ];
  $('summary').innerHTML = cells.map(([k, v]) =>
    `<div class="stat"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('');
}

function renderTable(samples) {
  const tbody = $('data-table').querySelector('tbody');
  const LIMIT = 3000;
  const shown = samples.slice(0, LIMIT);
  $('table-note').textContent = samples.length > LIMIT
    ? `showing first ${LIMIT} of ${samples.length} rows — download for the full set`
    : `${samples.length} rows`;

  if (!shown.length) {
    tbody.innerHTML = '<tr><td colspan="9" class="empty">No samples matched these parameters.</td></tr>';
    return;
  }
  tbody.innerHTML = shown.map(s => {
    const hard = s.flags.filter(f => f !== 'BELOW_MASK');
    const cls = hard.length ? 'violation' : (s.visible ? '' : 'below');
    const flags = s.flags.map(f => `<span class="flag flag-${f}">${f}</span>`).join('');
    return `<tr class="${cls}"><td>${s.time.replace('T', ' ').replace('+00:00', '')}</td>` +
      `<td>${fmt(s.azimuth_deg)}</td><td>${fmt(s.elevation_deg)}</td>` +
      `<td>${fmt(s.commanded_az_deg)}</td><td>${fmt(s.commanded_el_deg)}</td>` +
      `<td>${fmt(s.az_rate_dps, 3)}</td><td>${fmt(s.el_rate_dps, 3)}</td>` +
      `<td>${fmt(s.range_km, 1)}</td><td style="text-align:left">${flags}</td></tr>`;
  }).join('');
}

function renderPasses(passes) {
  const box = $('pass-list');
  if (!passes.length) {
    box.innerHTML = '<div class="empty">No passes above the elevation mask in this window.</div>';
    return;
  }
  box.innerHTML = passes.map((p, i) => {
    const t = (x) => x ? x.time.replace('T', ' ').replace('+00:00', '') : '—';
    const mins = p.duration_seconds ? (p.duration_seconds / 60).toFixed(1) : '—';
    const count = lastResult ? lastResult.samples.filter(s => {
      const [from, to] = passWindow(p), at = Date.parse(s.time);
      return at >= from && at <= to;
    }).length : 0;
    return `<div class="pass${i === selectedPass ? ' selected' : ''}" data-pass="${i}" role="button" tabindex="0"
      title="Show only this pass in the track, data and downloads">
      <div class="when">Pass ${i + 1} · ${t(p.rise)} UTC · ${mins} min</div>
      <div class="detail">AOS az ${fmt(p.rise?.azimuth_deg, 1)}° · TCA ${t(p.culminate)} el ${fmt(p.culminate?.elevation_deg, 1)}° az ${fmt(p.culminate?.azimuth_deg, 1)}° · LOS ${t(p.set)} az ${fmt(p.set?.azimuth_deg, 1)}°</div>
      <div class="detail pick">${count} samples · ${i === selectedPass ? 'showing — click to clear' : 'click to show only this pass'}</div>
    </div>`;
  }).join('');
}

/* ---------------- canvas plots ---------------- */

function drawSky(samples, mask) {
  const canvas = $('sky');
  // Hidden tabs report zero width; the tab switch redraws once it is visible.
  if (!canvas.clientWidth) return;
  canvas.style.height = canvas.clientWidth + 'px';
  const { ctx, w, h } = setupCanvas(canvas);
  const cx = w / 2, cy = h / 2, R = Math.max(10, Math.min(w, h) / 2 - 24);

  ctx.clearRect(0, 0, w, h);
  ctx.strokeStyle = '#24344a';
  ctx.fillStyle = '#8397b0';
  ctx.font = '11px system-ui';
  ctx.textAlign = 'center';

  for (let el = 0; el <= 90; el += 30) {
    const r = R * (1 - el / 90);
    ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.stroke();
    if (el < 90) ctx.fillText(el + '°', cx + 2, cy - r + 12);
  }
  for (let az = 0; az < 360; az += 30) {
    const a = (az - 90) * Math.PI / 180;
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.lineTo(cx + R * Math.cos(a), cy + R * Math.sin(a));
    ctx.stroke();
  }
  // elevation mask ring
  if (mask > 0 && mask < 90) {
    const r = R * (1 - mask / 90);
    ctx.strokeStyle = 'rgba(255,180,84,.7)';
    ctx.setLineDash([4, 4]);
    ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.stroke();
    ctx.setLineDash([]);
  }
  ctx.fillStyle = '#dbe6f3';
  ['N', 'E', 'S', 'W'].forEach((lbl, i) => {
    const a = (i * 90 - 90) * Math.PI / 180;
    ctx.fillText(lbl, cx + (R + 14) * Math.cos(a), cy + (R + 14) * Math.sin(a) + 4);
  });

  const pos = (s) => {
    const r = R * (1 - Math.max(0, Math.min(90, s.elevation_deg)) / 90);
    const a = (s.azimuth_deg - 90) * Math.PI / 180;
    return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
  };

  // below-horizon samples are drawn faintly at the rim so the track stays readable
  let prev = null, prevVisible = null;
  ctx.lineWidth = 2;
  for (const s of samples) {
    if (s.elevation_deg < 0) { prev = null; continue; }
    const p = pos(s);
    if (prev) {
      ctx.strokeStyle = (s.visible && prevVisible) ? '#35e0c3' : 'rgba(131,151,176,.45)';
      ctx.beginPath(); ctx.moveTo(prev[0], prev[1]); ctx.lineTo(p[0], p[1]); ctx.stroke();
    }
    prev = p; prevVisible = s.visible;
  }

  // Arrowheads every ~70 px along the track, so the direction the satellite
  // sweeps through the sky is unambiguous on a pass that doubles back.
  let ap = null, run = 35;
  for (const s of samples) {
    if (s.elevation_deg < 0) { ap = null; continue; }
    const p = pos(s);
    if (ap) {
      run += Math.hypot(p[0] - ap[0], p[1] - ap[1]);
      if (run >= 70) {
        ctx.fillStyle = s.visible ? '#35e0c3' : 'rgba(131,151,176,.55)';
        arrowHead(ctx, p[0], p[1], Math.atan2(p[1] - ap[1], p[0] - ap[0]), 6);
        run = 0;
      }
    }
    ap = p;
  }

  const visible = samples.filter(s => s.visible);
  if (visible.length) {
    const peak = visible.reduce((a, b) => (b.elevation_deg > a.elevation_deg ? b : a));
    const [px, py] = pos(peak);
    ctx.fillStyle = '#6ee787';
    ctx.beginPath(); ctx.arc(px, py, 4, 0, Math.PI * 2); ctx.fill();
  }
}

function drawTimeline(samples, acu) {
  const canvas = $('timeline');
  if (!canvas.clientWidth) return;
  canvas.style.height = Math.max(280, Math.round(canvas.clientWidth * 0.45)) + 'px';
  const { ctx, w, h } = setupCanvas(canvas);
  const L = 46, Rp = 46, T = 14, B = 26;
  const pw = w - L - Rp, ph = h - T - B;

  ctx.clearRect(0, 0, w, h);
  if (!samples.length) return;

  const azMin = Math.min(...samples.map(s => s.commanded_az_deg));
  const azMax = Math.max(...samples.map(s => s.commanded_az_deg));
  const azLo = Math.floor(azMin / 30) * 30, azHi = Math.ceil(azMax / 30) * 30 || 30;
  const xOf = (i) => L + pw * (i / Math.max(1, samples.length - 1));
  const yAz = (v) => T + ph * (1 - (v - azLo) / Math.max(1e-6, azHi - azLo));
  const yEl = (v) => T + ph * (1 - (v + 90) / 180);

  ctx.strokeStyle = '#24344a';
  ctx.fillStyle = '#8397b0';
  ctx.font = '10px system-ui';
  ctx.lineWidth = 1;
  for (let f = 0; f <= 4; f++) {
    const y = T + ph * f / 4;
    ctx.beginPath(); ctx.moveTo(L, y); ctx.lineTo(L + pw, y); ctx.stroke();
    ctx.textAlign = 'right';
    ctx.fillText((azHi - (azHi - azLo) * f / 4).toFixed(0) + '°', L - 5, y + 3);
    ctx.textAlign = 'left';
    ctx.fillText((90 - 180 * f / 4).toFixed(0) + '°', L + pw + 5, y + 3);
  }
  // horizon and mask reference lines on the elevation axis
  ctx.strokeStyle = 'rgba(255,180,84,.5)';
  ctx.setLineDash([4, 4]);
  ctx.beginPath();
  ctx.moveTo(L, yEl(acu.min_elevation_deg)); ctx.lineTo(L + pw, yEl(acu.min_elevation_deg));
  ctx.stroke();
  ctx.setLineDash([]);

  // Draw each series in runs of constant visibility: below the mask the antenna
  // is parked rather than following the target, so the two must not be joined
  // into one continuous line.
  const series = (key, y, color, dimColor) => {
    let i = 0;
    while (i < samples.length) {
      const visible = samples[i].visible;
      let j = i;
      while (j < samples.length && samples[j].visible === visible) j++;
      ctx.strokeStyle = visible ? color : dimColor;
      ctx.lineWidth = visible ? 1.8 : 1;
      ctx.beginPath();
      for (let k = i; k < j; k++) {
        const px = xOf(k), py = y(samples[k][key]);
        // Lift the pen across a 360° wrap so it does not draw a vertical streak.
        const wrapped = k > i && Math.abs(samples[k][key] - samples[k - 1][key]) > 180;
        if (k === i || wrapped) ctx.moveTo(px, py); else ctx.lineTo(px, py);
      }
      ctx.stroke();
      i = j;
    }
  };
  series('commanded_az_deg', yAz, '#35e0c3', 'rgba(53,224,195,.25)');
  series('commanded_el_deg', yEl, '#6ee787', 'rgba(110,231,135,.25)');

  ctx.fillStyle = '#8397b0';
  ctx.textAlign = 'left';
  ctx.fillText(samples[0].time.slice(11, 16), L, h - 8);
  ctx.textAlign = 'right';
  ctx.fillText(samples[samples.length - 1].time.slice(11, 16), L + pw, h - 8);
  ctx.textAlign = 'center';
  ctx.fillText('commanded azimuth (cyan) / elevation (green)', L + pw / 2, h - 8);
}

/* ---------------- actions ---------------- */

async function generate(event) {
  event?.preventDefault();
  const btn = $('generate');
  btn.disabled = true; btn.textContent = 'Computing…';
  showMsg($('track-msg'), '');
  // The page opens on Live; bring the run (or its error) into view rather than
  // leaving it behind a tab. From Data or Passes the operator already chose where to look.
  if (document.querySelector('.tabs button[data-tab="live"]').classList.contains('active')) {
    document.querySelector('.tabs button[data-tab="track"]').click();
  }
  try {
    const params = collect();
    const result = await api('/api/track', params);
    lastResult = result;
    // A fresh run invalidates the old choice: pass 2 of the previous window is
    // not pass 2 of this one.
    selectedPass = null;
    applyPassSelection();
    if (!result.samples.length) {
      showMsg($('track-msg'), 'No samples returned — check the time window or the "only above mask" option.', 'info');
    }
  } catch (err) {
    showMsg($('track-msg'), err.message);
  } finally {
    btn.disabled = false; btn.textContent = 'Generate Az/El';
  }
}

function download(name, text, type) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement('a');
  a.href = url; a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

function toCsv(samples) {
  const cols = ['time', 'azimuth_deg', 'elevation_deg', 'commanded_az_deg', 'commanded_el_deg',
    'az_rate_dps', 'el_rate_dps', 'range_km', 'visible', 'flags'];
  const rows = samples.map(s => cols.map(c => (c === 'flags' ? s.flags.join(' ') : s[c])).join(','));
  return [cols.join(','), ...rows].join('\n');
}

function stamp() {
  return new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
}
