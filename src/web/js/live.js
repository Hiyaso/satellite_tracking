/* ---------------- live tracking ---------------- */

async function livePoll() {
  try {
    const params = collect();
    const data = await api('/api/live', params);
    const s = data.sample;
    lastLive = { ...data, acu: params.acu };
    drawMap();
    $('lv-az').textContent = fmt(s.azimuth_deg) + '°';
    $('lv-el').textContent = fmt(s.elevation_deg) + '°';
    $('lv-caz').textContent = fmt(s.commanded_az_deg) + '°';
    $('lv-cel').textContent = fmt(s.commanded_el_deg) + '°';
    $('lv-rng').textContent = fmt(s.range_km, 0) + ' km';
    $('lv-slat').textContent = fmt(s.sub_lat_deg, 3) + '°';
    $('lv-slon').textContent = fmt(s.sub_lon_deg, 3) + '°';
    $('lv-alt').textContent = fmt(s.altitude_km, 0) + ' km';
    const hard = s.flags.filter(f => f !== 'BELOW_MASK');
    $('lv-state').textContent = hard.length ? hard[0] : (s.visible ? 'TRACKING' : 'BELOW MASK');
    $('lv-state').style.color = hard.length ? 'var(--bad)' : (s.visible ? 'var(--ok)' : 'var(--muted)');
    $('lv-time').textContent = `${data.satellite.name} · ${s.time.replace('T', ' ')} UTC`;
    showMsg($('live-msg'), '');
  } catch (err) {
    showMsg($('live-msg'), err.message);
    stopLive();
  }
}

function stopLive() {
  clearInterval(liveTimer);
  liveTimer = null;
  $('live-toggle').textContent = 'Live';
  $('live-toggle').classList.remove('primary');
}

function startLive() {
  if (liveTimer) return;
  $('live-toggle').textContent = 'Stop';
  $('live-toggle').classList.add('primary');
  livePoll();
  liveTimer = setInterval(livePoll, 1000);
}

function toggleLive() {
  if (liveTimer) { stopLive(); return; }
  document.querySelector('.tabs button[data-tab="live"]').click();
  startLive();
}
