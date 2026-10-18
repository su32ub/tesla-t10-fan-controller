/* GPU 风扇控制器前端逻辑（原生 JS，无依赖） */
(function () {
  'use strict';

  var REFRESH_INTERVAL_MS = 1000;
  var DEBOUNCE_MS = 180;
  var SLIDER_MIN = 20;   // 服务端未返回 limits 时的兜底值
  var SLIDER_MAX = 100;
  var TEMP_HIGH = 80;    // >= 视为高温（兜底值）
  var TEMP_CRIT = 90;    // >= 视为过热（兜底值）

  var cardsEl = document.getElementById('cards');
  var connEl = document.getElementById('conn-status');
  var dryRunBanner = document.getElementById('dry-run-banner');
  var errorBanner = document.getElementById('error-banner');
  var readonlyBanner = document.getElementById('readonly-banner');
  var emptyHint = document.getElementById('empty-hint');
  var lastUpdateEl = document.getElementById('last-update');
  var curveSvg = document.getElementById('curve-chart');
  var curveTooltip = document.getElementById('curve-tooltip');
  var curveTableBody = document.getElementById('curve-table-body');
  var curveMeta = document.getElementById('curve-meta');
  var curveStatusEl = document.getElementById('curve-status');
  var curveResetBtn = document.getElementById('curve-reset');

  var SVG_NS = 'http://www.w3.org/2000/svg';
  var CURVE_X_MIN = 30;
  var CURVE_X_MAX = 100;
  var CURVE_HANDLE_TEMPS = [30, 40, 50, 60, 70, 80];
  var COLORS = {
    line: '#2a78d6',
    point: '#eb6834',
    critical: '#d03b3b'
  };
  var curveGeom = null;
  var curveEditing = null;
  var curveDraft = null;
  var curvePending = false;
  var curveSaveTimer = 0;
  var curveNotice = null;

  var latestState = null;
  var stateLimits = { min_manual_duty: SLIDER_MIN, thermal_override_temp: 85 };
  var fetchController = null;
  var cardMap = new Map();   // id -> { root, els }
  var ctlMap = new Map();    // id -> 控制状态

  function getCtl(id) {
    if (!ctlMap.has(id)) {
      ctlMap.set(id, {
        seq: 0,
        abort: null,
        dragging: false,
        debounceTimer: 0,
        pending: false,
        lastError: null
      });
    }
    return ctlMap.get(id);
  }

  function fmtNum(v, digits) {
    if (v === null || v === undefined || typeof v !== 'number' || isNaN(v)) return '—';
    return v.toFixed(digits === undefined ? 0 : digits);
  }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  }

  /* ---------------- 卡片构建 ---------------- */

  function buildCard(id) {
    var root = el('article', 'card');
    root.dataset.gpuId = String(id);

    var header = el('header', 'card-header');
    var titleWrap = el('div');
    var h2 = el('h2', null, '');
    var pci = el('span', 'pci-addr');
    titleWrap.appendChild(h2);
    titleWrap.appendChild(pci);
    var badges = el('div', 'badges');
    badges.setAttribute('aria-label', '状态');
    header.appendChild(titleWrap);
    header.appendChild(badges);

    var tempRow = el('div', 'temp-row');
    var tempValue = el('span', 'temp-value');
    var tempBar = el('div', 'temp-bar');
    tempBar.setAttribute('role', 'img');
    var tempFill = el('div', 'temp-bar-fill');
    tempBar.appendChild(tempFill);
    tempRow.appendChild(tempValue);
    tempRow.appendChild(tempBar);

    var metrics = el('dl', 'metrics');
    var rpmDt = el('dt', null, '风扇实际转速');
    var rpmDd = el('dd', null, '—');
    var targetDt = el('dt', null, '目标占空比');
    var targetDd = el('dd', 'value-target', '—');
    var actualDt = el('dt', null, '实际占空比');
    var actualDd = el('dd', 'value-actual', '—');
    var m1 = el('div'); m1.appendChild(rpmDt); m1.appendChild(rpmDd);
    var m2 = el('div'); m2.appendChild(targetDt); m2.appendChild(targetDd);
    var m3 = el('div'); m3.appendChild(actualDt); m3.appendChild(actualDd);
    metrics.appendChild(m1); metrics.appendChild(m2); metrics.appendChild(m3);

    var modeRow = el('div', 'mode-row');
    var group = el('div', 'mode-group');
    group.setAttribute('role', 'group');
    group.setAttribute('aria-label', '风扇控制模式（GPU ' + id + '）');
    var autoBtn = el('button', 'mode-btn', '自动');
    autoBtn.type = 'button';
    autoBtn.dataset.mode = 'auto';
    autoBtn.setAttribute('aria-pressed', 'false');
    var manualBtn = el('button', 'mode-btn', '手动');
    manualBtn.type = 'button';
    manualBtn.dataset.mode = 'manual';
    manualBtn.setAttribute('aria-pressed', 'false');
    group.appendChild(autoBtn);
    group.appendChild(manualBtn);
    var modeHint = el('span', 'mode-hint');
    modeRow.appendChild(group);
    modeRow.appendChild(modeHint);

    var sliderRow = el('div', 'slider-row');
    var inputId = 'duty-input-' + id;
    var label = el('label', null, '占空比（' + SLIDER_MIN + '–' + SLIDER_MAX + '%）');
    htmlFor(label, inputId);
    var output = el('output', 'duty-output', '—');
    htmlFor(output, inputId);
    var slider = document.createElement('input');
    slider.type = 'range';
    slider.id = inputId;
    slider.min = String(SLIDER_MIN);
    slider.max = String(SLIDER_MAX);
    slider.step = '1';
    slider.value = String(SLIDER_MIN);
    slider.setAttribute('aria-label', 'GPU ' + id + ' 风扇占空比（百分比）');
    sliderRow.appendChild(label);
    sliderRow.appendChild(output);
    sliderRow.appendChild(slider);

    var cardError = el('p', 'card-error');
    cardError.hidden = true;

    root.appendChild(header);
    root.appendChild(tempRow);
    root.appendChild(metrics);
    root.appendChild(modeRow);
    root.appendChild(sliderRow);
    root.appendChild(cardError);

    attachCardEvents(id, root, slider, autoBtn, manualBtn);

    return {
      root: root,
      els: {
        h2: h2, pci: pci, badges: badges,
        tempValue: tempValue, tempFill: tempFill, tempBar: tempBar,
        rpmDd: rpmDd, targetDd: targetDd, actualDd: actualDd,
        autoBtn: autoBtn, manualBtn: manualBtn, modeHint: modeHint,
        slider: slider, sliderLabel: label, output: output, cardError: cardError
      }
    };
  }

  function htmlFor(node, id) {
    node.setAttribute('for', id);
    node.htmlFor = id;
  }

  function attachCardEvents(id, root, slider, autoBtn, manualBtn) {
    var ctl = getCtl(id);

    function commit(payload) {
      postControl(id, payload);
    }

    function currentDuty() {
      var g = findGpu(id);
      var d = null;
      if (g) {
        d = (typeof g.manual_duty === 'number') ? g.manual_duty
          : (typeof g.requested_duty === 'number') ? g.requested_duty : null;
      }
      if (d === null) d = parseInt(slider.value, 10);
      if (isNaN(d)) d = SLIDER_MIN;
      var min = parseInt(slider.min, 10);
      if (isNaN(min)) min = SLIDER_MIN;
      return Math.min(SLIDER_MAX, Math.max(min, d));
    }

    autoBtn.addEventListener('click', function () {
      commit({ mode: 'auto' });
    });

    manualBtn.addEventListener('click', function () {
      commit({ mode: 'manual', duty_percent: currentDuty() });
    });

    slider.addEventListener('pointerdown', function () { ctl.dragging = true; });
    window.addEventListener('pointerup', function () { ctl.dragging = false; });
    slider.addEventListener('keydown', function () { ctl.dragging = true; });
    slider.addEventListener('keyup', function () { ctl.dragging = false; });

    slider.addEventListener('input', function () {
      var v = parseInt(slider.value, 10);
      updateSliderFill(slider);
      setOutputText(root, v);
      if (ctl.debounceTimer) clearTimeout(ctl.debounceTimer);
      ctl.debounceTimer = setTimeout(function () {
        ctl.debounceTimer = 0;
        commit({ mode: 'manual', duty_percent: v });
      }, DEBOUNCE_MS);
    });

    slider.addEventListener('change', function () {
      ctl.dragging = false;
      var v = parseInt(slider.value, 10);
      if (ctl.debounceTimer) {
        clearTimeout(ctl.debounceTimer);
        ctl.debounceTimer = 0;
      }
      commit({ mode: 'manual', duty_percent: v });
    });
  }

  function setOutputText(root, v) {
    var out = root.querySelector('.duty-output');
    if (out) out.textContent = (isNaN(v) ? '—' : v + '%');
  }

  function updateSliderFill(slider) {
    var min = parseFloat(slider.min);
    var max = parseFloat(slider.max);
    var v = parseFloat(slider.value);
    var pct = max > min ? ((v - min) / (max - min)) * 100 : 0;
    slider.style.setProperty('--track-bg',
      'linear-gradient(to right, var(--accent-strong) 0%, var(--accent-strong) ' + pct + '%, var(--border) ' + pct + '%, var(--border) 100%)');
  }

  function findGpu(id) {
    if (!latestState || !latestState.gpus) return null;
    for (var i = 0; i < latestState.gpus.length; i++) {
      if (latestState.gpus[i].id === id) return latestState.gpus[i];
    }
    return null;
  }

  /* ---------------- 自动温度曲线 ---------------- */

  function svgEl(tag, attrs, text) {
    var node = document.createElementNS(SVG_NS, tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (key) {
        node.setAttribute(key, String(attrs[key]));
      });
    }
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }

  function curveDuty(temp, curve) {
    if (temp <= curve[0][0]) return curve[0][1];
    if (temp >= curve[curve.length - 1][0]) return curve[curve.length - 1][1];
    for (var i = 1; i < curve.length; i++) {
      var left = curve[i - 1];
      var right = curve[i];
      if (temp <= right[0]) {
        var ratio = (temp - left[0]) / (right[0] - left[0]);
        return Math.round(left[1] + ratio * (right[1] - left[1]));
      }
    }
    return curve[curve.length - 1][1];
  }

  function curvePathData(curve, sx, sy) {
    var points = [[CURVE_X_MIN, curveDuty(CURVE_X_MIN, curve)]];
    curve.forEach(function (point) {
      if (point[0] > CURVE_X_MIN && point[0] < CURVE_X_MAX) points.push([point[0], point[1]]);
    });
    points.push([CURVE_X_MAX, curveDuty(CURVE_X_MAX, curve)]);
    var line = points.map(function (point, index) {
      return (index === 0 ? 'M' : 'L') + sx(point[0]) + ' ' + sy(point[1]);
    }).join(' ');
    return {
      line: line,
      area: line + ' L' + sx(CURVE_X_MAX) + ' ' + sy(0)
        + ' L' + sx(CURVE_X_MIN) + ' ' + sy(0) + ' Z'
    };
  }

  function renderCurve(s) {
    hideCurveHover();
    var limits = (s && s.limits) || {};
    var gpus = Array.isArray(s.gpus) ? s.gpus : [];
    var curve = Array.isArray(limits.auto_curve) ? limits.auto_curve : null;
    var overrideTemp = numOr(limits.thermal_override_temp, 85);
    curveMeta.textContent = '热保护 ' + overrideTemp + '°C · 手动下限 '
      + numOr(limits.min_manual_duty, SLIDER_MIN) + '%';
    renderCurveStatus(s);
    if (curveResetBtn) {
      curveResetBtn.disabled = curvePending || curveEditing !== null || !latestState;
    }

    if (!curve || curve.length < 2) {
      curveSvg.replaceChildren();
      curveTableBody.replaceChildren();
      curveGeom = null;
      return;
    }
    // 拖动或保存期间不重建 SVG，避免指针捕获/键盘焦点被 1 秒轮询打断
    if (curveEditing !== null || curvePending || curveSvg.contains(document.activeElement)) {
      renderCurveTable(gpus);
      return;
    }

    var W = 720, H = 300, L = 58, R = 22, T = 26, B = 44;
    var pw = W - L - R, ph = H - T - B;
    var xSpan = CURVE_X_MAX - CURVE_X_MIN;
    var sx = function (temp) { return L + (clamp(temp, CURVE_X_MIN, CURVE_X_MAX) - CURVE_X_MIN) / xSpan * pw; };
    var sy = function (duty) { return T + (1 - clamp(duty, 0, 100) / 100) * ph; };

    curveSvg.replaceChildren();
    curveSvg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
    curveSvg.setAttribute('aria-label',
      '自动温度曲线：' + CURVE_X_MIN + ' 至 ' + CURVE_X_MAX
      + ' 摄氏度对应风扇占空比，可拖动 30 至 80 摄氏度的蓝色控制点调整');

    // 横向网格与 Y 轴刻度
    [0, 20, 40, 60, 80, 100].forEach(function (duty) {
      var y = sy(duty);
      curveSvg.appendChild(svgEl('line', { x1: L, y1: y, x2: L + pw, y2: y, class: 'curve-grid' }));
      curveSvg.appendChild(svgEl('text', {
        x: L - 10, y: y + 4, 'text-anchor': 'end', class: 'curve-tick'
      }, duty + '%'));
    });

    // X 轴刻度
    for (var temp = CURVE_X_MIN; temp <= CURVE_X_MAX; temp += 10) {
      var x = sx(temp);
      curveSvg.appendChild(svgEl('line', { x1: x, y1: T + ph, x2: x, y2: T + ph + 5, class: 'curve-axis' }));
      curveSvg.appendChild(svgEl('text', {
        x: x, y: T + ph + 20, 'text-anchor': 'middle', class: 'curve-tick'
      }, temp + '°'));
    }

    // 坐标轴
    curveSvg.appendChild(svgEl('line', { x1: L, y1: T, x2: L, y2: T + ph, class: 'curve-axis' }));
    curveSvg.appendChild(svgEl('line', { x1: L, y1: T + ph, x2: L + pw, y2: T + ph, class: 'curve-axis' }));
    curveSvg.appendChild(svgEl('text', {
      x: L + pw / 2, y: H - 6, 'text-anchor': 'middle', class: 'curve-axis-title'
    }, 'GPU 温度（°C）'));
    curveSvg.appendChild(svgEl('text', {
      x: 16, y: T + ph / 2, 'text-anchor': 'middle', class: 'curve-axis-title',
      transform: 'rotate(-90 16 ' + (T + ph / 2) + ')'
    }, '风扇占空比（%）'));

    // 热保护区域
    var thresholdX = sx(overrideTemp);
    if (thresholdX < L + pw) {
      curveSvg.appendChild(svgEl('rect', {
        x: thresholdX, y: T, width: L + pw - thresholdX, height: ph,
        fill: COLORS.critical, opacity: 0.05
      }));
      curveSvg.appendChild(svgEl('line', {
        x1: thresholdX, y1: T, x2: thresholdX, y2: T + ph, class: 'curve-threshold'
      }));
      curveSvg.appendChild(svgEl('text', {
        x: thresholdX + 6, y: T + 14, class: 'curve-threshold-text'
      }, '热保护 ' + overrideTemp + '°C'));
    }

    // 曲线
    var paths = curvePathData(curve, sx, sy);
    var areaPath = svgEl('path', { d: paths.area, fill: COLORS.line, opacity: 0.1 });
    var linePath = svgEl('path', { d: paths.line, class: 'curve-line' });
    curveSvg.appendChild(areaPath);
    curveSvg.appendChild(linePath);

    // 悬停十字线（在控制点之下）
    var hoverLine = svgEl('line', { x1: L, y1: T, x2: L, y2: T + ph, class: 'curve-hover', visibility: 'hidden' });
    var hoverDot = svgEl('circle', { cx: L, cy: T, r: 4.5, class: 'curve-hover-dot', visibility: 'hidden' });
    curveSvg.appendChild(hoverLine);
    curveSvg.appendChild(hoverDot);

    // 可拖拽控制点：30/40/50/60/70/80°C
    var handles = [];
    CURVE_HANDLE_TEMPS.forEach(function (handleTemp) {
      var duty = curveDuty(handleTemp, curve);
      var cx = sx(handleTemp);
      var cy = sy(duty);
      var group = svgEl('g', {
        class: 'curve-handle', tabindex: '0', role: 'slider',
        'aria-label': handleTemp + '°C 控制点',
        'aria-valuemin': '0', 'aria-valuemax': '100',
        'aria-valuenow': String(duty), 'aria-valuetext': duty + '%'
      });
      var hit = svgEl('circle', { cx: cx, cy: cy, r: 15, class: 'curve-handle-hit' });
      var dot = svgEl('circle', { cx: cx, cy: cy, r: 6.5, class: 'curve-handle-dot' });
      var title = svgEl('title', null,
        '拖动调整 ' + handleTemp + '°C 的控制点（当前 ' + duty + '%）');
      group.appendChild(hit);
      group.appendChild(dot);
      group.appendChild(title);
      curveSvg.appendChild(group);
      var handle = { temp: handleTemp, group: group, hit: hit, dot: dot, title: title };
      handles.push(handle);
      attachCurveHandle(handle);
    });

    // 当前 GPU 位置：圆点内为 GPU 编号；位置接近时纵向错开并用引线指回真实坐标
    var markers = [];
    gpus.forEach(function (gpu) {
      var gpuTemp = gpu.temperature_c;
      if (typeof gpuTemp !== 'number' || gpu.sensor_stale) return;
      var duty = (typeof gpu.requested_duty === 'number') ? gpu.requested_duty : 0;
      markers.push({ gpu: gpu, x: sx(gpuTemp), y: sy(duty) });
    });
    markers.sort(function (a, b) { return a.y - b.y || a.x - b.x; });

    var placed = [];
    markers.forEach(function (marker) {
      var step = 22;
      var offsets = [0, -step, step, -2 * step, 2 * step, -3 * step, 3 * step];
      var displayY = marker.y;
      for (var oi = 0; oi < offsets.length; oi++) {
        var candidate = clamp(marker.y + offsets[oi], T + 10, T + ph - 10);
        var free = true;
        for (var pi = 0; pi < placed.length; pi++) {
          if (Math.abs(placed[pi].x - marker.x) < 20 && Math.abs(placed[pi].y - candidate) < 20) {
            free = false;
            break;
          }
        }
        if (free) { displayY = candidate; break; }
      }
      marker.displayY = displayY;
      placed.push({ x: marker.x, y: displayY });
    });

    markers.forEach(function (marker) {
      var gpu = marker.gpu;
      if (Math.abs(marker.displayY - marker.y) > 1) {
        curveSvg.appendChild(svgEl('line', {
          x1: marker.x, y1: marker.y, x2: marker.x, y2: marker.displayY,
          class: 'curve-leader'
        }));
      }
      var dot = svgEl('circle', {
        cx: marker.x, cy: marker.displayY, r: 7.5, class: 'curve-dot'
      });
      dot.appendChild(svgEl('title', null,
        'GPU' + gpu.id + ' · ' + gpu.temperature_c.toFixed(1) + '°C · 目标 '
        + gpu.requested_duty + '% · 实际 '
        + (gpu.actual_duty === 0 ? 'BMC 自动' : gpu.actual_duty + '%')));
      curveSvg.appendChild(dot);
      curveSvg.appendChild(svgEl('text', {
        x: marker.x, y: marker.displayY, 'text-anchor': 'middle',
        'dominant-baseline': 'central', class: 'curve-gpu-num'
      }, gpu.id));
    });

    curveGeom = {
      L: L, T: T, pw: pw, ph: ph,
      curve: curve, hoverLine: hoverLine, hoverDot: hoverDot, sx: sx, sy: sy,
      linePath: linePath, areaPath: areaPath, handles: handles
    };

    renderCurveTable(gpus);
  }

  function renderCurveTable(gpus) {
    curveTableBody.replaceChildren();
    gpus.forEach(function (gpu) {
      var row = document.createElement('tr');

      var nameCell = el('td');
      nameCell.appendChild(el('span', 'curve-swatch'));
      nameCell.appendChild(document.createTextNode('GPU ' + gpu.id));
      row.appendChild(nameCell);

      var temp = gpu.temperature_c;
      var tempText = (typeof temp === 'number') ? temp.toFixed(1) + ' °C' : '—';
      if (gpu.sensor_stale) tempText += '（过期）';
      row.appendChild(el('td', null, tempText));

      row.appendChild(el('td', null, dutyText(gpu.requested_duty)));
      row.appendChild(el('td', null, actualDutyText(gpu.actual_duty)));

      var mode = gpu.mode === 'manual' ? '手动' : '自动';
      if (gpu.thermal_override) mode += ' · 热保护';
      row.appendChild(el('td', null, mode));

      curveTableBody.appendChild(row);
    });
  }

  function dutyText(value) {
    return (typeof value === 'number') ? value + '%' : '—';
  }

  function actualDutyText(value) {
    if (typeof value !== 'number') return '—';
    return value === 0 ? 'BMC 自动' : value + '%';
  }

  function setCurveStatus(text, kind, ttl) {
    if (!curveStatusEl) return;
    curveStatusEl.textContent = text || '';
    curveStatusEl.className = 'curve-status' + (kind ? ' is-' + kind : '');
    if (ttl) curveNotice = { text: text, kind: kind, until: Date.now() + ttl };
    else if (!text) curveNotice = null;
  }

  function renderCurveStatus(s) {
    if (curvePending) {
      setCurveStatus('正在保存曲线…', 'pending', 0);
      return;
    }
    if (s && s.curve_save_error) {
      setCurveStatus('已生效，但保存失败：' + s.curve_save_error, 'error', 0);
      return;
    }
    if (curveNotice && Date.now() < curveNotice.until) {
      curveStatusEl.textContent = curveNotice.text;
      curveStatusEl.className = 'curve-status' + (curveNotice.kind ? ' is-' + curveNotice.kind : '');
      return;
    }
    if (curveNotice) curveNotice = null;
    if (curveStatusEl.textContent) setCurveStatus('', '', 0);
  }

  function setCurveDuty(curve, temp, duty) {
    for (var i = 0; i < curve.length; i++) {
      if (Math.abs(Number(curve[i][0]) - temp) < 1e-6) {
        curve[i][1] = duty;
        return;
      }
    }
    curve.push([temp, duty]);
    curve.sort(function (a, b) { return Number(a[0]) - Number(b[0]); });
  }

  function refreshCurvePreview() {
    if (!curveGeom || !curveDraft) return;
    var paths = curvePathData(curveDraft, curveGeom.sx, curveGeom.sy);
    curveGeom.linePath.setAttribute('d', paths.line);
    curveGeom.areaPath.setAttribute('d', paths.area);
    curveGeom.handles.forEach(function (handle) {
      var duty = curveDuty(handle.temp, curveDraft);
      var cy = curveGeom.sy(duty);
      handle.hit.setAttribute('cy', cy);
      handle.dot.setAttribute('cy', cy);
      handle.group.setAttribute('aria-valuenow', String(duty));
      handle.group.setAttribute('aria-valuetext', duty + '%');
      if (handle.title) {
        handle.title.textContent = '拖动调整 ' + handle.temp + '°C 的控制点（当前 ' + duty + '%）';
      }
    });
  }

  function showCurveTooltipAt(clientX, clientY, text) {
    var wrap = curveSvg.parentElement.getBoundingClientRect();
    curveTooltip.textContent = text;
    curveTooltip.hidden = false;
    var left = clientX - wrap.left + 14;
    var top = clientY - wrap.top - curveTooltip.offsetHeight - 10;
    if (left + curveTooltip.offsetWidth > wrap.width - 4) {
      left = clientX - wrap.left - curveTooltip.offsetWidth - 14;
    }
    if (top < 0) top = clientY - wrap.top + 14;
    curveTooltip.style.left = Math.max(4, left) + 'px';
    curveTooltip.style.top = Math.max(4, top) + 'px';
  }

  function showCurveTooltipAtNode(node, text) {
    var rect = node.getBoundingClientRect();
    showCurveTooltipAt(rect.left + rect.width / 2, rect.top, text);
  }

  function updateCurveFromPointer(evt) {
    if (!curveGeom || !curveDraft || curveEditing === null) return;
    var point = curveSvg.createSVGPoint();
    point.x = evt.clientX;
    point.y = evt.clientY;
    var ctm = curveSvg.getScreenCTM();
    if (!ctm) return;
    var loc = point.matrixTransform(ctm.inverse());
    var duty = clamp(
      Math.round((curveGeom.T + curveGeom.ph - loc.y) / curveGeom.ph * 100),
      stateLimits.min_manual_duty, 100
    );
    setCurveDuty(curveDraft, curveEditing, duty);
    refreshCurvePreview();
    showCurveTooltipAt(evt.clientX, evt.clientY, curveEditing + '°C → ' + duty + '%');
  }

  function attachCurveHandle(handle) {
    var temp = handle.temp;

    handle.group.addEventListener('pointerdown', function (evt) {
      if (curvePending || !latestState || !latestState.limits) return;
      evt.preventDefault();
      evt.stopPropagation();
      curveEditing = temp;
      curveDraft = (latestState.limits.auto_curve || []).map(function (point) {
        return [Number(point[0]), Number(point[1])];
      });
      if (curveResetBtn) curveResetBtn.disabled = true;
      hideCurveHover();
      try { handle.group.setPointerCapture(evt.pointerId); } catch (e) { /* 忽略 */ }
      updateCurveFromPointer(evt);
    });

    handle.group.addEventListener('pointermove', function (evt) {
      if (curveEditing !== temp) return;
      evt.preventDefault();
      evt.stopPropagation();
      updateCurveFromPointer(evt);
    });

    function finish(evt) {
      if (curveEditing !== temp) return;
      evt.stopPropagation();
      try { handle.group.releasePointerCapture(evt.pointerId); } catch (e) { /* 忽略 */ }
      curveEditing = null;
      hideCurveHover();
      var draft = curveDraft;
      curveDraft = null;
      if (draft) saveCurve(draft);    }

    handle.group.addEventListener('pointerup', finish);
    handle.group.addEventListener('pointercancel', finish);

    handle.group.addEventListener('keydown', function (evt) {
      if (curvePending || !latestState || !latestState.limits) return;
      var delta = null;
      if (evt.key === 'ArrowUp' || evt.key === 'ArrowRight') delta = evt.shiftKey ? 5 : 1;
      else if (evt.key === 'ArrowDown' || evt.key === 'ArrowLeft') delta = -(evt.shiftKey ? 5 : 1);
      else if (evt.key === 'PageUp') delta = 10;
      else if (evt.key === 'PageDown') delta = -10;
      else if (evt.key === 'Home') delta = -1000;
      else if (evt.key === 'End') delta = 1000;
      if (delta === null) return;
      evt.preventDefault();
      evt.stopPropagation();

      curveEditing = temp;
      if (!curveDraft) {
        curveDraft = (latestState.limits.auto_curve || []).map(function (point) {
          return [Number(point[0]), Number(point[1])];
        });
      }
      var current = curveDuty(temp, curveDraft);
      var next = clamp(current + delta, stateLimits.min_manual_duty, 100);
      setCurveDuty(curveDraft, temp, next);
      refreshCurvePreview();
      showCurveTooltipAtNode(handle.dot, temp + '°C → ' + next + '%');

      clearTimeout(curveSaveTimer);
      curveSaveTimer = setTimeout(function () {
        var draft = curveDraft;
        curveDraft = null;
        curveEditing = null;
        if (draft) saveCurve(draft);
      }, 350);
    });
  }

  function saveCurve(curve) {
    if (curvePending) return;
    var payload = curve.map(function (point) {
      return [Number(point[0]), Number(point[1])];
    });
    curvePending = true;
    curveEditing = null;
    curveDraft = null;
    setCurveStatus('正在保存曲线…', 'pending', 0);
    fetch('/api/curve', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ curve: payload }),
      cache: 'no-store'
    })
      .then(function (resp) {
        return resp.json().then(function (data) {
          if (!resp.ok || (data && typeof data.error === 'string')) {
            throw new Error((data && data.error) ? data.error : ('HTTP ' + resp.status));
          }
          return data;
        });
      })
      .then(function (s) {
        curvePending = false;
        latestState = s;
        renderState(s);
        setConnOk(s);
        if (s.curve_save_error) {
          setCurveStatus('已生效，但保存失败：' + s.curve_save_error, 'error', 0);
        } else {
          setCurveStatus('曲线已保存', 'ok', 4000);
        }
      })
      .catch(function (err) {
        curvePending = false;
        setCurveStatus(
          '保存失败：' + (err && err.message ? err.message : '未知错误'),
          'error', 10000
        );
        // 下一次轮询会按服务器上的曲线重建图表
      });
  }

  function resetCurve() {
    if (curvePending || curveEditing !== null || !latestState) return;
    curvePending = true;
    curveDraft = null;
    curveEditing = null;
    if (curveResetBtn) curveResetBtn.disabled = true;
    setCurveStatus('正在恢复默认曲线…', 'pending', 0);
    fetch('/api/curve/reset', { method: 'POST', cache: 'no-store' })
      .then(function (resp) {
        return resp.json().then(function (data) {
          if (!resp.ok || (data && typeof data.error === 'string')) {
            throw new Error((data && data.error) ? data.error : ('HTTP ' + resp.status));
          }
          return data;
        });
      })
      .then(function (s) {
        curvePending = false;
        latestState = s;
        renderState(s);
        setConnOk(s);
        if (s.curve_save_error) {
          setCurveStatus('已恢复，但保存失败：' + s.curve_save_error, 'error', 0);
        } else {
          setCurveStatus('已恢复默认曲线', 'ok', 4000);
        }
      })
      .catch(function (err) {
        curvePending = false;
        setCurveStatus(
          '恢复失败：' + (err && err.message ? err.message : '未知错误'),
          'error', 10000
        );
      });
  }

  if (curveResetBtn) curveResetBtn.addEventListener('click', resetCurve);

  function curvePointerMove(evt) {
    if (!curveGeom) return;
    var point = curveSvg.createSVGPoint();
    point.x = evt.clientX;
    point.y = evt.clientY;
    var ctm = curveSvg.getScreenCTM();
    if (!ctm) return;
    var loc = point.matrixTransform(ctm.inverse());
    var g = curveGeom;
    if (loc.x < g.L || loc.x > g.L + g.pw || loc.y < g.T || loc.y > g.T + g.ph) {
      hideCurveHover();
      return;
    }
    var temp = CURVE_X_MIN + (loc.x - g.L) / g.pw * (CURVE_X_MAX - CURVE_X_MIN);
    var duty = curveDuty(temp, g.curve);
    g.hoverLine.setAttribute('x1', loc.x);
    g.hoverLine.setAttribute('x2', loc.x);
    g.hoverLine.setAttribute('visibility', 'visible');
    g.hoverDot.setAttribute('cx', loc.x);
    g.hoverDot.setAttribute('cy', g.sy(duty));
    g.hoverDot.setAttribute('visibility', 'visible');

    var wrap = curveSvg.parentElement.getBoundingClientRect();
    curveTooltip.textContent = temp.toFixed(0) + '°C → ' + duty + '%';
    curveTooltip.hidden = false;
    var left = evt.clientX - wrap.left + 14;
    var top = evt.clientY - wrap.top - curveTooltip.offsetHeight - 10;
    if (left + curveTooltip.offsetWidth > wrap.width - 4) {
      left = evt.clientX - wrap.left - curveTooltip.offsetWidth - 14;
    }
    if (top < 0) top = evt.clientY - wrap.top + 14;
    curveTooltip.style.left = Math.max(4, left) + 'px';
    curveTooltip.style.top = Math.max(4, top) + 'px';
  }

  function hideCurveHover() {
    curveTooltip.hidden = true;
    if (!curveGeom) return;
    curveGeom.hoverLine.setAttribute('visibility', 'hidden');
    curveGeom.hoverDot.setAttribute('visibility', 'hidden');
  }

  curveSvg.addEventListener('pointermove', curvePointerMove);
  curveSvg.addEventListener('pointerleave', hideCurveHover);

  /* ---------------- 渲染 ---------------- */

  function renderState(s) {
    var readonly = isReadonly(s);
    if (s.limits && typeof s.limits === 'object') {
      stateLimits = {
        min_manual_duty: numOr(s.limits.min_manual_duty, SLIDER_MIN),
        thermal_override_temp: numOr(s.limits.thermal_override_temp, 85),
        thermal_release_temp: numOr(s.limits.thermal_release_temp, 78),
        stale_after: numOr(s.limits.stale_after, 10)
      };
    }
    renderCurve(s);
    renderBanners(s, readonly);

    var seen = new Set();
    var list = Array.isArray(s.gpus) ? s.gpus : [];

    list.forEach(function (g) {
      var id = g.id;
      seen.add(id);
      var card = cardMap.get(id);
      if (!card) {
        card = buildCard(id);
        cardMap.set(id, card);
        cardsEl.appendChild(card.root);
      }
      renderCard(card, g, readonly);
    });

    cardMap.forEach(function (card, id) {
      if (!seen.has(id)) {
        card.root.remove();
        cardMap.delete(id);
      }
    });

    emptyHint.hidden = list.length > 0;
  }

  function isReadonly(s) {
    return !!(s && s.control_enabled === false);
  }

  function numOr(v, fallback) {
    return (typeof v === 'number' && !isNaN(v)) ? v : fallback;
  }

  function renderBanners(s, readonly) {
    dryRunBanner.hidden = false;
    if (s.dry_run) {
      dryRunBanner.className = 'banner banner-dryrun';
      dryRunBanner.innerHTML = '';
      var strong = el('strong', null, '演练模式（dry-run）：');
      dryRunBanner.appendChild(strong);
      dryRunBanner.appendChild(document.createTextNode(
        '当前不会向风扇下发真实控制命令，所有操作仅模拟执行。此横幅不可关闭。'));
    } else {
      dryRunBanner.className = 'banner banner-live';
      dryRunBanner.innerHTML = '';
      var strong2 = el('strong', null, '实机模式：');
      dryRunBanner.appendChild(strong2);
      dryRunBanner.appendChild(document.createTextNode(
        s.control_enabled
          ? '控制命令将真实下发到主板风扇。请确认 GPU 与风扇通道映射正确。此横幅不可关闭。'
          : '尚未启用真实控制（control_enabled=false），当前只监控、不写风扇。此横幅不可关闭。'));
    }

    if (readonly) {
      readonlyBanner.textContent = '只读模式：控制功能已被禁用（control_enabled=false）。所有控制控件已禁用。';
      readonlyBanner.hidden = false;
    } else {
      readonlyBanner.hidden = true;
    }

    // 顶层错误横幅（gpu / ipmi / control / mapping）
    var errs = s.errors || {};
    var lines = [];
    if (errs.gpu) lines.push('GPU 采样错误：' + errs.gpu);
    if (errs.ipmi) lines.push('BMC/IPMI 错误：' + errs.ipmi);
    if (errs.control) lines.push('控制错误：' + errs.control);
    if (errs.mapping) lines.push('GPU 映射警告：' + errs.mapping);
    if (lines.length) {
      errorBanner.textContent = lines.join('\n');
      errorBanner.hidden = false;
    } else {
      errorBanner.hidden = true;
    }
  }

  function renderCard(card, g, readonly) {
    var e = card.els;
    var ctl = getCtl(g.id);
    var tempCrit = stateLimits.thermal_override_temp;
    var tempHigh = Math.max(60, tempCrit - 10);

    e.h2.textContent = 'GPU ' + g.id + ' · ' + (g.name || '未知型号');
    e.pci.textContent = g.pci_bus_id ? ('PCI ' + g.pci_bus_id) : 'PCI —';

    // ---- 温度 ----
    var temp = (typeof g.temperature_c === 'number') ? g.temperature_c : null;
    e.tempValue.innerHTML = '';
    if (temp === null) {
      e.tempValue.appendChild(document.createTextNode('—'));
      e.tempValue.appendChild(el('small', null, ' °C'));
      e.tempFill.style.width = '0%';
      e.tempFill.className = 'temp-bar-fill';
      e.tempBar.setAttribute('aria-label', '核心温度不可用');
    } else {
      e.tempValue.appendChild(document.createTextNode(temp.toFixed(1)));
      e.tempValue.appendChild(el('small', null, ' °C'));
      if (typeof g.temperature_age_seconds === 'number' && g.temperature_age_seconds >= 2) {
        e.tempValue.appendChild(el('span', 'temp-age', '数据 ' + Math.round(g.temperature_age_seconds) + 's 前'));
      }
      var pct = Math.max(0, Math.min(100, temp));
      e.tempFill.style.width = pct + '%';
      e.tempFill.className = 'temp-bar-fill'
        + (temp >= tempCrit ? ' temp-critical' : temp >= tempHigh ? ' temp-high' : '');
      e.tempBar.setAttribute('aria-label', '核心温度 ' + temp.toFixed(1) + ' 摄氏度');
    }

    // ---- 状态徽章 ----
    e.badges.innerHTML = '';
    if (g.thermal_override) {
      e.badges.appendChild(makeBadge('badge-protect', '热保护'));
    } else if (temp !== null && temp >= tempCrit) {
      e.badges.appendChild(makeBadge('badge-critical', '过热'));
    } else if (temp !== null && temp >= tempHigh) {
      e.badges.appendChild(makeBadge('badge-high', '高温'));
    } else {
      e.badges.appendChild(makeBadge('badge-normal', '正常'));
    }
    if (g.sensor_stale) e.badges.appendChild(makeBadge('badge-stale', '传感器过期'));

    card.root.className = 'card'
      + (g.thermal_override ? ' is-protect' : '')
      + (g.sensor_stale ? ' is-stale' : '')
      + (!g.thermal_override && temp !== null && temp >= tempCrit ? ' is-critical' : '')
      + (!g.thermal_override && !g.sensor_stale && temp !== null && temp >= tempHigh && temp < tempCrit ? ' is-high' : '');

    // ---- 指标 ----
    e.rpmDd.textContent = (g.fan_rpm === null || g.fan_rpm === undefined) ? '—' : g.fan_rpm + ' RPM';
    e.rpmDd.title = g.fan_name ? ('主板风扇：' + g.fan_name + '（通道 ' + g.fan_channel + '）') : '';
    e.targetDd.textContent = fmtNum(g.requested_duty) === '—' ? '—' : fmtNum(g.requested_duty) + '%';
    if (g.actual_duty === 0) {
      e.actualDd.textContent = 'BMC 自动';
      e.actualDd.title = '主板当前处于自动控制，控制器尚未接管该通道';
    } else {
      e.actualDd.textContent = fmtNum(g.actual_duty) === '—' ? '—' : fmtNum(g.actual_duty) + '%';
      e.actualDd.title = '';
    }
    var fanLabel = g.fan_name
      ? ('风扇 ' + g.fan_name + (typeof g.fan_channel === 'number' ? '（通道 ' + g.fan_channel + '）' : ''))
      : '主板风扇';
    rpmDtSetText(card, fanLabel);

    // ---- 模式与控件 ----
    var auto = g.mode !== 'manual';
    var minDuty = stateLimits.min_manual_duty;
    e.slider.min = String(minDuty);
    e.sliderLabel.textContent = '占空比（' + minDuty + '–' + SLIDER_MAX + '%）';
    e.autoBtn.setAttribute('aria-pressed', String(auto));
    e.manualBtn.setAttribute('aria-pressed', String(!auto));
    e.autoBtn.disabled = readonly;
    e.manualBtn.disabled = readonly;
    e.slider.disabled = readonly || auto;

    // 滑块值：拖动/本地编辑期间不被 1 秒刷新覆盖
    var effective = (typeof g.requested_duty === 'number') ? g.requested_duty
      : (typeof g.manual_duty === 'number') ? g.manual_duty : null;
    if (!ctl.dragging && !ctl.debounceTimer) {
      e.slider.value = String(effective === null ? minDuty : effective);
      updateSliderFill(e.slider);
    }
    if (!ctl.dragging && !ctl.debounceTimer) {
      e.output.textContent = effective === null ? '—' : effective + '%';
    }

    if (readonly) {
      e.modeHint.textContent = '只读模式，无法修改';
    } else if (auto) {
      e.modeHint.textContent = '自动模式：滑块反映自动目标，已锁定；切换手动后可拖动';
    } else {
      e.modeHint.textContent = ctl.pending ? '正在提交…' : '拖动滑块或按方向键调节，松开即提交';
    }

    // ---- 卡片内错误：alarm 或提交失败 ----
    var msg = g.alarm ? String(g.alarm) : ctl.lastError;
    if (msg) {
      e.cardError.textContent = msg;
      e.cardError.hidden = false;
    } else {
      e.cardError.hidden = true;
    }
  }

  function makeBadge(cls, text) {
    var b = el('span', 'badge ' + cls, text);
    return b;
  }

  function rpmDtSetText(card, text) {
    var dt = card.root.querySelector('.metrics > div:first-child > dt');
    if (dt) dt.textContent = text;
  }

  /* ---------------- 网络请求 ---------------- */

  function poll() {
    if (fetchController) fetchController.abort();
    fetchController = new AbortController();

    fetch('/api/state', { signal: fetchController.signal, cache: 'no-store' })
      .then(function (resp) {
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        return resp.json();
      })
      .then(function (s) {
        latestState = s;
        ctlMap.forEach(function (ctl) { ctl.lastError = null; });
        renderState(s);
        setConnOk(s);
      })
      .catch(function (err) {
        if (err && err.name === 'AbortError') return;
        setConnError();
      })
      .finally(function () {
        setTimeout(poll, REFRESH_INTERVAL_MS);
      });
  }

  function setConnOk(s) {
    connEl.className = 'conn-status conn-ok';
    var t = '';
    if (s && typeof s.timestamp === 'number') {
      var d = new Date(s.timestamp * 1000);
      t = pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
    }
    connEl.textContent = '已连接' + (t ? ' · ' + t : '');
    if (t) lastUpdateEl.textContent = '上次更新：' + t;
    else lastUpdateEl.textContent = '上次更新：—';
  }

  function setConnError() {
    connEl.className = 'conn-status conn-error';
    connEl.textContent = '连接异常，正在重试…';
  }

  function pad(n) { return (n < 10 ? '0' : '') + n; }

  function postControl(id, payload) {
    var ctl = getCtl(id);
    ctl.seq += 1;
    var mySeq = ctl.seq;

    if (ctl.abort) ctl.abort.abort();
    ctl.abort = new AbortController();
    ctl.pending = true;
    updatePendingHint(id);

    fetch('/api/gpus/' + encodeURIComponent(id) + '/control', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: ctl.abort.signal,
      cache: 'no-store'
    })
      .then(function (resp) {
        return resp.json().then(function (data) {
          if (!resp.ok || (data && typeof data.error === 'string')) {
            throw new Error((data && data.error) ? data.error : ('HTTP ' + resp.status));
          }
          return data;
        });
      })
      .then(function (s) {
        if (mySeq !== ctl.seq) return; // 已有更新的请求，丢弃旧响应
        ctl.lastError = null;
        latestState = s;
        renderState(s);
        setConnOk(s);
      })
      .catch(function (err) {
        if (err && err.name === 'AbortError') return;
        if (mySeq !== ctl.seq) return;
        ctl.lastError = '控制提交失败：' + (err && err.message ? err.message : '未知错误');
        var card = cardMap.get(id);
        if (card) {
          card.els.cardError.textContent = ctl.lastError;
          card.els.cardError.hidden = false;
        }
      })
      .finally(function () {
        if (mySeq === ctl.seq) {
          ctl.pending = false;
          updatePendingHint(id);
        }
      });
  }

  function updatePendingHint(id) {
    var card = cardMap.get(id);
    if (!card) return;
    var g = findGpu(id);
    var ctl = getCtl(id);
    if (!g) return;
    var readonly = isReadonly(latestState);
    if (!readonly && g.mode === 'manual') {
      card.els.modeHint.textContent = ctl.pending ? '正在提交…' : '拖动滑块或按方向键调节，松开即提交';
    }
  }

  poll();
})();
