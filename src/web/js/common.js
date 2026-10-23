/* Shared state and helpers used by every other script on the page. */

const API = location.protocol.startsWith('http') ? '' : 'http://127.0.0.1:8000';
const $ = (id) => document.getElementById(id);
const PRESET_KEY = 'acu-console-params';

let lastResult = null;
let lastLive = null;
let liveTimer = null;
// The start field mirrors UTC now until the operator types a time of their own.
// While it is untouched the request still goes out with a blank start, so the
// window begins at the server's clock rather than at whatever this PC reads.
let startPristine = true;

/* ---------------- API helpers ---------------- */

async function api(path, body) {
  const res = await fetch(API + path, {
    method: body ? 'POST' : 'GET',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  let data;
  try { data = await res.json(); }
  catch { throw new Error(`Server returned ${res.status} (not JSON)`); }
  if (!res.ok || data.status === 'error') throw new Error(data.message || `HTTP ${res.status}`);
  return data;
}

function showMsg(el, text, kind = 'error') {
  el.className = 'msg ' + kind;
  el.textContent = text;
  if (!text) el.className = 'msg';
}

const fmt = (v, d = 2) => (v === undefined || v === null || Number.isNaN(v)) ? '—' : Number(v).toFixed(d);

const utcStamp = (iso) => iso ? iso.replace('T', ' ').replace('+00:00', '') : '—';

function setupCanvas(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.max(1, Math.round(rect.width * dpr));
  canvas.height = Math.max(1, Math.round((canvas.dataset.aspect ? rect.width * Number(canvas.dataset.aspect) : rect.height) * dpr));
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, w: rect.width, h: canvas.height / dpr };
}

/** Filled triangle at (x, y) pointing along `angle` in screen radians. */
function arrowHead(ctx, x, y, angle, size = 7) {
  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(angle);
  ctx.beginPath();
  ctx.moveTo(size, 0);
  ctx.lineTo(-size * 0.7, size * 0.6);
  ctx.lineTo(-size * 0.3, 0);
  ctx.lineTo(-size * 0.7, -size * 0.6);
  ctx.closePath();
  ctx.fill();
  ctx.restore();
}
