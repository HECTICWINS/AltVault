"use strict";
// AltVault's pages other than Accounts. Each is drawn from the data Python sends for it while it's
// open (see App.web_page_data); buttons send {type: "do", name, ...} back. Loaded before app.js,
// whose helpers (esc, $, send, state, page, pageData) these use when called.
const act = (name, extra = {}) => send({ type: "do", name, ...extra });
const card = (title, value, sub, t, subTone) =>
  `<div class="card"><small>${esc(title)}</small><b class="${t || ""}">${esc(value)}</b><span class="${subTone || ""}">${esc(sub || "")}</span></div>`;
const head = (title, sub, right = "") =>
  `<header class="head"><div class="titles"><h1>${esc(title)}</h1>${sub ? `<p class="sub" title="${esc(sub)}">${esc(sub)}</p>` : ""}</div><div class="actions">${right}</div></header>`;
const seg = (name, value, options) =>
  `<div class="seg" data-seg="${name}">${options.map(([k, l]) => `<button data-value="${k}"${k === value ? ' class="on"' : ""}>${esc(l)}</button>`).join("")}</div>`;
const btn = (label, name, attrs = "", cls = "") => `<button class="btn${cls ? " " + cls : ""}" data-do="${name}"${attrs}>${esc(label)}</button>`;
const cards = (list) => `<div class="cards">${list.map((c) => card(c.title, c.value, c.sub, c.tone, c.subTone)).join("")}</div>`;

function chart(title, series, ends, opts = {}) {
  // Bars for counts, lines for percentages. Hovering a bar shows its hour and value.
  const W = 600, H = 120, n = Math.max(1, ...series.map((s) => s.values.length));
  const top = opts.top || Math.max(1, ...series.flatMap((s) => s.values.filter((v) => v != null)));
  const x = (i) => (n === 1 ? W / 2 : (i / (n - 1)) * W), y = (v) => H - (v / top) * (H - 6);
  let body = "";
  series.forEach((s, k) => {
    if (opts.kind === "bars") {
      const slot = W / n, bw = Math.max(slot / series.length - 1, 1);
      s.values.forEach((v, i) => {
        if (v) body += `<rect class="${s.tone}" x="${(i * slot + k * bw).toFixed(1)}" y="${y(v).toFixed(1)}" width="${bw.toFixed(1)}" height="${(H - y(v)).toFixed(1)}"><title>${esc(opts.labels ? opts.labels[i] : "")} · ${esc(s.name)}: ${Math.round(v * 10) / 10}</title></rect>`;
      });
    } else {
      let d = "", pen = false;
      s.values.forEach((v, i) => {
        if (v == null) { pen = false; return; }
        d += `${pen ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)} `;
        pen = true;
      });
      if (d) body += `<path class="line ${s.tone}" d="${d}"/>`;
    }
  });
  const empty = series.every((s) => s.values.every((v) => !v));
  const legend = series.map((s) => `<span class="key ${s.tone}">${esc(s.name)}</span>`).join("");
  return `<div class="chart"><div class="chart-head"><b>${esc(title)}</b>${legend}</div>
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"><line class="axis" x1="0" y1="${H - 0.5}" x2="${W}" y2="${H - 0.5}"/>${body}</svg>
    ${empty ? '<div class="no-data">No data yet</div>' : ""}<div class="ends"><span>${esc(ends[0])}</span><span>${esc(ends[1])}</span></div></div>`;
}

