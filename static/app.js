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

  function formatRelative(epochSeconds) {
    if (epochSeconds === null || epochSeconds === undefined) return null;
    const numeric = Number(epochSeconds);
    if (!numeric) return null;
    const delta = Math.abs(Date.now() / 1000 - numeric);
    if (delta < 60) return "<1m";
    const minutes = Math.floor(delta / 60);
    const hours = Math.floor(minutes / 60);
    const days = Math.floor(hours / 24);
    if (days > 0) return `${days}d ${hours % 24}h`;
    if (hours > 0) return `${hours}h ${minutes % 60}m`;
    return `${minutes}m`;
  }

  function formatTimeWithRelative(epochSeconds) {
    const abs = formatMessageTime(epochSeconds);
    if (abs === "-") return "-";
    const rel = formatRelative(epochSeconds);
    return rel ? `${abs} (${rel} ago)` : abs;
  }

  function formatValue(value, digits) {
    if (digits === undefined) digits = 2;
    if (value === null || value === undefined || value === "") return "-";
    const n = Number(value);
    if (!Number.isFinite(n)) return "-";
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

  const LIVE_THRESHOLD_SECONDS = 120 * 60;

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
      `<span class="battery__icon">` +
      `<span class="battery__shell"><span class="battery__fill" style="width: ${info.level}%"></span></span>` +
      `<span class="battery__cap"></span>` +
      `</span>` +
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
  let openModalEl = null;

  function openModal(modal) {
    if (!modal) return;
    // Switching directly between modals (e.g. message → node) should not stack.
    if (openModalEl && openModalEl !== modal) {
      openModalEl.classList.remove("is-open");
      openModalEl.setAttribute("aria-hidden", "true");
    } else if (!openModalEl) {
      lastFocus = document.activeElement;
    }
    modal.classList.add("is-open");
    modal.setAttribute("aria-hidden", "false");
    document.body.style.overflow = "hidden";
    openModalEl = modal;
    const close = modal.querySelector(".modal__close");
    if (close) close.focus();
  }

  function closeModal(modal) {
    modal = modal || openModalEl;
    if (!modal) return;
    modal.classList.remove("is-open");
    modal.setAttribute("aria-hidden", "true");
    document.body.style.overflow = "";
    if (openModalEl === modal) openModalEl = null;
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

  function renderSections(sections) {
    return sections
      .filter(([_, content]) => content && content.trim().length)
      .map(([title, content]) =>
        `<div class="modal__section">` +
        `<h3 class="modal__section-title">${escapeHtml(title)}</h3>` +
        `<dl class="detail-grid">${content}</dl>` +
        `</div>`
      )
      .join("");
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
      row("Hardware", node.hw_model) +
      row("Role", node.role) +
      row("MAC address", node.macaddr) +
      row("Public key", node.public_key);

    const activity =
      row("First seen", formatTimeWithRelative(node.first_seen)) +
      row("Last seen", formatTimeWithRelative(node.last_seen)) +
      row("Online since", formatTimeWithRelative(node.online_since)) +
      row("Last ping", formatTimeWithRelative(node.last_ping)) +
      row("Last telemetry", formatTimeWithRelative(node.last_telemetry)) +
      row("Last position", formatTimeWithRelative(node.last_position)) +
      row("Last hops", formatValue(node.last_hops)) +
      row("SNR (last)", formatValue(node.last_rx_snr)) +
      row("RSSI (last)", formatValueUnit(node.last_rx_rssi, "dBm"));

    const sensors =
      row("Battery", formatValueUnit(node.battery_level, "%")) +
      row("Voltage", formatValueUnit(node.battery_voltage, "V")) +
      row("Channel util", formatValueUnit(node.channel_utilization, "%")) +
      row("Air util TX", formatValueUnit(node.air_util_tx, "%")) +
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
      const fixTime = p.time ?? p.fixTime;
      const source = p.locationSource ?? p.location_source;
      position =
        row("Latitude", lat) +
        row("Longitude", lon) +
        row("Altitude", alt !== undefined ? formatValueUnit(alt, "m") : null) +
        row("Source", source) +
        row("Fix time", fixTime ? formatTimeWithRelative(fixTime) : null);
    }

    const sections = [
      ["Identity", identity],
      ["Activity", activity],
      ["Latest sensors", sensors],
      ["Packet counts", counts],
      ["Position", position],
    ];

    const html = renderSections(sections);

    if (body) {
      body.innerHTML = html || '<p class="muted">No data recorded for this node yet.</p>';
    }
  }

  async function showNodeModal(nodeId) {
    if (!nodeId) return;
    openModal(document.getElementById("node-modal"));
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

  // ---------- Message modal ----------
  function messageKind(message) {
    if (message.is_tapback) return "Tapback reaction";
    if (message.reply_id !== null && message.reply_id !== undefined) return "Reply";
    return "Text message";
  }

  function setMessageModalLoading() {
    const titleName = document.getElementById("message-modal-name");
    const titleSub = document.getElementById("message-modal-sub");
    const avatar = document.getElementById("message-modal-avatar");
    const body = document.getElementById("message-modal-body");
    if (titleName) titleName.textContent = "Loading…";
    if (titleSub) titleSub.textContent = "";
    if (avatar) {
      avatar.textContent = "…";
      avatar.removeAttribute("style");
    }
    if (body) body.innerHTML = '<p class="muted">Fetching message details…</p>';
  }

  function isBroadcastTarget(toId) {
    if (toId === null || toId === undefined || toId === "") return false;
    const s = String(toId);
    return s === "^all" || s === "4294967295" || s === "!ffffffff";
  }

  // Renders an inline avatar + name pill for a message party (sender/recipient).
  function renderMsgParty(party) {
    const avatarText = party.avatarText || "?";
    const avCls = avatarLength(avatarText) >= 4 ? "avatar avatar--small" : "avatar";
    const style = buildAvatarStyle(party.avatar_bg, party.avatar_fg);
    const inner =
      `<span class="${avCls}"${style} aria-hidden="true">${escapeHtml(avatarText)}</span>` +
      `<span class="msg-party__name">${escapeHtml(party.name)}</span>`;
    if (party.node_id) {
      return `<button type="button" class="msg-party msg-party--link" ` +
        `data-node-id="${escapeHtml(String(party.node_id))}">${inner}</button>`;
    }
    const extra = party.broadcast ? " msg-party--broadcast" : "";
    return `<span class="msg-party${extra}">${inner}</span>`;
  }

  function senderParty(message) {
    return {
      node_id: message.from_id || null,
      name: message.long_name || message.short_name || message.from_id || "Unknown node",
      avatarText: avatarTextFor(message.short_name, message.long_name, message.from_id),
      avatar_bg: message.avatar_bg,
      avatar_fg: message.avatar_fg,
    };
  }

  function recipientParty(message) {
    if (isBroadcastTarget(message.to_id)) {
      return { broadcast: true, name: "Everyone", avatarText: "ALL" };
    }
    const node = message.to_node;
    if (node) {
      return {
        node_id: node.node_id || null,
        name: node.long_name || node.short_name || node.node_id || "Unknown node",
        avatarText: avatarTextFor(node.short_name, node.long_name, node.node_id),
        avatar_bg: node.avatar_bg,
        avatar_fg: node.avatar_fg,
      };
    }
    if (message.to_id !== null && message.to_id !== undefined && message.to_id !== "") {
      return {
        name: String(message.to_id),
        avatarText: avatarTextFor(null, null, String(message.to_id)),
      };
    }
    return null;
  }

  function renderReplyQuote(message) {
    if (message.reply_id === null || message.reply_id === undefined) return "";
    const parent = message.reply_to;
    if (parent) {
      const parentName = parent.long_name || parent.short_name || parent.from_id || "Unknown node";
      const parentText = parent.text || "(no text content)";
      return `<button type="button" class="msg-quote" title="Go to this message" ` +
        `data-message-id="${escapeHtml(String(parent.id))}">` +
        `<span class="msg-quote__bar" aria-hidden="true"></span>` +
        `<span class="msg-quote__main">` +
        `<span class="msg-quote__label">In reply to ${escapeHtml(parentName)}</span>` +
        `<span class="msg-quote__text">${escapeHtml(parentText)}</span>` +
        `</span>` +
        `<span class="msg-quote__go" aria-hidden="true">↗</span>` +
        `</button>`;
    }
    return `<div class="msg-quote msg-quote--missing">` +
      `<span class="msg-quote__bar" aria-hidden="true"></span>` +
      `<span class="msg-quote__main">` +
      `<span class="msg-quote__label">In reply to packet #${escapeHtml(String(message.reply_id))}</span>` +
      `<span class="msg-quote__text">Original message not in stored history</span>` +
      `</span>` +
      `</div>`;
  }

  function renderMsgContent(message) {
    if (message.is_tapback) {
      return `<div class="msg-content msg-content--tapback">` +
        `<span class="msg-content__kind">Reacted with</span>` +
        `<span class="msg-content__emoji">${escapeHtml(message.text || "·")}</span>` +
        `</div>`;
    }
    const text = message.text;
    const textHtml = text
      ? `<div class="msg-content__text">${escapeHtml(text)}</div>`
      : `<div class="msg-content__text msg-content__text--empty">No text payload</div>`;
    return `<div class="msg-content">` + textHtml + `</div>`;
  }

  function renderMessageDetail(message) {
    const avatar = document.getElementById("message-modal-avatar");
    const titleName = document.getElementById("message-modal-name");
    const titleSub = document.getElementById("message-modal-sub");
    const body = document.getElementById("message-modal-body");

    const sender = senderParty(message);

    if (titleName) titleName.textContent = messageKind(message);
    if (titleSub) titleSub.textContent = "Message #" + (message.id ?? "");
    if (avatar) {
      avatar.textContent = sender.avatarText;
      avatar.className = avatarLength(sender.avatarText) >= 4 ? "avatar avatar--small" : "avatar";
      const style = [];
      if (message.avatar_bg) style.push(`background: ${message.avatar_bg}`);
      if (message.avatar_fg) style.push(`color: ${message.avatar_fg}`);
      if (style.length) avatar.setAttribute("style", style.join("; "));
      else avatar.removeAttribute("style");
    }

    const decoded = message.decoded || {};
    const raw = message.raw || {};

    let channelLabel;
    if (message.channel_index !== null && message.channel_index !== undefined) {
      channelLabel = `Channel ${message.channel_index}`;
    } else if (message.channel_key && message.channel_key !== "unknown") {
      channelLabel = `Key ${message.channel_key}`;
    } else {
      channelLabel = "Unknown channel";
    }

    const recipient = recipientParty(message);
    const routeHtml = `<div class="msg-route">` +
      renderMsgParty(sender) +
      (recipient
        ? `<span class="msg-route__arrow" aria-hidden="true">→</span>` + renderMsgParty(recipient)
        : "") +
      `</div>`;

    const captionParts = [];
    if (channelLabel) captionParts.push(channelLabel);
    const received = formatTimeWithRelative(message.rx_time);
    if (received && received !== "-") captionParts.push(received);
    const captionHtml = captionParts.length
      ? `<div class="msg-caption">${captionParts.map(escapeHtml).join(" · ")}</div>`
      : "";

    const convo = `<div class="msg-convo">` +
      routeHtml +
      renderReplyQuote(message) +
      renderMsgContent(message) +
      captionHtml +
      `</div>`;

    const routing =
      row("Hops away", formatValue(message.hops)) +
      row("Hop start", formatValue(raw.hopStart)) +
      row("Hop limit", formatValue(raw.hopLimit)) +
      row("Relay node", formatValue(raw.relayNode)) +
      row("Transport", raw.transportMechanism) +
      row("Priority", raw.priority);

    const signal =
      row("RSSI", formatValueUnit(message.rx_rssi, "dBm")) +
      row("SNR", formatValueUnit(message.rx_snr, "dB"));

    const packet =
      row("Mesh packet id", message.packet_id) +
      row("Port", message.portnum || decoded.portnum) +
      row("Bitfield", formatValue(decoded.bitfield)) +
      row("Reply-to packet id", message.reply_id) +
      row("Payload (hex)", decoded.payload);

    const sections = [
      ["Routing", routing],
      ["Signal", signal],
      ["Packet", packet],
    ];

    if (body) {
      body.innerHTML = convo + renderSections(sections);
    }
  }

  async function showMessageModal(messageId) {
    if (messageId === null || messageId === undefined || messageId === "") return;
    openModal(document.getElementById("message-modal"));
    setMessageModalLoading();
    try {
      const response = await fetch(`/api/message/${encodeURIComponent(messageId)}`, { cache: "no-store" });
      const titleName = document.getElementById("message-modal-name");
      const body = document.getElementById("message-modal-body");
      if (!response.ok) {
        if (titleName) titleName.textContent = "Message";
        if (body) {
          body.innerHTML = response.status === 404
            ? '<p class="muted">No record for this message.</p>'
            : `<p class="muted">Failed to load message (${response.status}).</p>`;
        }
        return;
      }
      const payload = await response.json();
      if (payload && payload.message) renderMessageDetail(payload.message);
    } catch (err) {
      const body = document.getElementById("message-modal-body");
      if (body) body.innerHTML = '<p class="muted">Network error loading message.</p>';
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    const modals = [
      document.getElementById("node-modal"),
      document.getElementById("message-modal"),
    ];
    modals.forEach(function (modal) {
      if (!modal) return;
      modal.addEventListener("click", function (event) {
        if (event.target === modal) closeModal(modal);
      });
      const close = modal.querySelector(".modal__close");
      if (close) close.addEventListener("click", function () { closeModal(modal); });
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && openModalEl) closeModal(openModalEl);
    });

    // Delegated click handling so dynamically-rendered rows work too
    document.addEventListener("click", function (event) {
      const nodeRef = event.target.closest(".node-button, .msg-party--link");
      if (nodeRef) {
        const id = nodeRef.dataset.nodeId;
        if (id) {
          event.preventDefault();
          showNodeModal(id);
        }
        return;
      }
      const quote = event.target.closest(".msg-quote[data-message-id]");
      if (quote) {
        event.preventDefault();
        showMessageModal(quote.dataset.messageId);
        return;
      }
      const bubble = event.target.closest(".bubble[data-message-id]");
      if (bubble) {
        event.preventDefault();
        showMessageModal(bubble.dataset.messageId);
      }
    });

    // Keyboard activation for focused message bubbles
    document.addEventListener("keydown", function (event) {
      if (event.key !== "Enter" && event.key !== " ") return;
      const target = event.target;
      if (target && target.classList && target.classList.contains("bubble") && target.dataset.messageId) {
        event.preventDefault();
        showMessageModal(target.dataset.messageId);
      }
    });
  });

  // Expose helpers used by page templates
  window.MeshApp = {
    formatTime,
    formatMessageTime,
    formatRelative,
    formatTimeWithRelative,
    formatValue,
    formatValueUnit,
    escapeHtml,
    renderNodeButton,
    renderBattery,
    batteryInfo,
    showNodeModal,
    showMessageModal,
    isLive,
    LIVE_THRESHOLD_SECONDS,
  };
})();
