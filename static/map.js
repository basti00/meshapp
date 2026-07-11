(function () {
  "use strict";

  // MeshMap: reusable Leaflet view of node positions, used by the nodes page
  // (big split-view map) and the location modal (small windowed map). Markers
  // are the node's avatar circle with the name underneath; the highlighted
  // node additionally shows its position uncertainty as a low-contrast circle
  // (see _position_uncertainty_m in main.py for where the radius comes from).
  //
  // create(container, opts) -> view object. opts:
  //   onHover(nodeId|null)  hover enters/leaves a marker (not sent in fixed mode)
  //   onSelect(nodeId)      marker clicked
  //   fixed: {node, position}  lock the highlight to this node at this exact
  //          position (location modal); hover no longer moves the highlight.
  // Tiles come from openstreetmap.org, so they need internet in the browser;
  // without it the markers still lay out on a blank background.

  function nodeLatLng(position) {
    if (!position) return null;
    const lat = Number(position.latitude);
    const lon = Number(position.longitude);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;
    return [lat, lon];
  }

  function markerIcon(node) {
    const A = window.MeshApp;
    const name = node.long_name || node.short_name || node.node_id || "?";
    const html =
      A.renderAvatar({
        node_id: node.node_id,
        short_name: node.short_name,
        long_name: node.long_name,
        avatar_bg: node.avatar_bg,
        avatar_fg: node.avatar_fg,
      }) +
      `<span class="map-marker__name">${A.escapeHtml(name)}</span>`;
    return window.L.divIcon({
      className: "map-marker",
      html,
      iconSize: [32, 32],
      iconAnchor: [16, 16],
    });
  }

  function create(container, opts) {
    opts = opts || {};
    const L = window.L;
    if (!L) {
      container.innerHTML = '<p class="muted">Map library not available.</p>';
      return null;
    }

    const map = L.map(container, {
      worldCopyJump: true,
      zoomControl: true,
    });
    map.setView([20, 0], 2);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>',
    }).addTo(map);

    const markers = new Map(); // node_id -> { marker, node }
    let highlightId = null;
    let uncertaintyCircle = null;
    const fixedId = opts.fixed ? opts.fixed.node.node_id : null;

    // Chrome fires mouseover on markers sliding under a *stationary* cursor
    // while the map animates or is dragged; only real pointer movement since
    // the last map movement may move the highlight. mousemove re-arms --
    // markers additionally hover-start from their own mousemove because on
    // entry mouseover precedes the (re-arming) mousemove and would be
    // swallowed right after an animation.
    let hoverArmed = true;
    map.on("movestart zoomstart", function () { hoverArmed = false; });
    container.addEventListener("mousemove", function () { hoverArmed = true; });

    function positionOf(nodeId) {
      if (opts.fixed && nodeId === fixedId) return opts.fixed.position;
      const entry = markers.get(nodeId);
      return entry ? entry.node.position : null;
    }

    function removeCircle() {
      if (uncertaintyCircle) {
        uncertaintyCircle.remove();
        uncertaintyCircle = null;
      }
    }

    function applyHighlight(nodeId) {
      if (highlightId === nodeId) return;
      if (highlightId !== null) {
        const prev = markers.get(highlightId);
        if (prev) {
          const el = prev.marker.getElement();
          if (el) el.classList.remove("map-marker--active");
          prev.marker.setZIndexOffset(0);
        }
        removeCircle();
      }
      highlightId = nodeId;
      if (nodeId === null) return;
      const entry = markers.get(nodeId);
      if (!entry) { highlightId = null; return; }
      const el = entry.marker.getElement();
      if (el) el.classList.add("map-marker--active");
      entry.marker.setZIndexOffset(1000);
      const position = positionOf(nodeId);
      const latlng = nodeLatLng(position);
      const radius = position ? Number(position.uncertainty_m) : NaN;
      if (latlng && Number.isFinite(radius) && radius > 0) {
        uncertaintyCircle = L.circle(latlng, {
          radius,
          className: "map-uncertainty",
          interactive: false,
        }).addTo(map);
      }
    }

    function bindMarker(marker, node) {
      marker.on("click", function () {
        if (opts.onSelect) opts.onSelect(node.node_id);
      });
      if (fixedId === null) {
        const hoverStart = function () {
          if (highlightId === node.node_id) return;
          applyHighlight(node.node_id);
          if (opts.onHover) opts.onHover(node.node_id);
        };
        marker.on("mouseover", function () {
          if (hoverArmed) hoverStart();
        });
        marker.on("mousemove", function () {
          hoverArmed = true;
          hoverStart();
        });
        marker.on("mouseout", function () {
          if (highlightId !== node.node_id) return;
          applyHighlight(null);
          if (opts.onHover) opts.onHover(null);
        });
      }
    }

    // Replace/refresh the plotted node set. Existing markers are moved rather
    // than recreated so the periodic refresh doesn't flicker or drop an
    // in-progress hover; the view is never re-fitted here.
    function setNodes(nodes) {
      const seen = new Set();
      (nodes || []).forEach(function (node) {
        // In fixed mode the pinned marker shows the exact coordinates that
        // were clicked (possibly an old frame), not the node's latest fix.
        if (opts.fixed && node.node_id === fixedId) return;
        const latlng = nodeLatLng(node.position);
        if (!latlng || !node.node_id) return;
        seen.add(node.node_id);
        const existing = markers.get(node.node_id);
        if (existing) {
          existing.marker.setLatLng(latlng);
          existing.node = node;
        } else {
          const marker = L.marker(latlng, { icon: markerIcon(node), keyboard: false });
          marker.addTo(map);
          bindMarker(marker, node);
          markers.set(node.node_id, { marker, node });
        }
      });
      markers.forEach(function (entry, nodeId) {
        if (!seen.has(nodeId) && nodeId !== fixedId) {
          if (highlightId === nodeId) applyHighlight(null);
          entry.marker.remove();
          markers.delete(nodeId);
        }
      });
    }

    // Pan (keeping zoom) only when the node sits outside the current view;
    // the slight inward padding stops border-hugging markers counting as
    // visible while their name label is clipped.
    function ensureVisible(nodeId) {
      const latlng = nodeLatLng(positionOf(nodeId));
      if (!latlng) return;
      if (map.getBounds().pad(-0.08).contains(latlng)) return;
      map.flyTo(latlng, map.getZoom(), { duration: 0.8 });
    }

    // Initial view: frame the main cluster rather than every last node -- a
    // single far-away node (wrong fix, traveller) would otherwise zoom the
    // whole mesh out to a blob. Points beyond 8x the median distance from
    // the median centre (and beyond a 25 km floor) are treated as outliers.
    function fitAll() {
      const points = [];
      markers.forEach(function (entry) {
        points.push(entry.marker.getLatLng());
      });
      if (!points.length) return;
      const median = function (values) {
        const s = values.slice().sort(function (a, b) { return a - b; });
        return s[Math.floor(s.length / 2)];
      };
      const center = L.latLng(
        median(points.map(function (p) { return p.lat; })),
        median(points.map(function (p) { return p.lng; }))
      );
      const distances = points.map(function (p) { return center.distanceTo(p); });
      const cutoff = Math.max(25000, median(distances) * 8);
      const kept = points.filter(function (p, i) { return distances[i] <= cutoff; });
      map.fitBounds(L.latLngBounds(kept.length ? kept : points), {
        padding: [40, 40],
        maxZoom: 14,
      });
    }

    // Location modal: center on the pinned node, zoomed out just enough to
    // show the whole uncertainty circle when there is one.
    function focusFixed() {
      if (!opts.fixed) return;
      const latlng = nodeLatLng(opts.fixed.position);
      if (!latlng) return;
      const radius = Number(opts.fixed.position.uncertainty_m);
      if (Number.isFinite(radius) && radius > 0) {
        map.fitBounds(L.latLng(latlng).toBounds(radius * 2.6), { maxZoom: 16 });
      } else {
        map.setView(latlng, 14);
      }
    }

    if (opts.fixed) {
      const latlng = nodeLatLng(opts.fixed.position);
      if (latlng) {
        const marker = L.marker(latlng, { icon: markerIcon(opts.fixed.node), keyboard: false });
        marker.addTo(map);
        bindMarker(marker, opts.fixed.node);
        markers.set(fixedId, { marker, node: opts.fixed.node });
        applyHighlight(fixedId);
        focusFixed();
      }
    }

    return {
      setNodes,
      fitAll,
      ensureVisible,
      focusFixed,
      hasNode: function (nodeId) { return markers.has(nodeId); },
      setHighlight: function (nodeId) {
        if (fixedId !== null) return;
        applyHighlight(nodeId);
      },
      clearHighlight: function () {
        if (fixedId !== null) return;
        applyHighlight(null);
      },
      invalidateSize: function () { map.invalidateSize(); },
      remove: function () { map.remove(); },
    };
  }

  window.MeshMap = { create };
})();
