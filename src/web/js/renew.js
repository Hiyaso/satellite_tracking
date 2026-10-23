/* ---------------- automatic TLE renewal ---------------- */

let renewStatus = null;

/** "in 5h 12m" / "3h ago" — the schedule is easier to read as a distance. */
function relativeTime(iso) {
  if (!iso) return '—';
  const delta = (new Date(iso) - Date.now()) / 1000;
  const mag = Math.abs(delta);
  const span = mag < 90 ? 'less than a minute'
    : mag < 5400 ? `${Math.round(mag / 60)} min`
    : mag < 172800 ? `${Math.floor(mag / 3600)}h ${Math.round(mag % 3600 / 60)}m`
    : `${(mag / 86400).toFixed(1)} days`;
  return delta >= 0 ? `in ${span}` : `${span} ago`;
}


function renderRenew(status) {
  renewStatus = status;
  $('renew-enabled').checked = status.enabled;
  // Snap the selector to the stored interval; an interval set outside the UI
  // (straight in config/tle_renew.json) still shows honestly as a custom entry.
  const rate = $('renew-rate');
  const hours = String(status.interval_hours);
  if (![...rate.options].some(o => o.value === hours)) {
    rate.add(new Option(`${(24 / status.interval_hours).toFixed(2).replace(/\.?0+$/, '')} — every ${status.interval_hours}h`, hours));
  }
  rate.value = hours;

  $('renew-list').innerHTML = status.satellites.length
    ? status.satellites.map(s => {
        const when = s.error
          ? `failed: ${s.error}`
          : `last ${utcStamp(s.last_renewed)} · next ${relativeTime(s.next_renewal)}`;
        return `<div class="renew-sat">
          <div class="id">NORAD ${s.norad_id}<span class="hint" style="margin:0 0 0 6px">${s.epoch ? 'epoch ' + s.epoch.slice(0, 16) : 'no file yet'}</span></div>
          <button type="button" class="drop" data-drop="${s.norad_id}" title="Stop renewing this satellite">&times;</button>
          <div class="when${s.error ? ' err' : ''}">${when}</div>
        </div>`;
      }).join('')
    : '<div class="empty">No satellites scheduled — use “Add this satellite”.</div>';

  $('renew-note').textContent = status.enabled
    ? `Renewal runs while the console server is up${status.running ? '' : ' (scheduler not started)'}.`
    : 'Automatic renewal is off — element sets will go stale.';
}

async function loadRenew() {
  try {
    renderRenew(await api('/api/tle-renew'));
  } catch (err) {
    $('renew-list').innerHTML = `<div class="empty">${err.message}</div>`;
  }
}

async function configureRenew(patch) {
  try {
    renderRenew(await api('/api/tle-renew', patch));
    showMsg($('renew-msg'), '');
  } catch (err) {
    showMsg($('renew-msg'), err.message);
  }
}

/** The NORAD ID the parameter panel currently points at, whatever the source. */
function selectedNoradId() {
  if ($('sat-mode').value === 'norad') return Number($('norad-id').value);
  const match = /gp_(\d+)\.json/.exec($('gp-file').value || '');
  return match ? Number(match[1]) : null;
}
