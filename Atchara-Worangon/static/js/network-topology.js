/* ══════════════════════════════════════════════════════════════════════════
   Shared Network Topology helpers
   Used by BOTH the Dashboard and Devices pages so they always render the
   same set of devices and the same parent/child connection data — never
   duplicated, never invented.
   ══════════════════════════════════════════════════════════════════════════ */
(function (window) {

  // Only real network infrastructure belongs in the topology diagram:
  // Router, Core Switch, Access Switches. VPC / Client / Server rows are
  // excluded on purpose (per category + device_type, not by name).
  const NETWORK_TYPES = new Set(['Router', 'Core Device', 'Access Device']);
  const NETWORK_CATEGORIES = new Set(['Router', 'Switch']);

  function isNetworkDevice(d) {
    if (!d) return false;
    if (!NETWORK_TYPES.has(d.type)) return false;
    if (d.category && !NETWORK_CATEGORIES.has(d.category)) return false;
    return true;
  }

  function filterDevices(list) {
    return (list || []).filter(isNetworkDevice);
  }

  // Online / Offline ONLY — no "Unknown" state in this diagram.
  function statusColor(status) {
    return status === 'Online' ? '#22C55E' : '#F43F5E';
  }
  function statusLabel(status) {
    return status === 'Online' ? 'Online' : 'Offline';
  }

  // Site-wide time display format: "YYYY-MM-DD HH:MM:SS" — every page /
  // widget that shows a timestamp goes through one of the helpers below so
  // the format is identical everywhere (Dashboard, Devices, Activity Log,
  // topology details, etc).

  // Accepts either a "YYYY-MM-DD HH:MM:SS"-ish string (already Thai local
  // time from the backend, may also arrive as "YYYY-MM-DDTHH:MM:SS") or a
  // JS Date object, and normalizes it to "YYYY-MM-DD HH:MM:SS". We slice
  // strings directly instead of building a Date from them so the browser
  // never reinterprets the value in its own timezone.
  function fmtLastUpdated(s) {
    if (!s) return '-';
    if (s instanceof Date) return fmtLastUpdatedFromDate(s);
    const str = String(s).trim().replace('T', ' ');
    const [datePart, timePartRaw] = str.split(' ');
    if (!datePart) return str;
    const [y, m, d] = datePart.split('-').map(Number);
    if (!y || !m || !d) return str;
    const [hh, mm, ss] = (timePartRaw || '00:00:00').split(':');
    return `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')} `
         + `${String(hh || '00').padStart(2, '0')}:${String(mm || '00').padStart(2, '0')}:${String((ss || '00').split('.')[0]).padStart(2, '0')}`;
  }

  // Local JS Date -> "YYYY-MM-DD HH:MM:SS".
  function fmtLastUpdatedFromDate(dt) {
    const p = n => String(n).padStart(2, '0');
    return `${dt.getFullYear()}-${p(dt.getMonth() + 1)}-${p(dt.getDate())} ${p(dt.getHours())}:${p(dt.getMinutes())}:${p(dt.getSeconds())}`;
  }

  // Kept as an alias for backward compatibility with any caller that still
  // asks for "activity time" formatting — same "YYYY-MM-DD HH:MM:SS" format
  // as everything else now, per the site-wide time display standard.
  function fmtActivityTime(s) {
    return fmtLastUpdated(s);
  }

  function buildRelations(list) {
    const byId = {};
    list.forEach(d => { byId[d.id] = d; });
    const childrenOf = {};
    list.forEach(d => { childrenOf[d.id] = []; });
    list.forEach(d => {
      const pid = d.connected_to;
      if (pid && byId[pid]) childrenOf[pid].push(d.id);
    });
    return { byId, childrenOf };
  }

  // Hierarchical auto-layout, purely derived from real `connected_to` data.
  // No fixed x/y — positions are recomputed every time devices are added,
  // edited, or removed. Level 0 = roots (Main Router), level 1 = their
  // children (Core Switch), level 2 = grandchildren (Access Switches), etc.
  //
  // Uses a "tidy tree" placement: every leaf gets the next free horizontal
  // slot (equal spacing), and every parent is centered directly above its
  // own children instead of the whole row being spread across the full
  // canvas width. That's what keeps a newly-added device sitting right
  // beside/under the device it's connected to, at its own correct tree
  // level, instead of drifting toward the middle of a wider row above it.
  function computeLayout(list, opts) {
    opts = opts || {};
    const NODE_W = opts.nodeW || 168;
    const NODE_H = opts.nodeH || 66;
    const HGAP   = opts.hgap  || 40;
    const VGAP   = opts.vgap  || 108;
    const PAD    = opts.pad   || 40;
    const SLOT   = NODE_W + HGAP;

    const { byId, childrenOf } = buildRelations(list);
    const roots = list
      .filter(d => !(d.connected_to && byId[d.connected_to]))
      .map(d => d.id)
      .sort((a, b) => a - b);

    // Depth = shortest hop count from a root, via BFS (unique per node —
    // this is a tree, not a general graph).
    const depth = {};
    const visited = new Set();
    let frontier = roots;
    frontier.forEach(id => { depth[id] = 0; visited.add(id); });
    while (frontier.length) {
      const next = [];
      frontier.forEach(id => {
        (childrenOf[id] || []).slice().sort((a, b) => a - b).forEach(cid => {
          if (!visited.has(cid)) {
            depth[cid] = depth[id] + 1;
            visited.add(cid);
            next.push(cid);
          }
        });
      });
      frontier = next;
    }
    // Orphaned/cyclic rows BFS never reached still get shown, as extra
    // top-level roots on their own slot.
    const extraRoots = [];
    list.forEach(d => {
      if (!visited.has(d.id)) {
        depth[d.id] = 0;
        visited.add(d.id);
        extraRoots.push(d.id);
      }
    });
    const allRoots = roots.concat(extraRoots);

    // Post-order placement: a leaf claims the next free column; a parent
    // centers itself over the min/max of its own children's centers.
    const centerX = {};
    let cursor = 0;
    function place(id) {
      const kids = (childrenOf[id] || [])
        .filter(cid => depth[cid] === depth[id] + 1)
        .sort((a, b) => a - b);
      if (!kids.length) {
        centerX[id] = PAD + cursor * SLOT + NODE_W / 2;
        cursor += 1;
      } else {
        kids.forEach(place);
        const xs = kids.map(cid => centerX[cid]);
        centerX[id] = (Math.min(...xs) + Math.max(...xs)) / 2;
      }
    }
    allRoots.forEach(place);

    const maxDepth = Math.max(0, ...list.map(d => depth[d.id] || 0));
    const levels = [];
    for (let i = 0; i <= maxDepth; i++) levels.push([]);
    list.forEach(d => {
      if (depth[d.id] !== undefined) levels[depth[d.id]].push(d.id);
    });
    levels.forEach(row => row.sort((a, b) => centerX[a] - centerX[b]));

    const pos = {};
    let maxCenter = 0;
    list.forEach(d => {
      if (centerX[d.id] === undefined) return;
      maxCenter = Math.max(maxCenter, centerX[d.id]);
      pos[d.id] = {
        x: centerX[d.id] - NODE_W / 2,
        y: PAD + depth[d.id] * VGAP,
        w: NODE_W, h: NODE_H,
      };
    });

    const canvasW = Math.max(420, maxCenter + NODE_W / 2 + PAD);
    const canvasH = levels.length * VGAP + NODE_H + PAD * 2;

    return { byId, childrenOf, levels, pos, canvasW, canvasH, NODE_W, NODE_H, VGAP, PAD, depth };
  }

  // The 4 switches that exist in the physical eve.ng lab today, with real
  // clients already sitting behind them (matches the router's static ping
  // list in app.py's MONITOR_DEVICES). Only these get the cosmetic
  // "N Clients" box. A brand-new switch added later never gets one
  // automatically — even after it comes Online — since nothing has
  // actually been wired up behind it yet; it's shown as just the switch.
  const FIXED_CLIENT_SWITCH_IPS = new Set([
    '192.168.99.3',
    '192.168.99.66',
    '192.168.99.130',
    '192.168.99.194',
  ]);
  function hasKnownClients(d) {
    return !!(d && d.ip_address && FIXED_CLIENT_SWITCH_IPS.has(String(d.ip_address).trim()));
  }

  // Final placement pass. Re-lays-out the WHOLE tree (it ignores whatever
  // computeLayout put in layout.pos) so that space is always reserved for
  // every device that exists — no matter how many are added, or where.
  //
  // How it works (a "tidy tree" with reserved slots):
  //   * Every leaf owns exactly one equal-width slot on its row, so two
  //     leaves can never overlap and never sit closer than one slot apart.
  //   * A parent is centred above the span of ITS OWN children, so a new
  //     device always lands beside / under the device it is wired to —
  //     never floating somewhere else in the row.
  //   * The cosmetic "N Clients / Floor X" box under an original lab
  //     Access Switch is treated as one more (virtual) child of that switch,
  //     listed first. So a device added to that switch gets its own slot
  //     next to the box, the switch re-centres over both, and everything
  //     above (Core Switch, Router) re-centres in turn.
  //   * Devices can nest as deep as needed (a device added under a device
  //     that was added under a switch...) — each level is a new row, and
  //     every subtree keeps its own contiguous block of slots.
  //   * At the end the whole drawing is shifted so the left margin equals the
  //     right margin, i.e. the diagram is centred inside its canvas.
  //
  // Rules for the client box (unchanged): only the 4 original eve.ng lab
  // switches (FIXED_CLIENT_SWITCH_IPS) get one, Online or Offline. Nothing
  // added later ever gets an automatic client box.
  function addClientTier(layout, list, opts) {
    opts = opts || {};
    const NODE_W = layout.NODE_W;
    const NODE_H = layout.NODE_H;
    const HGAP = layout.HGAP || 40;
    const VGAP = layout.VGAP || opts.vgap || 92;
    const PAD = layout.PAD !== undefined ? layout.PAD : (opts.pad || 40);
    const SLOT = NODE_W + HGAP;
    const CW = opts.clientW || NODE_W;
    const CH = opts.clientH || NODE_H;
    const clientsPerSwitch = opts.clientsPerSwitch || 60;

    const { byId, childrenOf, depth } = layout;
    const kidsOf = id => (childrenOf[id] || [])
      .filter(cid => depth[cid] === depth[id] + 1)
      .sort((a, b) => a - b);

    // ── 1. Build the tree (real devices + virtual client boxes) ─────────
    const placed = new Set();
    function build(id) {
      placed.add(id);
      const d = byId[id];
      const kids = [];
      // Client box = first child, only for original lab Access Switches
      // sitting directly under the Core Switch tier (depth 2).
      if (d && d.type === 'Access Device' && depth[id] === 2 && hasKnownClients(d)) {
        kids.push({ kind: 'client', owner: id, depth: depth[id] + 1, kids: [] });
      }
      kidsOf(id).forEach(cid => { if (!placed.has(cid)) kids.push(build(cid)); });
      return { kind: 'device', id, depth: depth[id], kids };
    }
    const rootIds = list
      .filter(d => depth[d.id] === 0)
      .map(d => d.id)
      .sort((a, b) => a - b);
    const roots = rootIds.map(build);
    // Safety net: anything unreachable (should not happen) still gets a slot
    // as its own top-level node instead of silently disappearing.
    list.forEach(d => { if (!placed.has(d.id)) roots.push(build(d.id)); });

    // ── 2. Assign x: leaves take the next free slot, parents centre ─────
    let cursor = 0;
    function place(n) {
      if (!n.kids.length) {
        n.cx = PAD + cursor * SLOT + NODE_W / 2;
        cursor += 1;
        return;
      }
      n.kids.forEach(place);
      n.cx = (n.kids[0].cx + n.kids[n.kids.length - 1].cx) / 2;
    }
    roots.forEach(place);

    // ── 3. Turn the tree into boxes ─────────────────────────────────────
    const pos = {};
    const clientPos = {};
    let maxDepthRow = 0;
    const walkAll = (n) => {
      maxDepthRow = Math.max(maxDepthRow, n.depth);
      if (n.kind === 'client') {
        const owner = byId[n.owner];
        clientPos[n.owner] = {
          x: n.cx - CW / 2,
          y: PAD + n.depth * VGAP,
          w: CW, h: CH,
          label: `${clientsPerSwitch} Clients`,
          sub: (owner && owner.location) || '',
        };
        return;
      }
      pos[n.id] = { x: n.cx - NODE_W / 2, y: PAD + n.depth * VGAP, w: NODE_W, h: NODE_H };
      n.kids.forEach(walkAll);
    };
    roots.forEach(walkAll);

    // ── 4. Centre the drawing inside its canvas ─────────────────────────
    let minLeft = Infinity, maxRight = 0;
    Object.values(pos).concat(Object.values(clientPos)).forEach(b => {
      minLeft = Math.min(minLeft, b.x);
      maxRight = Math.max(maxRight, b.x + b.w);
    });
    if (!isFinite(minLeft)) { minLeft = PAD; maxRight = PAD + NODE_W; }
    const shiftX = PAD - minLeft;
    Object.keys(pos).forEach(id => { pos[id] = { ...pos[id], x: pos[id].x + shiftX }; });
    Object.keys(clientPos).forEach(id => { clientPos[id] = { ...clientPos[id], x: clientPos[id].x + shiftX }; });
    maxRight += shiftX;

    // Rows are (re)built from the final depths, left → right.
    const levels = [];
    for (let i = 0; i <= maxDepthRow; i++) levels.push([]);
    list.forEach(d => { if (pos[d.id] && levels[depth[d.id]]) levels[depth[d.id]].push(d.id); });
    levels.forEach(row => row.sort((a, b) => pos[a].x - pos[b].x));

    // Bottom row may be a client box (different height than a device box).
    const lastRowBoxes = Object.values(pos).concat(Object.values(clientPos))
      .filter(b => b.y === PAD + maxDepthRow * VGAP);
    const bottomH = Math.max(NODE_H, ...lastRowBoxes.map(b => b.h));

    // Very small diagrams: canvas has a 420px minimum — centre inside it.
    const naturalW = maxRight + PAD;
    const canvasW = Math.max(420, naturalW);
    const extra = (canvasW - naturalW) / 2;
    if (extra > 0) {
      Object.keys(pos).forEach(id => { pos[id] = { ...pos[id], x: pos[id].x + extra }; });
      Object.keys(clientPos).forEach(id => { clientPos[id] = { ...clientPos[id], x: clientPos[id].x + extra }; });
    }

    layout.pos = pos;
    layout.clientPos = clientPos;
    layout.levels = levels;
    layout.canvasW = canvasW;
    layout.canvasH = PAD * 2 + maxDepthRow * VGAP + bottomH;
    return layout;
  }

  // Full connection path for a clicked device: itself, its ancestor chain
  // up to the root, and all of its descendants — i.e. "what is this device
  // connected to" (up AND down the tree), built only from real connections.
  function chainFor(targetId, byId, childrenOf) {
    const chain = new Set();
    let cur = byId[targetId];
    while (cur) {
      chain.add(cur.id);
      cur = cur.connected_to ? byId[cur.connected_to] : null;
    }
    const stack = [targetId];
    while (stack.length) {
      const id = stack.pop();
      (childrenOf[id] || []).forEach(cid => {
        if (!chain.has(cid)) { chain.add(cid); stack.push(cid); }
      });
    }
    return chain;
  }

  window.NetworkTopology = {
    isNetworkDevice,
    filterDevices,
    statusColor,
    statusLabel,
    fmtLastUpdated,
    fmtLastUpdatedFromDate,
    fmtActivityTime,
    buildRelations,
    computeLayout,
    addClientTier,
    chainFor,
  };

})(window);
