/* FreshRoute AI charts.
 * Uses Chart.js when /static/vendor/chart.umd.min.js is present (run scripts/download_chartjs.py once),
 * otherwise renders accessible inline-SVG charts with the same specs, so charts work fully offline.
 * Series colours are validated for colour-blind separation: green, blue, coral.
 */
(function () {
  'use strict';
  var C = { green: '#178a55', blue: '#4b6cb7', coral: '#cf5c45', grid: '#e7e2d2', axis: '#6f7b74', ink: '#232b27' };
  var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  var dataEl = document.getElementById('chart-data');
  var DATA = {};
  try { DATA = dataEl ? JSON.parse(dataEl.textContent) : {}; } catch (e) { DATA = {}; }
  var UNIT = DATA.unit || '';

  function shortDate(s) { var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s || ''); return m ? (+m[3]) + ' ' + MONTHS[+m[2] - 1] : s; }
  function fmtNum(v) { if (v == null) return '-'; var a = Math.abs(v); return a >= 100 ? Math.round(v).toLocaleString() : (Math.round(v * 10) / 10).toLocaleString(); }
  function fmtPct(v) { return v == null ? '-' : (Math.round(v * 10) / 10) + '%'; }
  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function niceMax(max) {
    if (!(max > 0)) return 1;
    var p = Math.pow(10, Math.floor(Math.log10(max))), f = max / p;
    var steps = [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10], n = 10; for (var k = 0; k < steps.length; k++) { if (f <= steps[k]) { n = steps[k]; break; } }
    return n * p;
  }

  /* ---------------- specs built from server data ---------------- */
  var specs = {
    trend: function (d) {
      var t = d.trend || [];
      return { type: 'line', labels: t.map(function (r) { return r.date; }), fmtLabel: shortDate, fmt: fmtNum, unit: UNIT,
        series: [{ name: 'Consumed', color: C.green, values: t.map(function (r) { return r.consumed; }) },
                 { name: '7-day average', color: C.blue, dash: '5 4', values: t.map(function (r) { return r.ma7; }) }] };
    },
    waste_trend: function (d) {
      var t = d.trend || [];
      return { type: 'line', labels: t.map(function (r) { return r.date; }), fmtLabel: shortDate, fmt: fmtPct, unit: '%',
        series: [{ name: 'Waste %', color: C.coral, values: t.map(function (r) { return r.waste_pct; }) }] };
    },
    waste_item: function (d) {
      var t = (d.waste_item || d.items || []).slice().sort(function (a, b) { return (b.waste_pct || 0) - (a.waste_pct || 0); }).slice(0, 12);
      return { type: 'hbar', labels: t.map(function (r) { return r.food_item; }), fmt: fmtPct, unit: '%',
        series: [{ name: 'Waste %', color: C.coral, values: t.map(function (r) { return r.waste_pct; }) }] };
    },
    meals: function (d) {
      var t = d.meals || [];
      return { type: 'bar', labels: t.map(function (r) { return r.meal; }), fmt: fmtNum, unit: UNIT,
        series: [{ name: 'Prepared', color: C.blue, values: t.map(function (r) { return r.prepared; }) },
                 { name: 'Consumed', color: C.green, values: t.map(function (r) { return r.consumed; }) },
                 { name: 'Leftovers', color: C.coral, values: t.map(function (r) { return r.leftovers; }) }] };
    },
    meal_waste: function (d) {
      var t = d.meals || [];
      return { type: 'bar', labels: t.map(function (r) { return r.meal; }), fmt: fmtPct, unit: '%',
        series: [{ name: 'Waste %', color: C.coral, values: t.map(function (r) { return r.waste_pct; }) }] };
    },
    pva: function (d) {
      var t = d.pva || [], hasMl = t.some(function (r) { return r.ml != null; });
      var s = [{ name: 'Actual consumed', color: C.green, values: t.map(function (r) { return r.actual; }) },
               { name: 'Baseline forecast', color: C.blue, dash: '5 4', values: t.map(function (r) { return r.baseline; }) }];
      if (hasMl) s.push({ name: 'Random Forest forecast', color: C.coral, dash: '2 3', values: t.map(function (r) { return r.ml; }) });
      return { type: 'line', labels: t.map(function (r) { return r.date; }), fmtLabel: shortDate, fmt: fmtNum, unit: UNIT, series: s };
    },
    pvc: function (d) {
      var t = d.pvc || [];
      return { type: 'line', labels: t.map(function (r) { return r.date; }), fmtLabel: shortDate, fmt: fmtNum, unit: UNIT,
        series: [{ name: 'Prepared', color: C.blue, values: t.map(function (r) { return r.prepared; }) },
                 { name: 'Consumed', color: C.green, values: t.map(function (r) { return r.consumed; }) }] };
    },
    daily: function (d) {
      var t = d.daily || [];
      return { type: 'line', labels: t.map(function (r) { return r.date; }), fmtLabel: shortDate, fmt: fmtPct, unit: '%',
        series: [{ name: 'Daily waste %', color: C.coral, values: t.map(function (r) { return r.waste_pct; }) }] };
    },
    weekly: function (d) {
      var t = d.weekly || [];
      return { type: 'bar', labels: t.map(function (r) { return r.week; }), fmtLabel: function (s) { return 'Wk ' + shortDate(s); }, fmt: fmtPct, unit: '%',
        series: [{ name: 'Weekly waste %', color: C.coral, values: t.map(function (r) { return r.waste_pct; }) }] };
    },
    tm_frontier: function (d) {
      var t = d.tm_frontier || [];
      return { type: 'line', labels: t.map(function (r) { return r.buffer_pct; }), fmtLabel: function (v) { return v + '% buffer'; }, fmt: fmtPct, unit: '%',
        series: [{ name: 'Waste % (upper bound)', color: C.coral, values: t.map(function (r) { return r.waste_pct_high; }) },
                 { name: 'Certainly short %', color: C.blue, values: t.map(function (r) { return r.short_sure_pct; }) },
                 { name: 'Actual waste %', color: C.coral, dash: '5 4', values: t.map(function (r) { return r.actual_waste_pct; }) },
                 { name: 'Actual ran-out %', color: C.blue, dash: '5 4', values: t.map(function (r) { return r.actual_ran_out_pct; }) }] };
    },
    tm_daily: function (d) {
      var t = d.tm_daily || [];
      return { type: 'line', labels: t.map(function (r) { return r.date; }), fmtLabel: shortDate, fmt: fmtNum, unit: UNIT,
        series: [{ name: 'Actual leftovers', color: C.coral, values: t.map(function (r) { return r.actual; }) },
                 { name: 'With suggestions (at most)', color: C.green, dash: '5 4', values: t.map(function (r) { return r.policy_high; }) }] };
    },
    weekday: function (d) {
      var t = d.weekday || [];
      return { type: 'bar', labels: t.map(function (r) { return r.weekday; }), fmt: fmtPct, unit: '%',
        series: [{ name: 'Waste %', color: C.coral, values: t.map(function (r) { return r.waste_pct; }) }] };
    }
  };

  /* ---------------- accessible text alternative ---------------- */
  function srTable(el, spec) {
    var rows = Math.min(spec.labels.length, 60), h = '<table class="sr-only"><caption>' + esc(el.getAttribute('aria-label') || 'Chart data') + '</caption><thead><tr><th>Label</th>';
    spec.series.forEach(function (s) { h += '<th>' + esc(s.name) + '</th>'; });
    h += '</tr></thead><tbody>';
    for (var i = 0; i < rows; i++) {
      h += '<tr><th>' + esc(label(spec, i)) + '</th>';
      spec.series.forEach(function (s) { h += '<td>' + esc(spec.fmt(s.values[i])) + '</td>'; });
      h += '</tr>';
    }
    return h + '</tbody></table>';
  }
  function label(spec, i) { return spec.fmtLabel ? spec.fmtLabel(spec.labels[i]) : spec.labels[i]; }

  function legend(spec) {
    if (spec.series.length < 2) return '';
    return '<div class="legend">' + spec.series.map(function (s) {
      return '<span><i class="' + (spec.type === 'line' ? 'line' : '') + '" style="background:' + s.color + '"></i>' + esc(s.name) + '</span>';
    }).join('') + '</div>';
  }

  /* ---------------- SVG renderer ---------------- */
  function drawSVG(el, spec) {
    var H = parseInt(el.getAttribute('data-height') || '260', 10);
    var W = Math.max(el.clientWidth || 520, 260);
    var hb = spec.type === 'hbar';
    var maxLab = hb ? Math.min(150, Math.max.apply(null, spec.labels.map(function (l) { return String(l).length; })) * 6.4 + 12) : 0;
    var m = { l: hb ? maxLab : 44, r: hb ? 56 : 12, t: 8, b: hb ? 8 : 26 };
    var iw = W - m.l - m.r, ih = H - m.t - m.b;
    var all = [];
    spec.series.forEach(function (s) { s.values.forEach(function (v) { if (v != null) all.push(v); }); });
    var max = niceMax(Math.max.apply(null, all.concat([0])) * 1.04);
    var n = spec.labels.length;
    var out = ['<svg viewBox="0 0 ' + W + ' ' + H + '" width="' + W + '" height="' + H + '" aria-hidden="true">'];
    var X, Y;
    if (hb) { X = function (v) { return m.l + (v / max) * iw; }; }
    else { Y = function (v) { return m.t + ih - (v / max) * ih; }; }

    if (!hb) {
      for (var g = 0; g <= 4; g++) {
        var gv = max * g / 4, gy = Y(gv);
        out.push('<line x1="' + m.l + '" x2="' + (W - m.r) + '" y1="' + gy + '" y2="' + gy + '" stroke="' + (g === 0 ? '#c9c3ae' : C.grid) + '" stroke-width="1"/>');
        out.push('<text x="' + (m.l - 6) + '" y="' + (gy + 4) + '" text-anchor="end">' + esc(spec.fmt(gv).replace(/\.0%/, '%')) + '</text>');
      }
    }
    var xs = function (i) { return m.l + (n <= 1 ? iw / 2 : (i / (n - 1)) * iw); };
    var bandW = iw / Math.max(n, 1);

    if (spec.type === 'line') {
      var step = Math.ceil(n / Math.max(Math.floor(iw / 62), 2));
      for (var i = 0; i < n; i += step) out.push('<text x="' + xs(i) + '" y="' + (H - 8) + '" text-anchor="middle">' + esc(label(spec, i)) + '</text>');
      spec.series.forEach(function (s) {
        var d = '', pen = false;
        s.values.forEach(function (v, i) {
          if (v == null) { pen = false; return; }
          d += (pen ? 'L' : 'M') + xs(i).toFixed(1) + ' ' + Y(v).toFixed(1); pen = true;
        });
        out.push('<path d="' + d + '" fill="none" stroke="' + s.color + '" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"' + (s.dash ? ' stroke-dasharray="' + s.dash + '"' : '') + '/>');
        if (n <= 31) s.values.forEach(function (v, i) { if (v != null) out.push('<circle cx="' + xs(i) + '" cy="' + Y(v) + '" r="3" fill="' + s.color + '" stroke="#fff" stroke-width="1.5"/>'); });
      });
      out.push('<line class="xh" x1="0" x2="0" y1="' + m.t + '" y2="' + (m.t + ih) + '" stroke="#9aa59e" stroke-width="1" visibility="hidden"/>');
    } else if (spec.type === 'bar') {
      var k = spec.series.length, gap = 2, groupW = Math.min(bandW * 0.72, 72 * k), bw = (groupW - gap * (k - 1)) / k;
      var xstep = Math.ceil(n / Math.max(Math.floor(iw / 54), 2));
      for (var j = 0; j < n; j++) {
        var cx = m.l + bandW * (j + 0.5), x0 = cx - groupW / 2;
        if (j % xstep === 0) out.push('<text x="' + cx + '" y="' + (H - 8) + '" text-anchor="middle">' + esc(label(spec, j)) + '</text>');
        spec.series.forEach(function (s, si) {
          var v = s.values[j]; if (v == null) return;
          var h = Math.max((v / max) * ih, v > 0 ? 1.5 : 0), x = x0 + si * (bw + gap), y = m.t + ih - h, r = Math.min(4, bw / 2, h);
          out.push('<path d="M' + x + ' ' + (y + h) + 'V' + (y + r) + 'Q' + x + ' ' + y + ' ' + (x + r) + ' ' + y + 'H' + (x + bw - r) + 'Q' + (x + bw) + ' ' + y + ' ' + (x + bw) + ' ' + (y + r) + 'V' + (y + h) + 'Z" fill="' + s.color + '"/>');
        });
        out.push('<rect class="hit" data-i="' + j + '" x="' + (m.l + bandW * j) + '" y="' + m.t + '" width="' + bandW + '" height="' + ih + '" fill="transparent"/>');
      }
    } else { // hbar
      var rowH = ih / Math.max(n, 1), barH = Math.min(18, rowH * 0.62);
      spec.labels.forEach(function (lab, i) {
        var v = spec.series[0].values[i] || 0, cy = m.t + rowH * (i + 0.5), w = Math.max(X(v) - m.l, v > 0 ? 1.5 : 0), r = Math.min(4, barH / 2, w);
        var name = String(lab); if (name.length > 24) name = name.slice(0, 23) + '…';
        out.push('<text x="' + (m.l - 8) + '" y="' + (cy + 4) + '" text-anchor="end" style="fill:#232b27">' + esc(name) + '</text>');
        out.push('<path d="M' + m.l + ' ' + (cy - barH / 2) + 'H' + (m.l + w - r) + 'Q' + (m.l + w) + ' ' + (cy - barH / 2) + ' ' + (m.l + w) + ' ' + (cy - barH / 2 + r) + 'V' + (cy + barH / 2 - r) + 'Q' + (m.l + w) + ' ' + (cy + barH / 2) + ' ' + (m.l + w - r) + ' ' + (cy + barH / 2) + 'H' + m.l + 'Z" fill="' + spec.series[0].color + '"/>');
        out.push('<text x="' + (m.l + w + 6) + '" y="' + (cy + 4) + '" style="fill:#232b27;font-weight:600">' + esc(spec.fmt(v)) + '</text>');
        out.push('<rect class="hit" data-i="' + i + '" x="0" y="' + (m.t + rowH * i) + '" width="' + W + '" height="' + rowH + '" fill="transparent"/>');
      });
    }
    out.push('</svg>');
    el.innerHTML = legend(spec) + out.join('') + '<div class="chart-tip" role="presentation"></div>' + srTable(el, spec);
    el.setAttribute('role', 'img');
    var svg = el.querySelector('svg'), tip = el.querySelector('.chart-tip');
    var show = function (i, px, py) {
      var h = '<b>' + esc(label(spec, i)) + '</b>';
      spec.series.forEach(function (s) { var v = s.values[i]; h += '<div><span class="sw" style="background:' + s.color + '"></span>' + esc(s.name) + ': <b style="display:inline">' + esc(spec.fmt(v)) + (spec.unit && spec.unit !== '%' && v != null ? ' ' + esc(spec.unit) : '') + '</b></div>'; });
      tip.innerHTML = h; tip.classList.add('on');
      var left = Math.min(Math.max(px + 12, 4), el.clientWidth - tip.offsetWidth - 4);
      tip.style.left = left + 'px'; tip.style.top = Math.max(py - tip.offsetHeight - 10, 0) + 'px';
    };
    var hide = function () { tip.classList.remove('on'); var xh = el.querySelector('.xh'); if (xh) xh.setAttribute('visibility', 'hidden'); };
    if (spec.type === 'line') {
      svg.addEventListener('mousemove', function (e) {
        var box = svg.getBoundingClientRect(), x = (e.clientX - box.left) * (W / box.width);
        var i = Math.round(((x - m.l) / iw) * (n - 1)); i = Math.min(Math.max(i, 0), n - 1);
        var xh = el.querySelector('.xh'); xh.setAttribute('x1', xs(i)); xh.setAttribute('x2', xs(i)); xh.setAttribute('visibility', 'visible');
        show(i, xs(i) * box.width / W, e.clientY - box.top);
      });
      svg.addEventListener('mouseleave', hide);
    } else {
      svg.querySelectorAll('.hit').forEach(function (r) {
        r.addEventListener('mousemove', function (e) { var box = el.getBoundingClientRect(); show(+r.getAttribute('data-i'), e.clientX - box.left, e.clientY - box.top); });
        r.addEventListener('mouseleave', hide);
      });
    }
  }

  /* ---------------- Chart.js renderer (used when available) ---------------- */
  function drawChartJs(el, spec) {
    var H = parseInt(el.getAttribute('data-height') || '260', 10);
    el.innerHTML = legend(spec) + '<div style="position:relative;height:' + H + 'px"><canvas></canvas></div>' + srTable(el, spec);
    var hb = spec.type === 'hbar';
    var cfg = {
      type: spec.type === 'line' ? 'line' : 'bar',
      data: { labels: spec.labels.map(function (l, i) { return label(spec, i); }),
        datasets: spec.series.map(function (s) {
          return { label: s.name, data: s.values, borderColor: s.color, backgroundColor: s.color, borderWidth: spec.type === 'line' ? 2 : 0,
            borderDash: s.dash ? s.dash.split(' ').map(Number) : [], pointRadius: spec.labels.length > 31 ? 0 : 3, tension: 0, borderRadius: 4, spanGaps: false };
        }) },
      options: { responsive: true, maintainAspectRatio: false, indexAxis: hb ? 'y' : 'x', interaction: { mode: hb ? 'nearest' : 'index', intersect: false },
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: function (c) { return c.dataset.label + ': ' + spec.fmt(c.parsed[hb ? 'x' : 'y']); } } } },
        scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 8, color: C.axis } }, y: { beginAtZero: true, grid: { color: C.grid }, ticks: { color: C.axis } } } }
    };
    new window.Chart(el.querySelector('canvas'), cfg);
  }

  function render(el) {
    var name = el.getAttribute('data-chart'), build = specs[name];
    if (!build) return;
    var spec = build(DATA);
    var any = spec.series.some(function (s) { return s.values.some(function (v) { return v != null; }); });
    if (!spec.labels.length || !any) {
      el.innerHTML = '<div class="chart-empty">' + esc(el.getAttribute('data-empty') || 'Nothing to plot yet. Charts appear once records exist for these filters.') + '</div>';
      return;
    }
    if (window.Chart) { try { drawChartJs(el, spec); return; } catch (e) { /* fall back to SVG */ } }
    drawSVG(el, spec);
  }

  function renderAll() { document.querySelectorAll('[data-chart]').forEach(render); }
  renderAll();
  var timer;
  window.addEventListener('resize', function () {
    if (window.Chart) return;
    clearTimeout(timer); timer = setTimeout(renderAll, 150);
  });
  window.FreshRouteCharts = { renderAll: renderAll };
})();
