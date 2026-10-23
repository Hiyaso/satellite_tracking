/* ---------------- live world map ---------------- */
/* Equirectangular (plate carrée) projection: longitude and latitude map straight
   onto x and y, which keeps every conversion here to one line and matches the
   coastline rings in world.js. */

const DEG = Math.PI / 180;
const EARTH_RADIUS_KM = 6371.0;

function subsolarPoint(date) {
  // Low-precision solar position (NOAA/Astronomical Almanac); well under a degree,
  // which is far finer than a terminator drawn on a 1000-pixel-wide map.
  const n = date.getTime() / 86400000 + 2440587.5 - 2451545.0;   // days from J2000
  const meanLon = (280.460 + 0.9856474 * n) * DEG;
  const meanAnom = (357.528 + 0.9856003 * n) * DEG;
  const ecliptic = meanLon + (1.915 * Math.sin(meanAnom) + 0.020 * Math.sin(2 * meanAnom)) * DEG;
  const obliquity = 23.439 * DEG;
  const dec = Math.asin(Math.sin(obliquity) * Math.sin(ecliptic));
  const ra = Math.atan2(Math.cos(obliquity) * Math.sin(ecliptic), Math.cos(ecliptic));
  const gmstDeg = ((18.697374558 + 24.06570982441908 * n) % 24) * 15;
  return { lat: dec / DEG, lon: wrapLon(ra / DEG - gmstDeg) };
}

const wrapLon = (lon) => ((lon % 360) + 540) % 360 - 180;

function greatCirclePoint(latDeg, lonDeg, bearingRad, arcRad) {
  const lat = latDeg * DEG, lon = lonDeg * DEG;
  const lat2 = Math.asin(Math.sin(lat) * Math.cos(arcRad) + Math.cos(lat) * Math.sin(arcRad) * Math.cos(bearingRad));
  const lon2 = lon + Math.atan2(
    Math.sin(bearingRad) * Math.sin(arcRad) * Math.cos(lat),
    Math.cos(arcRad) - Math.sin(lat) * Math.sin(lat2),
  );
  return [lat2 / DEG, wrapLon(lon2 / DEG)];
}

/** Circle on the ground inside which the satellite sits above `maskDeg`. */
function footprint(latDeg, lonDeg, altitudeKm, maskDeg) {
  const mask = Math.max(0, maskDeg) * DEG;
  const ratio = EARTH_RADIUS_KM / (EARTH_RADIUS_KM + Math.max(0, altitudeKm)) * Math.cos(mask);
  const arc = Math.acos(Math.min(1, ratio)) - mask;
  if (!(arc > 0)) return [];   // mask so high nothing on the ground qualifies
  const points = [];
  for (let bearing = 0; bearing <= 360; bearing += 3) {
    points.push(greatCirclePoint(latDeg, lonDeg, bearing * DEG, arc));
  }
  return points;
}

/** Great-circle arc between two ground points, as [lat, lon] samples. */
function arcBetween(a, b, steps = 48) {
  const [lat1, lon1] = [a[0] * DEG, a[1] * DEG], [lat2, lon2] = [b[0] * DEG, b[1] * DEG];
  const v1 = [Math.cos(lat1) * Math.cos(lon1), Math.cos(lat1) * Math.sin(lon1), Math.sin(lat1)];
  const v2 = [Math.cos(lat2) * Math.cos(lon2), Math.cos(lat2) * Math.sin(lon2), Math.sin(lat2)];
  const dot = Math.max(-1, Math.min(1, v1[0] * v2[0] + v1[1] * v2[1] + v1[2] * v2[2]));
  const omega = Math.acos(dot);
  if (omega < 1e-9) return [a, b];
  const out = [];
  for (let i = 0; i <= steps; i++) {
    const f = i / steps;
    const s1 = Math.sin((1 - f) * omega) / Math.sin(omega), s2 = Math.sin(f * omega) / Math.sin(omega);
    const v = [0, 1, 2].map(k => s1 * v1[k] + s2 * v2[k]);
    out.push([Math.atan2(v[2], Math.hypot(v[0], v[1])) / DEG, Math.atan2(v[1], v[0]) / DEG]);
  }
  return out;
}

