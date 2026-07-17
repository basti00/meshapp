(function () {
  "use strict";
  // Stats page: three synced bar charts (online nodes / messages / frames)
  // over a lazily extending, scrollable time axis, plus per-node donut
  // charts. Data comes from /api/stats/* which reads the materialised
  // stats_blocks table; buckets arrive dense (zero-filled) and ascending.
  const M = window.MeshApp;
  const rootEl = document.querySelector(".stats-page");
  if (!rootEl || !M) return;

  const HOUR = 3600;
  const DAY = 86400;
  // Client UTC offset (east positive). The server snaps it to whole hours so
  // buckets stay aligned with the 1h stats blocks.
  const TZ = -(new Date().getTimezoneOffset()) * 60;
  const RANGES = { "3d": 3 * DAY, week: 7 * DAY, month: 30 * DAY, year: 365 * DAY };
  const RANGE_LABELS = {
    "3d": "last 3 days",
    week: "last 7 days",
    month: "last 30 days",
    year: "last 365 days",
  };
  // Bars are whole multiples of the 3h base bar, doubling until the selected
  // range fits the viewport at a sane bar pitch.
  const BUCKET_LADDER = [3, 6, 12, 24, 48, 96, 192, 384].map((h) => h * HOUR);
  const MIN_PITCH = 14; // px per bucket (bar + gap) before switching to a coarser bucket
  const MAX_PITCH = 40;
  const MAX_BAR_WIDTH = 24; // mark spec: bars never thicker than this, the rest is air
  const BAR_GAP = 2; // surface gap between adjacent bars
  const PLOT_HEIGHT = 160; // px, must match .chart CSS
  const RANGE_STORE_KEY = "meshapp.statsRange";
  const FRAME_TYPE_LABELS = {
    telemetry: "Telemetry",
    position: "Position",
    nodeinfo: "Node info",
    other: "Other",
  };

  const rangeSelect = document.getElementById("stats-range");
  const tooltip = document.getElementById("chart-tooltip");
  const tiles = {
    nodes: document.getElementById("stat-nodes"),
    messages: document.getElementById("stat-messages"),
    frames: document.getElementById("stat-frames"),
  };

  const METRICS = [
    {
      key: "online", title: "Online nodes", modal: "nodes",
      el: document.getElementById("chart-online"),
      unit: (v) => (v === 1 ? "node online" : "nodes online"),
    },
    {
      key: "messages", title: "Messages", modal: "messages",
      el: document.getElementById("chart-messages"),
      unit: (v) => (v === 1 ? "message" : "messages"),
    },
    {
      key: "frames", title: "Frames", modal: "frames",
      el: document.getElementById("chart-frames"),
      unit: (v) => (v === 1 ? "frame" : "frames"),
    },
  ];

  const state = {
    range: "week",
    bucket: 3 * HOUR,
    pitch: 16,
    buckets: [], // dense, ascending {start, online, messages, frames}
    dataStart: null,
    end: 0, // exclusive end of the newest bucket
    reachedStart: false,
    loadingOlder: false,
    seq: 0, // fetch generation; stale responses are dropped
  };

  // ---------- Formatting ----------

  function pad(n) { return n < 10 ? "0" + n : String(n); }
  function fmtDate(d) { return `${pad(d.getDate())}.${pad(d.getMonth() + 1)}.`; }
  function fmtDateYear(d) { return `${pad(d.getDate())}.${pad(d.getMonth() + 1)}.${d.getFullYear()}`; }
  function fmtHM(d) { return `${pad(d.getHours())}:${pad(d.getMinutes())}`; }
  function fmtFull(d) { return `${fmtDateYear(d)} ${fmtHM(d)}`; }
  function isMidnight(d) { return d.getHours() === 0 && d.getMinutes() === 0; }

  // Human label for one bucket's [start, end) — modal titles and tooltips.
  function bucketRangeText(start, end) {
    const a = new Date(start * 1000);
    const b = new Date(end * 1000);
    if (isMidnight(a) && isMidnight(b)) {
      const last = new Date((end - DAY) * 1000);
      if (a.toDateString() === last.toDateString()) return fmtDateYear(a);
      return `${fmtDateYear(a)} – ${fmtDateYear(last)}`;
    }
    if (a.toDateString() === b.toDateString()) return `${fmtDateYear(a)} ${fmtHM(a)} – ${fmtHM(b)}`;
    return `${fmtFull(a)} – ${fmtFull(b)}`;
  }

  function xAxisLabel(start) {
    const d = new Date(start * 1000);
    if (state.bucket < DAY) return isMidnight(d) ? fmtDate(d) : fmtHM(d);
    return fmtDate(d);
  }

  function bucketSizeLabel(bucket) {
    return bucket < DAY ? `${bucket / HOUR}h per bar` : `${bucket / DAY}d per bar`;
  }

  // ---------- Fetching ----------

  async function fetchJson(url) {
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }

  function fetchSeries(before, limit) {
    const params = new URLSearchParams({
      bucket: String(state.bucket),
      tz: String(TZ),
      limit: String(limit || 240),
    });
    if (before !== null && before !== undefined) params.set("before", String(before));
    return fetchJson(`/api/stats/series?${params}`);
  }

  function fetchRangeNodes(start, end) {
    return fetchJson(`/api/stats/range/nodes?start=${start}&end=${end}`);
  }

  // ---------- Chart skeletons ----------

  function initChart(metric) {
    metric.el.innerHTML =
      '<div class="chart__plot">' +
      '<div class="chart__hline chart__hline--top"></div>' +
      '<div class="chart__hline chart__hline--mid"></div>' +
      '<div class="chart__hline chart__hline--base"></div>' +
      '<div class="chart__scroll" tabindex="-1"><div class="chart__track"></div></div>' +
      '<span class="chart__ylabel chart__ylabel--top"></span>' +
      '<span class="chart__ylabel chart__ylabel--mid"></span>' +
      "</div>";
    metric.scroll = metric.el.querySelector(".chart__scroll");
    metric.track = metric.el.querySelector(".chart__track");
    metric.yTop = metric.el.querySelector(".chart__ylabel--top");
    metric.yMid = metric.el.querySelector(".chart__ylabel--mid");
    metric.midLine = metric.el.querySelector(".chart__hline--mid");

    metric.scroll.addEventListener("scroll", function () { onScroll(metric.scroll); }, { passive: true });
    metric.track.addEventListener("click", function (event) {
      const bar = event.target.closest(".chart-bar");
      if (bar) openBucketModal(metric, Number(bar.dataset.i));
    });
    metric.track.addEventListener("pointerover", function (event) {
      const bar = event.target.closest(".chart-bar");
      if (bar) showBarTooltip(metric, bar);
    });
    metric.track.addEventListener("pointerout", function (event) {
      if (event.target.closest(".chart-bar")) hideTooltip();
    });
    metric.track.addEventListener("focusin", function (event) {
      const bar = event.target.closest(".chart-bar");
      if (bar) showBarTooltip(metric, bar);
    });
    metric.track.addEventListener("focusout", hideTooltip);
  }

  // ---------- Rendering ----------

  function renderAll() {
    const n = state.buckets.length;
    const pitch = state.pitch;
    const width = n * pitch;
    const barWidth = Math.min(pitch - BAR_GAP, MAX_BAR_WIDTH);
    const inset = Math.max(1, Math.round((pitch - barWidth) / 2));
    // Label roughly every 64px, but for sub-day buckets snap the step so the
    // labelled interval divides (or is a whole multiple of) a day — otherwise
    // ticks drift through the clock (00:00, 18:00, 12:00, …) and stop being
    // readable.
    let labelStep = Math.max(1, Math.ceil(64 / pitch));
    if (state.bucket < DAY) {
      while (DAY % (labelStep * state.bucket) !== 0 && (labelStep * state.bucket) % DAY !== 0) {
        labelStep++;
      }
    }
    METRICS.forEach(function (metric) {
      let html = "";
      for (let i = 0; i < n; i++) {
        const b = state.buckets[i];
        const value = b[metric.key] || 0;
        html +=
          `<button type="button" class="chart-bar" data-i="${i}" ` +
          `style="left:${i * pitch}px;width:${pitch}px" ` +
          `aria-label="${value} ${metric.unit(value)}, ${bucketRangeText(b.start, b.start + state.bucket)}">` +
          `<span class="chart-bar__fill" style="--v:${value};left:${inset}px;width:${barWidth}px"></span>` +
          "</button>";
        const absIndex = Math.round((b.start + TZ) / state.bucket);
        if (absIndex % labelStep === 0) {
          html += `<span class="chart__xlabel" style="left:${i * pitch}px">${xAxisLabel(b.start)}</span>`;
        }
      }
      if (!n) html = '<p class="chart__empty muted">No activity recorded yet.</p>';
      metric.track.style.width = n ? width + "px" : "";
      metric.track.innerHTML = html;
    });
    document.querySelectorAll("[data-bucket-label]").forEach(function (el) {
      el.textContent = state.buckets.length ? bucketSizeLabel(state.bucket) : "";
    });
  }

  // Y scale follows the visible window: nice max over the buckets currently
  // on screen, one top and one optional mid gridline (mid only when it is a
  // whole number). Bars rescale via a per-track --scale custom property.
  function niceCeil(value) {
    if (value <= 1) return 1;
    const mag = Math.pow(10, Math.floor(Math.log10(value)));
    for (const f of [1, 2, 5, 10]) {
      if (f * mag >= value) return f * mag;
    }
    return 10 * mag;
  }

  function updateYScale() {
    if (!state.buckets.length) return;
    const scroll = METRICS[0].scroll;
    const first = Math.max(0, Math.floor(scroll.scrollLeft / state.pitch));
    const last = Math.min(
      state.buckets.length,
      Math.ceil((scroll.scrollLeft + scroll.clientWidth) / state.pitch) + 1
    );
    METRICS.forEach(function (metric) {
      let max = 0;
      for (let i = first; i < last; i++) {
        const v = state.buckets[i][metric.key] || 0;
        if (v > max) max = v;
      }
      const yMax = niceCeil(max * 1.05);
      metric.track.style.setProperty("--scale", String(PLOT_HEIGHT / yMax));
      metric.yTop.textContent = yMax.toLocaleString();
      const mid = yMax / 2;
      const showMid = Number.isInteger(mid);
      metric.yMid.textContent = showMid ? mid.toLocaleString() : "";
      metric.midLine.style.display = showMid ? "" : "none";
    });
  }

  let yScaleQueued = false;
  function scheduleYScale() {
    if (yScaleQueued) return;
    yScaleQueued = true;
    requestAnimationFrame(function () {
      yScaleQueued = false;
      updateYScale();
    });
  }

  // ---------- Scrolling: sync + lazy history ----------

  let syncingScroll = false;
  function onScroll(source) {
    if (syncingScroll) return;
    syncingScroll = true;
    METRICS.forEach(function (metric) {
      if (metric.scroll !== source) metric.scroll.scrollLeft = source.scrollLeft;
    });
    syncingScroll = false;
    hideTooltip();
    scheduleYScale();
    if (source.scrollLeft < 200) loadOlder();
  }

  function setScrollLeft(px) {
    syncingScroll = true;
    METRICS.forEach(function (metric) { metric.scroll.scrollLeft = px; });
    syncingScroll = false;
  }

  async function loadOlder() {
    if (state.loadingOlder || state.reachedStart || !state.buckets.length) return;
    state.loadingOlder = true;
    const seq = state.seq;
    try {
      const payload = await fetchSeries(state.buckets[0].start, 240);
      if (seq !== state.seq) return;
      let older = payload.buckets || [];
      if (payload.data_start === null || payload.data_start === undefined) {
        older = [];
      } else {
        older = older.filter(function (b) { return b.start + state.bucket > payload.data_start; });
      }
      if (!older.length) {
        state.reachedStart = true;
        return;
      }
      if (older[0].start <= payload.data_start) state.reachedStart = true;
      const keepScroll = METRICS[0].scroll.scrollLeft;
      state.buckets = older.concat(state.buckets);
      renderAll();
      setScrollLeft(keepScroll + older.length * state.pitch);
      updateYScale();
    } catch (err) {
      state.reachedStart = true; // stop hammering a failing endpoint
    } finally {
      state.loadingOlder = false;
    }
  }

  // ---------- Bucket sizing ----------

  function computeBucket() {
    const width = METRICS[0].scroll.clientWidth || rootEl.clientWidth || 600;
    const rangeSeconds = RANGES[state.range];
    const maxBars = Math.max(8, Math.floor(width / MIN_PITCH));
    let bucket = BUCKET_LADDER[BUCKET_LADDER.length - 1];
    for (const candidate of BUCKET_LADDER) {
      if (rangeSeconds / candidate <= maxBars) { bucket = candidate; break; }
    }
    state.bucket = bucket;
    const bars = Math.ceil(rangeSeconds / bucket);
    state.pitch = Math.max(MIN_PITCH, Math.min(MAX_PITCH, Math.floor(width / bars)));
  }

  // ---------- Page load / range change ----------

  async function reload() {
    const seq = ++state.seq;
    state.reachedStart = false;
    hideTooltip();
    document.querySelectorAll(".stats-card").forEach(function (card) {
      card.classList.add("stats-card--loading");
    });
    computeBucket();
    try {
      const rangeSeconds = RANGES[state.range];
      // Fetch enough buckets to cover both the selected range and the
      // viewport width — a wide screen with a capped pitch must still get a
      // scrollable track, or older history could never lazy-load.
      const viewportBuckets = Math.ceil((METRICS[0].scroll.clientWidth || 600) / state.pitch);
      const limit = Math.min(
        400,
        Math.max(90, Math.ceil(rangeSeconds / state.bucket) + 10, viewportBuckets + 10)
      );
      const series = await fetchSeries(null, limit);
      if (seq !== state.seq) return;
      state.bucket = series.bucket;
      state.end = series.end;
      state.dataStart = series.data_start;
      let buckets = series.buckets || [];
      if (series.data_start === null || series.data_start === undefined) {
        buckets = [];
      } else {
        buckets = buckets.filter(function (b) { return b.start + series.bucket > series.data_start; });
      }
      state.reachedStart =
        series.data_start === null || series.data_start === undefined ||
        (buckets.length > 0 && buckets[0].start <= series.data_start);
      state.buckets = buckets;
      renderAll();
      setScrollLeft(METRICS[0].scroll.scrollWidth);
      updateYScale();

      const nodesPayload = await fetchRangeNodes(state.end - rangeSeconds, state.end);
      if (seq !== state.seq) return;
      applyRangeData(nodesPayload.nodes || []);
    } catch (err) {
      METRICS.forEach(function (metric) {
        metric.track.innerHTML = '<p class="chart__empty muted">Failed to load stats.</p>';
      });
    } finally {
      if (seq === state.seq) {
        document.querySelectorAll(".stats-card").forEach(function (card) {
          card.classList.remove("stats-card--loading");
        });
      }
    }
  }

  // ---------- Tiles + pies (per-node data for the selected range) ----------

  function applyRangeData(nodes) {
    let messagesTotal = 0;
    let framesTotal = 0;
    nodes.forEach(function (n) {
      messagesTotal += n.messages || 0;
      framesTotal += n.frames || 0;
    });
    tiles.nodes.textContent = nodes.length.toLocaleString();
    tiles.messages.textContent = messagesTotal.toLocaleString();
    tiles.frames.textContent = framesTotal.toLocaleString();
    document.querySelectorAll("[data-range-label]").forEach(function (el) {
      el.textContent = RANGE_LABELS[state.range];
    });
    renderPie(document.getElementById("pie-messages"), nodes, "messages");
    renderPie(document.getElementById("pie-frames"), nodes, "frames");
  }

  const PIE_SLICES = 5; // top N nodes, the rest folds into "Other"

  function pieArcPath(cx, cy, rOuter, rInner, a0, a1) {
    const x0o = cx + rOuter * Math.cos(a0), y0o = cy + rOuter * Math.sin(a0);
    const x1o = cx + rOuter * Math.cos(a1), y1o = cy + rOuter * Math.sin(a1);
    const x0i = cx + rInner * Math.cos(a0), y0i = cy + rInner * Math.sin(a0);
    const x1i = cx + rInner * Math.cos(a1), y1i = cy + rInner * Math.sin(a1);
    const large = a1 - a0 > Math.PI ? 1 : 0;
    return `M ${x0o} ${y0o} A ${rOuter} ${rOuter} 0 ${large} 1 ${x1o} ${y1o} ` +
      `L ${x1i} ${y1i} A ${rInner} ${rInner} 0 ${large} 0 ${x0i} ${y0i} Z`;
  }

  function renderPie(container, nodes, key) {
    const unit = key === "messages" ? "messages" : "frames";
    const entries = nodes
      .filter(function (n) { return (n[key] || 0) > 0; })
      .sort(function (a, b) { return (b[key] || 0) - (a[key] || 0); });
    const total = entries.reduce(function (sum, n) { return sum + n[key]; }, 0);
    if (!total) {
      container.innerHTML = `<p class="muted pie__empty">No ${unit} in this range.</p>`;
      return;
    }
    const top = entries.slice(0, PIE_SLICES);
    const rest = entries.slice(PIE_SLICES);
    const slices = top.map(function (node, i) {
      return {
        name: node.long_name || node.short_name || node.node_id || "?",
        value: node[key],
        nodeId: node.node_id,
        color: `var(--series-${i + 1})`,
      };
    });
    if (rest.length) {
      slices.push({
        name: `Other (${rest.length} ${rest.length === 1 ? "node" : "nodes"})`,
        value: rest.reduce(function (sum, n) { return sum + n[key]; }, 0),
        nodeId: null,
        color: "var(--series-other)",
      });
    }

    let svg = "";
    if (slices.length === 1) {
      svg = `<circle cx="60" cy="60" r="44" fill="none" stroke-width="20" ` +
        `style="stroke:${slices[0].color}" data-slice="0"></circle>`;
    } else {
      let angle = -Math.PI / 2;
      svg = slices.map(function (slice, i) {
        const sweep = (slice.value / total) * 2 * Math.PI;
        const path = pieArcPath(60, 60, 54, 34, angle, angle + sweep);
        angle += sweep;
        return `<path d="${path}" style="fill:${slice.color}" data-slice="${i}"` +
          (slice.nodeId ? ` data-node-id="${M.escapeHtml(slice.nodeId)}"` : "") +
          `></path>`;
      }).join("");
    }

    const legend = slices.map(function (slice, i) {
      const pct = Math.round((slice.value / total) * 1000) / 10;
      const inner =
        `<span class="pie-legend__swatch" style="background:${slice.color}" aria-hidden="true"></span>` +
        `<span class="pie-legend__name">${M.escapeHtml(slice.name)}</span>` +
        `<span class="pie-legend__value">${slice.value.toLocaleString()} · ${pct}%</span>`;
      if (slice.nodeId) {
        return `<button type="button" class="pie-legend__row" data-slice="${i}" ` +
          `data-node-id="${M.escapeHtml(slice.nodeId)}">${inner}</button>`;
      }
      return `<span class="pie-legend__row pie-legend__row--static" data-slice="${i}">${inner}</span>`;
    }).join("");

    container.innerHTML =
      '<div class="pie__figure">' +
      `<svg viewBox="0 0 120 120" role="img" aria-label="${unit} by node">${svg}</svg>` +
      '<div class="pie__center">' +
      `<span class="pie__total">${total.toLocaleString()}</span>` +
      `<span class="pie__unit muted">${unit}</span>` +
      "</div></div>" +
      `<div class="pie-legend">${legend}</div>`;
    container._slices = slices;
    container._total = total;
  }

  function initPieInteraction(container) {
    container.addEventListener("click", function (event) {
      const target = event.target.closest("[data-node-id]");
      if (target) M.showNodeModal(target.dataset.nodeId);
    });
    container.addEventListener("pointerover", function (event) {
      const target = event.target.closest("[data-slice]");
      if (!target || !container._slices) return;
      const slice = container._slices[Number(target.dataset.slice)];
      if (!slice) return;
      const pct = Math.round((slice.value / container._total) * 1000) / 10;
      const rect = target.getBoundingClientRect();
      showTooltip(
        `${slice.value.toLocaleString()} · ${pct}%`,
        slice.name,
        rect.left + rect.width / 2,
        rect.top
      );
    });
    container.addEventListener("pointerout", function (event) {
      if (event.target.closest("[data-slice]")) hideTooltip();
    });
  }

  // ---------- Tooltip (shared, values also live in aria-labels & modals) ----------

  function showTooltip(valueText, subText, x, y) {
    tooltip.textContent = "";
    const strong = document.createElement("strong");
    strong.textContent = valueText;
    const sub = document.createElement("span");
    sub.textContent = subText;
    tooltip.appendChild(strong);
    tooltip.appendChild(sub);
    tooltip.hidden = false;
    const width = tooltip.offsetWidth;
    const clampedX = Math.min(Math.max(x, width / 2 + 6), window.innerWidth - width / 2 - 6);
    tooltip.style.left = `${clampedX}px`;
    tooltip.style.top = `${Math.max(y - 8, 8)}px`;
  }

  function hideTooltip() {
    tooltip.hidden = true;
  }

  function showBarTooltip(metric, bar) {
    const b = state.buckets[Number(bar.dataset.i)];
    if (!b) return;
    const value = b[metric.key] || 0;
    const fill = bar.querySelector(".chart-bar__fill");
    const barRect = bar.getBoundingClientRect();
    const fillRect = fill.getBoundingClientRect();
    const top = value > 0 ? fillRect.top : barRect.bottom - 24;
    showTooltip(
      `${value.toLocaleString()} ${metric.unit(value)}`,
      bucketRangeText(b.start, b.start + state.bucket),
      barRect.left + barRect.width / 2,
      top
    );
  }

  // ---------- Drill-down modal (click a bar) ----------

  function renderStatsMessageItem(m) {
    const name = m.long_name || m.short_name || m.from_id || "?";
    // The wire emoji field is sometimes just the tapback flag ("1"); the
    // reaction character then lives in text.
    const reaction = m.emoji && m.emoji !== "1" ? m.emoji : (m.text || "·");
    const text = m.is_tapback ? `reacted with ${reaction}` : (m.text || m.portnum || "(no text)");
    return `<button type="button" class="list-item" data-message-id="${m.id}">` +
      `<span class="list-item__text">` +
      M.renderAvatar({
        short_name: m.short_name, long_name: m.long_name, node_id: m.from_id,
        avatar_bg: m.avatar_bg, avatar_fg: m.avatar_fg, extraClass: "avatar--xs",
      }) +
      `<span class="list-item__name">${M.escapeHtml(name)}</span>` +
      `<span class="list-item__snippet">${M.escapeHtml(text)}</span>` +
      `</span>` +
      `<span class="list-item__meta">` +
      `<span class="pill">${M.escapeHtml(m.channel_label || "?")}</span>` +
      `<time class="list-item__time">${M.escapeHtml(M.formatMessageTime(m.rx_time))}</time>` +
      `</span></button>`;
  }

  function renderStatsFrameItem(f) {
    const name = f.long_name || f.short_name || f.node_id || "?";
    const type = FRAME_TYPE_LABELS[f.frame_type] || f.frame_type || "Frame";
    const typeCls = M.escapeHtml(f.frame_type || "other");
    return `<button type="button" class="list-item" data-frame-id="${f.id}">` +
      `<span class="list-item__text">` +
      M.renderAvatar({
        short_name: f.short_name, long_name: f.long_name, node_id: f.node_id,
        avatar_bg: f.avatar_bg, avatar_fg: f.avatar_fg, extraClass: "avatar--xs",
      }) +
      `<span class="list-item__name">${M.escapeHtml(name)}</span>` +
      `<span class="frame-tag frame-tag--${typeCls}">${M.escapeHtml(type)}</span>` +
      (f.frame_type === "other" && f.portnum
        ? `<span class="list-item__snippet">${M.escapeHtml(f.portnum)}</span>`
        : "") +
      `</span>` +
      `<span class="list-item__meta">` +
      `<time class="list-item__time">${M.escapeHtml(M.formatMessageTime(f.rx_time))}</time>` +
      `</span></button>`;
  }

  function openBucketModal(metric, index) {
    const b = state.buckets[index];
    if (!b) return;
    hideTooltip();
    const start = b.start;
    const end = b.start + state.bucket;
    const ctx = M.pushModal({});
    ctx.titleName.textContent = metric.title;
    ctx.titleSub.textContent = bucketRangeText(start, end);

    if (metric.modal === "nodes") {
      ctx.body.innerHTML =
        '<div class="modal-list modal-list--tall">' +
        '<div class="modal-list__status muted">Loading…</div></div>';
      const list = ctx.body.querySelector(".modal-list");
      fetchRangeNodes(start, end).then(function (payload) {
        const nodes = payload.nodes || [];
        if (!nodes.length) {
          list.innerHTML = '<div class="modal-list__status muted">No nodes were heard in this time frame.</div>';
          return;
        }
        list.innerHTML = nodes.map(function (node) {
          return M.renderNodeButton({
            node_id: node.node_id,
            short_name: node.short_name,
            long_name: node.long_name,
            avatar_bg: node.avatar_bg,
            avatar_fg: node.avatar_fg,
            last_seen: node.last_seen,
            subtitle: `${(node.messages || 0).toLocaleString()} messages · ${(node.frames || 0).toLocaleString()} frames`,
          });
        }).join("");
      }).catch(function () {
        list.innerHTML = '<div class="modal-list__status muted">Failed to load.</div>';
      });
      return;
    }

    const kind = metric.modal; // "messages" | "frames"
    ctx.body.innerHTML = M.lazyListHtml(kind);
    const container = ctx.body.querySelector(".modal-list");
    container.classList.add("modal-list--tall");
    M.initLazyList(
      container,
      async function (cursor) {
        const params = new URLSearchParams({ start: String(start), end: String(end) });
        if (cursor) params.set("before", cursor);
        const payload = await fetchJson(`/api/stats/range/${kind}?${params}`);
        return { items: payload[kind] || [], next_cursor: payload.next_cursor || null };
      },
      kind === "messages" ? renderStatsMessageItem : renderStatsFrameItem,
      `No ${kind} in this time frame.`
    );
  }

  // ---------- Boot ----------

  document.addEventListener("DOMContentLoaded", function () {
    let stored = null;
    try { stored = localStorage.getItem(RANGE_STORE_KEY); } catch (e) {}
    if (stored && RANGES[stored]) state.range = stored;
    rangeSelect.value = state.range;
    rangeSelect.addEventListener("change", function () {
      state.range = RANGES[rangeSelect.value] ? rangeSelect.value : "week";
      try { localStorage.setItem(RANGE_STORE_KEY, state.range); } catch (e) {}
      reload();
    });

    METRICS.forEach(initChart);
    initPieInteraction(document.getElementById("pie-messages"));
    initPieInteraction(document.getElementById("pie-frames"));
    window.addEventListener("scroll", hideTooltip, { passive: true });

    // Re-layout (bar pitch only, same data) when the viewport size changes.
    let resizeTimer = null;
    window.addEventListener("resize", function () {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(function () {
        if (!state.buckets.length) return;
        const scroll = METRICS[0].scroll;
        const atRight = scroll.scrollLeft + scroll.clientWidth >= scroll.scrollWidth - 4;
        const rangeSeconds = RANGES[state.range];
        const width = scroll.clientWidth || 600;
        const bars = Math.ceil(rangeSeconds / state.bucket);
        state.pitch = Math.max(MIN_PITCH, Math.min(MAX_PITCH, Math.floor(width / bars)));
        renderAll();
        setScrollLeft(atRight ? scroll.scrollWidth : scroll.scrollLeft);
        updateYScale();
      }, 200);
    });

    reload();
  });
})();
