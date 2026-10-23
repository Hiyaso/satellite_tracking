/* Parameter panel: UTC start field, point count, preset load/save, element files. */

/* ---------------- UTC start field ---------------- */

/* A datetime-local input carries no zone: the browser fills an empty one from
   the PC's local clock (UTC+8 here), which is not what this field means. Seeding
   it with the UTC wall clock makes the digits and the "UTC" label agree. */
const utcFieldValue = (date) => date.toISOString().slice(0, 19);

function tickStartField() {
  const el = $('w-start');
  if (startPristine && document.activeElement !== el) el.value = utcFieldValue(new Date());
}

/* ---------------- data point count ---------------- */

/* The server derives the sample count from duration and step; showing the same
   arithmetic beside the inputs means the size of a run is visible before it is
   asked for, rather than arriving as an error afterwards. Limits come from
   /api/health so this page holds no second copy of them. */
let limits = { max_samples: 50000, min_step_seconds: 0.1 };

/** Sample count for a window, matching the server: int(seconds / step) + 1. */
function pointCount(durationMinutes, stepSeconds) {
  if (!(durationMinutes > 0) || !(stepSeconds > 0)) return null;
  return Math.floor(durationMinutes * 60 / stepSeconds) + 1;
}

function renderPointCount() {
  const box = $('points');
  const duration = Number($('w-duration').value), step = Number($('w-step').value);
  const count = pointCount(duration, step);
  const cap = limits.max_samples;

  if (count === null) {
    box.className = 'points';
    box.innerHTML = '<span>Set a duration and step to size the run.</span>';
    return;
  }
  // A step under the server's floor is rejected however few points it produces,
  // so say that rather than quoting a count the run will never return.
  if (step < limits.min_step_seconds) {
    box.className = 'points over';
    box.innerHTML = `<span>Step must be at least ${limits.min_step_seconds} s.</span>`;
    return;
  }
  const over = count > cap;
  // At the cap, the step that would just fit is more use than "too many".
  // Rounded up, never down: a value rounded to the nearest tenth can still be
  // fractionally too small, and advice you can follow and still be over is worse
  // than none. 10.0008 s is shown as 10.1, which genuinely fits.
  const places = (v) => (v < 1 ? 2 : 1);
  const roundUp = (v) => (Math.ceil(v * 10 ** places(v)) / 10 ** places(v)).toFixed(places(v));
  const fitStep = over ? roundUp(duration * 60 / (cap - 1)) : null;
  box.className = 'points' + (over ? ' over' : '');
  box.innerHTML = `<b>${count.toLocaleString()}</b> <span>data points` +
    ` · limit ${cap.toLocaleString()}` +
    (over ? ` — over by ${(count - cap).toLocaleString()}; use a step of ${fitStep} s or more`
          : ` · ${Math.round(100 * count / cap)}% of it`) +
    `</span>`;
}

/* ---------------- parameter collection ---------------- */

function collect() {
  const mode = $('sat-mode').value;
  const gpValue = $('gp-file').value || '';
  const [gpFileName, gpIndex] = gpValue.split('|');

  return {
    satellite: {
      mode,
      name: $('sat-name').value,
      line1: $('line1').value,
      line2: $('line2').value,
      gp_file: gpFileName,
      gp_index: Number(gpIndex || 0),
      norad_id: Number($('norad-id').value),
    },
    station: {
      name: $('st-name').value,
      lat: Number($('st-lat').value),
      lon: Number($('st-lon').value),
      elevation_m: Number($('st-elev').value),
    },
    window: {
      start: (startPristine || !$('w-start').value) ? '' : $('w-start').value + 'Z',
      duration_minutes: Number($('w-duration').value),
      step_seconds: Number($('w-step').value),
    },
    acu: {
      min_elevation_deg: Number($('acu-mask').value),
      az_offset_deg: Number($('acu-az-off').value),
      el_offset_deg: Number($('acu-el-off').value),
      az_min_deg: Number($('acu-az-min').value),
      az_max_deg: Number($('acu-az-max').value),
      el_min_deg: Number($('acu-el-min').value),
      el_max_deg: Number($('acu-el-max').value),
      max_az_rate_dps: Number($('acu-az-rate').value),
      max_el_rate_dps: Number($('acu-el-rate').value),
    },
    visible_only: $('visible-only').checked,
  };
}

function applyPreset(p) {
  if (!p) return;
  const set = (id, v) => { if (v !== undefined && v !== null) $(id).value = v; };
  set('sat-mode', p.satellite?.mode);
  set('sat-name', p.satellite?.name);
  set('line1', p.satellite?.line1);
  set('line2', p.satellite?.line2);
  set('norad-id', p.satellite?.norad_id);
  set('st-name', p.station?.name);
  set('st-lat', p.station?.lat);
  set('st-lon', p.station?.lon);
  set('st-elev', p.station?.elevation_m);
  set('w-duration', p.window?.duration_minutes);
  set('w-step', p.window?.step_seconds);
  set('acu-mask', p.acu?.min_elevation_deg);
  set('acu-az-off', p.acu?.az_offset_deg);
  set('acu-el-off', p.acu?.el_offset_deg);
  set('acu-az-min', p.acu?.az_min_deg);
  set('acu-az-max', p.acu?.az_max_deg);
  set('acu-el-min', p.acu?.el_min_deg);
  set('acu-el-max', p.acu?.el_max_deg);
  set('acu-az-rate', p.acu?.max_az_rate_dps);
  set('acu-el-rate', p.acu?.max_el_rate_dps);
  $('visible-only').checked = !!p.visible_only;
  syncModeVisibility();
  return p.satellite?.gp_file;
}

function syncModeVisibility() {
  const mode = $('sat-mode').value;
  document.querySelectorAll('[data-mode]').forEach(el => { el.hidden = el.dataset.mode !== mode; });
}

async function loadGpFiles(preferred) {
  const select = $('gp-file');
  try {
    const data = await api('/api/gp-files');
    const options = [];
    for (const f of data.files) {
      for (const sat of f.satellites) {
        options.push({
          value: `${f.file}|${sat.index}`,
          label: `${sat.name} — ${f.file}${sat.epoch ? ' @ ' + sat.epoch.slice(0, 16) : ''}`,
        });
      }
    }
    select.innerHTML = options.length
      ? options.map(o => `<option value="${o.value}">${o.label}</option>`).join('')
      : '<option value="">no element files in data/</option>';
    if (preferred) {
      const match = options.find(o => o.value.startsWith(preferred + '|'));
      if (match) select.value = match.value;
    }
  } catch (err) {
    select.innerHTML = `<option value="">${err.message}</option>`;
  }
}
