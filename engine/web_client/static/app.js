/**
 * Mortals and Monsters — browser HUD client (vanilla JS, no bundler).
 *
 * WebSocket JSON envelopes (engine/ws_json.py):
 *   inbound  {op:"text", data} | {op:"gmcp", package, payload}
 *   outbound {op:"cmd", data} | {op:"gmcp", package, payload} | {op:"hello"}
 */

(function () {
  "use strict";

  const output = document.getElementById("output");
  const statusEl = document.getElementById("status");
  const form = document.getElementById("input-row");
  const cmd = document.getElementById("cmd");
  const sendBtn = document.getElementById("send");
  const gaugesEl = document.getElementById("gauges");
  const compassEl = document.getElementById("compass");
  const promptEl = document.getElementById("prompt-mirror");
  const atlasCanvas = document.getElementById("atlas");
  const atlasCaption = document.getElementById("atlas-caption");
  const atlasTitle = document.getElementById("atlas-title");
  const ctxMenu = document.getElementById("ctx-menu");
  const commPane = document.getElementById("comm-pane");
  const commLogPublic = document.getElementById("comm-log-public");
  const commLogTells = document.getElementById("comm-log-tells");
  const commTabPublic = document.getElementById("comm-tab-public");
  const commTabTells = document.getElementById("comm-tab-tells");
  const gagCheckbox = document.getElementById("gag-comms");
  const zonePanel = document.getElementById("zone-panel");
  const zoneCanvas = document.getElementById("zone-map");
  const zoneCaption = document.getElementById("zone-caption");
  const zoneTitle = document.getElementById("zone-title");
  const clientBuildEl = document.getElementById("client-build");
  const prefFontSize = document.getElementById("pref-fontsize");
  const prefTheme = document.getElementById("pref-theme");
  const shortcutsBtn = document.getElementById("show-shortcuts");
  const shortcutsDialog = document.getElementById("shortcuts-dialog");
  const shortcutsClose = document.getElementById("shortcuts-close");
  const displayBtn = document.getElementById("show-display-prefs");
  const displayDialog = document.getElementById("display-dialog");
  const displayClose = document.getElementById("display-close");

  const SGR16 = {
    0: null,
    30: "#505050",
    31: "#c04040",
    32: "#60a060",
    33: "#c9a227",
    34: "#6080c0",
    35: "#a060a0",
    36: "#7ec8a8",
    37: "#e8e8e8",
    90: "#5a5a5a",
    91: "#e07070",
    92: "#80c080",
    93: "#e8d080",
    94: "#80a0e0",
    95: "#d080d0",
    96: "#90e0d0",
    97: "#ffffff",
  };

  // Bump this string with each shipped client change so bug reports about
  // "the web client" can be matched to a build (help/ops QoL, not a version
  // negotiation -- GMCP Core.Hello version above is the protocol number).
  const CLIENT_BUILD = "2026-08-25a";

  const MAX_LINES = 8000;
  const MAX_COMM_LINES = 500;
  const HISTORY_MAX = 80;
  const HISTORY_KEY = "riftforge-cmd-history";
  const GAG_KEY = "riftforge-gag-comms";
  const FONTSIZE_KEY = "riftforge-fontsize";
  const THEME_KEY = "riftforge-theme";
  const PANE_COLLAPSE_KEY = "riftforge-pane-collapsed";
  const ZOOM_KEY = "riftforge-map-zoom";
  const PASTE_LINES_CONFIRM = 3;
  const ZOOM_MIN = 100;
  const ZOOM_MAX = 200;
  const ZOOM_STEP = 25;
  const GMCP_SUPPORTS = [
    "Char 1",
    "Char.Name 1",
    "Char.Status 1",
    "Char.Vitals 1",
    "Room 1",
    "Room.Info 1",
    "Comm 1",
    "Comm.Channel 1",
    "RiftForge.Map 1",
    "RiftForge.Map.Atlas 1",
    "RiftForge.Zone 1",
    "RiftForge.Combat 1",
  ];

  let socket = null;
  let passwordMode = false;
  let rawTail = "";
  let history = [];
  let historyIdx = -1;
  let reconnectTimer = null;
  let reconnectDelay = 1000;
  let userClosed = false;
  let atlasState = null;
  let atlasCountry = null;
  let hoverCell = null;
  let zoneState = null;
  let zoneBounds = null;
  let zoneHover = null;
  let gagStored = false;
  let commTab = "public";
  let paneCollapsed = {};
  let mapZoom = { atlas: 100, zone: 100 };

  const ATLAS_GLYPH_BG = {
    "~": "#1a2848",
    o: "#1a3a3a",
    ".": "#243018",
    T: "#1e3a1e",
    "^": "#3a3a3a",
    n: "#2a3a22",
    ",": "#5a4a18",
    "'": "#1a3a3a",
    "*": "#4a3a12",
    "=": "#3a3a3a",
    "@": "#c9a227",
    L: "#3a2a10",
    " ": "#080a0e",
  };

  // Interior squares: gothic fills by area_type (caption carries the name).
  const ZONE_AREA_FILL = {
    city: "#5a1a28",
    city_street: "#3a3a3a",
    plains: "#1e3a1e",
    forest: "#1e3a1e",
    mountains: "#3a3a3a",
    hills: "#2a3a22",
    ocean: "#1a2848",
    lake: "#1a3a3a",
    desert: "#5a4a18",
    swamp: "#1a3a2a",
    wetland: "#1a3a3a",
    highway: "#5a4a18",
    road: "#4a3a12",
    ruins: "#404040",
    shop: "#3a2a10",
    indoor: "#1a1a28",
  };

  function commPaneVisible() {
    if (!commPane || commPane.hidden) {
      return false;
    }
    const style = window.getComputedStyle(commPane);
    return style.display !== "none" && style.visibility !== "hidden";
  }

  function readStoredGag() {
    try {
      const raw = localStorage.getItem(GAG_KEY);
      if (raw === "1") {
        gagStored = true;
        return true;
      }
      if (raw === "0") {
        gagStored = true;
        return false;
      }
    } catch (err) {
      /* private mode / quota */
    }
    gagStored = false;
    return null;
  }

  function writeStoredGag(on) {
    try {
      localStorage.setItem(GAG_KEY, on ? "1" : "0");
      gagStored = true;
    } catch (err) {
      /* private mode / quota */
    }
  }

  function readStoredJson(key, fallback) {
    try {
      const raw = localStorage.getItem(key);
      if (!raw) {
        return fallback;
      }
      const parsed = JSON.parse(raw);
      return parsed && typeof parsed === "object" ? parsed : fallback;
    } catch (err) {
      return fallback;
    }
  }

  function writeStoredJson(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch (err) {
      /* private mode / quota */
    }
  }

  function readStoredString(key, fallback) {
    try {
      const raw = localStorage.getItem(key);
      return raw || fallback;
    } catch (err) {
      return fallback;
    }
  }

  function writeStoredString(key, value) {
    try {
      localStorage.setItem(key, value);
    } catch (err) {
      /* private mode / quota */
    }
  }

  // --- Collapsible panes ---------------------------------------------
  // Each pane's header (tabs / gag checkbox / zoom / title) stays visible
  // when collapsed -- only the body hides, so the control that re-expands
  // it is never lost along with the content.
  const PANES = [
    { id: "comm", panel: commPane, toggle: null },
    { id: "atlas", panel: document.getElementById("atlas-panel"), toggle: null },
    { id: "zone", panel: document.getElementById("zone-panel"), toggle: null },
    { id: "status", panel: document.getElementById("status-panel"), toggle: null },
  ];
  PANES.forEach(function (p) {
    p.toggle = document.getElementById(p.id + "-toggle");
  });

  function applyPaneCollapse(id) {
    const pane = PANES.filter(function (p) { return p.id === id; })[0];
    if (!pane || !pane.panel) {
      return;
    }
    const collapsed = !!paneCollapsed[id];
    pane.panel.setAttribute("data-collapsed", collapsed ? "true" : "false");
    if (pane.toggle) {
      pane.toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
    }
  }

  function togglePane(id) {
    paneCollapsed[id] = !paneCollapsed[id];
    writeStoredJson(PANE_COLLAPSE_KEY, paneCollapsed);
    applyPaneCollapse(id);
    if (id === "atlas") {
      drawAtlas();
    } else if (id === "zone") {
      drawZone();
    }
  }

  paneCollapsed = readStoredJson(PANE_COLLAPSE_KEY, {});
  PANES.forEach(function (p) {
    applyPaneCollapse(p.id);
    if (p.toggle) {
      p.toggle.addEventListener("click", function () {
        togglePane(p.id);
      });
    }
  });

  // --- Map zoom (atlas / interior) ------------------------------------
  // Zoom widens the canvas itself, not its wrapper; #atlas-scroll /
  // #zone-scroll keep overflow:auto at their normal width so the extra
  // width scrolls inside that one small pane instead of stretching the
  // rest of the HUD column.
  function applyZoom(which) {
    const pct = mapZoom[which] || 100;
    const canvas = which === "atlas" ? atlasCanvas : zoneCanvas;
    if (canvas) {
      canvas.style.width = pct + "%";
    }
    if (which === "atlas") {
      drawAtlas();
    } else {
      drawZone();
    }
  }

  function stepZoom(which, delta) {
    const next = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, (mapZoom[which] || 100) + delta));
    mapZoom[which] = next;
    writeStoredJson(ZOOM_KEY, mapZoom);
    applyZoom(which);
  }

  mapZoom = readStoredJson(ZOOM_KEY, { atlas: 100, zone: 100 });
  applyZoom("atlas");
  applyZoom("zone");

  const atlasZoomOut = document.getElementById("atlas-zoom-out");
  const atlasZoomIn = document.getElementById("atlas-zoom-in");
  const zoneZoomOut = document.getElementById("zone-zoom-out");
  const zoneZoomIn = document.getElementById("zone-zoom-in");
  if (atlasZoomOut) {
    atlasZoomOut.addEventListener("click", function () { stepZoom("atlas", -ZOOM_STEP); });
  }
  if (atlasZoomIn) {
    atlasZoomIn.addEventListener("click", function () { stepZoom("atlas", ZOOM_STEP); });
  }
  if (zoneZoomOut) {
    zoneZoomOut.addEventListener("click", function () { stepZoom("zone", -ZOOM_STEP); });
  }
  if (zoneZoomIn) {
    zoneZoomIn.addEventListener("click", function () { stepZoom("zone", ZOOM_STEP); });
  }

  // --- Text size + theme prefs ----------------------------------------
  function applyFontSize(name) {
    document.body.setAttribute("data-fontsize", name);
  }

  function applyTheme(name) {
    document.body.classList.toggle("theme-contrast", name === "contrast");
  }

  const storedFontSize = readStoredString(FONTSIZE_KEY, "normal");
  applyFontSize(storedFontSize);
  if (prefFontSize) {
    prefFontSize.value = storedFontSize;
    prefFontSize.addEventListener("change", function () {
      writeStoredString(FONTSIZE_KEY, prefFontSize.value);
      applyFontSize(prefFontSize.value);
    });
  }

  const storedTheme = readStoredString(THEME_KEY, "gothic");
  applyTheme(storedTheme);
  if (prefTheme) {
    prefTheme.value = storedTheme;
    prefTheme.addEventListener("change", function () {
      writeStoredString(THEME_KEY, prefTheme.value);
      applyTheme(prefTheme.value);
    });
  }

  // --- Shortcuts dialog -------------------------------------------------
  function openShortcuts() {
    if (!shortcutsDialog) {
      return;
    }
    shortcutsDialog.hidden = false;
    if (shortcutsClose) {
      shortcutsClose.focus();
    }
  }

  function closeShortcuts() {
    if (!shortcutsDialog) {
      return;
    }
    shortcutsDialog.hidden = true;
    cmd.focus();
  }

  if (shortcutsBtn) {
    shortcutsBtn.addEventListener("click", openShortcuts);
  }
  if (shortcutsClose) {
    shortcutsClose.addEventListener("click", closeShortcuts);
  }
  if (shortcutsDialog) {
    shortcutsDialog.addEventListener("click", function (ev) {
      if (ev.target === shortcutsDialog) {
        closeShortcuts();
      }
    });
  }

  // --- Display preferences dialog ---------------------------------------
  // Text size / theme moved out of a permanent header row (it forced a
  // whole extra line on every load) and into this on-demand popover.
  function openDisplayPrefs() {
    if (!displayDialog) {
      return;
    }
    displayDialog.hidden = false;
    if (prefFontSize) {
      prefFontSize.focus();
    }
  }

  function closeDisplayPrefs() {
    if (!displayDialog) {
      return;
    }
    displayDialog.hidden = true;
    cmd.focus();
  }

  if (displayBtn) {
    displayBtn.addEventListener("click", openDisplayPrefs);
  }
  if (displayClose) {
    displayClose.addEventListener("click", closeDisplayPrefs);
  }
  if (displayDialog) {
    displayDialog.addEventListener("click", function (ev) {
      if (ev.target === displayDialog) {
        closeDisplayPrefs();
      }
    });
  }

  if (clientBuildEl) {
    clientBuildEl.textContent = "build " + CLIENT_BUILD;
  }

  function syncGagToServer() {
    let on = !!(gagCheckbox && gagCheckbox.checked);
    if (!commPaneVisible()) {
      on = false;
    }
    sendGmcp("RiftForge.Client", { gag_comms: on });
  }

  function setCommTab(name) {
    commTab = name === "tells" ? "tells" : "public";
    const publicOn = commTab === "public";
    if (commTabPublic) {
      commTabPublic.setAttribute("aria-selected", publicOn ? "true" : "false");
    }
    if (commTabTells) {
      commTabTells.setAttribute("aria-selected", publicOn ? "false" : "true");
    }
    if (commLogPublic) {
      commLogPublic.hidden = !publicOn;
      commLogPublic.setAttribute("aria-live", publicOn ? "polite" : "off");
    }
    if (commLogTells) {
      commLogTells.hidden = publicOn;
      commLogTells.setAttribute("aria-live", publicOn ? "off" : "polite");
    }
  }

  function trimCommLog(el) {
    if (!el) {
      return;
    }
    while (el.childNodes.length > MAX_COMM_LINES) {
      el.removeChild(el.firstChild);
    }
  }

  function appendCommLine(el, text) {
    if (!el || !text) {
      return;
    }
    const line = document.createElement("span");
    line.className = "comm-line";
    line.textContent = text;
    el.appendChild(line);
    trimCommLog(el);
    el.scrollTop = el.scrollHeight;
  }

  function isCapturedPublic(chan) {
    const c = String(chan || "").toLowerCase();
    if (!c || c === "tell" || c === "say" || c === "emote") {
      return false;
    }
    return true;
  }

  function applyComm(payload) {
    if (!payload || typeof payload !== "object") {
      return;
    }
    const chan = String(payload.chan || "");
    const player = String(payload.player || "");
    const msg = String(payload.msg || "");
    if (chan.toLowerCase() === "tell") {
      appendCommLine(commLogTells, player + ": " + msg);
      return;
    }
    if (!isCapturedPublic(chan)) {
      return;
    }
    appendCommLine(commLogPublic, "[" + chan + "] " + player + ": " + msg);
  }

  function wsUrl() {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    return proto + "//" + window.location.host + "/";
  }

  function setStatus(text, kind) {
    statusEl.textContent = text;
    statusEl.className = kind || "";
  }

  function setInputEnabled(on) {
    cmd.disabled = !on;
    sendBtn.disabled = !on;
    if (on) {
      cmd.focus();
    }
  }

  function xterm256ToCss(n) {
    n = Number(n);
    if (Number.isNaN(n) || n < 0 || n > 255) {
      return "#e8e8e8";
    }
    if (n < 16) {
      const map = [
        "#0a0a0a", "#8b2942", "#3d6b3d", "#8b7a2a", "#3d4a6b", "#6b3d6b",
        "#2a6b6b", "#b8b8b8", "#505050", "#c04040", "#60a060", "#c9a227",
        "#6080c0", "#a060a0", "#7ec8a8", "#e8e8e8",
      ];
      return map[n] || "#e8e8e8";
    }
    if (n < 232) {
      n -= 16;
      const r = Math.floor(n / 36);
      const g = Math.floor((n % 36) / 6);
      const b = n % 6;
      const step = [0, 95, 135, 175, 215, 255];
      return "rgb(" + step[r] + "," + step[g] + "," + step[b] + ")";
    }
    const gray = 8 + (n - 232) * 10;
    return "rgb(" + gray + "," + gray + "," + gray + ")";
  }

  function escapeHtml(s) {
    return s
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function ansiToHtml(text) {
    const parts = [];
    let i = 0;
    let color = "#e8e8e8";
    let open = false;

    function closeSpan() {
      if (open) {
        parts.push("</span>");
        open = false;
      }
    }

    function openSpan(c) {
      closeSpan();
      const safe = String(c).replace(/"/g, "");
      parts.push('<span style="color:' + safe + '">');
      open = true;
    }

    while (i < text.length) {
      if (text.charCodeAt(i) === 27 && text[i + 1] === "[") {
        const end = text.indexOf("m", i);
        if (end === -1) {
          break;
        }
        const seq = text.slice(i + 2, end);
        i = end + 1;
        const codes = seq.split(";").filter(Boolean);
        if (!codes.length) {
          color = "#e8e8e8";
          closeSpan();
          continue;
        }
        for (let c = 0; c < codes.length; c++) {
          const code = parseInt(codes[c], 10);
          if (code === 0) {
            color = "#e8e8e8";
            closeSpan();
          } else if (code === 38 && codes[c + 1] === "5") {
            color = xterm256ToCss(codes[c + 2]);
            c += 2;
            openSpan(color);
          } else if (SGR16[code]) {
            color = SGR16[code];
            openSpan(color);
          }
        }
        continue;
      }
      const next = text.indexOf("\x1b", i);
      const chunk = next === -1 ? text.slice(i) : text.slice(i, next);
      if (chunk) {
        if (color && color !== "#e8e8e8") {
          if (!open) {
            openSpan(color);
          }
          parts.push(escapeHtml(chunk));
        } else {
          closeSpan();
          parts.push(escapeHtml(chunk));
        }
      }
      i = next === -1 ? text.length : next;
    }
    closeSpan();
    return parts.join("");
  }

  function trimOutput() {
    while (output.childNodes.length > MAX_LINES) {
      output.removeChild(output.firstChild);
    }
  }

  function appendOutput(text) {
    if (!text) {
      return;
    }
    rawTail = (rawTail + text).slice(-4000);
    const line = document.createElement("span");
    line.innerHTML = ansiToHtml(text);
    output.appendChild(line);
    trimOutput();
    output.scrollTop = output.scrollHeight;

    const lower = rawTail.toLowerCase();
    if (
      lower.includes("password:") ||
      lower.includes("password for") ||
      lower.includes("new password:")
    ) {
      passwordMode = true;
      cmd.type = "password";
    } else if (passwordMode && text.indexOf("\n") !== -1) {
      if (!lower.endsWith("password:") && !lower.endsWith("password for")) {
        passwordMode = false;
        cmd.type = "text";
      }
    }
    if (text.indexOf("[WAIT]") !== -1) {
      setStatus("World restarting — stay connected", "wait");
    }
  }

  function sendEnvelope(obj) {
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      return;
    }
    socket.send(JSON.stringify(obj));
  }

  function sendCmd(line) {
    sendEnvelope({ op: "cmd", data: line });
  }

  function sendGmcp(packageName, payload) {
    sendEnvelope({ op: "gmcp", package: packageName, payload: payload });
  }

  function handshakeHud() {
    sendEnvelope({ op: "hello", client: "riftforge-web", version: "2" });
    sendGmcp("Core.Hello", { client: "riftforge-web", version: "2" });
    sendGmcp("Core.Supports.Set", GMCP_SUPPORTS);
    syncGagToServer();
  }

  function pct(value) {
    const n = parseFloat(value);
    if (Number.isNaN(n)) {
      return 0;
    }
    return Math.max(0, Math.min(100, n));
  }

  function renderGauge(id, label, value, klass) {
    const n = pct(value);
    return (
      '<div class="gauge" id="g-' +
      id +
      '">' +
      '<div class="gauge-label"><span>' +
      escapeHtml(label) +
      "</span><strong>" +
      escapeHtml(String(Math.round(n))) +
      "%</strong></div>" +
      '<div class="gauge-track" role="meter" aria-valuemin="0" aria-valuemax="100" aria-valuenow="' +
      Math.round(n) +
      '" aria-label="' +
      escapeHtml(label) +
      '">' +
      '<div class="gauge-fill ' +
      klass +
      '" style="width:' +
      n +
      '%"></div></div></div>'
    );
  }

  function applyVitals(v) {
    if (!v || typeof v !== "object") {
      return;
    }
    const bits = [];
    bits.push(renderGauge("hp", "Lifeforce", v.hp, "hp"));
    bits.push(renderGauge("st", "Stamina", v.stamina, "stamina"));
    bits.push(renderGauge("en", "Focus", v.energy, "energy"));
    if (v.mana != null) {
      bits.push(renderGauge("mn", "Mana", v.mana, "mana"));
    }
    if (v.fuel != null) {
      const fl = v.fuel_label ? String(v.fuel_label) : "Fuel";
      bits.push(renderGauge("fu", fl.charAt(0).toUpperCase() + fl.slice(1), v.fuel, "fuel"));
    }
    const mom = parseFloat(v.momentum);
    if (!Number.isNaN(mom) && mom !== 0) {
      const shown = ((mom + 100) / 2);
      bits.push(renderGauge("mo", "Momentum", shown, "momentum"));
    }
    if (v.cash) {
      bits.push(
        '<div class="gauge-label"><span>Cash</span><strong>' +
          escapeHtml(String(v.cash)) +
          "</strong></div>"
      );
    }
    gaugesEl.innerHTML = bits.join("");
    const mirror = [];
    if (v.hp != null) {
      mirror.push("H " + Math.round(pct(v.hp)) + "%");
    }
    if (v.stamina != null) {
      mirror.push("S " + Math.round(pct(v.stamina)) + "%");
    }
    if (!Number.isNaN(mom) && mom !== 0) {
      mirror.push("Mo " + mom);
    }
    promptEl.textContent = mirror.join("  ");
  }

  function applyRoom(info) {
    if (!info || typeof info !== "object") {
      return;
    }
    const exits = info.exits || {};
    const keys = Object.keys(exits);
    compassEl.innerHTML = "";
    if (!keys.length) {
      compassEl.textContent = "";
      return;
    }
    const order = [
      "northwest", "north", "northeast",
      "west", "east",
      "southwest", "south", "southeast",
      "up", "down", "in", "out",
    ];
    const abbr = {
      north: "N", south: "S", east: "E", west: "W",
      northeast: "NE", northwest: "NW", southeast: "SE", southwest: "SW",
      up: "U", down: "D", in: "in", out: "out",
    };
    const seen = {};
    function addBtn(dir) {
      const d = String(dir);
      if (seen[d]) {
        return;
      }
      seen[d] = true;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = abbr[d] || d;
      btn.title = d;
      btn.addEventListener("click", function () {
        sendCmd(d);
      });
      compassEl.appendChild(btn);
    }
    order.forEach(function (dir) {
      if (exits[dir] !== undefined) {
        addBtn(dir);
      }
    });
    keys.forEach(function (dir) {
      if (!seen[dir]) {
        addBtn(dir);
      }
    });
  }

  function hideMenu() {
    ctxMenu.hidden = true;
    ctxMenu.innerHTML = "";
  }

  function showMenu(x, y, verbs) {
    ctxMenu.innerHTML = "";
    if (!verbs || !verbs.length) {
      hideMenu();
      return;
    }
    verbs.forEach(function (row) {
      const li = document.createElement("li");
      li.setAttribute("role", "none");
      const btn = document.createElement("button");
      btn.type = "button";
      btn.setAttribute("role", "menuitem");
      btn.textContent = row.label;
      btn.addEventListener("click", function () {
        sendCmd(row.command);
        hideMenu();
        cmd.focus();
      });
      li.appendChild(btn);
      ctxMenu.appendChild(li);
    });
    ctxMenu.hidden = false;
    ctxMenu.style.left = x + "px";
    ctxMenu.style.top = y + "px";
    const first = ctxMenu.querySelector("button");
    if (first) {
      first.focus();
    }
  }

  function countrySize() {
    if (atlasCountry && atlasCountry.w && atlasCountry.h) {
      return { w: atlasCountry.w, h: atlasCountry.h };
    }
    if (atlasState && atlasState.w && atlasState.h) {
      return { w: atlasState.w, h: atlasState.h };
    }
    return null;
  }

  function cellAtEvent(ev) {
    const size = countrySize();
    if (!size || !atlasCountry || !atlasCountry.rows) {
      return null;
    }
    const rect = atlasCanvas.getBoundingClientRect();
    const w = size.w;
    const h = size.h;
    const mx = ((ev.clientX - rect.left) / rect.width) * w;
    const my = ((ev.clientY - rect.top) / rect.height) * h;
    const x = Math.floor(mx);
    // Atlas rows are north-first; canvas y is top→bottom. Flip to game y.
    const y = h - 1 - Math.floor(my);
    if (x < 0 || y < 0 || x >= w || y >= h) {
      return null;
    }
    return { x: x, y: y };
  }

  function markAt(x, y) {
    const marks = (atlasState && atlasState.marks) || [];
    for (let i = 0; i < marks.length; i++) {
      if (marks[i].x === x && marks[i].y === y) {
        return marks[i];
      }
    }
    return null;
  }

  function verbsForCell(x, y) {
    const mark = markAt(x, y);
    return (mark && mark.v) || [];
  }

  function setCaptionDefault() {
    if (!atlasCaption) {
      return;
    }
    const st = atlasState;
    if (st && st.mark && st.you && st.you.length === 2) {
      atlasCaption.textContent =
        "You are at (" + st.you[0] + ", " + st.you[1] + "). Right-click a landmark.";
    } else {
      atlasCaption.textContent = "Right-click a landmark to walk, drive, or enter.";
    }
  }

  function drawAtlas() {
    if (!atlasCanvas) {
      return;
    }
    const rows = atlasCountry && atlasCountry.rows;
    const w = atlasCountry && atlasCountry.w;
    const h = atlasCountry && atlasCountry.h;
    if (!rows || !rows.length || !w || !h) {
      return;
    }
    const st = atlasState || {};
    const dpr = window.devicePixelRatio || 1;
    const cssW = atlasCanvas.clientWidth || 260;
    const cssH = Math.max(72, Math.round(cssW * (h / w)));
    atlasCanvas.style.height = cssH + "px";
    atlasCanvas.width = Math.round(cssW * dpr);
    atlasCanvas.height = Math.round(cssH * dpr);
    const ctx = atlasCanvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const cw = cssW / w;
    const ch = cssH / h;
    function canvasY(gameY) {
      return h - 1 - gameY;
    }
    for (let r = 0; r < h; r++) {
      const row = rows[r] || "";
      for (let x = 0; x < w; x++) {
        const chGlyph = row.charAt(x) || " ";
        ctx.fillStyle = ATLAS_GLYPH_BG[chGlyph] || "#1a2848";
        ctx.fillRect(x * cw, r * ch, cw + 0.5, ch + 0.5);
      }
    }
    (st.marks || []).forEach(function (m) {
      ctx.fillStyle = "rgba(201,162,39,0.55)";
      ctx.fillRect(m.x * cw, canvasY(m.y) * ch, cw, ch);
    });
    if (hoverCell) {
      ctx.strokeStyle = "#e8e8e8";
      ctx.lineWidth = 1.5;
      const hy = canvasY(hoverCell.y);
      ctx.strokeRect(
        hoverCell.x * cw + 0.5,
        hy * ch + 0.5,
        Math.max(1, cw - 1),
        Math.max(1, ch - 1)
      );
    }
    if (st.mark && st.you && st.you.length === 2) {
      const yx = st.you[0];
      const yy = canvasY(st.you[1]);
      ctx.fillStyle = "#e8e8e8";
      ctx.fillRect(yx * cw, yy * ch, cw, ch);
      ctx.fillStyle = "#c9a227";
      ctx.fillRect(yx * cw + cw * 0.2, yy * ch + ch * 0.2, cw * 0.6, ch * 0.6);
    }
    if (!hoverCell) {
      setCaptionDefault();
    }
    if (atlasCountry.title) {
      atlasTitle.textContent = atlasCountry.title;
    } else if (st.title) {
      atlasTitle.textContent = st.title;
    } else if (st.map === "earth_america") {
      atlasTitle.textContent = "America";
    }
  }

  function applyCountryAtlas(payload) {
    if (!payload || typeof payload !== "object") {
      return;
    }
    const rows = payload.rows;
    const w = Number(payload.width || payload.w || 0);
    const h = Number(payload.height || payload.h || 0);
    if (!Array.isArray(rows) || !rows.length || w < 1 || h < 1) {
      return;
    }
    let title = "America";
    const prefix = String(payload.prefix || payload.title || "");
    const mapId = String(payload.id || payload.map || "");
    if (prefix && prefix.toLowerCase().indexOf("america") === -1) {
      title = prefix.replace(/\s+Overland$/i, "") || title;
    } else if (mapId && mapId !== "earth_america") {
      title = prefix || mapId;
    }
    atlasCountry = {
      w: w,
      h: h,
      rows: rows,
      id: mapId,
      title: title,
    };
    drawAtlas();
  }

  function applyMap(payload) {
    if (!payload || typeof payload !== "object") {
      return;
    }
    if (!atlasState) {
      atlasState = {};
    }
    if (payload.you !== undefined) {
      atlasState.you = payload.you;
    }
    if (payload.mark !== undefined) {
      atlasState.mark = payload.mark;
    }
    if (payload.marks) {
      atlasState.marks = payload.marks;
    }
    if (payload.title) {
      atlasState.title = payload.title;
    }
    if (payload.map) {
      atlasState.map = payload.map;
    }
    drawAtlas();
  }

  function zoneRoomAt(x, y) {
    const rooms = (zoneState && zoneState.rooms) || [];
    let found = null;
    for (let i = 0; i < rooms.length; i++) {
      if (rooms[i].x === x && rooms[i].y === y) {
        found = rooms[i];
        if (
          zoneState.you &&
          zoneState.you[0] === x &&
          zoneState.you[1] === y
        ) {
          return found;
        }
      }
    }
    return found;
  }

  function zoneCaptionFor(room) {
    if (!room) {
      return "Streets plus places you have been. Click a room to name it.";
    }
    const z = zoneState && zoneState.z != null ? zoneState.z : 0;
    let text = room.n + " (floor " + z + ")";
    if (room.m) {
      text += " — exit";
    }
    const extra = [];
    const ex = room.ex || [];
    for (let i = 0; i < ex.length; i++) {
      const d = ex[i];
      if (d === "u" || d === "d" || d === "in" || d === "out") {
        extra.push(d);
      }
    }
    if (extra.length) {
      text += " [" + extra.join(", ") + "]";
    }
    if (
      zoneState &&
      zoneState.you &&
      zoneState.you[0] === room.x &&
      zoneState.you[1] === room.y
    ) {
      text = "You are here: " + text;
    }
    return text;
  }

  function setZoneCaptionDefault() {
    if (!zoneCaption) {
      return;
    }
    if (zoneState && zoneState.you && zoneState.you.length === 2) {
      const here = zoneRoomAt(zoneState.you[0], zoneState.you[1]);
      zoneCaption.textContent = here
        ? zoneCaptionFor(here)
        : "You are here. Click a room to name it. E marks a way out.";
      return;
    }
    zoneCaption.textContent =
      "Streets plus places you have been. Click a room to name it.";
  }

  function applyZone(payload) {
    if (!zonePanel) {
      return;
    }
    if (!payload || typeof payload !== "object") {
      zonePanel.hidden = true;
      zoneState = null;
      return;
    }
    const rooms = payload.rooms;
    const hide =
      payload.hidden === true || !Array.isArray(rooms) || !rooms.length;
    zonePanel.hidden = hide;
    if (hide) {
      zoneState = null;
      zoneBounds = null;
      return;
    }
    zoneState = payload;
    if (zoneTitle && payload.title) {
      zoneTitle.textContent = payload.title;
    }
    drawZone();
  }

  function drawZone() {
    if (!zoneCanvas || !zoneState || !Array.isArray(zoneState.rooms)) {
      return;
    }
    const rooms = zoneState.rooms;
    if (!rooms.length) {
      return;
    }
    let minX = rooms[0].x;
    let maxX = rooms[0].x;
    let minY = rooms[0].y;
    let maxY = rooms[0].y;
    for (let i = 0; i < rooms.length; i++) {
      if (rooms[i].x < minX) minX = rooms[i].x;
      if (rooms[i].x > maxX) maxX = rooms[i].x;
      if (rooms[i].y < minY) minY = rooms[i].y;
      if (rooms[i].y > maxY) maxY = rooms[i].y;
    }
    minX -= 1;
    maxX += 1;
    minY -= 1;
    maxY += 1;
    const cellsW = maxX - minX + 1;
    const cellsH = maxY - minY + 1;
    zoneBounds = { minX: minX, maxX: maxX, minY: minY, maxY: maxY };
    const dpr = window.devicePixelRatio || 1;
    const cssW = zoneCanvas.clientWidth || 260;
    const cssH = Math.min(
      220,
      Math.max(72, Math.round(cssW * (cellsH / cellsW)))
    );
    zoneCanvas.style.height = cssH + "px";
    zoneCanvas.width = Math.round(cssW * dpr);
    zoneCanvas.height = Math.round(cssH * dpr);
    const ctx = zoneCanvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const cw = cssW / cellsW;
    const ch = cssH / cellsH;
    function canvasXY(x, y) {
      return {
        x: (x - minX) * cw,
        y: (maxY - y) * ch,
      };
    }
    ctx.fillStyle = "#080a0e";
    ctx.fillRect(0, 0, cssW, cssH);
    const byXY = {};
    for (let i = 0; i < rooms.length; i++) {
      byXY[rooms[i].x + "," + rooms[i].y] = rooms[i];
    }
    ctx.strokeStyle = "#3a3a3a";
    ctx.lineWidth = 1;
    for (let i = 0; i < rooms.length; i++) {
      const room = rooms[i];
      const pos = canvasXY(room.x, room.y);
      const cx = pos.x + cw / 2;
      const cy = pos.y + ch / 2;
      const ex = room.ex || [];
      function lineTo(nx, ny) {
        if (!byXY[nx + "," + ny]) {
          return;
        }
        const npos = canvasXY(nx, ny);
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.lineTo(npos.x + cw / 2, npos.y + ch / 2);
        ctx.stroke();
      }
      for (let e = 0; e < ex.length; e++) {
        if (ex[e] === "n") lineTo(room.x, room.y + 1);
        if (ex[e] === "s") lineTo(room.x, room.y - 1);
        if (ex[e] === "e") lineTo(room.x + 1, room.y);
        if (ex[e] === "w") lineTo(room.x - 1, room.y);
      }
    }
    const you = zoneState.you;
    for (let i = 0; i < rooms.length; i++) {
      const room = rooms[i];
      const pos = canvasXY(room.x, room.y);
      const fill = ZONE_AREA_FILL[room.e] || ZONE_AREA_FILL.indoor;
      ctx.fillStyle = fill;
      ctx.fillRect(pos.x + 1, pos.y + 1, Math.max(1, cw - 2), Math.max(1, ch - 2));
      if (room.m) {
        ctx.fillStyle = "#e8e8e8";
        ctx.font = Math.max(8, Math.floor(Math.min(cw, ch) * 0.55)) + "px monospace";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText("E", pos.x + cw / 2, pos.y + ch / 2);
      }
    }
    if (you && you.length === 2) {
      const pos = canvasXY(you[0], you[1]);
      ctx.strokeStyle = "#e8e8e8";
      ctx.lineWidth = 2;
      ctx.strokeRect(pos.x + 1, pos.y + 1, Math.max(1, cw - 2), Math.max(1, ch - 2));
      ctx.fillStyle = "#c9a227";
      ctx.fillRect(
        pos.x + cw * 0.3,
        pos.y + ch * 0.3,
        cw * 0.4,
        ch * 0.4
      );
    }
    if (zoneHover) {
      const pos = canvasXY(zoneHover.x, zoneHover.y);
      ctx.strokeStyle = "#7ec8a8";
      ctx.lineWidth = 1.5;
      ctx.strokeRect(pos.x + 0.5, pos.y + 0.5, Math.max(1, cw - 1), Math.max(1, ch - 1));
    }
    if (!zoneHover) {
      setZoneCaptionDefault();
    }
  }

  function zoneCellAtEvent(ev) {
    if (!zoneBounds || !zoneCanvas) {
      return null;
    }
    const rect = zoneCanvas.getBoundingClientRect();
    const cellsW = zoneBounds.maxX - zoneBounds.minX + 1;
    const cellsH = zoneBounds.maxY - zoneBounds.minY + 1;
    const col = Math.floor(((ev.clientX - rect.left) / rect.width) * cellsW);
    const row = Math.floor(((ev.clientY - rect.top) / rect.height) * cellsH);
    const x = zoneBounds.minX + col;
    const y = zoneBounds.maxY - row;
    if (
      x < zoneBounds.minX ||
      x > zoneBounds.maxX ||
      y < zoneBounds.minY ||
      y > zoneBounds.maxY
    ) {
      return null;
    }
    return { x: x, y: y };
  }

  function applyGmcp(packageName, payload) {
    const name = String(packageName || "");
    if (name === "Char.Vitals") {
      applyVitals(payload);
    } else if (name === "Room.Info") {
      applyRoom(payload);
    } else if (name === "RiftForge.Map.Atlas") {
      applyCountryAtlas(payload);
    } else if (name === "RiftForge.Map") {
      applyMap(payload);
    } else if (name === "RiftForge.Zone") {
      applyZone(payload);
    } else if (name === "Comm.Channel") {
      applyComm(payload);
    } else if (name === "Char.Status") {
      applyStatus(payload);
    }
  }

  function applyStatus(status) {
    if (!status || typeof status !== "object") {
      return;
    }
    const sr = String(status.screenreader || "") === "1";
    document.body.classList.toggle("screenreader", sr);
    if (gagCheckbox && !gagStored) {
      gagCheckbox.checked = sr;
    }
    syncGagToServer();
  }

  function handleFrame(raw) {
    const text = String(raw || "");
    if (!text) {
      return;
    }
    if (text.charAt(0) !== "{") {
      appendOutput(text);
      return;
    }
    let obj;
    try {
      obj = JSON.parse(text);
    } catch (err) {
      appendOutput(text);
      return;
    }
    if (!obj || typeof obj !== "object") {
      appendOutput(text);
      return;
    }
    if (obj.op === "text") {
      appendOutput(obj.data || "");
      return;
    }
    if (obj.op === "gmcp") {
      applyGmcp(obj.package, obj.payload);
      return;
    }
    appendOutput(text);
  }

  function connect() {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    setStatus("Connecting…", "");
    setInputEnabled(false);
    try {
      socket = new WebSocket(wsUrl());
    } catch (err) {
      setStatus("WebSocket failed: " + err.message, "error");
      scheduleReconnect();
      return;
    }
    socket.addEventListener("open", function () {
      setStatus("Connected", "connected");
      setInputEnabled(true);
      reconnectDelay = 1000;
      handshakeHud();
    });
    socket.addEventListener("message", function (ev) {
      handleFrame(ev.data);
    });
    socket.addEventListener("close", function () {
      setStatus("Disconnected — reconnecting…", "error");
      setInputEnabled(false);
      socket = null;
      if (!userClosed) {
        scheduleReconnect();
      }
    });
    socket.addEventListener("error", function () {
      setStatus("Connection error", "error");
    });
  }

  function scheduleReconnect() {
    if (userClosed) {
      return;
    }
    reconnectTimer = setTimeout(connect, reconnectDelay);
    reconnectDelay = Math.min(15000, reconnectDelay * 2);
  }

  function echoOwnInput(line) {
    // Never echo while a password prompt is active -- that would put the
    // typed secret in the transcript even though the field masks it.
    if (passwordMode || !line) {
      return;
    }
    const el = document.createElement("span");
    el.className = "echo-line";
    el.textContent = "> " + line;
    output.appendChild(el);
    trimOutput();
    output.scrollTop = output.scrollHeight;
  }

  function submitLine(line) {
    if (line !== "" && !passwordMode) {
      history.push(line);
      if (history.length > HISTORY_MAX) {
        history.shift();
      }
      historyIdx = history.length;
      try {
        localStorage.setItem(HISTORY_KEY, JSON.stringify(history));
      } catch (err) {
        /* private mode / quota */
      }
    }
    echoOwnInput(line);
    sendCmd(line);
  }

  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    const line = cmd.value;
    submitLine(line);
    cmd.value = "";
  });

  // Pasting a multi-line block (a saved macro, a sequence of directions,
  // an old session log) into the single-line input would otherwise just
  // flatten into one garbled command. Offer to send each line separately
  // instead of silently mangling or silently flooding the server.
  cmd.addEventListener("paste", function (ev) {
    if (passwordMode) {
      return;
    }
    const clip = ev.clipboardData || window.clipboardData;
    if (!clip) {
      return;
    }
    const text = clip.getData("text");
    if (!text || text.indexOf("\n") === -1) {
      return;
    }
    const lines = text.split(/\r\n|\r|\n/).map(function (l) {
      return l.trim();
    }).filter(function (l) {
      return l !== "";
    });
    if (lines.length < 2) {
      return;
    }
    ev.preventDefault();
    if (lines.length > PASTE_LINES_CONFIRM) {
      const ok = window.confirm(
        "Send " + lines.length + " pasted lines as separate commands?"
      );
      if (!ok) {
        return;
      }
    }
    lines.forEach(function (l) {
      submitLine(l);
    });
    cmd.value = "";
  });

  cmd.addEventListener("keydown", function (ev) {
    if (ev.key === "ArrowUp") {
      if (!history.length || passwordMode) {
        return;
      }
      ev.preventDefault();
      historyIdx = Math.max(0, historyIdx - 1);
      cmd.value = history[historyIdx] || "";
    } else if (ev.key === "ArrowDown") {
      if (passwordMode) {
        return;
      }
      ev.preventDefault();
      historyIdx = Math.min(history.length, historyIdx + 1);
      cmd.value = historyIdx >= history.length ? "" : history[historyIdx];
    } else if (ev.key === "Escape") {
      hideMenu();
    }
  });

  atlasCanvas.addEventListener("contextmenu", function (ev) {
    ev.preventDefault();
    const cell = cellAtEvent(ev);
    if (!cell) {
      hideMenu();
      return;
    }
    const verbs = verbsForCell(cell.x, cell.y);
    if (!verbs.length) {
      hideMenu();
      atlasCaption.textContent = "Nothing to travel from (" + cell.x + ", " + cell.y + ").";
      return;
    }
    showMenu(ev.clientX, ev.clientY, verbs);
  });

  atlasCanvas.addEventListener("mousemove", function (ev) {
    const cell = cellAtEvent(ev);
    const same =
      hoverCell &&
      cell &&
      hoverCell.x === cell.x &&
      hoverCell.y === cell.y;
    hoverCell = cell;
    if (!same) {
      drawAtlas();
    }
    if (!cell) {
      setCaptionDefault();
      return;
    }
    const mark = markAt(cell.x, cell.y);
    if (mark && mark.n) {
      atlasCaption.textContent = mark.n + " — right-click for travel.";
    } else {
      atlasCaption.textContent = "(" + cell.x + ", " + cell.y + ")";
    }
  });

  atlasCanvas.addEventListener("mouseleave", function () {
    hoverCell = null;
    drawAtlas();
    setCaptionDefault();
  });

  atlasCanvas.addEventListener("click", hideMenu);
  document.addEventListener("click", function (ev) {
    if (ctxMenu.hidden) {
      return;
    }
    if (!ctxMenu.contains(ev.target) && ev.target !== atlasCanvas) {
      hideMenu();
    }
  });
  ctxMenu.addEventListener("keydown", function (ev) {
    const items = Array.prototype.slice.call(ctxMenu.querySelectorAll("button"));
    if (!items.length) {
      return;
    }
    const idx = items.indexOf(document.activeElement);
    if (ev.key === "ArrowDown") {
      ev.preventDefault();
      items[(idx + 1) % items.length].focus();
    } else if (ev.key === "ArrowUp") {
      ev.preventDefault();
      items[(idx - 1 + items.length) % items.length].focus();
    } else if (ev.key === "Escape") {
      hideMenu();
      cmd.focus();
    }
  });
  window.addEventListener("resize", function () {
    drawAtlas();
    drawZone();
  });

  document.addEventListener("keydown", function (ev) {
    if (ev.key !== "Escape") {
      return;
    }
    if (shortcutsDialog && !shortcutsDialog.hidden) {
      closeShortcuts();
    }
    if (displayDialog && !displayDialog.hidden) {
      closeDisplayPrefs();
    }
  });

  if (commTabPublic) {
    commTabPublic.addEventListener("click", function () {
      setCommTab("public");
    });
  }
  if (commTabTells) {
    commTabTells.addEventListener("click", function () {
      setCommTab("tells");
    });
  }
  if (gagCheckbox) {
    gagCheckbox.addEventListener("change", function () {
      writeStoredGag(gagCheckbox.checked);
      syncGagToServer();
    });
  }

  if (zoneCanvas) {
    zoneCanvas.addEventListener("click", function (ev) {
      const cell = zoneCellAtEvent(ev);
      if (!cell) {
        setZoneCaptionDefault();
        return;
      }
      const room = zoneRoomAt(cell.x, cell.y);
      if (zoneCaption) {
        zoneCaption.textContent = room
          ? zoneCaptionFor(room)
          : "No mapped room there.";
      }
    });
    zoneCanvas.addEventListener("mousemove", function (ev) {
      const cell = zoneCellAtEvent(ev);
      const room = cell ? zoneRoomAt(cell.x, cell.y) : null;
      const next = room ? { x: room.x, y: room.y } : null;
      const same =
        zoneHover &&
        next &&
        zoneHover.x === next.x &&
        zoneHover.y === next.y;
      zoneHover = next;
      if (!same) {
        drawZone();
      }
      if (room) {
        zoneCaption.textContent = zoneCaptionFor(room);
      } else {
        setZoneCaptionDefault();
      }
    });
    zoneCanvas.addEventListener("mouseleave", function () {
      zoneHover = null;
      drawZone();
      setZoneCaptionDefault();
    });
  }

  const helpBrowser = document.getElementById("help-browser");
  if (helpBrowser) {
    helpBrowser.addEventListener("click", function (ev) {
      ev.preventDefault();
      sendCmd("help browser");
      cmd.focus();
    });
  }

  try {
    const saved = JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]");
    if (Array.isArray(saved)) {
      history = saved.filter(function (row) {
        return typeof row === "string";
      }).slice(-HISTORY_MAX);
      historyIdx = history.length;
    }
  } catch (err) {
    history = [];
  }

  const storedGag = readStoredGag();
  if (gagCheckbox) {
    gagCheckbox.checked = storedGag === true;
  }

  connect();
})();