const PAGES = {
  home(d) {
    const left = d.steps
      ? `<div class="panel"><h2>Get started</h2>${d.steps.map((s) => `<div class="line-item"><span class="dot ${s.done ? "ok" : "off"}"></span><span>${esc(s.text)}</span></div>`).join("")}</div>`
      : `<div class="panel"><h2>Needs attention</h2>${d.attention.map((a) => a.target
          ? `<button class="line-item link" data-do="open" data-target="${esc(a.target)}"><span class="dot ${a.tone}"></span><span>${esc(a.text)}</span></button>`
          : `<div class="line-item"><span class="dot ${a.tone}"></span><span>${esc(a.text)}</span></div>`).join("")}</div>`;
    const feed = d.activity.length
      ? d.activity.map((a) => `<div class="line-item"><span class="time">${esc(a.time)}</span><span class="${a.warn ? "warn" : "sub"} cut" title="${esc(a.text)}">${esc(a.text)}</span></div>`).join("")
      : '<p class="sub">Nothing yet.</p>';
    return head("Home", d.sub, `${seg("mode", state.mode, [["normal", "Normal"], ["grind", "Grind"], ["max", "Max alts"]])}<button class="btn primary" data-page="accounts">Join accounts</button>`)
      + cards(d.cards)
      + `<div class="quick">${btn("Add account", "add")}${btn("Pick a game", "goto", ' data-target="games"')}${btn("Watch windows", "goto", ' data-target="live"')}${btn("Arrange windows", "tile")}</div>
      <div class="split">${left}<div class="panel grow-panel"><h2>Recent activity</h2><div class="feed">${feed}</div></div></div>`;
  },

  profiles(d) {
    const rows = d.profiles.map((p) => `<div class="row plain">
      <span class="cut" title="${esc(p.name)}"><b>${esc(p.name)}</b></span><span class="sub cut" title="${esc(p.accounts)}">${esc(p.accounts)}</span><span class="sub cut" title="${esc(p.game)}">${esc(p.game)}</span>
      <span class="row-actions">${btn("Launch", "launch-profile", ` data-index="${p.index}"`, "primary small")}${btn("Edit", "edit-profile", ` data-index="${p.index}"`, "small")}${btn("Delete", "delete-profile", ` data-index="${p.index}"`, "small")}</span></div>`).join("");
    return head("Profiles", "A saved setup: some accounts and a game, launched with one click.", btn("New profile", "new-profile", "", "primary"))
      + `<div class="table list profiles-list"><div class="row head-row plain"><span>Name</span><span>Accounts</span><span>Game</span><span></span></div>
      <div class="rows">${rows || '<div class="empty"><strong>No profiles yet</strong><p>Save some accounts and a game to launch them together with one click.</p></div>'}</div></div>`;
  },

  games(d) {
    const rows = d.games.map((g) => `<div class="row plain">${g.icon ? `<img class="icon" src="${esc(g.icon)}" alt="">` : '<span class="icon"></span>'}
      <span class="cut" title="${esc(g.name)}"><b>${esc(g.name)}</b></span><span class="sub">${g.starred ? "Favourite" : "Recent"}</span><span class="sub">${esc(g.id)}</span>
      <span class="row-actions">${btn("Pick", "pick-game", ` data-place="${esc(g.id)}"`, "primary small")}${btn("Servers", "servers", ` data-place="${esc(g.id)}"`, "small")}${btn(g.starred ? "Unstar" : "Star", "star", ` data-place="${esc(g.id)}"`, "small")}${btn("Open", "open-game", ` data-place="${esc(g.id)}"`, "small")}${btn("Remove", "remove-game", ` data-place="${esc(g.id)}"`, "small")}</span></div>`).join("");
    return head("Games", "Your favourites first, then games you joined recently.")
      + `<div class="toolbar"><label class="field game"><span>Add a game</span><input id="add-game" placeholder="Paste a game link or place ID" autocomplete="off" spellcheck="false"></label>${btn("Add to favourites", "add-game")}</div>
      <div class="table list games-list"><div class="row head-row plain"><span></span><span>Game</span><span>Kind</span><span>Place ID</span><span></span></div>
      <div class="rows">${rows || '<div class="empty"><strong>No games yet</strong><p>Games you join show up here. Paste a link above to add a favourite.</p></div>'}</div></div>`;
  },

  live(d) {
    const tiles = d.windows.map((w) => `<div class="tile" data-hwnd="${w.hwnd}"><div class="tile-head"><span class="dot ${w.tone}"></span><b class="cut" title="${esc(w.name)}">${esc(w.name)}</b><span class="sub">${esc(w.usage)}</span>
      <button class="x" data-do="close-window" data-hwnd="${w.hwnd}" title="Close this window" aria-label="Close this window">×</button></div>
      <button class="tile-body" data-do="show-window" data-hwnd="${w.hwnd}" title="Bring this window to the front">${w.note ? `<span class="sub">${esc(w.note)}</span>` : ""}</button></div>`).join("");
    return head("Live", d.sub, `${btn("Tile", "tile")}${btn(d.allMin ? "Restore all" : "Minimize all", "minimize")}${btn("Close all", "close-all")}`)
      + (tiles ? `<div class="tiles cols-${Number(d.cols) || 3}">${tiles}</div>`
        : '<div class="empty"><strong>No Roblox windows open</strong><p>Join some accounts and their windows show up here, live.</p></div>');
  },

  stats(d) {
    return head("Stats", "How your accounts have been doing. Counted by AltVault while it runs.", seg("range", d.range, [["day", "24 hours"], ["week", "7 days"]]))
      + cards(d.cards)
      + `<div class="charts">${chart("Roblox windows", [{ name: "Open", values: d.open, tone: "grey" }, { name: "In a game", values: d.ingame, tone: "ok" }], d.ends, { kind: "bars", labels: d.labels })}
      ${chart("Anti-AFK", [{ name: "Pings", values: d.pings, tone: "text" }, { name: "Missed", values: d.missed, tone: "bad" }], d.ends, { kind: "bars", labels: d.labels })}</div>`;
  },

  pulse(d) {
    return head("Pulse", "Your PC and Roblox. Live while this page is open, sampled once a minute otherwise.", seg("range", d.range, [["hour", "Last hour"], ["day", "24 hours"], ["week", "7 days"]]))
      + cards(d.cards)
      + `<div class="charts">${chart("CPU", [{ name: "PC", values: d.pc_cpu, tone: "text" }, { name: "Roblox", values: d.rbx_cpu, tone: "ok" }], d.ends, { top: 100 })}
      ${chart("Memory", [{ name: "PC", values: d.pc_ram, tone: "text" }, { name: "Roblox", values: d.rbx_ram, tone: "ok" }], d.ends, { top: 100 })}</div>`;
  },

  doctor(d) {
    const pick = d.items.find((i) => i.key === d.chosen) || d.items[0];
    const list = d.items.map((i) => `<button class="row plain${pick && i.key === pick.key ? " sel" : ""}" data-do="doctor-pick" data-key="${esc(i.key)}">
      <span class="sub">${esc(i.when)}</span><span class="cut" title="${esc(i.who)}">${esc(i.who)}</span><span class="${i.tone} cut" title="${esc(i.title)}">${esc(i.title)}</span></button>`).join("");
    const detail = pick
      ? `<h2 class="${pick.tone}">${esc(pick.title)}</h2><p class="sub">${esc(pick.who)} · ${esc(pick.when)}</p><h3>Why</h3><p>${esc(pick.why)}</p><h3>What to do</h3><p>${esc(pick.fix)}</p>${pick.action ? btn(pick.action, "doctor-act", ` data-key="${esc(pick.key)}"`, "primary") : ""}`
      : '<h2>All clear</h2><p class="sub">Doctor reads Roblox\'s logs while your accounts run.</p>';
    return head("Doctor", "When an account drops: what happened, why and what to do.", d.items.length ? btn("Clear", "doctor-clear") : "")
      + cards(d.cards)
      + `<div class="split"><div class="table list doctor-list"><div class="row head-row plain"><span>When</span><span>Account</span><span>What happened</span></div>
      <div class="rows">${list || '<div class="empty"><strong>Nothing has gone wrong</strong><p>Findings show up here when an account drops.</p></div>'}</div></div><div class="panel grow-panel detail">${detail}</div></div>`;
  },

  settings(d) {
    const v = d.values, T = d.text;
    const toggle = (key, title, text) => `<label class="option"><input type="checkbox" data-set="${key}"${v[key] ? " checked" : ""}><span><b>${esc(title)}</b><small>${esc(text)}</small></span></label>`;
    const select = (key, title, options, text = "") => `<label class="option"><span><b>${esc(title)}</b>${text ? `<small>${esc(text)}</small>` : ""}</span>
      <select data-set="${key}">${options.map(([val, l]) => `<option value="${esc(val)}"${String(v[key]) === String(val) ? " selected" : ""}>${esc(l)}</option>`).join("")}</select></label>`;
    const tabs = [["general", "General"], ["modes", "Modes"], ["afk", "Anti-AFK"], ["alerts", "Alerts"], ["privacy", "Privacy"], ["data", "Data"]];
    const body = {
      general: `<h2>Joining</h2>${toggle("multi", "Multi-Roblox", T.multi)}${toggle("same_server", "Put accounts in the same server", T.same_server)}
        ${toggle("auto_tile", "Arrange windows after joining", T.auto_tile)}${select("launch_delay", "Wait between launches", d.delays, T.launch_delay)}
        ${toggle("launch_memory_wait", "Wait for free memory before each launch", T.launch_memory_wait)}`,
      modes: `<h2>Mode</h2>${select("mode", "Mode", [["normal", "Normal"], ["grind", "Grind"], ["max", "Max alts"]], T.modes)}${select("main_account", "Main account", d.mains, T.main)}
        <h2>Lighter rendering</h2>${toggle("lighter_rendering", "Lower textures and effects on lowest graphics", T.lighter)}`,
      afk: `<h2>Anti-AFK</h2>${toggle("anti_afk", "Anti-AFK", T.afk_how)}${select("afk_key", "Key it presses", d.keys, T.afk_key)}`,
      alerts: `<h2>Discord alerts</h2><p class="sub">${esc(T.webhook)}</p>
        <div class="toolbar"><label class="field grow-field"><input id="webhook" type="password" placeholder="https://discord.com/api/webhooks/..." autocomplete="off" spellcheck="false"></label>${btn("Save", "webhook-save", "", "primary")}${btn("Send a test", "webhook-test")}${btn("Remove", "webhook-remove")}</div>
        <p class="sub">${esc(d.webhook)}</p><h2>Panic hotkey</h2>${toggle("panic_hotkey", `Close everything with ${d.panicKey}`, T.panic)}`,
      privacy: `<h2>Privacy</h2>${toggle("stream_mode", "Stream mode", T.stream)}${toggle("hide_taskbar", "Hide Roblox from the taskbar", T.taskbar)}
        <h2>Private servers</h2><p class="sub">${esc(T.private)}</p><div class="btn-row">${btn("Forget private link", "forget-link")}</div>`,
      data: `<h2>Your accounts</h2><p class="sub">${esc(T.accounts)}</p><div class="btn-row">${btn("Export accounts...", "export-accounts")}${btn("Import accounts...", "import-accounts")}</div>
        <h2>Your settings</h2><p class="sub">${esc(T.backup)}</p><div class="btn-row">${btn("Back up settings...", "backup")}${btn("Restore settings...", "restore")}</div>
        <h2>Upkeep</h2>${toggle("clean_logs", "Delete old Roblox logs", T.clean_logs)}${toggle("update_check", "Check for updates", T.update_check)}
        <h2>About</h2><p>AltVault ${esc(d.version)} · Made by HecticWins</p>
        <div class="btn-row">${btn("Open on GitHub", "github")}${btn("Open log folder", "log-folder")}${btn("Check for updates", "check-updates")}${btn("Copy diagnostics", "diagnostics")}</div>`,
    }[d.tab] || "";
    return head("Settings", "Changes are saved straight away.", seg("tab", d.tab, tabs)) + `<div class="settings">${body}</div>`;
  },
};

