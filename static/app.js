(function () {
  "use strict";

  // ---------- Theme ----------
  const THEME_KEY = "meshapp.theme";

  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
  }

  function initTheme() {
    let saved = null;
    try { saved = localStorage.getItem(THEME_KEY); } catch (e) { /* ignore */ }
    if (saved === "dark" || saved === "light") {
      applyTheme(saved);
      return;
    }
    const prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    applyTheme(prefersDark ? "dark" : "light");
  }

  function toggleTheme() {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    applyTheme(next);
    try { localStorage.setItem(THEME_KEY, next); } catch (e) { /* ignore */ }
  }

  initTheme();
  document.addEventListener("DOMContentLoaded", function () {
    const btn = document.getElementById("theme-toggle");
    if (btn) btn.addEventListener("click", toggleTheme);
  });

  // ---------- Formatters ----------
  function pad(value) {
    return String(value).padStart(2, "0");
  }

  function formatTime(epochSeconds) {
    if (epochSeconds === null || epochSeconds === undefined) return "-";
    const numeric = Number(epochSeconds);
    if (!numeric) return "-";
    const date = new Date(numeric * 1000);
    if (Number.isNaN(date.getTime())) return "-";
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
      `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
  }

  function formatMessageTime(epochSeconds) {
    if (epochSeconds === null || epochSeconds === undefined) return "-";
    const numeric = Number(epochSeconds);
    if (!numeric) return "-";
    const date = new Date(numeric * 1000);
    if (Number.isNaN(date.getTime())) return "-";
    const now = new Date();
    const sameDay = date.getFullYear() === now.getFullYear() &&
                    date.getMonth() === now.getMonth() &&
                    date.getDate() === now.getDate();
    const hm = `${pad(date.getHours())}:${pad(date.getMinutes())}`;
    if (sameDay) return hm;
    return `${pad(date.getDate())}.${pad(date.getMonth() + 1)}.${date.getFullYear()} ${hm}`;
  }

  function formatValue(value, digits) {
    if (digits === undefined) digits = 2;
    if (value === null || value === undefined || value === "") return "-";
    const n = Number(value);
    if (!Number.isFinite(n)) return String(value);
    if (Number.isInteger(n)) return String(n);
    return n.toFixed(digits);
  }

  function formatValueUnit(value, unit, digits) {
    const f = formatValue(value, digits);
    if (f === "-") return f;
    return `${f} ${unit}`;
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function avatarTextFor(short, long, id) {
    const raw = short || (long ? long.slice(0, 2) : (id ? id.slice(0, 2) : "?"));
    return Array.from(String(raw)).slice(0, 4).join("").toUpperCase();
  }

  function avatarLength(value) {
    return Array.from(String(value)).length;
  }

  function buildAvatarStyle(bg, fg) {
    const parts = [];
    if (bg) parts.push(`background: ${bg}`);
    if (fg) parts.push(`color: ${fg}`);
    return parts.length ? ` style="${parts.join("; ")}"` : "";
  }

  const LIVE_THRESHOLD_SECONDS = 90 * 3600;

  function isLive(lastSeen) {
    if (lastSeen === null || lastSeen === undefined) return false;
    const n = Number(lastSeen);
    if (!n) return false;
    const ageSeconds = (Date.now() / 1000) - n;
    return ageSeconds >= 0 && ageSeconds <= LIVE_THRESHOLD_SECONDS;
  }

  function batteryInfo(level) {
    if (level === null || level === undefined) return null;
    const n = Number(level);
    if (!Number.isFinite(n)) return null;
    const lvl = Math.max(0, Math.min(100, Math.round(n)));
    const cssClass = lvl <= 20 ? "battery--low" : lvl <= 50 ? "battery--medium" : "battery--high";
    return { level: lvl, cssClass };
  }

  function renderBattery(level) {
    const info = batteryInfo(level);
    if (!info) return "";
    return `<span class="battery ${info.cssClass}" aria-label="Battery ${info.level}%">` +
      `<span class="battery__shell"><span class="battery__fill" style="width: ${info.level}%"></span></span>` +
      `<span class="battery__cap"></span>` +
      `<span class="battery__label">${info.level} %</span>` +
      `</span>`;
  }

  function renderNodeButton(opts) {
    // opts: { node_id, short_name, long_name, avatar_bg, avatar_fg,
    //         last_seen?, subtitle?, show_status?, battery_level? }
    const name = opts.long_name || opts.short_name || opts.node_id || "-";
    const avatarText = avatarTextFor(opts.short_name, opts.long_name, opts.node_id);
    const cls = avatarLength(avatarText) >= 4 ? "avatar avatar--small" : "avatar";
    const style = buildAvatarStyle(opts.avatar_bg, opts.avatar_fg);
    const id = opts.node_id ? ` data-node-id="${escapeHtml(opts.node_id)}"` : "";
    const showStatus = opts.show_status !== false && opts.last_seen !== undefined && opts.last_seen !== null;
    const dotHtml = showStatus
      ? `<span class="status-dot${isLive(opts.last_seen) ? " status-dot--live" : ""}" aria-hidden="true"></span>`
      : "";
    const batteryHtml = renderBattery(opts.battery_level);
    const subtitle = opts.subtitle
      ? `<span class="node-button__sub">${escapeHtml(opts.subtitle)}</span>`
      : "";
    return `<button type="button" class="node-button"${id}>` +
      dotHtml +
      `<span class="${cls}"${style}>${escapeHtml(avatarText)}</span>` +
      `<span class="node-button__text">` +
      `<span class="node-button__primary">` +
      `<span class="node-name">${escapeHtml(name)}</span>` +
      batteryHtml +
      `</span>` +
      subtitle +
      `</span>` +
      `</button>`;
  }

  // ---------- Modal ----------
  let lastFocus = null;

  function getModal() {
    return document.getElementById("node-modal");
  }

  function openModal() {
    const modal = getModal();
    if (!modal) return;
    lastFocus = document.activeElement;
    modal.classList.add("is-open");
    modal.setAttribute("aria-hidden", "false");
    document.body.style.overflow = "hidden";
    const close = modal.querySelector(".modal__close");
    if (close) close.focus();
  }

  function closeModal() {
    const modal = getModal();
    if (!modal) return;
    modal.classList.remove("is-open");
    modal.setAttribute("aria-hidden", "true");
    document.body.style.overflow = "";
    if (lastFocus && typeof lastFocus.focus === "function") lastFocus.focus();
  }

  function setModalLoading(nodeId) {
    const titleName = document.getElementById("node-modal-name");
    const titleSub = document.getElementById("node-modal-sub");
    const avatar = document.getElementById("node-modal-avatar");
    const body = document.getElementById("node-modal-body");
    if (titleName) titleName.textContent = "Loading…";
    if (titleSub) titleSub.textContent = nodeId || "";
    if (avatar) {
      avatar.textContent = "…";
      avatar.removeAttribute("style");
    }
    if (body) body.innerHTML = '<p class="muted">Fetching node details…</p>';
  }

  function row(label, value) {
    if (value === undefined || value === null || value === "" || value === "-") return "";
    return `<dt>${escapeHtml(label)}</dt><dd>${escapeHtml(String(value))}</dd>`;
  }

  function renderDetail(node) {
    const avatar = document.getElementById("node-modal-avatar");
    const titleName = document.getElementById("node-modal-name");
    const titleSub = document.getElementById("node-modal-sub");
    const body = document.getElementById("node-modal-body");

    const name = node.long_name || node.short_name || node.node_id || "Unknown node";
    const avatarText = avatarTextFor(node.short_name, node.long_name, node.node_id);

    if (titleName) titleName.textContent = name;
    if (titleSub) titleSub.textContent = node.node_id || "";
    if (avatar) {
      avatar.textContent = avatarText;
      avatar.className = avatarLength(avatarText) >= 4 ? "avatar avatar--small" : "avatar";
      const style = [];
      if (node.avatar_bg) style.push(`background: ${node.avatar_bg}`);
      if (node.avatar_fg) style.push(`color: ${node.avatar_fg}`);
      if (style.length) avatar.setAttribute("style", style.join("; "));
      else avatar.removeAttribute("style");
    }

    const identity =
      row("Node ID", node.node_id) +
      row("Short name", node.short_name) +
      row("Long name", node.long_name) +
      row("Hardware", node.hw_model);

    const activity =
      row("Last seen", formatTime(node.last_seen)) +
      row("Last ping", formatTime(node.last_ping)) +
      row("Last telemetry", formatTime(node.last_telemetry)) +
      row("Last position", formatTime(node.last_position)) +
      row("Last hops", formatValue(node.last_hops));

    const sensors =
      row("Battery", formatValueUnit(node.battery_level, "%")) +
      row("Voltage", formatValueUnit(node.battery_voltage, "V")) +
      row("Temperature", formatValueUnit(node.temperature, "°C")) +
      row("Humidity", formatValueUnit(node.humidity, "%hr")) +
      row("Pressure", formatValueUnit(node.pressure, "mbar"));

    const fmtCount = (t, d) => `${formatValue(t)} total · ${formatValue(d)} today`;
    const counts =
      row("Telemetry", fmtCount(node.telemetry_count_total, node.telemetry_count_daily)) +
      row("Node info", fmtCount(node.nodeinfo_count_total, node.nodeinfo_count_daily)) +
      row("Position", fmtCount(node.position_count_total, node.position_count_daily)) +
      row("Other", fmtCount(node.other_count_total, node.other_count_daily));

    let position = "";
    if (node.position && typeof node.position === "object") {
      const p = node.position;
      const lat = p.latitude ?? p.latitudeI;
      const lon = p.longitude ?? p.longitudeI;
      const alt = p.altitude;
      position =
        row("Latitude", lat) +
        row("Longitude", lon) +
        row("Altitude", alt !== undefined ? formatValueUnit(alt, "m") : null);
    }

    const sections = [
      ["Identity", identity],
      ["Activity", activity],
      ["Latest sensors", sensors],
      ["Packet counts", counts],
      ["Position", position],
    ];

    const html = sections
      .filter(([_, content]) => content && content.trim().length)
      .map(([title, content]) =>
        `<div class="modal__section">` +
        `<h3 class="modal__section-title">${escapeHtml(title)}</h3>` +
        `<dl class="detail-grid">${content}</dl>` +
        `</div>`
      )
      .join("");

    if (body) {
      body.innerHTML = html || '<p class="muted">No data recorded for this node yet.</p>';
    }
  }

  async function showNodeModal(nodeId) {
    if (!nodeId) return;
    openModal();
    setModalLoading(nodeId);
    try {
      const response = await fetch(`/api/nodes/${encodeURIComponent(nodeId)}`, { cache: "no-store" });
      if (!response.ok) {
        const body = document.getElementById("node-modal-body");
        const titleName = document.getElementById("node-modal-name");
        if (titleName) titleName.textContent = "Node";
        if (body) {
          body.innerHTML = response.status === 404
            ? '<p class="muted">No record for this node yet.</p>'
            : `<p class="muted">Failed to load node (${response.status}).</p>`;
        }
        return;
      }
      const payload = await response.json();
      if (payload && payload.node) renderDetail(payload.node);
    } catch (err) {
      const body = document.getElementById("node-modal-body");
      if (body) body.innerHTML = '<p class="muted">Network error loading node.</p>';
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    const modal = getModal();
    if (modal) {
      modal.addEventListener("click", function (event) {
        if (event.target === modal) closeModal();
      });
      const close = modal.querySelector(".modal__close");
      if (close) close.addEventListener("click", closeModal);
    }
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && modal && modal.classList.contains("is-open")) {
        closeModal();
      }
    });

    // Delegated click handling so dynamically-rendered rows work too
    document.addEventListener("click", function (event) {
      const button = event.target.closest(".node-button");
      if (!button) return;
      const id = button.dataset.nodeId;
      if (id) {
        event.preventDefault();
        showNodeModal(id);
      }
    });
  });

  // Expose helpers used by page templates
  window.MeshApp = {
    formatTime,
    formatMessageTime,
    formatValue,
    formatValueUnit,
    escapeHtml,
    renderNodeButton,
    renderBattery,
    batteryInfo,
    showNodeModal,
    isLive,
    LIVE_THRESHOLD_SECONDS,
  };
})();
