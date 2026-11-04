"use strict";
// AltVault's page. Python sends the whole state as one object whenever something changes; the page
// only draws it and sends back what the user asked for. Nothing here talks to the internet.
const host = window.chrome && window.chrome.webview;
const send = (msg) => host && host.postMessage(msg);
const $ = (id) => document.getElementById(id);

const ICONS = {
  home: '<path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>',
  accounts: '<circle cx="9" cy="8" r="4"/><path d="M2 21v-1a6 6 0 0 1 6-6h2a6 6 0 0 1 6 6v1"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/><path d="M22 21v-1a6 6 0 0 0-4-5.65"/>',
  profiles: '<path d="M12 2 2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/>',
  games: '<rect x="2" y="6" width="20" height="12" rx="4"/><path d="M6 12h4M8 10v4"/><path d="M15 13h.01M18 11h.01"/>',
  live: '<rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/>',
  stats: '<path d="M3 3v18h18"/><path d="m7 15 4-4 3 3 5-6"/>',
  pulse: '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
  doctor: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/>',
  settings: '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/>',
};
const svg = (name) => `<svg viewBox="0 0 24 24">${ICONS[name] || ""}</svg>`;
const NAV = [["home", "Home"], ["accounts", "Accounts"], ["profiles", "Profiles"], ["games", "Games"], null,
             ["live", "Live"], ["stats", "Stats"], ["pulse", "Pulse"], ["doctor", "Doctor"]];

let state = { accounts: [], mode: "normal" }, selected = new Set(), closed = new Set(), page = "home";
const pageData = {};   // the latest data Python sent for each page

function esc(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function drawNav() {
  const counts = { accounts: state.accounts.length || "", live: state.open || "" };
  $("nav").innerHTML = NAV.map((item) => item ? `<button class="nav${item[0] === page ? " on" : ""}" data-page="${item[0]}">
      <span class="ico">${svg(item[0])}</span>${item[1]}${counts[item[0]] ? `<span class="n">${counts[item[0]]}</span>` : ""}</button>`
    : '<div class="nav-gap"></div>').join("");
  document.querySelector('.side > [data-page="settings"] .ico').innerHTML = svg("settings");
}

function visible(acc) {
  const q = $("search").value.trim().toLowerCase();
  if (q && !(`${acc.display} ${acc.name} ${acc.note}`.toLowerCase().includes(q))) return false;
  const f = $("filter").value;
  return f === "all" || (f === "ingame" && acc.kind === "ingame") || (f === "offline" && acc.kind === "offline")
    || (f === "problems" && acc.kind === "problem");
}

function drawRows() {
  const groups = new Map();
  for (const acc of state.accounts.filter(visible)) {
    const g = acc.group || "";
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push(acc);
  }
  const named = [...groups.keys()].filter(Boolean).sort((a, b) => a.localeCompare(b));
  const order = groups.has("") && named.length ? [...named, ""] : [...named, ...(groups.has("") ? [""] : [])];
  let html = "";
  for (const g of order) {
    const list = groups.get(g);
    if (named.length) {
      const label = g || "Ungrouped";
      html += `<div class="row group${closed.has(label) ? " closed" : ""}" data-group="${esc(label)}">
        <svg class="chev" viewBox="0 0 10 10"><path d="M2 3.5l3 3 3-3"/></svg>${esc(label)}<span class="n">${list.length}</span></div>`;
      if (closed.has(label)) continue;
    }
    for (const a of list) {
      const shot = a.shot ? `<img class="shot" src="${esc(a.shot)}" alt="">` : '<span class="shot"></span>';
      html += `<div class="row${selected.has(a.id) ? " sel" : ""}" data-id="${a.id}">
        <span class="c-check"><input type="checkbox"${selected.has(a.id) ? " checked" : ""} aria-label="Select ${esc(a.display)}"></span>
        <span class="c-acc" title="${esc(a.display)} (@${esc(a.name)})">${shot}<span class="names"><b>${esc(a.display)}</b><small>@${esc(a.name)}${a.main ? " · main" : ""}</small></span></span>
        <span class="c-status ${a.kind}" title="${esc(a.status)}">${esc(a.status)}</span>
        <span class="c-game${a.game ? "" : " dim"}" title="${esc(a.game)}">${esc(a.game || "—")}</span>
        <span class="c-perf" title="${esc(a.perf)}">${esc(a.perf || "")}</span>
        <span class="c-up" title="${esc(a.note)}">${esc(a.note || "")}</span></div>`;
    }
  }
  $("rows").innerHTML = html;
  $("empty").hidden = state.accounts.length > 0;
  $("check-all").checked = state.accounts.length > 0 && state.accounts.every((a) => selected.has(a.id));
  const n = selected.size;
  $("join").textContent = state.joining ? "Joining... (stop)" : n > 1 ? `Join ${n} selected` : "Join";
  $("acc-count").textContent = state.accounts.length || "";
}

function drawStatus() {
  const s = state;
  const parts = [`<span>${s.ingame || 0} of ${s.open || 0} windows in a game</span>`];
  if (s.roblox) parts.push(`<span>Roblox ${esc(s.roblox)}</span>`);
  parts.push(s.afk ? `<span class="ok">Anti-AFK on${s.afkNext ? `, next ping ${esc(s.afkNext)}` : ""}</span>` : '<span class="warn">Anti-AFK off</span>');
  if (s.note) parts.push(`<span>${esc(s.note)}</span>`);
  parts.push(`<span class="right">${selected.size ? `${selected.size} selected` : ""}</span>`);
  $("status").innerHTML = parts.join("");
}

function draw() {
  drawNav();
  drawRows();
  drawStatus();
  $("mode").value = state.mode || "normal";
  if (document.activeElement !== $("place") && state.place !== undefined) $("place").value = state.place;
}

function showPage(name) {
  if (!document.getElementById("page-" + name)) return;
  page = name;
  for (const sec of document.querySelectorAll(".page")) sec.hidden = sec.id !== "page-" + name;
  send({ type: "page", page: name });
  drawNav();
  if (name === "accounts") draw(); else drawPage();
}

function menu(x, y, items) {
  closeMenu();
  const m = document.createElement("div");
  m.className = "menu";
  m.innerHTML = items.map((it) => it === "-" ? "<hr>" : `<button data-item="${it[0]}"${it[2] ? ' class="danger"' : ""}>${esc(it[1])}</button>`).join("");
  document.body.append(m);
  m.style.left = Math.min(x, innerWidth - m.offsetWidth - 8) + "px";
  m.style.top = Math.min(y, innerHeight - m.offsetHeight - 8) + "px";
  m.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (b) { send({ type: "menu", item: b.dataset.item, ids: [...selected] }); closeMenu(); }
  });
}
function closeMenu() { document.querySelectorAll(".menu").forEach((m) => m.remove()); }

