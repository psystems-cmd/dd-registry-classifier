/* Demo-only "Architecture" panel: inline SVG flow chart + event log.
   API: Flow.mount(el), Flow.event(type, ctx). Renders via el.innerHTML. */
(function () {
  'use strict';
  var C = { ink: '#1f2328', muted: '#6b7280', border: '#d1d5db', box: '#ffffff', acc: '#4f46e5',
            accBg: '#eef2ff', ok: '#16a34a', err: '#dc2626', errBg: '#fef2f2', edge: '#9ca3af', grey: '#f3f4f6' };
  var W = 440, BW = 140, SW = 134, H = 38;
  // id: [x, y, w, h, title, subtitle]
  var NODES = {
    excel: [150, 22, BW, H, 'Target export', '.xlsx from IT / finance'],
    intake: [150, 80, BW, H, 'Intake', 'column mapping'],
    inventory: [150, 138, BW, H, 'Inventory', 'read-only'],
    rules: [150, 196, BW, H, 'Rules engine', 'name, alias, category'],
    plan: [4, 196, SW, H, 'Plan model', 'Kimi-K3.1-Pro · once'],
    exec: [302, 196, SW, H, 'Execute model', 'GLM-5.3-Flash · per row'],
    guard: [150, 254, BW, H, 'Guardrails', 'K1 never off · reasons'],
    ui: [4, 312, SW, H, 'Browser UI', 'renders API output'],
    dal: [150, 312, BW, H, 'Data access layer', 'permission check (403)'],
    results: [4, 376, 99, 28, 'results.json'],
    registry: [115, 376, 99, 28, 'registry.json'],
    questions: [226, 376, 99, 28, 'questions.json'],
    audit: [337, 376, 99, 28, 'audit.jsonl']
  };
  var EDGES = [['excel', 'intake', 'b', 't'], ['intake', 'inventory', 'b', 't'], ['inventory', 'rules', 'b', 't'],
    ['plan', 'rules', 'r', 'l'], ['rules', 'exec', 'r', 'l'], ['exec', 'guard', 'b', 'r'], ['rules', 'guard', 'b', 't'],
    ['guard', 'dal', 'b', 't'], ['ui', 'dal', 'r', 'l'], ['dal', 'results', 'b', 't'], ['dal', 'registry', 'b', 't'],
    ['dal', 'questions', 'b', 't'], ['dal', 'audit', 'b', 't']];
  // Space-separated tokens; "a>b>c" is a chain of edges, a bare word is a node.
  var EV = {
    login: 'ui>dal dal>audit',
    upload: 'excel>intake>inventory dal>audit',
    run: 'inventory>rules plan>rules rules>exec exec>guard rules>guard guard>dal dal>results dal>registry dal>audit',
    select: 'ui',
    confirm: 'ui>dal>results dal>registry dal>audit',
    override: 'ui>dal guard dal>results dal>registry dal>audit',
    flag: 'ui>dal>results dal>audit',
    question: 'ui>dal>questions dal>audit', answer: 'ui>dal>questions dal>audit', resolve: 'ui>dal>questions dal>audit',
    extract: 'ui>dal>results dal>audit',
    edit: 'ui>dal>registry dal>audit',
    denied: 'ui>dal dal>audit'
  };
  var CLS = { K1: '#dc2626', K2: '#d97706', K3: '#16a34a', needs_human: '#4f46e5' };
  var TRACK = { semantic_layer: ['Semantic layer', 'read-only map until Day 90'], sweep: ['Legacy Sweep', 'move docs by Day 60'],
    sunset: ['Export & switch off', 'Day 30'], keep_local: ['Stays local', "finance, founder's rule"] };
  var CSS = '.flow-act{stroke-dasharray:6 4;animation:flow-dash .8s linear infinite}' +
    '@keyframes flow-dash{to{stroke-dashoffset:-20}}';

  var state = { type: null, ctx: {}, row: null, log: [], el: null };

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function cut(s, n) { s = String(s == null ? '' : s); return s.length > n ? s.slice(0, n - 1) + '…' : s; }
  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function pt(b, s) {
    return s === 't' ? [b[0] + b[2] / 2, b[1]] : s === 'b' ? [b[0] + b[2] / 2, b[1] + b[3]] :
      s === 'l' ? [b[0], b[1] + b[3] / 2] : [b[0] + b[2], b[1] + b[3] / 2];
  }
  function path(a, b, sa, sb) {
    var p = pt(a, sa), q = pt(b, sb);
    if (p[0] === q[0] || p[1] === q[1]) return 'M' + p + 'L' + q;
    if (sa === 'b' && sb === 't') return 'M' + p + 'V' + (p[1] + q[1]) / 2 + 'H' + q[0] + 'V' + q[1];
    return sa === 'b' || sa === 't' ? 'M' + p + 'V' + q[1] + 'H' + q[0] : 'M' + p + 'H' + q[0] + 'V' + q[1];
  }
  function parse(type) {
    var out = { edges: {}, nodes: {} };
    String(EV[type] || '').split(' ').forEach(function (tok) {
      var p = tok.split('>');
      p.forEach(function (n, i) { if (n) out.nodes[n] = 1; if (i) out.edges[p[i - 1] + '>' + n] = 1; });
    });
    return out;
  }

  // Shrink font so an estimated text width (chars * size * k) fits the box.
  function fit(t, w, size, k) { return Math.max(8.5, Math.min(size, +((w - 10) / (String(t).length * k)).toFixed(1))); }
  function box(x, y, w, h, t1, t2, o) {
    o = o || {};
    var stroke = o.stroke || (o.err ? C.err : o.act ? C.acc : C.border);
    var fill = o.err ? C.errBg : o.act ? C.accBg : o.grey ? C.grey : C.box;
    var tc = o.grey ? C.muted : C.ink, cx = x + w / 2;
    var f1 = fit(t1, w, 12, 0.56), f2 = t2 ? fit(t2, w, 10.5, 0.5) : 0;
    return '<rect class="flow-box" x="' + x + '" y="' + y + '" width="' + w + '" height="' + h + '" rx="8" fill="' + fill +
      '" stroke="' + stroke + '" stroke-width="' + (o.act || o.err || o.stroke ? 1.5 : 1) + '"' + (o.dash ? ' stroke-dasharray="4 3"' : '') + '/>' +
      '<text x="' + cx + '" y="' + (t2 ? y + h / 2 - 2 : y + h / 2 + 4) + '" text-anchor="middle" font-size="' + f1 + '" font-weight="600" fill="' + tc + '">' + esc(t1) + '</text>' +
      (t2 ? '<text x="' + cx + '" y="' + (y + h / 2 + 11) + '" text-anchor="middle" font-size="' + f2 + '" fill="' + C.muted + '">' + esc(t2) + '</text>' : '');
  }
  function line(d, mode, dash) {
    var m = mode === 'err' ? 'e' : mode === 'act' ? 'a' : 'n', col = mode === 'err' ? C.err : mode === 'act' ? C.acc : C.edge;
    return '<path d="' + d + '" fill="none" stroke="' + col + '" stroke-width="' + (mode ? 2.5 : 1.5) + '"' +
      (mode ? ' class="flow-act"' : dash ? ' stroke-dasharray="4 3"' : '') + ' marker-end="url(#flow-m' + m + ')"/>';
  }
  function marker(id, col) {
    return '<marker id="flow-m' + id + '" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">' +
      '<path d="M0,1 L9,5 L0,9 z" fill="' + col + '"/></marker>';
  }
  function heading(y, t) {
    return '<text x="4" y="' + y + '" font-size="11" letter-spacing="0.8" fill="' + C.muted + '">' + esc(t) + '</text>';
  }

  function top(hl, denied) {
    var s = heading(12, 'SYSTEM PATH'), i, k, e, late = '';
    for (i = 0; i < EDGES.length; i++) {
      e = EDGES[i]; k = e[0] + '>' + e[1];
      var d = path(NODES[e[0]], NODES[e[1]], e[2], e[3]);
      if (hl.edges[k]) late += line(d, denied && k === 'ui>dal' ? 'err' : 'act'); else s += line(d, '');
    }
    s += late;
    for (k in NODES) {
      var n = NODES[k], on = hl.nodes[k], bad = denied && (k === 'ui' || k === 'dal');
      s += box(n[0], n[1], n[2], n[3], n[4], n[5], { act: on && !bad, err: bad && on });
    }
    if (denied) {
      var b = NODES.dal;
      s += '<rect x="' + (b[0] + b[2] - 22) + '" y="' + (b[1] - 8) + '" width="30" height="16" rx="8" fill="' + C.err + '"/>' +
        '<text x="' + (b[0] + b[2] - 7) + '" y="' + (b[1] + 4) + '" text-anchor="middle" font-size="10" font-weight="700" fill="#fff">403</text>';
    }
    return s;
  }

  function bottom(row, extracting) {
    var s = heading(432, 'DOCKING: TARGET_1 → UCM'), y = 442, h = 40;
    if (!row) {
      return s + box(4, y, 432, h, 'Click a system to see where it goes', null, { dash: true, grey: true });
    }
    var human = row.class === 'needs_human', local = row.track === 'keep_local';
    var mid = human ? ['Decision needed', 'human, before closing'] : TRACK[row.track] || ['Not classified', 'no track yet'];
    var grey = human || local, kn = Array.isArray(row.knowledge) && row.knowledge.length ? row.knowledge : null;
    var T = [4, y, 130, h], M = [155, y, 130, h], U = [306, y, 130, h];
    s += line(path(T, M, 'r', 'l'), '') + line(path(M, U, 'r', 'l'), '', local);
    s += box(T[0], y, 130, h, cut(row.name || 'unnamed', 17), 'target_1', { stroke: CLS[row.class] });
    s += box(M[0], y, 130, h, mid[0], mid[1]);
    s += box(U[0], y, 130, h, cut(row.ucm_name || row.ucm_match || 'no UCM match', 17), 'ucm', { grey: grey });
    if (row.sunset_confirmed_by) {
      s += '<text x="220" y="496" text-anchor="middle" font-size="10" fill="' + C.ok + '">✓ sunset confirmed</text>';
    }
    if (kn) {
      var done = kn.filter(function (x) { return x && x.done; }).length, K = [155, 508, 281, h];
      s += line(path([4, y, 130, h], K, 'b', 'l'), extracting ? 'act' : '');
      s += box(K[0], K[1], K[2], h, 'Knowledge extraction', done + '/' + kn.length + ' items → Confluence/Drive', { act: extracting });
    }
    return s;
  }

  function render() {
    if (!state.el) return;
    var hl = parse(state.type), denied = state.type === 'denied', row = state.row;
    var svgH = row && Array.isArray(row.knowledge) && row.knowledge.length ? 560 : 500;
    var log = state.log.map(function (l) {
      return '<div style="padding:3px 0;border-top:1px solid ' + C.border + ';color:' + (l.err ? C.err : C.ink) + '">' +
        '<span style="color:' + (l.err ? C.err : C.muted) + ';font-variant-numeric:tabular-nums;margin-right:6px">' + esc(l.t) + '</span>' + esc(l.m) + '</div>';
    }).join('') || '<div style="color:' + C.muted + '">No events yet.</div>';
    state.el.innerHTML = '<div class="flow-root" style="font-family:inherit;color:' + C.ink + ';background:#f9fafb;padding:8px 0">' +
      '<style>' + CSS + '</style>' +
      '<svg width="100%" viewBox="0 0 ' + W + ' ' + svgH + '" style="display:block" font-family="inherit">' +
      '<defs>' + marker('n', C.edge) + marker('a', C.acc) + marker('e', C.err) + '</defs>' +
      top(hl, denied) + bottom(row, state.type === 'extract') + '</svg>' +
      '<div style="font-size:11px;letter-spacing:.8px;color:' + C.muted + ';margin:12px 4px 4px">WHAT JUST HAPPENED</div>' +
      '<div style="font-size:12px;padding:0 4px">' + log + '</div></div>';
  }

  function say(type, c) {
    var r = c.row && c.row.name ? c.row.name : 'row';
    switch (type) {
      case 'login': return (c.user || 'User') + ' signed in.';
      case 'upload': return 'Uploaded ' + (c.file || 'file') + (c.rows != null ? ' (' + c.rows + ' rows)' : '') + '.';
      case 'run': return 'Run finished: rules ' + (c.by_rules || 0) + ', model ' + (c.by_model || 0) + ', human ' + (c.by_human || 0) + '.';
      case 'select': return 'Opened ' + r + '.';
      case 'confirm': return 'Confirmed ' + r + '.';
      case 'override': return 'Overrode ' + r + (c.from || c.to ? ': ' + (c.from || '?') + ' → ' + (c.to || '?') : '') + '.';
      case 'flag': return 'Flagged ' + r + ' for a human.';
      case 'question': return 'Question ' + (c.label ? 'raised: ' + c.label : 'raised') + '.';
      case 'answer': return 'Question ' + (c.label ? 'answered: ' + c.label : 'answered') + '.';
      case 'resolve': return 'Question ' + (c.label ? 'resolved: ' + c.label : 'resolved') + '.';
      case 'extract': return 'Extracted ' + (c.item || 'item') + ' for ' + r + '.';
      case 'edit': return 'Edited registry entry ' + (c.id != null ? c.id : '') + '.';
      case 'denied': return 'Denied ' + (c.action || 'action') + (c.role ? ' for role ' + c.role : '') + ' (403).';
      default: return 'Event: ' + type;
    }
  }

  window.Flow = {
    mount: function (el) { state.el = el || null; render(); },
    event: function (type, ctx) {
      try {
        var c = ctx && typeof ctx === 'object' ? ctx : {}, d = new Date();
        type = String(type == null ? '' : type);
        if (c.row) state.row = typeof c.row === 'object' ? c.row : { name: String(c.row) };
        state.type = type; state.ctx = c;
        state.log.unshift({ t: pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds()),
          m: c.message || say(type, c), err: type === 'denied' });
        state.log.length = Math.min(state.log.length, 8);
        render();
      } catch (e) { if (typeof console !== 'undefined') console.error('Flow.event', e); }
    }
  };
})();
