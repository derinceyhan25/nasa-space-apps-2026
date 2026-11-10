// Fire Calendar: reads the pre-computed files in data/ (made by backend/app/export_static.py).
// Replaces the server endpoints /api/geocode, /api/world/calendar and /api/world/globe.
const FC = (() => {
  const BASE = "data/";
  const cache = new Map();

  function json(path) {
    if (!cache.has(path)) {
      const p = fetch(BASE + path).then(r => {
        if (r.status === 404) return null;
        if (!r.ok) throw new Error(`Could not load ${path} (${r.status})`);
        return r.json();
      });
      p.catch(() => cache.delete(path));
      cache.set(path, p);
    }
    return cache.get(path);
  }

  // ---------------------------------------------------------------- search
  const norm = s => String(s || "").replace(/\u0131/g, "i").normalize("NFKD").replace(/[\u0300-\u036f]/g, "")
    .toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
  const ALIAS = {
    "turkiye": "turkey", "usa": "united states of america", "us": "united states of america",
    "united states": "united states of america", "america": "united states of america",
    "uk": "united kingdom", "britain": "united kingdom", "great britain": "united kingdom",
    "holland": "netherlands", "czech republic": "czechia", "ivory coast": "cote d ivoire",
    "drc": "democratic republic of the congo", "congo kinshasa": "democratic republic of the congo",
  };
  const alias = s => ALIAS[s] || s;

  let INDEX = null;
  async function index() {
    if (!INDEX) {
      INDEX = json("search.json").then(d => ({
        regions: d.regions.map(([name, country, id, bbox, center]) => ({
          name, country, id, bbox, center, kind: id.startsWith("c-") ? "country" : "state",
          n: norm(name), cn: norm(country || name),
          area: (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]),
        })),
        cities: d.cities.map(([name, country, adm1, lat, lon], i) => ({
          name, country, adm1, lat, lon, rank: i, n: norm(name), cn: norm(country), an: norm(adm1),
        })),
      }));
      INDEX.catch(() => (INDEX = null));
    }
    return INDEX;
  }

  const fits = (b, ...names) => !b || names.some(x => x && (x === b || x.startsWith(b)));

  function candidates(ix, a, b, prefix) {
    const hit = x => prefix ? x.startsWith(a) : x === a;
    const out = [];
    for (const r of ix.regions) {
      if (!hit(r.n)) continue;
      if (r.kind === "country" ? !b : fits(b, r.cn))
        out.push({ score: (r.kind === "country" ? 3 : 2) + (prefix ? 0 : 10), tie: r.area, r });
    }
    for (const c of ix.cities) {
      if (hit(c.n) && fits(b, c.cn, c.an)) out.push({ score: 1 + (prefix ? 0 : 10), tie: -c.rank, c });
    }
    return out.sort((x, y) => y.score - x.score || y.tie - x.tie);
  }

  function cellPlace(lat, lon, name, sub) {
    lat = Math.max(-90, Math.min(89.999, lat));
    lon = ((lon + 180) % 360 + 360) % 360 - 180;
    const gx = Math.floor(lon) + 180, gy = Math.floor(lat) + 90;
    const w = gx - 180, s = gy - 90;
    return {
      name, kind: "cell", lat, lon, bbox: [w, s, w + 1, s + 1],
      sub: sub || `1\u00b0\u00d71\u00b0 cell ${s}\u00b0\u2026${s + 1}\u00b0 N, ${w}\u00b0\u2026${w + 1}\u00b0 E`,
      file: `grid/${Math.floor(gx / 10)}_${Math.floor(gy / 10)}.json`, key: `${gx}_${gy}`,
    };
  }

  const DISPLAY = { "Turkey": "T\u00fcrkiye", "United States of America": "USA", "United Kingdom": "UK" };  // preferred English short names
  function regionPlace(r) {
    const [w, s, e, n] = r.bbox, ctr = r.center || [(w + e) / 2, (s + n) / 2];
    return {
      name: r.kind === "state" ? `${r.name}, ${DISPLAY[r.country] || r.country}` : (DISPLAY[r.name] || r.name), kind: r.kind,
      lat: ctr[1], lon: ctr[0], bbox: r.bbox, file: `regions/${r.id}.json`,
      sub: r.kind === "state" ? "state / province boundary (Natural Earth)" : "country boundary (Natural Earth)",
    };
  }

  // "California", "Amazonas, Brazil", "Izmir", "38.4, 27.1" -> place
  async function find(q) {
    q = String(q).trim();
    const ll = q.match(/^\s*(-?\d+(?:\.\d+)?)\s*[,;\s]\s*(-?\d+(?:\.\d+)?)\s*$/);
    if (ll) {
      const lat = +ll[1], lon = +ll[2];
      if (Math.abs(lat) > 90 || Math.abs(lon) > 180) throw new Error("Coordinates must be latitude (-90\u202690), longitude (-180\u2026180).");
      return cellPlace(lat, lon, `${lat.toFixed(2)}, ${lon.toFixed(2)}`);
    }
    const ix = await index();
    const parts = q.split(",").map(norm).filter(Boolean);
    if (!parts.length) throw new Error("Type a place name or coordinates.");
    const a = alias(parts[0]), b = parts[1] ? alias(parts[1]) : null;
    const best = candidates(ix, a, b, false)[0] || candidates(ix, a, b, true)[0];
    if (!best) throw new Error(`No place called \u201c${q}\u201d in the list. Try a country, a state or province, a bigger city, or coordinates like 38.4, 27.1.`);
    if (best.r) return regionPlace(best.r);
    const c = best.c;
    return cellPlace(c.lat, c.lon, `${c.name}, ${c.country}`, `1\u00b0\u00d71\u00b0 area around ${c.name} (about 110 \u00d7 ${Math.max(10, Math.round(111 * Math.cos(c.lat * Math.PI / 180)))} km)`);
  }

  async function suggest(q, limit = 8) {
    const a = alias(norm(q.split(",")[0]));
    if (a.length < 2) return [];
    const ix = await index(), seen = new Set(), out = [];
    for (const x of candidates(ix, a, null, false).concat(candidates(ix, a, null, true))) {
      const label = x.r ? (x.r.kind === "state" ? `${x.r.name}, ${x.r.country}` : x.r.name) : `${x.c.name}, ${x.c.country}`;
      if (!seen.has(label)) { seen.add(label); out.push(label); }
      if (out.length >= limit) break;
    }
    return out;
  }

  // ---------------------------------------------------------------- calendars
  // numpy-style rounding (round half to even after scaling), like the Python side
  function npRound(x, d) {
    const k = 10 ** d, y = x * k, f = Math.floor(y), r = y - f;
    return (r > 0.5 ? f + 1 : r < 0.5 ? f : (f % 2 === 0 ? f : f + 1)) / k;
  }
  function median(v) {
    const s = [...v].sort((p, q) => p - q), m = s.length >> 1;
    return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
  }
  const FLAG = { "1": true, "0": false, "-": null };

  // compact file entry -> the same shape /api/world/calendar returned
  function expand(c) {
    const y0 = +c.start.slice(0, 4), m0 = +c.start.slice(5, 7) - 1;
    const months = c.count.map((_, i) => {
      const t = m0 + i;
      return `${y0 + Math.floor(t / 12)}-${String(t % 12 + 1).padStart(2, "0")}`;
    });
    const byCal = {};
    months.forEach((mo, i) => { if (c.count[i] != null) (byCal[mo.slice(5)] ||= []).push(c.count[i]); });
    const med = {};
    for (const k in byCal) med[k] = median(byCal[k]);
    const calendar = months.map((mo, i) => {
      const n = c.count[i], md = med[mo.slice(5)];
      const vs = n == null || md == null || md <= 0 ? null : npRound(n / md, 2);
      return {
        month: mo, count: n, estimated: FLAG[c.est[i]], provisional: FLAG[c.prov[i]],
        median: md == null ? null : npRound(md, 1), vs_median: vs, anomaly: vs != null && vs >= 1.5,
      };
    });
    return {
      calendar, ratio: c.ratio, ratio_basis: c.ratio_basis, overlap_months: c.overlap_months,
      current_month: c.current_month, months_with_data: calendar.filter(r => r.count != null).length,
    };
  }

  async function calendar(place) {
    const d = await json(place.file);
    const c = d && (place.key ? d[place.key] : d);
    if (!c) throw new Error("No fires were detected in this 1\u00b0\u00d71\u00b0 area since 2000 (open ocean, ice or desert?). Try a nearby place.");
    return expand(c);
  }

  async function globeMonths() { return (await json("globe/months.json")) || []; }
  async function globeCells(m) { return (await json(`globe/${m}.json`)) || []; }

  return { json, find, suggest, calendar, expand, globeMonths, globeCells, npRound, norm };
})();
if (typeof module !== "undefined") module.exports = FC;

