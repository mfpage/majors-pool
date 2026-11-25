const REFRESH_MS = 5 * 60 * 1000;
const state = { years: [], year: null, data: null };
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const SHORT = { masters: "Masters", pga: "PGA", usopen: "US Open", open: "The Open" };
const tabLabel = (m) => m.test ? "Test · " + m.name.replace(/\s+(Championship|Tournament|Classic)$/i, "") : (SHORT[m.key] || m.name);

function scoreNum(s) { return s === "E" ? 0 : Number(s); }
function isUnder(score) { return typeof score === "string" && score.startsWith("-"); }
function ordinal(n) { const s = ["th", "st", "nd", "rd"], v = n % 100; return n + (s[(v - 20) % 10] || s[v] || s[0]); }
function when(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const date = d.toLocaleDateString("en-US", { month: "2-digit", day: "2-digit", year: "numeric", timeZone: "America/New_York" });
  const time = d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", timeZone: "America/New_York" });
  return `${date} ${time} ET`;
}
function dateRange(a, b) {
  if (!a) return "";
  const [y1, m1, d1] = a.split("-").map(Number);
  const s = new Date(y1, m1 - 1, d1);
  const mon = (d) => d.toLocaleDateString("en-US", { month: "short" });
  if (!b) return `${mon(s)} ${d1}, ${y1}`;
  const [y2, m2, d2] = b.split("-").map(Number);
  const e = new Date(y2, m2 - 1, d2);
  return m1 === m2 ? `${mon(s)} ${d1}–${d2}, ${y1}` : `${mon(s)} ${d1} – ${mon(e)} ${d2}, ${y2}`;
}
function venueLine(m) {
  const v = m.venue || {};
  const parts = [];
  if (v.course) parts.push(`<b>${esc(v.course)}</b>`);
  if (v.place) parts.push(esc(v.place));
  if (m.start) parts.push(dateRange(m.start, m.end));
  if (v.par) parts.push(`Par ${v.par}`);
  if (v.yards) parts.push(`${Number(v.yards).toLocaleString("en-US")} yards`);
  return parts.join('<span class="sep">·</span>');
}
function day(isoDate) {
  if (!isoDate) return "";
  const [y, m, d] = isoDate.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}
function statusText(g) {
  if (g.out) return g.status;  // CUT, WD, DQ, DNS, DNP
  const s = g.status || "";
  if (/^\d+$/.test(s)) return "Thru " + s;
  if (/[AP]M$/i.test(s)) return s.replace(/\s+/, " ");
  return s;
}

// ---- Page addresses ----------------------------------------------------------
// The pool: <site>/2026-masters, <site>/2026-overall ...  The health page:
// <site>/health, <site>/health/roster, <site>/health/run.
const SITE_ROOT = new URL(".", document.currentScript.src).pathname;
const HEALTH_DIR = new URL(".", location.href).pathname;
const SLUG = { masters: "masters", pga: "pga-championship", usopen: "us-open", open: "british-open", overall: "overall" };
const KEY_BY_SLUG = Object.fromEntries(Object.entries(SLUG).map(([k, v]) => [v, k]));
function lastSegment() {
  return (location.pathname.split("/").filter(Boolean).pop() || "").replace(/\.html$/, "");
}
function routeFromPath() {
  const m = lastSegment().match(/^(\d{4})-([a-z-]+)$/);
  if (m && KEY_BY_SLUG[m[2]]) return { year: Number(m[1]), tab: KEY_BY_SLUG[m[2]] };
  const old = location.hash.slice(1);  // links from before, like #masters
  if (SLUG[old]) return { year: null, tab: old };
  return null;
}
function pageUrl(year, tab) { return SITE_ROOT + year + "-" + SLUG[tab]; }
function fileUrl(file) { return (window.POOL_HEALTH ? HEALTH_DIR : SITE_ROOT) + encodeURI(file); }

async function getJSON(path) {
  const r = await fetch(path + "?t=" + Date.now(), { cache: "no-store" });
  if (!r.ok) throw new Error(r.status);
  return r.json();
}

async function load() {
  try {
    const idx = await getJSON(SITE_ROOT + "data/index.json");
    state.years = idx.years || [];
    if (!state.year || !state.years.includes(state.year)) state.year = state.years[0];
    state.data = state.year ? await getJSON(`${SITE_ROOT}data/${state.year}.json`) : null;
  } catch (e) {
    if (!state.data) {
      $("#main").innerHTML = `<div class="empty"><b>No standings yet.</b> The page fills in the first time the update job runs during a major week.</div>`;
      return;
    }
  }
  render();
}