function drawPage() {
  const sec = $("page-" + page), d = pageData[page];
  if (!sec || page === "accounts") return;
  if (!d) { sec.innerHTML = ""; return; }
  const focused = document.activeElement && document.activeElement.id;   // keep what's being typed across redraws
  const typed = focused && $(focused) ? $(focused).value : null;
  const scroller = sec.querySelector(".rows, .settings, .feed");
  const scroll = scroller ? scroller.scrollTop : 0;
  sec.innerHTML = PAGES[page](d);
  if (typed !== null && $(focused)) { $(focused).value = typed; $(focused).focus(); }
  const again = sec.querySelector(".rows, .settings, .feed");
  if (again) again.scrollTop = scroll;
  if (page === "live") requestAnimationFrame(reportTiles);
}

// Live: Windows itself draws each Roblox window's live preview over its tile, so Python needs to
// know where the tiles are, in real pixels.
function reportTiles() {
  if (page !== "live") return;
  const r = devicePixelRatio;
  send({ type: "tiles", tiles: [...document.querySelectorAll(".tile")].map((t) => {
    const b = t.querySelector(".tile-body").getBoundingClientRect();
    return { hwnd: Number(t.dataset.hwnd), x: Math.round(b.left * r), y: Math.round(b.top * r), w: Math.round(b.width * r), h: Math.round(b.height * r) };
  }) });
}
addEventListener("DOMContentLoaded", () => new ResizeObserver(() => { if (page === "live") reportTiles(); }).observe(document.documentElement));

document.addEventListener("click", (e) => {
  const sg = e.target.closest("[data-seg] button");
  if (sg) {
    const name = sg.parentElement.dataset.seg;
    if (name === "mode") send({ type: "mode", value: sg.dataset.value });
    else act("seg", { seg: name, value: sg.dataset.value, page });
    return;
  }
  const b = e.target.closest("[data-do]");
  if (!b) return;
  const extra = { ...b.dataset };
  delete extra.do;
  if (b.dataset.do === "add-game") extra.text = $("add-game").value;
  if (b.dataset.do === "webhook-save") extra.url = $("webhook").value;
  act(b.dataset.do, extra);
});
document.addEventListener("change", (e) => {
  const el = e.target.closest("[data-set]");
  if (el) act("set", { key: el.dataset.set, value: el.type === "checkbox" ? el.checked : el.value });
});
