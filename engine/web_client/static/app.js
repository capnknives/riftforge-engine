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
  const liveOutput = document.getElementById("output-live");
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
  const moreBuildEl = document.getElementById("more-build");
  const prefFontSize = document.getElementById("pref-fontsize");
  const prefTheme = document.getElementById("pref-theme");
  const shortcutsBtn = document.getElementById("show-shortcuts");
  const shortcutsDialog = document.getElementById("shortcuts-dialog");
  const shortcutsClose = document.getElementById("shortcuts-close");
  const displayBtn = document.getElementById("show-display-prefs");
  const displayDialog = document.getElementById("display-dialog");
  const displayClose = document.getElementById("display-close");
  const saveLogBtn = document.getElementById("save-log");
  const vitalsAlertEl = document.getElementById("vitals-alert");
  const playRoot = document.getElementById("play");
  const playMain = document.getElementById("play-main");
  const splitComm = document.getElementById("split-comm");
  const splitHud = document.getElementById("split-hud");
  const hudEl = document.getElementById("hud");
  const roomCard = document.getElementById("room-card");
  const roomTitle = document.getElementById("room-title");
  const roomSub = document.getElementById("room-sub");
  const moreSheet = document.getElementById("more-sheet");
  const phoneNav = document.getElementById("phone-nav");

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
  const CLIENT_BUILD = "2026-09-16-atlas-wsdrain";

  const MAX_LINES = 8000;
  const MAX_COMM_LINES = 500;
  const HISTORY_MAX = 80;
  const HISTORY_KEY = "riftforge-cmd-history";
  const GAG_KEY = "riftforge-gag-comms";
  const FONTSIZE_KEY = "riftforge-fontsize";
  const THEME_KEY = "riftforge-theme";
  const PANE_COLLAPSE_KEY = "riftforge-pane-collapsed";
  const PANE_SIZE_KEY = "riftforge-pane-sizes";
  const ZOOM_KEY = "riftforge-map-zoom";
  const PHONE_TAB_KEY = "riftforge-phone-tab";
  const STORY_DB = "riftforge-play";
  const STORY_STORE = "kv";
  const STORY_KEY = "story-html";
  const STORY_MAX_CHARS = 800000;
  const PASTE_LINES_CONFIRM = 3;
  const LONG_PRESS_MS = 480;
  const PRESS_MOVE_CANCEL_PX = 12;
  const ZOOM_MIN = 100;
  const ZOOM_MAX = 200;
  const ZOOM_STEP = 25;
  const COMM_HEIGHT_DEFAULT_VH = 28;
  const HUD_WIDTH_DEFAULT = 420;
  const COMM_HEIGHT_MIN = 72;
  const HUD_WIDTH_MIN = 240;
  const COMM_HEIGHT_MAX_RATIO = 0.5;
  const HUD_WIDTH_MAX_RATIO = 0.55;
  const SPLIT_KEYBOARD_STEP = 16;
  const GMCP_SUPPORTS = [
    "Char 1",
    "Char.Name 1",
    "Char.Status 1",
    "Char.Vitals 1",
    "Room 1",
    "Room.Info 1",
    "Room.Occupants 1",
    "Comm 1",
    "Comm.Channel 1",
    "RiftForge.Map 1",
    "RiftForge.Map.Atlas 1",
    "RiftForge.Map.Here 1",
    "RiftForge.Map.View 1",
    "RiftForge.Zone 1",
    "RiftForge.Combat 1",
  ];

  let socket = null;
  let passwordMode = false;
  // True after we sent the masked line -- only then may incoming text
  // drop password mode. Extra banner lines after "Password:" must not
  // unmask the field while the player is still typing.
  let passwordSent = false;
  let storyHydrated = false;
  let persistStoryTimer = null;
  let rawTail = "";
  // Incomplete telnet line (no trailing newline yet). Re-painted as
  // .out-partial so a WebSocket split never becomes two stacked rows.
  let lineBuf = "";
  let partialEl = null;
  // When false, new story lines do not yank scroll position (reader scrolled up).
  let outputFollow = true;
  // SGR color carries across chunks so a split mid-line does not snap
  // back to silver. Committed only when a complete line is flushed.
  let sgrColor = "#e8e8e8";
  // Game-only restart keeps this WebSocket. Arm after MSG_AFTER (reattach)
  // so handshakeHud can run once the game child is actually back.
  let hudResumeArmed = false;
  let history = [];
  let historyIdx = -1;
  let historyDraft = "";
  let reconnectTimer = null;
  let reconnectDelay = 1000;
  let userClosed = false;
  let atlasState = null;
  let atlasCountry = null;
  let hoverCell = null;
  let zoneState = null;
  let zoneWho = [];
  let zoneBounds = null;
  let zoneHover = null;
  let gagStored = false;
  let commTab = "public";
  let paneCollapsed = {};
  let paneSizes = {};
  let mapZoom = { atlas: 100, zone: 100 };
  let prevHpPct = null;
  let vitalsAlertTimer = null;

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

  // --- Drag-resize splitters (comm / log / HUD) -----------------------
  // Desktop only: stacked + phone tabs keep CSS defaults. Sizes persist
  // in localStorage as pixels so reload restores the player's layout.
  function paneSplitEnabled() {
    if (phoneShell()) {
      return false;
    }
    return !window.matchMedia("(max-width: 720px)").matches;
  }

  function commHeightMax() {
    if (!playMain) {
      return 480;
    }
    return Math.max(COMM_HEIGHT_MIN, Math.floor(playMain.clientHeight * COMM_HEIGHT_MAX_RATIO));
  }

  function hudWidthMax() {
    if (!playRoot) {
      return 720;
    }
    return Math.max(HUD_WIDTH_MIN, Math.floor(playRoot.clientWidth * HUD_WIDTH_MAX_RATIO));
  }

  function clampCommHeight(px) {
    return Math.max(COMM_HEIGHT_MIN, Math.min(commHeightMax(), Math.round(px)));
  }

  function clampHudWidth(px) {
    return Math.max(HUD_WIDTH_MIN, Math.min(hudWidthMax(), Math.round(px)));
  }

  function currentCommHeightPx() {
    if (commPane) {
      const h = commPane.getBoundingClientRect().height;
      if (h >= COMM_HEIGHT_MIN) {
        return Math.round(h);
      }
    }
    if (typeof paneSizes.commPx === "number" && paneSizes.commPx > 0) {
      return clampCommHeight(paneSizes.commPx);
    }
    if (playMain) {
      return clampCommHeight(playMain.clientHeight * (COMM_HEIGHT_DEFAULT_VH / 100));
    }
    return clampCommHeight(180);
  }

  function currentHudWidthPx() {
    if (hudEl) {
      const w = hudEl.getBoundingClientRect().width;
      if (w >= HUD_WIDTH_MIN) {
        return Math.round(w);
      }
    }
    if (typeof paneSizes.hudPx === "number" && paneSizes.hudPx > 0) {
      return clampHudWidth(paneSizes.hudPx);
    }
    return HUD_WIDTH_DEFAULT;
  }

  function paintCommHeight(px) {
    document.documentElement.style.setProperty("--comm-height", clampCommHeight(px) + "px");
  }

  function paintHudWidth(px) {
    document.documentElement.style.setProperty("--hud-width", clampHudWidth(px) + "px");
  }

  function updateSplitterAria() {
    if (splitComm) {
      const h = currentCommHeightPx();
      splitComm.setAttribute("aria-valuemin", String(COMM_HEIGHT_MIN));
      splitComm.setAttribute("aria-valuemax", String(commHeightMax()));
      splitComm.setAttribute("aria-valuenow", String(h));
    }
    if (splitHud) {
      const w = currentHudWidthPx();
      splitHud.setAttribute("aria-valuemin", String(HUD_WIDTH_MIN));
      splitHud.setAttribute("aria-valuemax", String(hudWidthMax()));
      splitHud.setAttribute("aria-valuenow", String(w));
    }
  }

  function persistPaneSizes() {
    writeStoredJson(PANE_SIZE_KEY, paneSizes);
  }

  function applyPaneSizes() {
    document.body.setAttribute("data-pane-split-off", paneSplitEnabled() ? "false" : "true");
    if (!paneSplitEnabled()) {
      document.documentElement.style.removeProperty("--comm-height");
      document.documentElement.style.removeProperty("--hud-width");
      updateSplitterAria();
      return;
    }
    const commPx =
      typeof paneSizes.commPx === "number" && paneSizes.commPx > 0
        ? clampCommHeight(paneSizes.commPx)
        : currentCommHeightPx();
    const hudPx =
      typeof paneSizes.hudPx === "number" && paneSizes.hudPx > 0
        ? clampHudWidth(paneSizes.hudPx)
        : HUD_WIDTH_DEFAULT;
    paintCommHeight(commPx);
    paintHudWidth(hudPx);
    updateSplitterAria();
  }

  function clampStoredPaneSizes() {
    let changed = false;
    if (typeof paneSizes.commPx === "number") {
      const next = clampCommHeight(paneSizes.commPx);
      if (next !== paneSizes.commPx) {
        paneSizes.commPx = next;
        changed = true;
      }
    }
    if (typeof paneSizes.hudPx === "number") {
      const next = clampHudWidth(paneSizes.hudPx);
      if (next !== paneSizes.hudPx) {
        paneSizes.hudPx = next;
        changed = true;
      }
    }
    if (changed) {
      persistPaneSizes();
    }
  }

  function nudgeCommHeight(delta) {
    const next = clampCommHeight(currentCommHeightPx() + delta);
    paneSizes.commPx = next;
    paintCommHeight(next);
    persistPaneSizes();
    updateSplitterAria();
  }

  function nudgeHudWidth(delta) {
    const next = clampHudWidth(currentHudWidthPx() + delta);
    paneSizes.hudPx = next;
    paintHudWidth(next);
    persistPaneSizes();
    updateSplitterAria();
    window.requestAnimationFrame(function () {
      drawAtlas();
      drawZone();
    });
  }

  function bindPaneSplitter(splitEl, axis, onDelta) {
    if (!splitEl) {
      return;
    }
    let drag = null;

    function finishDrag() {
      if (!drag) {
        return;
      }
      drag = null;
      document.body.classList.remove("pane-split-dragging");
      window.requestAnimationFrame(function () {
        drawAtlas();
        drawZone();
      });
    }

    splitEl.addEventListener("pointerdown", function (ev) {
      if (!paneSplitEnabled() || ev.button !== 0) {
        return;
      }
      ev.preventDefault();
      splitEl.setPointerCapture(ev.pointerId);
      drag = { start: axis === "y" ? ev.clientY : ev.clientX };
    });

    splitEl.addEventListener("pointermove", function (ev) {
      if (!drag) {
        return;
      }
      const delta =
        axis === "y" ? ev.clientY - drag.start : ev.clientX - drag.start;
      if (delta === 0) {
        return;
      }
      drag.start = axis === "y" ? ev.clientY : ev.clientX;
      document.body.classList.add("pane-split-dragging");
      onDelta(delta);
    });

    splitEl.addEventListener("pointerup", finishDrag);
    splitEl.addEventListener("pointercancel", finishDrag);

    splitEl.addEventListener("keydown", function (ev) {
      if (!paneSplitEnabled()) {
        return;
      }
      let delta = 0;
      if (axis === "y") {
        if (ev.key === "ArrowDown") {
          delta = SPLIT_KEYBOARD_STEP;
        } else if (ev.key === "ArrowUp") {
          delta = -SPLIT_KEYBOARD_STEP;
        } else if (ev.key === "Home") {
          nudgeCommHeight(COMM_HEIGHT_MIN - currentCommHeightPx());
          ev.preventDefault();
          return;
        } else if (ev.key === "End") {
          nudgeCommHeight(commHeightMax() - currentCommHeightPx());
          ev.preventDefault();
          return;
        }
      } else {
        if (ev.key === "ArrowRight") {
          delta = SPLIT_KEYBOARD_STEP;
        } else if (ev.key === "ArrowLeft") {
          delta = -SPLIT_KEYBOARD_STEP;
        } else if (ev.key === "Home") {
          nudgeHudWidth(HUD_WIDTH_MIN - currentHudWidthPx());
          ev.preventDefault();
          return;
        } else if (ev.key === "End") {
          nudgeHudWidth(hudWidthMax() - currentHudWidthPx());
          ev.preventDefault();
          return;
        }
      }
      if (delta !== 0) {
        ev.preventDefault();
        onDelta(delta);
      }
    });
  }

  paneSizes = readStoredJson(PANE_SIZE_KEY, {});
  applyPaneSizes();

  bindPaneSplitter(splitComm, "y", function (delta) {
    const next = clampCommHeight(currentCommHeightPx() + delta);
    paneSizes.commPx = next;
    paintCommHeight(next);
    persistPaneSizes();
    updateSplitterAria();
  });

  bindPaneSplitter(splitHud, "x", function (delta) {
    const next = clampHudWidth(currentHudWidthPx() + delta);
    paneSizes.hudPx = next;
    paintHudWidth(next);
    persistPaneSizes();
    updateSplitterAria();
    window.requestAnimationFrame(function () {
      drawAtlas();
      drawZone();
    });
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
  if (moreBuildEl) {
    moreBuildEl.textContent = "build " + CLIENT_BUILD;
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

  function consumeCsi(text, i) {
    // CSI is ESC [ … final-byte (0x40–0x7E). Looking for the letter "m"
    // used to swallow the rest of a room when the sequence was not SGR
    // (erase, cursor) or when "m" appeared later in the prose.
    if (text.charCodeAt(i) !== 27 || text.charAt(i + 1) !== "[") {
      return null;
    }
    let j = i + 2;
    while (j < text.length) {
      const code = text.charCodeAt(j);
      if (code >= 0x40 && code <= 0x7E) {
        return {
          next: j + 1,
          final: text.charAt(j),
          body: text.slice(i + 2, j),
        };
      }
      j += 1;
    }
    return { next: text.length, final: "", body: "" };
  }

  function applySgrCodes(codes, color, closeSpan, openSpan) {
    let next = color;
    if (!codes.length) {
      closeSpan();
      return "#e8e8e8";
    }
    for (let c = 0; c < codes.length; c++) {
      const code = parseInt(codes[c], 10);
      if (code === 0) {
        next = "#e8e8e8";
        closeSpan();
      } else if (code === 38 && codes[c + 1] === "5") {
        next = xterm256ToCss(codes[c + 2]);
        c += 2;
        openSpan(next);
      } else if (SGR16[code]) {
        next = SGR16[code];
        openSpan(next);
      }
    }
    return next;
  }

  function ansiToHtml(text, commit) {
    const parts = [];
    let i = 0;
    let color = sgrColor;
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
      const cc = text.charCodeAt(i);
      if (cc === 27) {
        const csi = consumeCsi(text, i);
        if (csi) {
          i = csi.next;
          if (csi.final === "m") {
            const codes = csi.body.split(";").filter(Boolean);
            color = applySgrCodes(codes, color, closeSpan, openSpan);
          }
          continue;
        }
        i += 1;
        continue;
      }
      const nextEsc = text.indexOf("\x1b", i);
      let chunk = nextEsc === -1 ? text.slice(i) : text.slice(i, nextEsc);
      chunk = chunk.replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, "");
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
      i = nextEsc === -1 ? text.length : nextEsc;
    }
    closeSpan();
    if (commit) {
      sgrColor = color;
    }
    return parts.join("");
  }

  function trimOutput() {
    if (!output) {
      return;
    }
    while (output.childNodes.length > MAX_LINES) {
      output.removeChild(output.firstChild);
    }
    if (liveOutput) {
      const liveCap = 400;
      while (liveOutput.childNodes.length > liveCap) {
        liveOutput.removeChild(liveOutput.firstChild);
      }
    }
    if (partialEl && !partialEl.parentNode) {
      partialEl = null;
    }
  }

  function divertingOutput() {
    return !outputFollow && liveOutput;
  }

  function showLivePane() {
    if (!liveOutput || !liveOutput.hidden) {
      if (liveOutput && !liveOutput.hidden && output) {
        output.setAttribute("aria-live", "off");
      }
      return;
    }
    liveOutput.hidden = false;
    if (output) {
      output.setAttribute("aria-live", "off");
    }
  }

  function catchUpOutput() {
    if (liveOutput) {
      liveOutput.innerHTML = "";
      liveOutput.hidden = true;
    }
    outputFollow = true;
    if (output) {
      output.setAttribute("aria-live", "polite");
      output.scrollTop = output.scrollHeight;
    }
    if (partialEl && liveOutput && partialEl.parentNode === liveOutput && output) {
      output.appendChild(partialEl);
    }
  }

  function cloneToLive(el) {
    if (!divertingOutput() || !el) {
      return;
    }
    showLivePane();
    const clone = el.cloneNode(true);
    liveOutput.appendChild(clone);
    liveOutput.scrollTop = liveOutput.scrollHeight;
  }

  function openStoryDb() {
    return new Promise(function (resolve, reject) {
      if (!window.indexedDB) {
        reject(new Error("no indexedDB"));
        return;
      }
      const req = window.indexedDB.open(STORY_DB, 1);
      req.onupgradeneeded = function () {
        const db = req.result;
        if (!db.objectStoreNames.contains(STORY_STORE)) {
          db.createObjectStore(STORY_STORE);
        }
      };
      req.onsuccess = function () {
        resolve(req.result);
      };
      req.onerror = function () {
        reject(req.error || new Error("indexedDB open failed"));
      };
    });
  }

  function storyHtmlForStore() {
    if (!output) {
      return "";
    }
    let html = output.innerHTML || "";
    if (html.length <= STORY_MAX_CHARS) {
      return html;
    }
    const lines = output.querySelectorAll(".out-line, .echo-line");
    let start = 0;
    if (lines.length > 400) {
      start = lines.length - 400;
    }
    let packed = "";
    for (let i = start; i < lines.length; i++) {
      packed += lines[i].outerHTML;
    }
    return packed.slice(-STORY_MAX_CHARS);
  }

  function persistStoryNow() {
    if (!storyHydrated || passwordMode || !output || !window.indexedDB) {
      return;
    }
    // Copyover [WAIT] dumps a lot of HTML; skip IDB while the world is down
    // so the main thread is not rewriting up to 800KB mid-rewrite.
    if (statusEl && statusEl.className === "wait") {
      return;
    }
    const html = storyHtmlForStore();
    if (!html) {
      return;
    }
    openStoryDb()
      .then(function (db) {
        return new Promise(function (resolve, reject) {
          const tx = db.transaction(STORY_STORE, "readwrite");
          tx.oncomplete = function () {
            db.close();
            resolve();
          };
          tx.onerror = function () {
            db.close();
            reject(tx.error);
          };
          tx.objectStore(STORY_STORE).put(html, STORY_KEY);
        });
      })
      .catch(function () {
        /* local cache is optional */
      });
  }

  function schedulePersistStory() {
    if (passwordMode) {
      return;
    }
    if (persistStoryTimer) {
      window.clearTimeout(persistStoryTimer);
    }
    persistStoryTimer = window.setTimeout(persistStoryNow, 800);
  }

  function restoreStoryLog() {
    return new Promise(function (resolve) {
      function done() {
        storyHydrated = true;
        resolve();
      }
      if (!output || !window.indexedDB) {
        done();
        return;
      }
      if (output.querySelector(".out-line")) {
        done();
        return;
      }
      const timeout = window.setTimeout(done, 400);
      openStoryDb()
        .then(function (db) {
          return new Promise(function (resolveGet, reject) {
            const tx = db.transaction(STORY_STORE, "readonly");
            const req = tx.objectStore(STORY_STORE).get(STORY_KEY);
            req.onsuccess = function () {
              db.close();
              resolveGet(req.result || "");
            };
            req.onerror = function () {
              db.close();
              reject(req.error);
            };
          });
        })
        .then(function (html) {
          window.clearTimeout(timeout);
          if (
            html &&
            output &&
            !output.querySelector(".out-line") &&
            !output.querySelector(".echo-line")
          ) {
            output.innerHTML = html;
            catchUpOutput();
          }
          done();
        })
        .catch(function () {
          window.clearTimeout(timeout);
          done();
        });
    });
  }

  function lastNonEmptyLine(text) {
    const bits = String(text || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
    for (let i = bits.length - 1; i >= 0; i--) {
      const t = bits[i].trim().toLowerCase();
      if (t) {
        return t;
      }
    }
    return "";
  }

  function looksLikePasswordPrompt(line) {
    // Login prompts only — not arbitrary prose ending in "password:".
    return (
      line.endsWith("account password:") ||
      line.endsWith("character password:") ||
      line.indexOf("new password:") !== -1 ||
      line.indexOf("password for ") !== -1
    );
  }

  function flushCompleteLine(piece) {
    const line = document.createElement("div");
    line.className = "out-line";
    line.innerHTML = ansiToHtml(piece, true);
    output.appendChild(line);
    cloneToLive(line);
  }

  function renderPartial() {
    if (partialEl && partialEl.parentNode) {
      partialEl.parentNode.removeChild(partialEl);
    }
    partialEl = null;
    if (!lineBuf) {
      return;
    }
    partialEl = document.createElement("span");
    partialEl.className = "out-partial";
    partialEl.innerHTML = ansiToHtml(lineBuf, false);
    const sink = divertingOutput() ? liveOutput : output;
    if (divertingOutput()) {
      showLivePane();
    }
    sink.appendChild(partialEl);
  }

  function isOutputAtBottom() {
    if (!output) {
      return true;
    }
    const top = output.scrollTop;
    return output.scrollHeight - output.clientHeight - top < 24;
  }

  function scrollOutputIfFollowing() {
    if (outputFollow && output) {
      output.scrollTop = output.scrollHeight;
    }
  }

  function appendOutput(text) {
    if (!text || !output) {
      return;
    }
    rawTail = (rawTail + text).slice(-4000);
    const normalized = String(text).replace(/\r\n/g, "\n").replace(/\r/g, "\n");
    lineBuf += normalized;
    const parts = lineBuf.split("\n");
    lineBuf = parts.pop();
    parts.forEach(function (piece) {
      flushCompleteLine(piece);
    });
    renderPartial();
    trimOutput();
    scrollOutputIfFollowing();

    const lastLine = lastNonEmptyLine(rawTail);
    if (looksLikePasswordPrompt(lastLine)) {
      passwordMode = true;
      passwordSent = false;
      cmd.type = "password";
      cmd.value = "";
    } else if (passwordMode && passwordSent && lastLine) {
      // Clear before flipping to text so a leftover secret never flashes.
      cmd.value = "";
      passwordMode = false;
      passwordSent = false;
      cmd.type = "text";
    }
    if (!passwordMode) {
      schedulePersistStory();
    }
    noteHoldOrResume(text);
  }

  function noteHoldOrResume(text) {
    // [WAIT] is hold music or MSG_AFTER — never the handshake trigger.
    // Hello / Core.* envelopes drop while the game child is down.
    const raw = String(text || "");
    if (!raw) {
      return;
    }
    if (raw.indexOf("[WAIT]") !== -1) {
      setStatus("World restarting. Hold the line.", "wait");
      // MSG_AFTER: socket stayed up, new Session exists, commands still wait.
      // Arm only — handshake on the next real game line (look / rewrite done).
      if (raw.indexOf("You are still here") !== -1) {
        hudResumeArmed = true;
      }
      return;
    }
    const lower = raw.toLowerCase();
    const rewriteDone = lower.indexOf("rewrite is complete") !== -1;
    // Login splash after a hold: unbound sockets skip MSG_AFTER, but the
    // new Session is already painting the connect card (IPC is up).
    const held = statusEl.className === "wait";
    const loginSplash = held && raw.indexOf("Hunters work the cases") !== -1;
    if (hudResumeArmed || rewriteDone || loginSplash) {
      hudResumeArmed = false;
      setStatus("Connected", "connected");
      handshakeHud();
    }
  }

  function sendEnvelope(obj) {
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      return;
    }
    socket.send(JSON.stringify(obj));
  }

  function sendCmd(line) {
    // Typing or tapping compass cancels an in-progress map walk.
    cancelWalk();
    sendEnvelope({ op: "cmd", data: line });
  }

  function sendGmcp(packageName, payload) {
    sendEnvelope({ op: "gmcp", package: packageName, payload: payload });
  }

  function handshakeHud() {
    // Also re-fired after a game-only restart (see noteHoldOrResume).
    // Inbound GMCP SB sets session.gmcp_enabled without a second IAC DO
    // (gateway already sent DO once on this held socket).
    sendEnvelope({ op: "hello", client: "riftforge-web", version: "2" });
    sendGmcp("Core.Hello", { client: "riftforge-web", version: "2" });
    sendGmcp("Core.Supports.Set", GMCP_SUPPORTS);
    syncGagToServer();
  }

  function numericVital(value) {
    // Missing / blank / NaN must not become 0% — that looks like a wipe.
    if (value == null || value === "") {
      return null;
    }
    const n = parseFloat(value);
    if (Number.isNaN(n)) {
      return null;
    }
    return Math.max(0, Math.min(100, n));
  }

  function upsertGauge(id, label, value, klass) {
    const n = numericVital(value);
    if (n == null || !gaugesEl) {
      return false;
    }
    let el = document.getElementById("g-" + id);
    if (!el) {
      el = document.createElement("div");
      el.className = "gauge";
      el.id = "g-" + id;
      const lab = document.createElement("div");
      lab.className = "gauge-label";
      const span = document.createElement("span");
      const strong = document.createElement("strong");
      lab.appendChild(span);
      lab.appendChild(strong);
      const track = document.createElement("div");
      track.className = "gauge-track";
      track.setAttribute("role", "meter");
      track.setAttribute("aria-valuemin", "0");
      track.setAttribute("aria-valuemax", "100");
      const fill = document.createElement("div");
      fill.className = "gauge-fill";
      track.appendChild(fill);
      el.appendChild(lab);
      el.appendChild(track);
      gaugesEl.appendChild(el);
    }
    const span = el.querySelector(".gauge-label span");
    const strong = el.querySelector(".gauge-label strong");
    const track = el.querySelector(".gauge-track");
    const fill = el.querySelector(".gauge-fill");
    if (span) {
      span.textContent = label;
    }
    if (strong) {
      strong.textContent = String(Math.round(n)) + "%";
    }
    if (track) {
      track.setAttribute("aria-valuenow", String(Math.round(n)));
      track.setAttribute("aria-label", label);
    }
    if (fill) {
      fill.className = "gauge-fill " + klass;
      fill.style.width = n + "%";
    }
    return true;
  }

  function applyVitals(v) {
    if (!v || typeof v !== "object" || !gaugesEl) {
      return;
    }
    const hpNow = numericVital(v.hp);
    if (hpNow != null) {
      if (hpNow < 20 && prevHpPct != null && hpNow < prevHpPct) {
        if (typeof navigator.vibrate === "function") {
          navigator.vibrate(200);
        }
        if (vitalsAlertEl) {
          vitalsAlertEl.textContent = "Lifeforce critical";
          if (vitalsAlertTimer) {
            clearTimeout(vitalsAlertTimer);
          }
          vitalsAlertTimer = setTimeout(function () {
            vitalsAlertEl.textContent = "";
            vitalsAlertTimer = null;
          }, 3500);
        }
      }
      prevHpPct = hpNow;
    }
    // Empty / pre-attach payloads must leave the existing HUD alone.
    // Never wipe #gauges (no innerHTML = "").
    const painted = [];
    if (upsertGauge("hp", "Lifeforce", v.hp, "hp")) {
      painted.push("hp");
    }
    if (upsertGauge("st", "Stamina", v.stamina, "stamina")) {
      painted.push("st");
    }
    if (upsertGauge("en", "Focus", v.energy, "energy")) {
      painted.push("en");
    }
    if (v.mana != null) {
      if (upsertGauge("mn", "Mana", v.mana, "mana")) {
        painted.push("mn");
      }
    }
    if (v.fuel != null) {
      const fl = v.fuel_label ? String(v.fuel_label) : "Fuel";
      const fuelLabel = fl.charAt(0).toUpperCase() + fl.slice(1);
      if (upsertGauge("fu", fuelLabel, v.fuel, "fuel")) {
        painted.push("fu");
      }
    }
    const mom = parseFloat(v.momentum);
    if (!Number.isNaN(mom)) {
      const shown = (mom + 100) / 2;
      if (upsertGauge("mo", "Momentum", shown, "momentum")) {
        painted.push("mo");
      }
    }
    if (v.cash) {
      let cashEl = document.getElementById("g-cash");
      if (!cashEl) {
        cashEl = document.createElement("div");
        cashEl.className = "gauge-label";
        cashEl.id = "g-cash";
        const span = document.createElement("span");
        span.textContent = "Cash";
        const strong = document.createElement("strong");
        cashEl.appendChild(span);
        cashEl.appendChild(strong);
        gaugesEl.appendChild(cashEl);
      }
      const strong = cashEl.querySelector("strong");
      if (strong) {
        strong.textContent = String(v.cash);
      }
      painted.push("cash");
    }
    if (!painted.length) {
      return;
    }
    const mirror = [];
    const hp = numericVital(v.hp);
    const st = numericVital(v.stamina);
    if (hp != null) {
      mirror.push("H " + Math.round(hp) + "%");
    }
    if (st != null) {
      mirror.push("S " + Math.round(st) + "%");
    }
    if (!Number.isNaN(mom) && mom !== 0) {
      mirror.push("Mo " + mom);
    }
    if (mirror.length) {
      promptEl.textContent = mirror.join("  ");
    }
  }

  function paintRoomCard(info) {
    // Player-facing title only -- never Room.Info id / num / map keys.
    if (!roomCard || !roomTitle || !info) {
      return;
    }
    const name = String(info.name || "").trim();
    if (!name) {
      return;
    }
    roomTitle.textContent = name;
    if (roomSub) {
      const env = String(info.environment || "").trim().replace(/_/g, " ");
      const skip = !env || env === "plains" || /earth |map id/i.test(env);
      roomSub.textContent = skip ? "" : env;
    }
    roomCard.hidden = false;
  }

  function applyRoom(info) {
    if (!info || typeof info !== "object") {
      return;
    }
    paintRoomCard(info);
    if (!compassEl) {
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
    ctxMenu.classList.remove("ctx-sheet");
    ctxMenu.innerHTML = "";
  }

  function phoneShell() {
    // Touch phones only -- a mouse desktop at 700px still keeps the HUD.
    // Must stay in lockstep with the CSS `@media (max-width: 720px) and
    // (pointer: coarse)` block (header hide + Log/Maps/Status/More tabs).
    return window.matchMedia("(max-width: 720px) and (pointer: coarse)").matches;
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
    if (phoneShell()) {
      ctxMenu.classList.add("ctx-sheet");
      ctxMenu.style.left = "";
      ctxMenu.style.top = "";
    } else {
      ctxMenu.classList.remove("ctx-sheet");
      ctxMenu.style.left = x + "px";
      ctxMenu.style.top = y + "px";
    }
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
        "You are at (" + st.you[0] + ", " + st.you[1] + "). Tap or right-click a landmark.";
    } else {
      atlasCaption.textContent = "Tap or right-click a landmark to walk, drive, or enter.";
    }
  }

  function paintAtlas() {
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
    const cssW = atlasCanvas.clientWidth;
    if (!cssW) {
      // Hidden pane (phone Log tab) or a backgrounded tab -- a 260px
      // fallback bitmap stretches into smear when the canvas is shown later.
      return;
    }
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
    // Country glyphs are stable between Here pings. Cache that layer so
    // drive steps only composite @ / marks (Pass 25 G36).
    const countrySig = [
      atlasCountry.id || "",
      w,
      h,
      rows.length,
      cssW,
      cssH,
      dpr,
    ].join("|");
    if (!atlasCountryLayer || atlasCountrySig !== countrySig) {
      const layer = document.createElement("canvas");
      layer.width = Math.round(cssW * dpr);
      layer.height = Math.round(cssH * dpr);
      const lctx = layer.getContext("2d");
      lctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      for (let r = 0; r < h; r++) {
        const row = rows[r] || "";
        for (let x = 0; x < w; x++) {
          const chGlyph = row.charAt(x) || " ";
          lctx.fillStyle = ATLAS_GLYPH_BG[chGlyph] || "#1a2848";
          lctx.fillRect(x * cw, r * ch, cw + 0.5, ch + 0.5);
        }
      }
      atlasCountryLayer = layer;
      atlasCountrySig = countrySig;
    }
    ctx.drawImage(atlasCountryLayer, 0, 0, cssW, cssH);
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

  // Map + Atlas + Here often arrive in the same tick (copyover / Supports).
  // Coalesce O(W×H) fillRect paints to one frame.
  let atlasPaintRaf = 0;
  let atlasCountryLayer = null;
  let atlasCountrySig = "";
  function drawAtlas() {
    if (atlasPaintRaf) {
      return;
    }
    atlasPaintRaf = window.requestAnimationFrame(function () {
      atlasPaintRaf = 0;
      paintAtlas();
    });
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
    if (payload.marks !== undefined) {
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

  function applyMapHere(payload) {
    // Compact you-are-here ping -- move @ without waiting for a new Atlas.
    if (!payload || typeof payload !== "object") {
      return;
    }
    if (!atlasState) {
      atlasState = {};
    }
    if (payload.you !== undefined) {
      atlasState.you = payload.you;
    }
    drawAtlas();
  }

  function applyMapView(payload) {
    // Viewport camera (Mudlet onView). Browser atlas is the country
    // bitmap -- honor you/origin without rebuilding glyphs.
    if (!payload || typeof payload !== "object") {
      return;
    }
    applyMapHere(payload);
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

  function roomHasDevilsTrapMark(room) {
    if (!room || !Array.isArray(room.marks)) {
      return false;
    }
    for (let i = 0; i < room.marks.length; i++) {
      if (room.marks[i] === "devils_trap") {
        return true;
      }
    }
    return false;
  }

  function cardinalWalkDir(youX, youY, tx, ty) {
    // North is +y (same as zone_hud / drawZone canvasY flip).
    const dx = tx - youX;
    const dy = ty - youY;
    if (dx === 0 && dy === 1) return "north";
    if (dx === 0 && dy === -1) return "south";
    if (dx === 1 && dy === 0) return "east";
    if (dx === -1 && dy === 0) return "west";
    return null;
  }

  function shortExitToken(dir) {
    if (dir === "north") return "n";
    if (dir === "south") return "s";
    if (dir === "east") return "e";
    if (dir === "west") return "w";
    return null;
  }

  function youRoomHasExit(dir) {
    if (!zoneState || !Array.isArray(zoneState.you)) return false;
    const here = zoneRoomAt(zoneState.you[0], zoneState.you[1]);
    const tok = shortExitToken(dir);
    if (!here || !tok) return false;
    const ex = here.ex || [];
    return ex.indexOf(tok) >= 0;
  }

  // Cardinal steps only (same as Later B). North is +y.
  const WALK_DIRS = [
    ["north", 0, 1, "n"],
    ["south", 0, -1, "s"],
    ["east", 1, 0, "e"],
    ["west", -1, 0, "w"],
  ];
  const WALK_PATH_CAP = 48;
  let walkQueue = [];
  let walkExpect = null;
  let walkPending = false;

  function walkRoomKey(x, y) {
    return String(x) + "," + String(y);
  }

  function neighborAfter(x, y, dir) {
    for (let i = 0; i < WALK_DIRS.length; i++) {
      if (WALK_DIRS[i][0] === dir) {
        return [x + WALK_DIRS[i][1], y + WALK_DIRS[i][2]];
      }
    }
    return null;
  }

  function cancelWalk() {
    walkQueue = [];
    walkExpect = null;
    walkPending = false;
  }

  function zoneWalkPath(rooms, fromX, fromY, toX, toY) {
    // BFS on mapped interior-map cells. Missing rooms are fog — not walkable.
    if (fromX === toX && fromY === toY) {
      return [];
    }
    if (!Array.isArray(rooms)) {
      return null;
    }
    const byXY = {};
    for (let i = 0; i < rooms.length; i++) {
      const r = rooms[i];
      byXY[walkRoomKey(r.x, r.y)] = r;
    }
    const startK = walkRoomKey(fromX, fromY);
    const goalK = walkRoomKey(toX, toY);
    if (!byXY[startK] || !byXY[goalK]) {
      return null;
    }
    const q = [[fromX, fromY]];
    const prev = {};
    prev[startK] = null;
    let qi = 0;
    while (qi < q.length) {
      const cur = q[qi++];
      const here = byXY[walkRoomKey(cur[0], cur[1])];
      if (!here) {
        continue;
      }
      const ex = here.ex || [];
      for (let d = 0; d < WALK_DIRS.length; d++) {
        const name = WALK_DIRS[d][0];
        const tok = WALK_DIRS[d][3];
        if (ex.indexOf(tok) < 0) {
          continue;
        }
        const nx = cur[0] + WALK_DIRS[d][1];
        const ny = cur[1] + WALK_DIRS[d][2];
        const nk = walkRoomKey(nx, ny);
        if (prev[nk] !== undefined) {
          continue;
        }
        if (!byXY[nk]) {
          continue;
        }
        prev[nk] = { x: cur[0], y: cur[1], dir: name };
        if (nx === toX && ny === toY) {
          const dirs = [];
          let k = nk;
          while (prev[k]) {
            const p = prev[k];
            dirs.push(p.dir);
            k = walkRoomKey(p.x, p.y);
          }
          dirs.reverse();
          if (dirs.length > WALK_PATH_CAP) {
            return null;
          }
          return dirs;
        }
        q.push([nx, ny]);
      }
    }
    return null;
  }

  function sendNextWalk() {
    if (!walkQueue.length) {
      walkPending = false;
      walkExpect = null;
      return;
    }
    if (!zoneState || !Array.isArray(zoneState.you) || zoneState.you.length !== 2) {
      cancelWalk();
      return;
    }
    const dir = walkQueue[0];
    const nxt = neighborAfter(zoneState.you[0], zoneState.you[1], dir);
    if (!nxt || !youRoomHasExit(dir)) {
      cancelWalk();
      if (zoneCaption) {
        zoneCaption.textContent = "No walkable path there.";
      }
      return;
    }
    walkExpect = nxt;
    walkPending = true;
    sendEnvelope({ op: "cmd", data: dir });
    if (zoneCaption) {
      const left = walkQueue.length;
      zoneCaption.textContent =
        left > 1
          ? "Walking " + dir + " (" + left + " steps)."
          : "Walking " + dir + ".";
    }
  }

  function continueWalkFromZone() {
    if (!walkPending || !walkExpect) {
      return;
    }
    if (!zoneState || !Array.isArray(zoneState.you) || zoneState.you.length !== 2) {
      cancelWalk();
      return;
    }
    const yx = zoneState.you[0];
    const yy = zoneState.you[1];
    if (yx !== walkExpect[0] || yy !== walkExpect[1]) {
      // Occupants / same-room Zone refresh — keep waiting.
      return;
    }
    walkQueue.shift();
    walkPending = false;
    walkExpect = null;
    if (!walkQueue.length) {
      if (zoneCaption) {
        zoneCaption.textContent = "You arrive.";
      }
      return;
    }
    sendNextWalk();
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
    if (roomHasDevilsTrapMark(room)) {
      text += " — warded";
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

  function formatOccupantsCaption(who) {
    if (!Array.isArray(who) || !who.length) {
      return "";
    }
    const names = [];
    for (let i = 0; i < who.length && names.length < 3; i++) {
      const row = who[i];
      if (row && row.name) {
        names.push(String(row.name));
      }
    }
    const extra = who.length - names.length;
    let text = "Here: " + names.join(", ");
    if (extra > 0) {
      text += " and " + extra + " more";
    }
    return text;
  }

  function occupantsCaptionWithWard(who) {
    let text = formatOccupantsCaption(who);
    if (!text || !zoneState || !zoneState.you || zoneState.you.length !== 2) {
      return text;
    }
    const here = zoneRoomAt(zoneState.you[0], zoneState.you[1]);
    if (here && roomHasDevilsTrapMark(here)) {
      text += " (warded)";
    }
    return text;
  }

  function applyOccupants(payload) {
    if (!payload || typeof payload !== "object") {
      return;
    }
    if (payload.visible === false) {
      zoneWho = [];
    } else {
      zoneWho = Array.isArray(payload.who) ? payload.who : [];
    }
    if (!zoneState) {
      return;
    }
    zoneState.who = zoneWho;
    drawZone();
  }

  function applyZone(payload) {
    if (!zonePanel) {
      return;
    }
    if (!payload || typeof payload !== "object") {
      cancelWalk();
      zonePanel.hidden = true;
      zoneState = null;
      return;
    }
    const rooms = payload.rooms;
    const hide =
      payload.hidden === true || !Array.isArray(rooms) || !rooms.length;
    zonePanel.hidden = hide;
    if (hide) {
      cancelWalk();
      zoneState = null;
      zoneBounds = null;
      return;
    }
    zoneState = payload;
    if (!Array.isArray(zoneState.who) || !zoneState.who.length) {
      if (zoneWho.length) {
        zoneState.who = zoneWho;
      }
    } else {
      zoneWho = zoneState.who;
    }
    if (zoneTitle && payload.title) {
      zoneTitle.textContent = payload.title;
    }
    drawZone();
    continueWalkFromZone();
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
    const cssW = zoneCanvas.clientWidth;
    if (!cssW) {
      return;
    }
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
      if (roomHasDevilsTrapMark(room)) {
        ctx.fillStyle = "#e8e8e8";
        ctx.font = Math.max(8, Math.floor(Math.min(cw, ch) * 0.55)) + "px monospace";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        const trapX = room.m ? pos.x + cw * 0.78 : pos.x + cw / 2;
        const trapY = room.m ? pos.y + ch * 0.78 : pos.y + ch / 2;
        ctx.fillText("+", trapX, trapY);
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
      const whoList = zoneState.who;
      if (Array.isArray(whoList) && whoList.length) {
        const occupants = whoList.slice(0, 8);
        const cols = Math.max(1, Math.ceil(Math.sqrt(occupants.length)));
        const sq = Math.min(cw, ch) * 0.18;
        for (let oi = 0; oi < occupants.length; oi++) {
          const col = oi % cols;
          const row = Math.floor(oi / cols);
          const ox = pos.x + 2 + col * (sq + 1);
          const oy = pos.y + 2 + row * (sq + 1);
          ctx.fillStyle = occupants[oi].you ? "#c9a227" : "#8aa4c9";
          ctx.fillRect(ox, oy, sq, sq);
          ctx.strokeStyle = "#e8e8e8";
          ctx.lineWidth = 0.5;
          ctx.strokeRect(ox, oy, sq, sq);
        }
        if (zoneCaption) {
          zoneCaption.textContent = occupantsCaptionWithWard(whoList);
        }
      }
    }
    if (zoneHover) {
      const pos = canvasXY(zoneHover.x, zoneHover.y);
      ctx.strokeStyle = "#7ec8a8";
      ctx.lineWidth = 1.5;
      ctx.strokeRect(pos.x + 0.5, pos.y + 0.5, Math.max(1, cw - 1), Math.max(1, ch - 1));
    } else if (
      Array.isArray(zoneState.who) &&
      zoneState.who.length &&
      zoneCaption
    ) {
      zoneCaption.textContent = occupantsCaptionWithWard(zoneState.who);
    } else {
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
    } else if (name === "RiftForge.Map.Here") {
      applyMapHere(payload);
    } else if (name === "RiftForge.Map.View") {
      applyMapView(payload);
    } else if (name === "RiftForge.Zone") {
      applyZone(payload);
    } else if (name === "Room.Occupants") {
      applyOccupants(payload);
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

  // Post-hitch WS bursts used to apply every GMCP/text frame synchronously
  // on the message event (Pass 25 G38). Keep order, keep every line --
  // just spend at most ~8ms per animation frame so the tab stays live.
  let wsPending = [];
  let wsRead = 0;
  let wsDrainTimer = 0;

  function scheduleWsDrain() {
    if (wsDrainTimer) {
      return;
    }
    const hidden =
      typeof document !== "undefined" && document.hidden;
    if (hidden || typeof window.requestAnimationFrame !== "function") {
      wsDrainTimer = window.setTimeout(function () {
        wsDrainTimer = 0;
        drainWsFrames();
      }, 0);
      return;
    }
    wsDrainTimer = window.requestAnimationFrame(function () {
      wsDrainTimer = 0;
      drainWsFrames();
    });
  }

  function drainWsFrames() {
    const start = performance.now();
    while (wsRead < wsPending.length && performance.now() - start < 8) {
      handleFrame(wsPending[wsRead]);
      wsRead += 1;
    }
    if (wsRead >= wsPending.length) {
      wsPending = [];
      wsRead = 0;
      return;
    }
    scheduleWsDrain();
  }

  function queueWsFrame(raw) {
    wsPending.push(raw);
    scheduleWsDrain();
  }

  function connect() {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    // Close a leftover socket first. Safari tab-sleep can leave
    // readyState OPEN with a silent inbound path; opening a second
    // socket without dropping the first is what flapped live :4080.
    const previous = socket;
    socket = null;
    if (previous) {
      try {
        previous.close();
      } catch (err) {
        /* already closed */
      }
    }
    setStatus("Connecting…", "");
    setInputEnabled(false);
    let opened;
    try {
      opened = new WebSocket(wsUrl());
    } catch (err) {
      setStatus("WebSocket failed: " + err.message, "error");
      scheduleReconnect();
      return;
    }
    socket = opened;
    opened.addEventListener("open", function () {
      if (socket !== opened) {
        return;
      }
      setStatus("Connected", "connected");
      setInputEnabled(true);
      reconnectDelay = 1000;
      handshakeHud();
    });
    opened.addEventListener("message", function (ev) {
      if (socket !== opened) {
        return;
      }
      if (typeof ev.data !== "string") {
        return;
      }
      queueWsFrame(ev.data);
    });
    opened.addEventListener("close", function () {
      if (socket !== opened) {
        return;
      }
      setStatus("Disconnected. Reconnecting…", "error");
      setInputEnabled(false);
      socket = null;
      if (!userClosed) {
        scheduleReconnect();
      }
    });
    opened.addEventListener("error", function () {
      if (socket !== opened) {
        return;
      }
      setStatus("Connection failed", "error");
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
    cloneToLive(el);
    trimOutput();
    scrollOutputIfFollowing();
    schedulePersistStory();
  }

  function submitLine(line) {
    const hidingSecret = passwordMode;
    if (line !== "" && !hidingSecret) {
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
    if (hidingSecret) {
      passwordSent = true;
      cmd.value = "";
      cmd.type = "password";
    }
  }

  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    const line = cmd.value;
    submitLine(line);
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
      if (historyIdx >= history.length) {
        historyDraft = cmd.value;
      }
      historyIdx = Math.max(0, historyIdx - 1);
      cmd.value = history[historyIdx] || "";
    } else if (ev.key === "ArrowDown") {
      if (passwordMode) {
        return;
      }
      ev.preventDefault();
      historyIdx = Math.min(history.length, historyIdx + 1);
      cmd.value = historyIdx >= history.length ? historyDraft : history[historyIdx];
    } else if (ev.key === "Escape") {
      hideMenu();
      cmd.value = "";
      ev.preventDefault();
      ev.stopPropagation();
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

  let atlasPress = null;

  function cancelAtlasPress() {
    if (atlasPress && atlasPress.timer) {
      clearTimeout(atlasPress.timer);
    }
    atlasPress = null;
  }

  function openAtlasTravel(clientX, clientY, cell) {
    if (!cell) {
      hideMenu();
      return false;
    }
    const verbs = verbsForCell(cell.x, cell.y);
    if (!verbs.length) {
      hideMenu();
      if (atlasCaption) {
        atlasCaption.textContent =
          "Nothing to travel from (" + cell.x + ", " + cell.y + ").";
      }
      return false;
    }
    showMenu(clientX, clientY, verbs);
    return true;
  }

  atlasCanvas.addEventListener("pointerdown", function (ev) {
    if (ev.pointerType === "mouse") {
      return;
    }
    if (ev.button !== 0 && ev.button !== -1) {
      return;
    }
    const cell = cellAtEvent(ev);
    cancelAtlasPress();
    atlasPress = {
      x: ev.clientX,
      y: ev.clientY,
      cell: cell,
      opened: false,
    };
    atlasPress.timer = setTimeout(function () {
      if (!atlasPress) {
        return;
      }
      atlasPress.opened = true;
      openAtlasTravel(atlasPress.x, atlasPress.y, atlasPress.cell);
    }, LONG_PRESS_MS);
  });
  atlasCanvas.addEventListener("pointermove", function (ev) {
    if (!atlasPress) {
      return;
    }
    const dx = ev.clientX - atlasPress.x;
    const dy = ev.clientY - atlasPress.y;
    if (dx * dx + dy * dy > PRESS_MOVE_CANCEL_PX * PRESS_MOVE_CANCEL_PX) {
      cancelAtlasPress();
    }
  });
  atlasCanvas.addEventListener("pointerup", function (ev) {
    if (!atlasPress) {
      return;
    }
    const opened = atlasPress.opened;
    const cell = atlasPress.cell;
    const x = atlasPress.x;
    const y = atlasPress.y;
    cancelAtlasPress();
    if (opened) {
      ev.preventDefault();
      return;
    }
    if (phoneShell()) {
      openAtlasTravel(x, y, cell);
    }
  });
  atlasCanvas.addEventListener("pointercancel", cancelAtlasPress);
  atlasCanvas.addEventListener("click", function () {
    if (phoneShell()) {
      return;
    }
    hideMenu();
  });
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
    if (document.hidden) {
      return;
    }
    syncVisualViewport();
    clampStoredPaneSizes();
    applyPaneSizes();
    drawAtlas();
    drawZone();
  });

  function pinDocumentScroll() {
    if (window.scrollY || window.scrollX) {
      window.scrollTo(0, 0);
    }
    if (document.documentElement) {
      document.documentElement.scrollTop = 0;
    }
    if (document.body) {
      document.body.scrollTop = 0;
    }
  }

  function syncVisualViewport() {
    // Chrome fires visualViewport resize with height 0 (or a stub) when
    // the tab is backgrounded. Stamping that onto --app-height crushes
    // the flex shell; on return the log and maps smear until a refresh.
    if (document.hidden) {
      return;
    }
    const vv = window.visualViewport;
    let height = window.innerHeight || 0;
    if (vv && vv.height >= 120) {
      height = Math.round(vv.height);
    }
    if (window.innerHeight >= 120) {
      height = Math.min(height, Math.round(window.innerHeight));
    }
    if (height < 120) {
      return;
    }
    document.documentElement.style.setProperty("--app-height", height + "px");
    pinDocumentScroll();
  }

  function bustCanvas(canvas) {
    if (!canvas) {
      return;
    }
    canvas.width = 0;
    canvas.height = 0;
  }

  function webkitStoryPaint() {
    // Safari and iOS browsers (including Chrome-on-iOS) paint with WebKit.
    // Desktop Chrome/Edge/Chromium keep the display:none compositor bust.
    const ua = String(navigator.userAgent || "");
    return /AppleWebKit/i.test(ua) && !/Chrome\/|Chromium\/|Edg\//i.test(ua);
  }

  function socketIsOpen() {
    return !!(socket && socket.readyState === WebSocket.OPEN);
  }

  function ensureOpenSocket() {
    // Never close an OPEN socket from a wake handler -- that drops a live
    // play session to the login prompt. Only open one when this tab has none.
    if (userClosed || socketIsOpen()) {
      return;
    }
    connect();
  }

  function wakeStoryLog() {
    catchUpOutput();
    if (output) {
      output.scrollTop = output.scrollHeight;
    }
  }

  function repaintStoryLog() {
    if (!output) {
      return;
    }
    if (webkitStoryPaint()) {
      // Safari: taking #output out of flow with display:none freezes the
      // compositor tile. Commands still send; incoming lines never paint.
      wakeStoryLog();
      return;
    }
    const top = output.scrollTop;
    const atBottom = output.scrollHeight - output.clientHeight - top < 24;
    // Drop Chrome's stale compositor tile by taking the scroller out of
    // flow for a frame -- transform:translateZ on a scrolling pane is what
    // smeared rows after a long tab-away.
    output.style.display = "none";
    void output.offsetHeight;
    output.style.display = "";
    output.scrollTop = atBottom ? output.scrollHeight : top;
    if (atBottom) {
      outputFollow = true;
    }
  }

  function refreshForeground() {
    if (document.visibilityState && document.visibilityState !== "visible") {
      return;
    }
    pinDocumentScroll();
    syncVisualViewport();
    bustCanvas(atlasCanvas);
    bustCanvas(zoneCanvas);
    repaintStoryLog();
    window.requestAnimationFrame(function () {
      window.requestAnimationFrame(function () {
        drawAtlas();
        drawZone();
        pinDocumentScroll();
      });
    });
  }

  function onPageShow(ev) {
    // bfcache restore: inbound WS events may be dead even when readyState
    // still says OPEN. Reconnect only when the socket is actually gone.
    if (ev && ev.persisted) {
      ensureOpenSocket();
      wakeStoryLog();
    }
    refreshForeground();
  }

  if (window.visualViewport) {
    window.visualViewport.addEventListener("resize", syncVisualViewport);
    window.visualViewport.addEventListener("scroll", syncVisualViewport);
  }
  window.addEventListener(
    "scroll",
    function () {
      pinDocumentScroll();
    },
    { passive: true }
  );
  window.addEventListener("orientationchange", function () {
    window.setTimeout(refreshForeground, 50);
  });
  document.addEventListener("visibilitychange", refreshForeground);
  window.addEventListener("pageshow", onPageShow);
  // Do not redraw on window focus -- Mac Safari fires that on every
  // click back into the tab, and display:none there freezes the log.
  syncVisualViewport();

  function applyPhoneTab(name, persist) {
    const tab = name === "maps" || name === "status" || name === "more" ? name : "log";
    if (playRoot) {
      playRoot.setAttribute("data-phone-tab", tab);
    }
    document.body.setAttribute("data-phone-tab", tab);
    if (moreSheet) {
      moreSheet.hidden = tab !== "more";
    }
    if (phoneNav) {
      const buttons = phoneNav.querySelectorAll("[data-phone-tab]");
      for (let i = 0; i < buttons.length; i++) {
        const on = buttons[i].getAttribute("data-phone-tab") === tab;
        buttons[i].setAttribute("aria-pressed", on ? "true" : "false");
      }
    }
    if (persist !== false) {
      writeStoredString(PHONE_TAB_KEY, tab);
    }
    if (tab === "maps" || tab === "status") {
      window.requestAnimationFrame(function () {
        drawAtlas();
        drawZone();
      });
    }
  }

  if (phoneNav) {
    phoneNav.addEventListener("click", function (ev) {
      const btn = ev.target.closest("[data-phone-tab]");
      if (!btn) {
        return;
      }
      applyPhoneTab(btn.getAttribute("data-phone-tab"), true);
    });
  }
  applyPhoneTab(readStoredString(PHONE_TAB_KEY, "log"), false);

  function exportSessionLog() {
    if (!output) {
      return;
    }
    const text = output.textContent || "";
    const date = new Date().toISOString().slice(0, 10);
    const blob = new Blob([text], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "riftforge-session-" + date + ".txt";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  }

  if (saveLogBtn) {
    saveLogBtn.addEventListener("click", exportSessionLog);
  }

  function dialogOpen() {
    return (
      (shortcutsDialog && !shortcutsDialog.hidden) ||
      (displayDialog && !displayDialog.hidden)
    );
  }

  function screenreaderOn() {
    return document.body.classList.contains("screenreader");
  }

  function isTypingField(el) {
    if (!el) {
      return false;
    }
    const tag = (el.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select") {
      return true;
    }
    return !!el.isContentEditable;
  }

  function hasTextSelection() {
    const sel = window.getSelection();
    return !!(sel && String(sel).length);
  }

  function focusCommand() {
    if (!cmd || cmd.disabled) {
      return false;
    }
    cmd.focus();
    return document.activeElement === cmd;
  }

  function clickShouldFocusCmd(ev) {
    if (dialogOpen() || !ev.target || !ev.target.closest) {
      return false;
    }
    if (
      ev.target.closest(
        "button, a, input, select, textarea, label, canvas, .pane-split, #ctx-menu, #phone-nav, #input-row, header"
      )
    ) {
      return false;
    }
    if (!ev.target.closest("#output, #room-card, .comm-log, #play-main, #more-sheet")) {
      return false;
    }
    return !hasTextSelection();
  }

  function insertTypedChar(ch) {
    const start = cmd.selectionStart == null ? cmd.value.length : cmd.selectionStart;
    const end = cmd.selectionEnd == null ? cmd.value.length : cmd.selectionEnd;
    cmd.value = cmd.value.slice(0, start) + ch + cmd.value.slice(end);
    const pos = start + ch.length;
    cmd.setSelectionRange(pos, pos);
  }

  if (output) {
    output.addEventListener(
      "scroll",
      function () {
        const atBottom = isOutputAtBottom();
        if (atBottom) {
          catchUpOutput();
        } else {
          outputFollow = false;
        }
      },
      { passive: true }
    );
  }
  if (liveOutput) {
    liveOutput.addEventListener("click", function () {
      catchUpOutput();
    });
  }

  document.addEventListener("click", function (ev) {
    if (!clickShouldFocusCmd(ev)) {
      return;
    }
    focusCommand();
  });

  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape") {
      if (shortcutsDialog && !shortcutsDialog.hidden) {
        closeShortcuts();
        ev.preventDefault();
        ev.stopPropagation();
        return;
      }
      if (displayDialog && !displayDialog.hidden) {
        closeDisplayPrefs();
        ev.preventDefault();
        ev.stopPropagation();
        return;
      }
      if (cmd && !cmd.disabled) {
        if (document.activeElement !== cmd) {
          focusCommand();
        }
        cmd.value = "";
        ev.preventDefault();
        ev.stopPropagation();
      }
      return;
    }
    if (ev.key === "Tab") {
      if (dialogOpen() || screenreaderOn()) {
        return;
      }
      ev.preventDefault();
      focusCommand();
      return;
    }
    if (ev.key === "End" && document.activeElement !== cmd && output) {
      ev.preventDefault();
      catchUpOutput();
      return;
    }
    if (dialogOpen() || ev.ctrlKey || ev.metaKey || ev.altKey || ev.isComposing) {
      return;
    }
    if (isTypingField(document.activeElement)) {
      return;
    }
    if (ev.key === "Unidentified" || ev.key === "Process" || ev.key === "Dead") {
      return;
    }
    if (ev.key.length !== 1) {
      return;
    }
    if (!focusCommand()) {
      return;
    }
    ev.preventDefault();
    insertTypedChar(ev.key);
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
      if (
        zoneState &&
        zoneState.you &&
        zoneState.you.length === 2 &&
        room
      ) {
        const yx = zoneState.you[0];
        const yy = zoneState.you[1];
        if (yx === room.x && yy === room.y) {
          cancelWalk();
        } else {
          const path = zoneWalkPath(
            zoneState.rooms,
            yx,
            yy,
            room.x,
            room.y
          );
          if (path && path.length) {
            walkQueue = path;
            walkExpect = null;
            walkPending = false;
            sendNextWalk();
            return;
          }
          if (zoneCaption) {
            zoneCaption.textContent =
              "No walkable path there. " + zoneCaptionFor(room);
            return;
          }
        }
      }
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
  function typeHelpBrowser(ev) {
    if (ev) {
      ev.preventDefault();
    }
    sendCmd("help browser");
    cmd.focus();
  }
  if (helpBrowser) {
    helpBrowser.addEventListener("click", typeHelpBrowser);
  }
  const moreHelp = document.getElementById("more-help");
  const moreShortcuts = document.getElementById("more-shortcuts");
  const moreDisplay = document.getElementById("more-display");
  if (moreHelp) {
    moreHelp.addEventListener("click", typeHelpBrowser);
  }
  if (moreShortcuts) {
    moreShortcuts.addEventListener("click", openShortcuts);
  }
  if (moreDisplay) {
    moreDisplay.addEventListener("click", openDisplayPrefs);
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

  restoreStoryLog().then(function () {
    connect();
  });

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("./service-worker.js").catch(function () {
        /* offline shell optional */
      });
    });
  }
})();