function defaultTab() {
  const ms = state.data?.majors || [];
  const live = ms.find((m) => m.state === "live") || ms.find((m) => m.state === "pre");
  if (live) return live.key;
  const done = ms.filter((m) => m.state === "final");
  return done.length ? done[done.length - 1].key : (ms[0]?.key || "overall");
}

function render() {
  const data = state.data;
  if (!data) return;
  const majors = data.majors || [];
  const keys = majors.map((m) => m.key).concat("overall");
  let tab = state.wantTab;
  if (!keys.includes(tab)) tab = defaultTab();  // the home page opens on the latest tournament
  state.tab = tab;
  const here = pageUrl(state.year, tab);
  if (location.pathname !== here || location.hash) history.replaceState(null, "", here);

  $("#year").innerHTML = state.years.length >= 1
    ? `<label>Season <select id="yearSel">${state.years.map((y) => `<option ${y === state.year ? "selected" : ""}>${y}</option>`).join("")}</select></label>`
    : `${data.year} season`;
  const sel = $("#yearSel");
  if (sel) sel.onchange = () => {
    // stay on the same tab (Masters stays Masters) in the other season
    state.year = Number(sel.value);
    state.wantTab = state.tab;
    history.pushState(null, "", pageUrl(state.year, state.tab));
    load();
  };

  $("#tabs").innerHTML = majors.map((m) =>
    `<a href="${pageUrl(state.year, m.key)}" data-nav ${tab === m.key ? 'aria-current="page"' : ""}>${m.state === "live" ? '<span class="dot" aria-label="Live"></span>' : ""}${esc(tabLabel(m))}</a>`
  ).join("") + `<a href="${pageUrl(state.year, "overall")}" data-nav ${tab === "overall" ? 'aria-current="page"' : ""}>Overall</a>`;

  $("#main").innerHTML = tab === "overall" ? overallView(data) : majorView(majors.find((m) => m.key === tab), data);
  document.title = `Page Family Golf Pool ${data.year}`;
}

function getOrder() { try { return localStorage.getItem("poolOrder") || "draft"; } catch (e) { return "draft"; } }
function setOrder(v) { try { localStorage.setItem("poolOrder", v); } catch (e) {} }
function ordered(standings) {
  return getOrder() === "rank" ? [...standings].sort((a, b) => a.rank - b.rank) : standings;
}
function orderToggle() {
  const cur = getOrder();
  return `<div class="order" role="group" aria-label="Order players by">
    <button type="button" data-order="draft" aria-pressed="${cur === "draft"}">Draft order</button>
    <button type="button" data-order="rank" aria-pressed="${cur === "rank"}">Ranking</button></div>`;
}
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-order]");
  if (!b) return;
  setOrder(b.dataset.order);
  if (window.POOL_HEALTH) renderHealth(); else render();
});