function drawMap() {
  const canvas = $('map');
  if (!canvas.clientWidth) return;   // hidden tab; redrawn when it becomes visible
  canvas.style.height = Math.round(canvas.clientWidth / 2) + 'px';
  const { ctx, w, h } = setupCanvas(canvas);
  const xy = (lat, lon) => [(lon + 180) / 360 * w, (90 - lat) / 180 * h];

  // A path over [lat, lon] samples, lifting the pen at nulls and at the
  // antimeridian so nothing is drawn straight across the map.
  const path = (points) => {
    ctx.beginPath();
    let prevLon = null;
    for (const p of points) {
      if (!p) { prevLon = null; continue; }
      const [x, y] = xy(p[0], p[1]);
      if (prevLon === null || Math.abs(p[1] - prevLon) > 180) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      prevLon = p[1];
    }
    ctx.stroke();
  };

  // Arrowheads spaced along a [lat, lon] path, pointing the way the satellite
  // flies. Nulls and antimeridian jumps break the run exactly as they do in
  // `path`, so no arrow lands on a seam the line itself does not draw.
  const arrows = (points, spacing = 120) => {
    let prev = null, prevLon = null, run = spacing / 2;
    for (const p of points) {
      if (!p) { prev = null; prevLon = null; continue; }
      const [x, y] = xy(p[0], p[1]);
      if (prev && Math.abs(p[1] - prevLon) <= 180) {
        run += Math.hypot(x - prev[0], y - prev[1]);
        if (run >= spacing) {
          arrowHead(ctx, x, y, Math.atan2(y - prev[1], x - prev[0]));
          run = 0;
        }
      } else {
        run = spacing / 2;
      }
      prev = [x, y]; prevLon = p[1];
    }
  };

  ctx.fillStyle = '#0a1420';
  ctx.fillRect(0, 0, w, h);

  // land
  ctx.fillStyle = '#17293b';
  ctx.strokeStyle = '#2c4a68';
  ctx.lineWidth = 0.6;
  for (const ring of (typeof WORLD_LAND === 'undefined' ? [] : WORLD_LAND)) {
    ctx.beginPath();
    for (let i = 0; i < ring.length; i += 2) {
      const [x, y] = xy(ring[i + 1], ring[i]);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
  }

  // graticule + equator
  ctx.lineWidth = 1;
  ctx.strokeStyle = 'rgba(36,52,74,.8)';
  for (let lon = -180; lon <= 180; lon += 30) {
    const [x] = xy(0, lon);
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
  }
  for (let lat = -60; lat <= 60; lat += 30) {
    const [, y] = xy(lat, 0);
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
  }

  // night side: the terminator is the latitude where the sun sits on the horizon
  const when = lastLive ? new Date(lastLive.sample.time) : new Date();
  const sun = subsolarPoint(when);
  const dec = (Math.abs(sun.lat) < 0.05 ? Math.sign(sun.lat || 1) * 0.05 : sun.lat) * DEG;
  ctx.beginPath();
  for (let lon = -180; lon <= 180; lon += 2) {
    const hourAngle = (lon - sun.lon) * DEG;
    const lat = Math.atan(-Math.cos(hourAngle) / Math.tan(dec)) / DEG;
    const [x, y] = xy(lat, lon);
    if (lon === -180) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  }
  const darkEdge = dec > 0 ? h : 0;   // sun north of the equator ⇒ south pole in darkness
  ctx.lineTo(w, darkEdge); ctx.lineTo(0, darkEdge);
  ctx.closePath();
  ctx.fillStyle = 'rgba(3,7,14,.5)';
  ctx.fill();

  if (!lastLive) {
    ctx.fillStyle = '#8397b0';
    ctx.font = '13px system-ui';
    ctx.textAlign = 'center';
    ctx.fillText('Press Live to plot the satellite position', w / 2, h / 2);
    return;
  }

  const s = lastLive.sample;
  const station = lastLive.station || {};
  const sat = [s.sub_lat_deg, s.sub_lon_deg];
  const track = lastLive.ground_track;

  // Ground track: the part already flown, then each revolution still to come.
  // Later orbits are drawn fainter so three overlapping sinusoids stay readable
  // and it is obvious which crossing is the imminent one.
  if (track && track.points) {
    const i = track.now_index;
    const flown = track.points.slice(0, i + 1);
    ctx.lineWidth = 1.6;
    ctx.strokeStyle = '#1d7f70';
    path(flown);
    ctx.fillStyle = 'rgba(29,127,112,.95)';
    arrows(flown);

    const AHEAD = ['#35e0c3', 'rgba(53,224,195,.5)', 'rgba(53,224,195,.28)'];
    const perRev = track.period_minutes * 60 / track.step_seconds;
    for (let rev = 0; ; rev++) {
      const from = i + Math.round(rev * perRev);
      if (from >= track.points.length - 1) break;
      // +1 so consecutive revolutions share a point and meet without a gap.
      const leg = track.points.slice(from, i + Math.round((rev + 1) * perRev) + 1);
      const shade = AHEAD[Math.min(rev, AHEAD.length - 1)];
      ctx.strokeStyle = shade;
      path(leg);
      ctx.fillStyle = shade;
      arrows(leg);
    }
  }

  // footprint of the current position
  ctx.lineWidth = 1.2;
  ctx.strokeStyle = 'rgba(255,180,84,.85)';
  ctx.setLineDash([5, 4]);
  path(footprint(sat[0], sat[1], s.altitude_km, lastLive.acu.min_elevation_deg));
  ctx.setLineDash([]);

  // line of sight from the station, drawn only while the satellite is up
  if (station.lat !== undefined && s.elevation_deg > 0) {
    ctx.strokeStyle = s.visible ? 'rgba(110,231,135,.8)' : 'rgba(131,151,176,.5)';
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 3]);
    path(arcBetween([station.lat, station.lon], sat));
    ctx.setLineDash([]);
  }

  // station marker
  if (station.lat !== undefined) {
    const [sx, sy] = xy(station.lat, station.lon);
    ctx.fillStyle = '#6ee787';
    ctx.fillRect(sx - 3, sy - 3, 6, 6);
    ctx.font = '11px system-ui';
    ctx.textAlign = 'left';
    ctx.fillText(station.name || 'Station', sx + 7, sy + 4);
  }

  // satellite marker
  const [px, py] = xy(sat[0], sat[1]);
  ctx.strokeStyle = 'rgba(53,224,195,.6)';
  ctx.lineWidth = 1;
  ctx.beginPath(); ctx.arc(px, py, 9, 0, Math.PI * 2); ctx.stroke();
  ctx.fillStyle = '#35e0c3';
  ctx.beginPath(); ctx.arc(px, py, 4.5, 0, Math.PI * 2); ctx.fill();

  // Heading arrow just clear of the marker, aimed at the next track sample, so
  // the current direction of travel reads without following the whole track.
  if (track && track.points) {
    const a = track.points[track.now_index], b = track.points[track.now_index + 1];
    if (a && b && Math.abs(b[1] - a[1]) <= 180) {
      const [ax, ay] = xy(a[0], a[1]), [bx, by] = xy(b[0], b[1]);
      const heading = Math.atan2(by - ay, bx - ax);
      arrowHead(ctx, px + 16 * Math.cos(heading), py + 16 * Math.sin(heading), heading, 8);
    }
  }

  ctx.font = '12px system-ui';
  ctx.textAlign = px > w - 140 ? 'right' : 'left';
  ctx.fillText(lastLive.satellite.name || 'Satellite', px + (px > w - 140 ? -13 : 13), py - 8);

  // position banner
  const ns = sat[0] >= 0 ? 'N' : 'S', ew = sat[1] >= 0 ? 'E' : 'W';
  const banner = `${Math.abs(sat[0]).toFixed(2)}°${ns}  ${Math.abs(sat[1]).toFixed(2)}°${ew}  ·  ` +
    `${fmt(s.altitude_km, 0)} km  ·  ${s.time.replace('T', ' ').replace('+00:00', '')} UTC`;
  ctx.font = '12px ui-monospace, Consolas, monospace';
  ctx.textAlign = 'left';
  ctx.fillStyle = 'rgba(11,15,20,.72)';
  ctx.fillRect(8, 8, ctx.measureText(banner).width + 16, 22);
  ctx.fillStyle = '#dbe6f3';
  ctx.fillText(banner, 16, 23);
}