document.addEventListener("click", (e) => {
  if (!e.target.closest(".menu")) closeMenu();
  const nav = e.target.closest("[data-page]");
  if (nav) return showPage(nav.dataset.page);
  const act = e.target.closest("[data-action]");
  if (act) return send({ type: "action", name: act.dataset.action });
  const grp = e.target.closest("[data-group]");
  if (grp) { const g = grp.dataset.group; closed.has(g) ? closed.delete(g) : closed.add(g); return drawRows(); }
  const row = e.target.closest(".row[data-id]");
  if (row) {
    const id = Number(row.dataset.id);
    if (e.target.type === "checkbox" || e.ctrlKey) selected.has(id) ? selected.delete(id) : selected.add(id);
    else { selected = new Set([id]); }
    drawRows(); drawStatus();
  }
});
// a headshot that can't load becomes the plain grey circle, never a broken-image icon
document.addEventListener("error", (e) => {
  if (e.target.tagName === "IMG" && e.target.classList.contains("shot")) e.target.replaceWith(Object.assign(document.createElement("span"), { className: "shot" }));
}, true);
document.addEventListener("dblclick", (e) => {
  const row = e.target.closest(".row[data-id]");
  if (row) { selected = new Set([Number(row.dataset.id)]); join(); }
});
document.addEventListener("contextmenu", (e) => {
  e.preventDefault();
  const row = e.target.closest(".row[data-id]");
  if (!row) return;
  const id = Number(row.dataset.id);
  if (!selected.has(id)) { selected = new Set([id]); drawRows(); drawStatus(); }
  const one = selected.size === 1 ? state.accounts.find((a) => a.id === id) : null;
  menu(e.clientX, e.clientY, [["join", "Join game"], "-",
    ...(one ? [["main", one.main ? "✓ Main account" : "Main account"]] : []),
    ["low", "Lowest graphics"], ["mute", "Mute"], "-", ["login", "Log in again..."], ["remove", "Remove...", true]]);
});
$("check-all").addEventListener("change", (e) => {
  selected = e.target.checked ? new Set(state.accounts.filter(visible).map((a) => a.id)) : new Set();
  drawRows(); drawStatus();
});
for (const id of ["search", "filter"]) $(id).addEventListener("input", drawRows);
$("mode").addEventListener("change", (e) => send({ type: "mode", value: e.target.value }));
function join() { send({ type: "join", ids: [...selected], place: $("place").value, job: $("job").value }); }
$("join").addEventListener("click", join);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeMenu();
  if (e.ctrlKey && e.key.toLowerCase() === "a" && !["INPUT", "SELECT"].includes(document.activeElement.tagName)) {
    e.preventDefault(); selected = new Set(state.accounts.filter(visible).map((a) => a.id)); drawRows(); drawStatus();
  }
  if (e.ctrlKey && e.key.toLowerCase() === "m") { e.preventDefault(); send({ type: "do", name: "minimize" }); }
});

if (host) host.addEventListener("message", (e) => {
  const msg = e.data;
  if (msg.type === "page") {
    pageData[msg.page] = msg.data;
    if (msg.page === page) drawPage();
    return;
  }
  if (msg.type === "show") return showPage(msg.page);
  if (msg.type === "state") {
    state = msg;
    const ids = new Set(state.accounts.map((a) => a.id));
    selected = new Set([...selected].filter((id) => ids.has(id)));
    if (msg.select) selected = new Set(msg.select);
    draw();
  }
});
draw();
showPage("home");
send({ type: "ready" });