function majorView(m, data) {
  if (!m) return "";
  let pill, meta = "";
  if (m.state === "live") { pill = `<span class="pill live"><span class="dot"></span>Live · Round ${m.round}</span>`; }
  else if (m.state === "final") { pill = `<span class="pill final">Final</span>`; }
  else if (m.state === "pre") { pill = `<span class="pill">Field set · starts ${day(m.start)}</span>`; }
  else {
    const past = Number(state.year) < new Date().getFullYear();
    pill = `<span class="pill">${(!m.event_id && past) || m.state === "notplayed" ? "Not played" : "Upcoming"}</span>`;
  }
  if (m.updated && m.state !== "final") meta = `Updated ${when(m.updated)}${m.state === "live" ? " · checks every 15 minutes" : ""}`;

  if (m.test) pill += ` <span class="pill">Test run · doesn't count</span>`;
  let html = `<section class="head"><h2>${esc(m.name)}</h2>${pill}<span class="meta">${meta}</span></section>`;
  const facts = venueLine(m);
  if (facts) html += `<p class="venue">${facts}</p>`;
  if (m.error) html += `<div class="banner">${esc(m.error)}</div>`;

  if (!m.standings) return html;  // not played yet, or never played: just the heading

  html += `<section><div class="section-head"><h3>${m.state === "final" ? "Final standings" : "Standings"}</h3>${orderToggle()}</div><div class="board">` +
    ordered(m.standings).map((s) => `<div class="place ${s.rank === 1 ? "first" : ""}">
      <span class="rk">${ordinal(s.rank).toUpperCase()}</span>
      <span class="who">${esc(s.person)}</span>
      <span class="tot ${isUnder(s.total) ? "under" : ""}">${esc(s.total)}</span>
      <span class="sub">${s.usable} ${s.usable === 1 ? "player" : "players"}</span>
    </div>`).join("") + `</div></section>`;

  html += `<section><h3>Top 5</h3><div class="teams">` + ordered(m.standings).map((s) => {
    const top = s.golfers.filter((g) => g.counts).sort((a, b) => scoreNum(a.score) - scoreNum(b.score));
    return `<div class="team"><h4>${esc(s.person)}<span class="${isUnder(s.total) ? "under" : ""}">${esc(s.total)}</span></h4><ol>` +
      (top.length ? top.map((g) => `<li class="counts"><span class="mark"></span>
        <span class="nm">${esc(g.name)}</span>
        <span class="sc ${isUnder(g.score) ? "under" : ""}">${esc(g.score)}</span>
        <span class="st">${esc(statusText(g))}</span></li>`).join("") : `<li><span></span><span class="nm">No golfers left</span></li>`) +
      `</ol></div>`;
  }).join("") + `</div></section>`;

  html += `<section><h3>Teams</h3><div class="teams">` + ordered(m.standings).map((s) => `
    <div class="team"><h4>${esc(s.person)}<span>${ordinal(s.rank)}</span></h4><ol>` +
      s.golfers.map((g, i) => `<li class="${g.counts ? "counts" : ""} ${g.out ? "out" : ""} ${i % 4 === 0 && i ? "gap" : ""}">
        <span class="mark">${g.counts ? "●" : ""}</span>
        <span class="nm" title="${esc(g.espn && g.espn !== g.name ? `${g.name} (ESPN: ${g.espn})` : g.name)}">${esc(g.name)}</span>
        <span class="sc ${!g.out && isUnder(g.score) ? "under" : ""}">${g.out ? "" : esc(g.score)}</span>
        <span class="st">${esc(statusText(g))}</span>
      </li>`).join("") + `</ol></div>`).join("") +
    `</div><p class="legend">● Top five</p></section>`;

  const snaps = Object.fromEntries((m.snapshots || []).map((s) => [s.day, s]));
  html += `<section><h3>End-of-day files</h3><div class="days">` + [1, 2, 3, 4].map((d) => {
    const s = snaps[d];
    if (!s) return `<div class="chip pending"><b>Day ${d}</b><small>${d === 2 ? "Saved when round 2 is done and the cut is posted" : `Saved when round ${d} is done`}</small></div>`;
    const leader = Object.entries(s.ranks).find(([, r]) => r === 1)?.[0];
    return `<a class="chip" href="${fileUrl(s.file)}" download><b>Day ${d}</b><small>${esc(leader)} leads at ${esc(s.totals[leader])}${m.state === "final" ? "" : " · " + when(s.saved)}</small></a>`;
  }).join("") + `</div></section>`;

  return html;
}

function overallView(data) {
  const majors = (data.majors || []).filter((m) => !m.test);
  const rows = data.overall || [];
  const played = majors.filter((m) => m.state === "live" || m.state === "final");
  let html = `<section class="head"><h2>${data.year} Overall</h2><span class="meta">${data.updated && majors.some((m) => m.state === "live") ? "Updated " + when(data.updated) : ""}</span></section>`;
  if (!played.length) {
    return html + `<div class="empty"><b>No majors played yet.</b> Each player's finishing position in each major is added up here, and the lowest total wins.</div>`;
  }
  html += `<div class="tablewrap"><table><thead><tr><th class="who">Player</th>` +
    majors.map((m) => `<th>${esc(SHORT[m.key] || m.name)}</th>`).join("") +
    `<th>Total</th><th>Place</th></tr></thead><tbody>` +
    rows.map((r) => `<tr><td class="who">${esc(r.person)}</td>` +
      majors.map((m) => {
        const v = r.ranks[m.key];
        if (v == null) return `<td class="n"></td>`;
        return `<td class="n">${m.state === "live" ? `<span class="lv" title="Live, not final">${v}</span>` : v}</td>`;
      }).join("") +
      `<td class="total">${r.total}</td><td class="place-col">${ordinal(r.place)}</td></tr>`).join("") +
    `</tbody></table></div>`;

  return html;
}


// ---- Health check page (unlisted) ------------------------------------------
let health = null;
async function loadHealth() {
  try { health = await getJSON(HEALTH_DIR + "health.json"); }
  catch (e) { $("#main").innerHTML = `<div class="empty"><b>No health check yet.</b> It appears after the next scheduled run.</div>`; return; }
  renderHealth();
}

