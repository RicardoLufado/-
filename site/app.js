/* 中金所杯 · 一键持仓建议：读取 data/latest.json 并渲染。纯原生 JS，无外部依赖。 */
(function () {
  "use strict";

  var DEFAULT_REPO = "RicardoLufado/-";
  var MODE_SHORT = { A: "模式 A", D: "模式 D", empty: "空仓", ts1: "1 手 TS" };
  var COMPARE_ORDER = ["empty", "ts1", "A", "D"];
  var state = { data: null, histMode: null };

  function $(id) { return document.getElementById(id); }
  function isNum(x) { return typeof x === "number" && isFinite(x); }
  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  var MISSING = '<span class="missing">数据缺失</span>';
  function money(x) { return isNum(x) ? Math.round(x).toLocaleString("zh-CN") : MISSING; }
  function wan(x, d) { return isNum(x) ? (x / 10000).toFixed(d === undefined ? 1 : d) + " 万" : MISSING; }
  function wanShort(x) { return isNum(x) ? (x / 10000).toFixed(1) : MISSING; }
  function pct(x, d) { return isNum(x) ? (x * 100).toFixed(d === undefined ? 1 : d) + "%" : MISSING; }
  function price(x) { return isNum(x) ? String(+x.toFixed(4)) : MISSING; }
  function get(o, path) {
    var parts = path.split(".");
    for (var i = 0; i < parts.length; i++) { if (o == null) return undefined; o = o[parts[i]]; }
    return o;
  }
  function bjTime(iso) {
    // "2026-10-09T08:47:12+08:00" → "10-09 08:47"
    if (!iso) return null;
    var m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso);
    return m ? m[2] + "-" + m[3] + " " + m[4] + ":" + m[5] : iso;
  }
  function ago(iso) {
    if (!iso) return { text: "未知", stale: true };
    var t = new Date(iso).getTime();
    if (!isFinite(t)) return { text: "未知", stale: true };
    var mins = Math.max(0, (Date.now() - t) / 60000);
    var text = mins < 60 ? Math.round(mins) + " 分钟前"
      : mins < 60 * 48 ? (mins / 60).toFixed(1) + " 小时前"
      : (mins / 1440).toFixed(1) + " 天前";
    return { text: text, stale: mins > 24 * 60 };
  }
  function modeById(d, id) {
    var ms = d.modes || [];
    for (var i = 0; i < ms.length; i++) if (ms[i].id === id) return ms[i];
    return null;
  }

  function row(label, value) {
    return '<div class="row"><span class="label">' + label + '</span><span class="value">' + value + "</span></div>";
  }

  function renderSummary(d) {
    var banners = "";
    if (d.synthetic) banners += '<div class="banner bad">⚠ 合成数据（仅测试）：页面上的数字没有任何市场含义，不要据此下单。</div>';
    if (d.fallback) banners += '<div class="banner bad">⚠ 本次计算失败，显示的是上一次成功的结果（' + esc(bjTime(d.fallback.showing) || "无") + "）。原因：" + esc(d.fallback.reason || "未知") + "</div>";
    if (d.market_status === "休市") banners += '<div class="banner warn">今天休市：结果基于最近一个交易日的收盘数据。</div>';
    $("banners").innerHTML = banners;

    var a = ago(d.generated_at);
    var statusCls = d.status === "追赶" ? "bad" : d.status === "锁定" ? "good" : d.status === "持平" ? "warn" : "bad";
    var thr = d.threshold || {};
    var spot = get(d, "account.spot") || {};
    var html = "";
    html += row("数据截至", d.data_asof ? esc(d.data_asof) + " 收盘" : MISSING);
    html += row("计算时间（北京）", d.generated_at ? esc(bjTime(d.generated_at)) + ' · <span class="' + (a.stale ? "bad" : "good") + '">' + a.text + "</span>" : MISSING);
    html += row("当前总金额", '<span class="big num">' + money(get(d, "account.total")) + "</span>");
    html += row("　现货（模型估计）", '<span class="num">' + money(spot.value) + "</span>" +
      (isNum(spot.etf) ? '<br><span class="muted small">ETF ' + wan(spot.etf) + " + 债券 " + wan(spot.bond) + "</span>" : ""));
    var acc = d.account || {};
    var eqNote = acc.futures_equity_estimated
      ? "App 报告 " + wan(acc.futures_equity_reported) + "（" + esc(String(acc.updated_at || "").slice(5, 10)) + "）" +
        (acc.futures_equity >= acc.futures_equity_reported ? " + " : " − ") + "盯市 " +
        wan(Math.abs(acc.futures_equity - acc.futures_equity_reported))
      : "account.json 更新于 " + esc(acc.updated_at || "");
    html += row(acc.futures_equity_estimated ? "　期货权益（模型估计）" : "　期货权益",
      '<span class="num">' + money(acc.futures_equity) + '</span><br><span class="muted small">' + eqNote + "</span>");
    html += row("晋级线区间", isNum(thr.low) ? wan(thr.low, 0) + " – " + wan(thr.high, 0) : MISSING);
    html += row("当前状态", d.status ? '<span class="pill ' + statusCls + '">' + esc(d.status) + "</span>" : MISSING);
    if (d.horizon) html += row("剩余交易日", d.horizon.trading_days + " 天（" + esc(String(d.horizon.start).slice(5)) + " → " + esc(String(d.horizon.end).slice(5)) + "）");
    $("summary").innerHTML = html;
  }

  function renderWarnings(d) {
    var w = d.warnings || [];
    $("warnings-sec").hidden = w.length === 0;
    $("warn-count").textContent = w.length;
    $("warnings").innerHTML = w.map(function (x) { return "<li>" + esc(x) + "</li>"; }).join("");
  }

  function metricChips(m) {
    if (!m) return "";
    return '<div class="chips">' +
      '<span class="chip">晋级概率 <b>' + pct(m.promotion_prob) + "</b></span>" +
      '<span class="chip">爆仓 <b>' + pct(m.liquidation_prob) + "</b></span>" +
      '<span class="chip">穿仓 <b>' + pct(m.wipeout_prob) + "</b></span>" +
      '<span class="chip">保证金占用 <b>' + pct(m.margin_ratio) + "</b></span>" +
      '<span class="chip">首日 99% VaR <b>' + wan(m.var99_1d) + "</b></span>" +
      '<span class="chip">总金额中位数 <b>' + wan(m.W_p50) + "</b></span>" +
      "</div>";
  }

  function renderPositions(d) {
    var m = modeById(d, d.recommended_mode);
    if (!m) { $("positions").innerHTML = MISSING; $("orders").innerHTML = MISSING; $("rec-mode").textContent = ""; return; }
    $("rec-mode").textContent = m.name;
    var rob = m.robustness;
    var html = "";
    if (!m.target_positions || m.target_positions.length === 0) {
      html += '<p><strong>空仓</strong>：模型认为不持有任何期货最好。</p>';
    } else {
      html += '<ul class="pos-list">' + m.target_positions.map(function (p) {
        var sideCls = p.side === "long" ? "side-long" : "side-short";
        return "<li><div class=\"pos-head\"><span>" + esc(p.name) + ' <span class="muted small">' + esc(p.code) + "</span></span>" +
          '<span class="' + sideCls + '">' + (p.side === "long" ? "多" : "空") + " " + p.lots + " 手</span></div>" +
          '<div class="pos-sub"><span>参考价 <b class="num">' + price(p.ref_price) + "</b></span>" +
          "<span>占用保证金 <b class=\"num\">" + money(p.margin) + "</b></span></div></li>";
      }).join("") + "</ul>";
    }
    html += metricChips(m.metrics);
    if (rob && isNum(rob.min)) {
      html += '<p class="muted small">换 3 个随机种子重算最优解：晋级概率 ' + pct(rob.min) + " – " + pct(rob.max) +
        (rob.same_solution ? "，三次最优手数相同。" : "，三次最优手数不完全相同（见方案对比下方明细）。") + "</p>";
    }
    $("positions").innerHTML = html;

    var orders = m.orders || [];
    if (orders.length === 0) {
      $("orders").innerHTML = "<p>无需下单：当前持仓已经等于目标持仓。</p>" +
        (m.target_positions && m.target_positions.length === 0 ? '<p class="bad small">注意：晋级要求至少完成一笔有效交易（委托并成交）。</p>' : "");
    } else {
      $("orders").innerHTML = '<ol class="orders">' + orders.map(function (o) {
        return "<li>" + esc(o.name) + "（" + esc(o.code) + "）<strong>" + esc(o.direction) + " " + esc(o.offset) + " " + o.lots + " 手</strong>，参考价 " +
          (isNum(o.ref_price) ? '<b class="num">' + price(o.ref_price) + "</b>" : MISSING) + "</li>";
      }).join("") + "</ol>" +
        '<p class="muted small">建议用限价单；参考价来自' + refSourceText(d) + "，开盘后请以 App 实时价为准。先平后开。</p>";
    }
  }

  function refSourceText(d) {
    var src = {};
    (d.contracts || []).forEach(function (c) { if (c.ref_source) src[c.ref_source] = 1; });
    var k = Object.keys(src);
    return k.length ? k.join(" / ") : "最近收盘价";
  }

  function renderCompare(d) {
    var cols = COMPARE_ORDER.map(function (id) { return modeById(d, id); }).filter(Boolean);
    if (!cols.length) { $("compare").innerHTML = MISSING; return; }
    var rec = d.recommended_mode;
    function th(m) { return '<th class="' + (m.id === rec ? "rec" : "") + '">' + esc(MODE_SHORT[m.id] || m.id) + (m.id === rec ? "<br><span class=\"small\">推荐</span>" : "") + "</th>"; }
    function tr(label, f) {
      return "<tr><td>" + label + "</td>" + cols.map(function (m) {
        return '<td class="num ' + (m.id === rec ? "rec" : "") + '">' + f(m) + "</td>";
      }).join("") + "</tr>";
    }
    var html = '<ul class="legend">' + cols.map(function (m) {
      return "<li><b>" + esc(MODE_SHORT[m.id] || m.id) + "</b>：" + esc(m.summary || "") + "</li>";
    }).join("") + "</ul>";
    html += '<table class="compact"><thead><tr><th></th>' + cols.map(th).join("") + "</tr></thead><tbody>";
    html += tr("晋级概率", function (m) { return pct(get(m, "metrics.promotion_prob")); });
    html += tr("爆仓概率", function (m) { return pct(get(m, "metrics.liquidation_prob")); });
    html += tr("穿仓概率", function (m) { return pct(get(m, "metrics.wipeout_prob")); });
    html += tr("中位数", function (m) { return wanShort(get(m, "metrics.W_p50")); });
    html += tr("5%分位", function (m) { return wanShort(get(m, "metrics.W_p05")); });
    html += tr("95%分位", function (m) { return wanShort(get(m, "metrics.W_p95")); });
    html += tr("保证金", function (m) { return pct(get(m, "metrics.margin_ratio")); });
    html += tr("首日VaR99", function (m) { return wanShort(get(m, "metrics.var99_1d")); });
    html += "</tbody></table>";
    html += '<p class="muted small">中位数 / 分位 / VaR 单位：万元（期末总金额，模型估计）；保证金 = 开仓保证金占期货权益比例。</p>';
    var A = modeById(d, "A");
    var runs = get(A, "robustness.runs");
    if (runs && runs.length) {
      html += '<p class="muted small">稳健性（模式 A 换种子重算）：' + runs.map(function (r) {
        var lots = Object.keys(r.lots).filter(function (k) { return r.lots[k] !== 0; })
          .map(function (k) { return k + (r.lots[k] > 0 ? "+" : "") + r.lots[k]; }).join(" ") || "空仓";
        return "seed " + r.seed + "：" + lots + "，" + pct(r.promotion_prob);
      }).join("；") + "。路径数 " + (get(d, "engine.paths_final") || "?") + "。</p>";
    }
    $("compare").innerHTML = html;
  }

  function renderHistogram(d) {
    var h = d.histogram;
    if (!h || !h.modes) { $("hist").innerHTML = MISSING; $("hist-tabs").innerHTML = ""; return; }
    var ids = COMPARE_ORDER.filter(function (id) { return h.modes[id]; });
    if (!ids.length) { $("hist").innerHTML = MISSING; return; }
    if (!state.histMode || ids.indexOf(state.histMode) < 0) state.histMode = h.modes[d.recommended_mode] ? d.recommended_mode : ids[0];
    $("hist-tabs").innerHTML = ids.map(function (id) {
      return '<button type="button" role="tab" data-id="' + id + '" aria-selected="' + (id === state.histMode) + '">' + esc(MODE_SHORT[id] || id) + "</button>";
    }).join("");
    Array.prototype.forEach.call($("hist-tabs").querySelectorAll("button"), function (b) {
      b.onclick = function () { state.histMode = b.getAttribute("data-id"); renderHistogram(d); };
    });

    var e = h.modes[state.histMode].edges, s = h.modes[state.histMode].freq;
    var W = 360, H = 200, L = 34, R = 8, T = 22, B = 30;
    var x0 = e[0], x1 = e[e.length - 1];
    var ymax = Math.max.apply(null, s.concat([0.0001]));
    function X(v) { return L + (v - x0) / (x1 - x0) * (W - L - R); }
    function Y(v) { return T + (1 - v / ymax) * (H - T - B); }
    var svg = '<svg viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="期末总金额分布直方图">';
    var thr = d.threshold || {};
    if (isNum(thr.low)) {
      var bx0 = X(thr.low), bx1 = X(thr.high);
      svg += '<rect class="band" x="' + bx0.toFixed(1) + '" y="' + (T - 14) + '" width="' + Math.max(1, bx1 - bx0).toFixed(1) + '" height="' + (H - B - T + 14) + '"></rect>';
      svg += '<text class="band-label" x="' + ((bx0 + bx1) / 2).toFixed(1) + '" y="' + (T - 4) + '" text-anchor="middle">晋级线区间</text>';
    }
    for (var i = 0; i < s.length; i++) {
      var mid = (e[i] + e[i + 1]) / 2;
      var cls = isNum(thr.low) && mid >= thr.low ? "bar hit" : "bar";
      var rx = X(e[i]), rw = Math.max(0.5, X(e[i + 1]) - X(e[i]) - 0.6), ry = Y(s[i]);
      svg += '<rect class="' + cls + '" x="' + rx.toFixed(1) + '" y="' + ry.toFixed(1) + '" width="' + rw.toFixed(1) + '" height="' + Math.max(0, H - B - ry).toFixed(1) + '"><title>' +
        (e[i] / 10000).toFixed(1) + "–" + (e[i + 1] / 10000).toFixed(1) + " 万：" + (s[i] * 100).toFixed(2) + "%</title></rect>";
    }
    var total = get(d, "account.total");
    if (isNum(total) && total > x0 && total < x1) {
      svg += '<line class="now" x1="' + X(total).toFixed(1) + '" x2="' + X(total).toFixed(1) + '" y1="' + T + '" y2="' + (H - B) + '"></line>';
    }
    svg += '<line class="axis" x1="' + L + '" x2="' + (W - R) + '" y1="' + (H - B) + '" y2="' + (H - B) + '"></line>';
    var step = niceStep((x1 - x0) / 5);
    for (var v = Math.ceil(x0 / step) * step; v <= x1; v += step) {
      svg += '<text x="' + X(v).toFixed(1) + '" y="' + (H - B + 14) + '" text-anchor="middle">' + (v / 10000).toFixed(step >= 10000 ? 0 : 1) + "</text>";
    }
    svg += '<text x="' + (W - R) + '" y="' + (H - 4) + '" text-anchor="end">万元</text>';
    for (var k = 0; k <= 2; k++) {
      var yv = ymax * k / 2;
      svg += '<text x="' + (L - 4) + '" y="' + (Y(yv) + 3).toFixed(1) + '" text-anchor="end">' + (yv * 100).toFixed(1) + "%</text>";
    }
    svg += "</svg>";
    $("hist").innerHTML = svg;
    var m = modeById(d, state.histMode);
    $("hist-note").innerHTML = "阴影 = 晋级线区间；深色柱 = 达到区间下沿；红色虚线 = 当前总金额。" +
      (m ? esc(m.summary) + "：晋级概率 " + pct(get(m, "metrics.promotion_prob")) + "。" : "") + (h.note ? esc(h.note) + "。" : "");
  }

  function niceStep(raw) {
    var p = Math.pow(10, Math.floor(Math.log(raw) / Math.LN10)), n = raw / p;
    return (n < 1.5 ? 1 : n < 3.5 ? 2 : n < 7.5 ? 5 : 10) * p;
  }

  function renderLists(d) {
    $("explain").innerHTML = (d.explanations || []).map(function (x) { return "<li>" + esc(x) + "</li>"; }).join("") || "<li>" + MISSING + "</li>";
    $("limits").innerHTML = (d.limitations || []).map(function (x) { return "<li>" + esc(x) + "</li>"; }).join("");
    var src = d.data_sources || [];
    var stName = { ok: "成功", cache: "用缓存", missing: "缺失", synthetic: "合成" };
    $("sources").innerHTML = src.length ? "<table><thead><tr><th>数据</th><th>状态</th><th>条数</th><th>日期范围</th><th>抓取时间</th></tr></thead><tbody>" +
      src.map(function (s) {
        var cls = s.status === "ok" ? "good" : "bad";
        return "<tr><td class=\"wrap\">" + esc(s.name) + '<br><span class="muted small">' + esc(s.call) + "</span></td>" +
          '<td class="' + cls + '">' + (stName[s.status] || esc(s.status)) + "</td><td class=\"num\">" + (s.rows || 0) + "</td>" +
          "<td>" + (s.first_date ? esc(s.first_date) + "<br>" + esc(s.last_date) : (s.status === "ok" ? "" : MISSING)) + "</td>" +
          "<td>" + esc(bjTime(s.fetched_at) || "") + "</td></tr>";
      }).join("") + "</tbody></table>" : MISSING;

    var cs = d.contracts || [];
    $("contracts").innerHTML = cs.length ? "<table><thead><tr><th>合约</th><th>t0 收盘</th><th>参考价</th><th>1 手保证金</th><th>基差</th><th>年化波动</th></tr></thead><tbody>" +
      cs.map(function (c) {
        return "<tr><td>" + esc(c.name) + '<br><span class="muted small">' + esc(c.code) + "</span></td>" +
          '<td class="num">' + price(c.F0) + "</td>" +
          '<td class="num">' + price(c.ref_price) + '<br><span class="muted small">' + esc(c.ref_source || "") + "</span></td>" +
          '<td class="num">' + money(c.margin_per_lot) + "</td>" +
          '<td class="num">' + (c.kind === "equity" ? (isNum(c.basis) ? (c.basis * 100).toFixed(2) + "%" : MISSING) : "—") + "</td>" +
          '<td class="num">' + pct(c.vol_annual) + "</td></tr>";
      }).join("") + "</tbody></table>" : MISSING;

    var en = d.engine || {};
    var repo = en.repo || DEFAULT_REPO;
    $("gh").href = "https://github.com/" + repo + "/actions/workflows/recommend.yml";
    $("engine-info").innerHTML = "引擎：akshare " + esc(en.akshare_version || "—") + " · Python " + esc(en.python || "—") +
      " · 路径 " + esc(en.paths_screen || "—") + "/" + esc(en.paths_final || "—") + " · 用时 " + esc(en.runtime_sec || "—") + "s" +
      (en.run_url ? ' · <a href="' + esc(en.run_url) + '" target="_blank" rel="noopener">本次运行日志</a>' : "");
  }

  function render(d) {
    state.data = d;
    renderSummary(d);
    renderWarnings(d);
    renderPositions(d);
    renderCompare(d);
    renderHistogram(d);
    renderLists(d);
  }

  function load() {
    $("summary").innerHTML = '<p class="muted">正在读取 latest.json…</p>';
    var xhr = new XMLHttpRequest();
    xhr.open("GET", "data/latest.json?t=" + Date.now(), true);
    xhr.setRequestHeader("Cache-Control", "no-cache");
    xhr.onload = function () {
      if (xhr.status !== 200) return fail("HTTP " + xhr.status);
      try { render(JSON.parse(xhr.responseText)); } catch (e) { fail("解析失败：" + e.message); }
    };
    xhr.onerror = function () { fail("网络错误"); };
    xhr.send();
  }

  function fail(msg) {
    $("summary").innerHTML = '<p class="bad">读取 latest.json 失败（' + esc(msg) + "）。可能还没有运行过 recommend 工作流，请点下方「去 GitHub 重新计算」。</p>";
  }

  $("reload").onclick = load;
  load();
})();
