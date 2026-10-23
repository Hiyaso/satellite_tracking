/* Event wiring and start-up. Loaded last, after every function it binds exists. */

$('sat-mode').addEventListener('change', syncModeVisibility);
$('params').addEventListener('submit', generate);
$('live-toggle').addEventListener('click', toggleLive);
$('reload-files').addEventListener('click', () => loadGpFiles($('gp-file').value.split('|')[0]));

$('w-duration').addEventListener('input', renderPointCount);
$('w-step').addEventListener('input', renderPointCount);

// Choosing a pass in the list filters the track, table and downloads.
$('pass-list').addEventListener('click', (e) => {
  const card = e.target.closest('[data-pass]');
  if (card) selectPass(Number(card.dataset.pass));
});
$('pass-list').addEventListener('keydown', (e) => {
  if (e.key !== 'Enter' && e.key !== ' ') return;
  const card = e.target.closest('[data-pass]');
  if (!card) return;
  e.preventDefault();               // Space would scroll the list
  selectPass(Number(card.dataset.pass));
});

$('renew-enabled').addEventListener('change', (e) => configureRenew({ enabled: e.currentTarget.checked }));
$('renew-rate').addEventListener('change', (e) => configureRenew({ interval_hours: Number(e.currentTarget.value) }));

$('renew-add').addEventListener('click', () => {
  const norad_id = selectedNoradId();
  if (!norad_id) {
    showMsg($('renew-msg'), 'Pick a gp_<norad>.json file or the NORAD source to schedule a satellite.');
    return;
  }
  const satellites = (renewStatus?.satellites || []).map(s => ({ norad_id: s.norad_id, file: s.file }));
  if (satellites.some(s => s.norad_id === norad_id)) {
    showMsg($('renew-msg'), `NORAD ${norad_id} is already scheduled.`, 'info');
    return;
  }
  configureRenew({ satellites: [...satellites, { norad_id, file: `gp_${norad_id}.json` }] });
});

$('renew-list').addEventListener('click', (e) => {
  const drop = e.target.closest('[data-drop]');
  if (!drop) return;
  const norad_id = Number(drop.dataset.drop);
  const satellites = (renewStatus?.satellites || [])
    .filter(s => s.norad_id !== norad_id)
    .map(s => ({ norad_id: s.norad_id, file: s.file }));
  configureRenew({ satellites });
});

$('renew-now').addEventListener('click', async (e) => {
  const btn = e.currentTarget;
  btn.disabled = true; btn.textContent = 'Renewing…';
  try {
    renderRenew(await api('/api/tle-renew/run', {}));
    await loadGpFiles($('gp-file').value.split('|')[0]);
    showMsg($('renew-msg'), 'Element sets renewed from CelesTrak.', 'info');
  } catch (err) {
    showMsg($('renew-msg'), err.message);
    loadRenew();
  } finally {
    btn.disabled = false; btn.textContent = 'Renew now';
  }
});

// An edit pins the window; emptying the field hands it back to the UTC clock.
$('w-start').addEventListener('input', (e) => {
  startPristine = !e.currentTarget.value;
});

$('fetch-tle').addEventListener('click', async (e) => {
  const btn = e.currentTarget;
  btn.disabled = true; btn.textContent = 'Fetching…';
  try {
    const data = await api('/api/fetch-tle', { norad_id: Number($('norad-id').value) });
    await loadGpFiles(data.file);
    loadRenew();   // a manual fetch resets that satellite's renewal clock
    showMsg($('params-msg'), `Saved ${data.satellite.name} to data/${data.file} (epoch ${data.satellite.epoch})`, 'info');
  } catch (err) {
    showMsg($('params-msg'), err.message);
  } finally {
    btn.disabled = false; btn.textContent = 'Fetch & save to data/';
  }
});

// Downloads follow the choice, so exporting a single pass needs no editing after
// the fact; the filename says which pass it holds.
const exportName = () => (selectedPass === null ? 'azel' : `azel-pass${selectedPass + 1}`) + `-${stamp()}`;

$('dl-csv').addEventListener('click', () => {
  if (!lastResult) return;
  download(`${exportName()}.csv`, toCsv(activeSamples()), 'text/csv');
});
$('dl-json').addEventListener('click', () => {
  if (!lastResult) return;
  const chosen = selectedPass === null ? lastResult.passes : [lastResult.passes[selectedPass]];
  const samples = activeSamples();
  const out = { ...lastResult, samples, passes: chosen, summary: selectedPass === null ? lastResult.summary : summarise(samples) };
  download(`${exportName()}.json`, JSON.stringify(out, null, 2), 'application/json');
});

$('save-preset').addEventListener('click', () => {
  localStorage.setItem(PRESET_KEY, JSON.stringify(collect()));
  showMsg($('params-msg'), 'Parameters saved in this browser.', 'info');
});
$('reset-preset').addEventListener('click', () => {
  localStorage.removeItem(PRESET_KEY);
  location.reload();
});

document.querySelectorAll('.tabs button').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tabs button').forEach(b => b.classList.toggle('active', b === btn));
    document.querySelectorAll('.tab-body').forEach(b => b.classList.toggle('active', b.id === 'tab-' + btn.dataset.tab));
    if (btn.dataset.tab === 'track' && lastResult) {
      drawSky(activeSamples(), lastResult.acu.min_elevation_deg);
      drawTimeline(activeSamples(), lastResult.acu);
    }
    if (btn.dataset.tab === 'live') {
      drawMap();
      // Opening the tab shows the current position straight away; the Live button
      // is only needed to keep it updating.
      if (!liveTimer && !lastLive) livePoll();
    }
  });
});

window.addEventListener('resize', () => {
  drawMap();
  if (!lastResult) return;
  drawSky(activeSamples(), lastResult.acu.min_elevation_deg);
  drawTimeline(activeSamples(), lastResult.acu);
});

(async function init() {
  let preferred;
  try {
    preferred = applyPreset(JSON.parse(localStorage.getItem(PRESET_KEY)));
  } catch { /* ignore malformed preset */ }
  syncModeVisibility();
  tickStartField();
  setInterval(tickStartField, 1000);
  renderPointCount();               // drawn from the defaults before any server reply
  await loadGpFiles(preferred);
  loadRenew();
  setInterval(loadRenew, 60000);   // keeps the "next in …" countdown honest
  try {
    const h = await api('/api/health');
    $('server-status').textContent = `server OK · ${h.time} UTC`;
    $('server-status').style.color = 'var(--ok)';
    if (h.limits) { limits = h.limits; renderPointCount(); }
    startLive();
  } catch {
    $('server-status').textContent = 'server unreachable — start src/server/app.py';
    $('server-status').style.color = 'var(--bad)';
  }
})();