function renderHealth() {
  const h = health;
  if (!h) return;
  const tabs = [["tournament", "Test Tournament Status", ""], ["roster", "Roster Status", "roster"], ["checks", "Run Status", "run"]];
  const seg = lastSegment(), old = location.hash.slice(1);
  let tab = seg === "roster" ? "roster" : seg === "run" ? "checks" : (old === "roster" || old === "checks") ? old : "tournament";
  const here = HEALTH_DIR + tabs.find(([k]) => k === tab)[2];
  if (location.pathname !== here || location.hash) history.replaceState(null, "", here);
  const overall = h.checks.some((c) => c.status === "fail") ? "fail" : h.checks.some((c) => c.status === "warn") ? "warn" : "pass";
  const roster = h.roster_rows || [];
  const found = roster.filter((r) => r.found).length;

  $("#year").textContent = "";
  $("#tabs").innerHTML = tabs.map(([k, label, path]) => {
    let flag = "";
    if (k === "checks" && overall !== "pass") flag = `<span class="flag ${overall}">${overall === "fail" ? "Failed" : "Warning"}</span>`;
    if (k === "roster" && roster.length && found < roster.length) flag = `<span class="flag warn">${roster.length - found}</span>`;
    return `<a href="${HEALTH_DIR + path}" data-nav ${tab === k ? 'aria-current="page"' : ""}>${label}${flag}</a>`;
  }).join("");

  let html = "";
  if (tab === "tournament") {
    html = h.event
      ? majorView(h.event, h)
      : `<div class="empty"><b>No tournament on ESPN right now.</b></div>`;
  } else if (tab === "roster") {
    html = `<section class="head"><h2>${found} of ${roster.length} found</h2><span class="meta">${h.roster_year} season</span></section>

      <div class="teams">` +
      h.people.map((p) => `<div class="team"><h4>${esc(p)}</h4><ol>` +
        roster.filter((r) => r.person === p).map((r) => r.found
          ? `<li class="name-ok" title="Found at ${esc(r.found)}"><span class="mark">●</span><span class="nm">${esc(r.name)}</span></li>`
          : `<li class="name-missing"><span class="mark">●</span><span class="nm">${esc(r.name)}</span><span class="st">Not yet</span>
             ${r.hint ? `<span class="hint">ESPN lists <b>${esc(r.hint)}</b>${
               { spelling: " (accents or punctuation differ)", typo: " (one letter off)", flipped: " (names swapped)" }[r.kind] || ""} at ${esc(r.where)}</span>` : ""}</li>`
        ).join("") + `</ol></div>`).join("") + `</div>
      <p class="note legend-note">Green: ESPN has listed this exact name in at least one tournament field this season (hover to see where).
      Red: not found yet, which is normal until he plays. If ESPN lists a near match (only accents or punctuation different,
      one letter off, or first and last name swapped), it's shown so you can fix the teams file.</p>`;
  } else {
    const icon = { pass: "Pass", warn: "Warning", fail: "Failed" };
    const head = { pass: "Everything is working", warn: "Working, with warnings", fail: "Something is broken" }[overall];
    html = `<section class="head"><h2>${head}</h2><span class="meta">Checked ${when(h.updated)} against ${esc(h.event_name || "no tournament")}</span></section>
      <div class="checks">` + h.checks.map((c) =>
        `<div class="check ${c.status === "pass" ? "spelling" : c.status === "warn" ? "likely" : "dnp"}"><span class="tag">${icon[c.status]}</span>
         <span><b>${esc(c.name)}.</b> ${esc(c.detail)}</span></div>`).join("") + `</div>`;
  }
  $("#main").innerHTML = html;
}

// Tabs switch without reloading; the address changes and Back/Forward work
function onRoute() {
  if (window.POOL_HEALTH) return renderHealth();
  const r = routeFromPath();
  state.wantTab = r ? r.tab : null;
  if (r && r.year && r.year !== state.year) { state.year = r.year; load(); } else render();
}
document.addEventListener("click", (e) => {
  const a = e.target.closest("a[data-nav]");
  if (!a || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  e.preventDefault();
  if (a.getAttribute("href") !== location.pathname) history.pushState(null, "", a.getAttribute("href"));
  onRoute();
});
window.addEventListener("popstate", onRoute);

if (window.POOL_HEALTH) { loadHealth(); setInterval(loadHealth, REFRESH_MS); }
else {
  const r = routeFromPath();
  state.year = r && r.year ? r.year : null;
  state.wantTab = r ? r.tab : null;
  load();
  setInterval(load, REFRESH_MS);
}
