/* Baptou Stocks — static dashboard. No dependencies, no network beyond the
   one data file. */
(function () {
  "use strict";

  var DATA = null;
  var state = { theme: "all", dir: "all", action: "all", conv: 55,
                q: "", sort: "conviction", open: {} };

  var $ = function (id) { return document.getElementById(id); };

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;",
               '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function num(v, digits) {
    if (v == null || isNaN(v)) return "–";
    return Number(v).toFixed(digits == null ? 2 : digits);
  }

  function signed(v, digits) {
    if (v == null || isNaN(v)) return "–";
    return (v > 0 ? "+" : "") + Number(v).toFixed(digits == null ? 1 : digits);
  }

  function money(v) {
    if (v == null || isNaN(v)) return "–";
    if (v >= 1e9) return (v / 1e9).toFixed(1) + "B";
    if (v >= 1e6) return (v / 1e6).toFixed(0) + "M";
    if (v >= 1e3) return (v / 1e3).toFixed(0) + "K";
    return String(Math.round(v));
  }

  /* ------------------------------------------------------------- header -- */
  function renderMarket() {
    var m = DATA.market, s = DATA.stats;
    var reg = String(m.regime || "").replace("-", "");
    $("market").innerHTML =
      chip("SPY", num(m.benchmark_price) + " (" + signed(m.benchmark_ret_21d) + "% 21d)") +
      chip("Breadth", Math.round((m.breadth_above_200dma || 0) * 100) + "% above 200DMA") +
      '<span class="mchip"><span class="k">Regime</span><b class="regime-' +
        esc(reg) + '">' + esc(m.regime) + "</b></span>" +
      chip("Scanned", s.liquid + " liquid / " + s.universe + " listed");

    $("stamp").textContent =
      "Data as of " + DATA.as_of_date + " · built " +
      new Date(DATA.generated_at).toLocaleString() +
      " · " + s.opportunities + " opportunities from " + s.setup_hits +
      " setup hits (" + s.build_seconds + "s)";
  }

  function chip(k, v) {
    return '<span class="mchip"><span class="k">' + esc(k) + "</span><b>" +
           esc(v) + "</b></span>";
  }

  /* ---------------------------------------------------------- filtering -- */
  var ACTION_GROUPS = {
    equity: ["long", "short"],
    options: ["long_call", "long_put", "debit_spread", "bull_put_spread",
              "bear_call_spread"],
    premium_sell: ["bull_put_spread", "bear_call_spread"],
    premium_buy: ["long_call", "long_put", "debit_spread"],
    leveraged: ["leveraged_long", "leveraged_short"]
  };

  function matches(o) {
    if (state.theme !== "all" && o.theme !== state.theme) return false;
    if (state.dir !== "all" && o.direction !== state.dir) return false;
    if (o.conviction < state.conv) return false;

    if (state.action !== "all") {
      var want = ACTION_GROUPS[state.action] || [];
      var hit = o.actions.some(function (a) { return want.indexOf(a.type) >= 0; });
      if (!hit) return false;
    }

    if (state.q) {
      var hay = (o.ticker + " " + o.name + " " + o.theme_label + " " +
                 o.industry + " " +
                 o.setups.map(function (s) { return s.label; }).join(" ")
                ).toLowerCase();
      if (hay.indexOf(state.q) < 0) return false;
    }
    return true;
  }

  function sortKey(o) {
    switch (state.sort) {
      case "score": return -(o.setups[0] ? o.setups[0].score : 0);
      case "rr": return -(o.levels.risk_reward || 0);
      case "liquidity": return -(o.metrics.dollar_vol || 0);
      default: return -o.conviction;
    }
  }

  /* -------------------------------------------------------------- cards -- */
  function levelCells(o) {
    var L = o.levels, up = o.direction === "long";
    return '<div class="levels">' +
      cell("Entry", "$" + num(L.entry)) +
      cell("Stop", "$" + num(L.stop), up ? "dn" : "up") +
      cell("Target", "$" + num(L.target), up ? "up" : "dn") +
      cell("R:R", num(L.risk_reward, 2) + "×") +
      "</div>";
  }

  function cell(k, v, cls) {
    return '<div class="lv"><div class="k">' + esc(k) + '</div><div class="v ' +
           (cls || "") + '">' + esc(v) + "</div></div>";
  }

  function mcell(k, v) {
    return '<div><div class="k">' + esc(k) + '</div><div class="v">' +
           esc(v) + "</div></div>";
  }

  function ivPill(m) {
    if (!m.iv_regime || m.iv_regime === "unknown") return "";
    var cls = m.iv_regime === "rich" ? "rich"
            : m.iv_regime === "cheap" ? "cheap"
            : m.iv_regime === "unreliable" ? "unreliable" : "";
    var txt = m.iv_regime === "unreliable"
      ? "IV unreliable"
      : "IV " + m.iv_regime + (m.iv_rv ? " (" + num(m.iv_rv, 2) + "×RV)" : "");
    return '<span class="pill ' + cls + '">' + esc(txt) + "</span>";
  }

  function actionBlock(a) {
    var h = '<div class="action"><div class="ttl">' + esc(a.label) +
            '<span class="kind">' + esc(a.type.replace(/_/g, " ")) + "</span></div>";
    if (a.instrument && a.instrument !== a.label) {
      h += '<div class="instr">' + esc(a.instrument) + "</div>";
    }
    h += '<div class="why">' + esc(a.rationale) + "</div>";
    (a.warnings || []).forEach(function (w) {
      h += '<div class="warn">' + esc(w) + "</div>";
    });
    return h + "</div>";
  }

  function detail(o) {
    var h = '<div class="detail">';

    h += "<section><h4>Setups matched</h4>";
    o.setups.forEach(function (s) {
      h += '<div class="setup-detail"><div class="nm">' + esc(s.label) +
           " · " + num(s.score, 1) + "</div>" +
           "<p>" + esc(s.thesis) + "</p>" +
           '<p class="inval"><strong>Invalidated by:</strong> ' +
           esc(s.invalidation) + "</p>" +
           '<div class="ev">' +
           (s.evidence || []).map(function (e) {
             return "<span>" + esc(e) + "</span>";
           }).join("") + "</div></div>";
    });
    h += "</section>";

    h += "<section><h4>Ways to express it</h4>";
    o.actions.forEach(function (a) { h += actionBlock(a); });
    h += "</section>";

    if (o.conviction_notes && o.conviction_notes.length) {
      h += '<section><h4>Conviction adjustments</h4><ul class="notes">' +
           o.conviction_notes.map(function (n) {
             return "<li>" + esc(n) + "</li>";
           }).join("") + "</ul></section>";
    }

    var m = o.metrics;
    h += '<section><h4>Metrics</h4><div class="metrics">' +
      mcell("RSI", num(m.rsi, 0)) +
      mcell("ADX", num(m.adx, 0)) +
      mcell("ATR %", num(m.atr_pct * 100, 1) + "%") +
      mcell("vs SPY 3m", signed(m.rs_63d) + "%") +
      mcell("From 52w hi", num(m.pct_from_high, 1) + "%") +
      mcell("21d return", signed(m.ret_21d) + "%") +
      mcell("$ volume", "$" + money(m.dollar_vol)) +
      mcell("Mkt cap", "$" + money(m.market_cap)) +
      (m.atm_iv ? mcell("ATM IV", num(m.atm_iv * 100, 0) + "%") : "") +
      (m.days_to_earnings != null
        ? mcell("Earnings in", m.days_to_earnings + "d") : "") +
      "</div>";
    if (o.industry) {
      h += '<p class="why" style="margin-top:8px;color:var(--ink-3);font-size:12px">' +
           esc(o.sector) + (o.industry ? " · " + esc(o.industry) : "") +
           " · " + esc(o.cap_bucket) + " cap</p>";
    }
    h += "</section></div>";
    return h;
  }

  function card(o) {
    var open = !!state.open[o.ticker];
    var top = o.actions[0];
    var h = '<article class="card ' + o.direction + '" data-t="' + esc(o.ticker) + '">';

    h += '<div class="chead"><div class="mid">' +
         '<div class="crow1"><span class="tick">' + esc(o.ticker) + "</span>" +
         '<span class="badge ' + o.direction + '">' + o.direction + "</span>" +
         '<span class="pill theme">' + esc(o.theme_label) + "</span></div>" +
         '<div class="cname">' + esc(o.name) + "</div></div>" +
         '<div class="conv"><div class="v">' + num(o.conviction, 0) +
         '</div><div class="l">conviction</div></div></div>';

    h += '<div class="csub"><div class="setuplist">' +
      o.setups.map(function (s) {
        return '<span class="pill score">' + esc(s.label) + " · " +
               num(s.score, 0) + "</span>";
      }).join("") + ivPill(o.metrics) + "</div>";

    h += levelCells(o);

    if (top) {
      h += '<div class="topaction"><span class="t">' + esc(top.label) +
           "</span></div>";
    }
    h += "</div>";

    if (open) h += detail(o);
    return h + "</article>";
  }

  /* ------------------------------------------------------------- render -- */
  function renderThemes() {
    var counts = {};
    DATA.opportunities.forEach(function (o) {
      counts[o.theme] = (counts[o.theme] || 0) + 1;
    });
    var html = '<button class="tchip' + (state.theme === "all" ? " on" : "") +
      '" data-theme="all">All themes<span class="n">' +
      DATA.opportunities.length + "</span></button>";
    DATA.themes.forEach(function (t) {
      if (!counts[t.id]) return;
      html += '<button class="tchip' + (state.theme === t.id ? " on" : "") +
        '" data-theme="' + esc(t.id) + '">' + esc(t.label) +
        '<span class="n">' + counts[t.id] + "</span></button>";
    });
    $("themebar").innerHTML = html;
  }

  function renderGrid() {
    var list = DATA.opportunities.filter(matches);
    list.sort(function (a, b) { return sortKey(a) - sortKey(b); });

    var nL = list.filter(function (o) { return o.direction === "long"; }).length;
    $("count").textContent = list.length + " shown · " + nL + " long · " +
                             (list.length - nL) + " short";
    $("empty").hidden = list.length > 0;
    // One malformed record must not be able to blank the whole dashboard.
    $("grid").innerHTML = list.map(function (o) {
      try {
        return card(o);
      } catch (err) {
        console.error("card render failed for " + o.ticker, err);
        return '<article class="card"><div class="chead"><div class="mid">' +
               '<span class="tick">' + esc(o.ticker) + "</span>" +
               '<div class="cname">could not be rendered</div></div></div></article>';
      }
    }).join("");
  }

  function renderPairs() {
    if (!DATA.pairs.length) {
      $("pairsList").innerHTML =
        '<div class="empty">No spreads are stretched far enough today.</div>';
      return;
    }
    $("pairsList").innerHTML = DATA.pairs.map(function (p) {
      return '<div class="pair"><div class="legs">' +
        '<div class="pill theme" style="margin-bottom:6px;display:inline-block">' +
          esc(p.theme_label) + "</div>" +
        '<div class="leg"><span class="tag l">Long</span><b>' +
          esc(p.long_leg) + '</b><span class="nm">' + esc(p.long_name) + "</span></div>" +
        '<div class="leg"><span class="tag s">Short</span><b>' +
          esc(p.short_leg) + '</b><span class="nm">' + esc(p.short_name) + "</span></div>" +
        '</div><div class="stats">' +
        pstat("z-score", signed(p.z, 2)) +
        pstat("corr", num(p.corr, 2)) +
        pstat("hedge β", num(p.beta, 2)) +
        pstat("half-life", num(p.half_life, 0) + "d") +
        "</div></div>";
    }).join("");
  }

  function pstat(k, v) {
    return '<div><div class="k">' + esc(k) + '</div><div class="v">' +
           esc(v) + "</div></div>";
  }

  /* ---------------------------------------------------------- follow-up --
     JOURNAL is fetched lazily the first time the tab is opened, because it is
     considerably larger than the opportunity feed and most visits never look
     at it. */
  var JOURNAL = null, jstate = { st: "all", q: "", sort: "date",
                                 open: {}, entry: {} };

  function loadJournal() {
    if (JOURNAL) { renderJournal(); return; }
    var el = $("jlist");
    el.innerHTML = '<div class="empty">Loading trade history…</div>';
    fetch("data/journal.json", { cache: "no-cache" })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (j) { JOURNAL = j; renderJournal(); })
      .catch(function (err) {
        el.innerHTML = '<div class="empty">No journal yet — ' +
          esc(err.message) + ".<br>It is created the first time the " +
          "screener runs.</div>";
      });
  }

  /* Mirror of `walk` in engine/journal.py. The two must agree: the server
     computes the headline numbers, this recomputes them live as the slider
     moves. Rule constants come from the file so they can never drift. */
  /* Mirror of `_last_finite` in journal.py. A halted or untraded final bar
     serialises as null, and `null - price` silently evaluates to `-price` in
     JavaScript rather than NaN — which turned a +0.06R trade into -6.58R
     before this existed. */
  function lastClose(t, upto) {
    for (var k = Math.min(upto, t.c.length - 1); k >= 0; k--) {
      if (t.c[k] != null && isFinite(t.c[k])) return t.c[k];
    }
    return null;
  }

  function simulateTrade(t, entryIdx) {
    var R = JOURNAL.rules;
    var n = t.c.length;
    if (entryIdx >= n) return null;
    var entry = t.o[entryIdx];
    if (entry == null || !(entry > 0)) return null;

    var long = t.direction === "long";
    var stop = long ? entry - R.stop_atr_mult * t.atr
                    : entry + R.stop_atr_mult * t.atr;
    var target = long ? entry + R.target_atr_mult * t.atr
                      : entry - R.target_atr_mult * t.atr;
    var risk = Math.abs(entry - stop);

    var last = Math.min(n - 1, entryIdx + R.max_hold_days);
    var mfe = 0, mae = 0, status = "open", exitIdx = null, exitPrice = null;

    for (var j = entryIdx; j <= last; j++) {
      var hi = t.h[j], lo = t.l[j];
      if (hi == null || lo == null) continue;

      mfe = Math.max(mfe, long ? hi - entry : entry - lo);
      mae = Math.max(mae, long ? entry - lo : hi - entry);

      var hitStop = long ? lo <= stop : hi >= stop;
      var hitTarget = long ? hi >= target : lo <= target;

      // Same-bar ambiguity resolves to the stop — daily bars cannot say
      // which came first, and assuming the good one inflates everything.
      if (hitStop) { status = "stop"; exitIdx = j; exitPrice = stop; break; }
      if (hitTarget) { status = "target"; exitIdx = j; exitPrice = target; break; }
    }

    if (status === "open" && last === entryIdx + R.max_hold_days && last < n - 1) {
      status = "expired"; exitIdx = last; exitPrice = lastClose(t, last);
    }

    var mark = exitPrice != null ? exitPrice : lastClose(t, n - 1);
    if (mark == null) mark = entry;
    var pnl = long ? mark - entry : entry - mark;

    return {
      status: status, entry_idx: entryIdx, entry: entry, stop: stop,
      target: target, exit_idx: exitIdx, exit_price: exitPrice, mark: mark,
      pnl_pct: pnl / entry * 100,
      r_multiple: risk > 0 ? pnl / risk : null,
      mfe_r: risk > 0 ? mfe / risk : null,
      mae_r: risk > 0 ? -mae / risk : null,
      bars_held: exitIdx != null ? exitIdx - entryIdx : n - 1 - entryIdx
    };
  }

  var STATUS_LABEL = { open: "Open", target: "Target hit", stop: "Stopped out",
                       expired: "Timed out", pending: "Pending" };

  function chart(t, sim) {
    var W = 720, H = 210, PADL = 46, PADR = 12, PADT = 12, PADB = 22;
    var n = t.c.length;
    var lo = Infinity, hi = -Infinity, i;
    for (i = 0; i < n; i++) {
      if (t.l[i] != null) lo = Math.min(lo, t.l[i]);
      if (t.h[i] != null) hi = Math.max(hi, t.h[i]);
    }
    lo = Math.min(lo, sim.stop, sim.target);
    hi = Math.max(hi, sim.stop, sim.target);
    if (!isFinite(lo) || !isFinite(hi) || hi === lo) return "";
    var pad = (hi - lo) * 0.06; lo -= pad; hi += pad;

    var x = function (k) {
      return PADL + (n < 2 ? 0 : k / (n - 1) * (W - PADL - PADR));
    };
    var y = function (v) {
      return PADT + (hi - v) / (hi - lo) * (H - PADT - PADB);
    };

    var s = '<svg viewBox="0 0 ' + W + " " + H + '" class="tchart" ' +
            'preserveAspectRatio="none" role="img" aria-label="price since signal">';

    // Shade the period the position is actually held.
    var endIdx = sim.exit_idx != null ? sim.exit_idx : n - 1;
    s += '<rect class="held" x="' + x(sim.entry_idx).toFixed(1) + '" y="' + PADT +
         '" width="' + Math.max(1, x(endIdx) - x(sim.entry_idx)).toFixed(1) +
         '" height="' + (H - PADT - PADB) + '"/>';

    // Stop / target / entry levels.
    s += '<line class="lvl stop" x1="' + PADL + '" x2="' + (W - PADR) +
         '" y1="' + y(sim.stop).toFixed(1) + '" y2="' + y(sim.stop).toFixed(1) + '"/>';
    s += '<line class="lvl tgt" x1="' + PADL + '" x2="' + (W - PADR) +
         '" y1="' + y(sim.target).toFixed(1) + '" y2="' + y(sim.target).toFixed(1) + '"/>';
    s += '<line class="lvl entry" x1="' + PADL + '" x2="' + (W - PADR) +
         '" y1="' + y(sim.entry).toFixed(1) + '" y2="' + y(sim.entry).toFixed(1) + '"/>';

    // High-low range, then the close line on top.
    var band = "";
    for (i = 0; i < n; i++) {
      if (t.h[i] == null || t.l[i] == null) continue;
      band += '<line x1="' + x(i).toFixed(1) + '" x2="' + x(i).toFixed(1) +
              '" y1="' + y(t.h[i]).toFixed(1) + '" y2="' + y(t.l[i]).toFixed(1) + '"/>';
    }
    s += '<g class="range">' + band + "</g>";

    var d = "", started = false;
    for (i = 0; i < n; i++) {
      if (t.c[i] == null) continue;
      d += (started ? "L" : "M") + x(i).toFixed(1) + " " + y(t.c[i]).toFixed(1);
      started = true;
    }
    s += '<path class="px" d="' + d + '"/>';

    // Entry and exit markers.
    s += '<circle class="mk entry" cx="' + x(sim.entry_idx).toFixed(1) +
         '" cy="' + y(sim.entry).toFixed(1) + '" r="4"/>';
    if (sim.exit_idx != null) {
      s += '<circle class="mk ' + sim.status + '" cx="' + x(sim.exit_idx).toFixed(1) +
           '" cy="' + y(sim.exit_price).toFixed(1) + '" r="4.5"/>';
    }

    // Axis labels.
    s += '<text class="ax" x="4" y="' + (y(hi - pad) + 4).toFixed(1) + '">' +
         num(hi - pad, 2) + "</text>";
    s += '<text class="ax" x="4" y="' + (y(lo + pad) + 4).toFixed(1) + '">' +
         num(lo + pad, 2) + "</text>";
    s += '<text class="ax" x="' + PADL + '" y="' + (H - 6) + '">' +
         esc(t.dates[0]) + "</text>";
    s += '<text class="ax end" x="' + (W - PADR) + '" y="' + (H - 6) +
         '" text-anchor="end">' + esc(t.dates[n - 1]) + "</text>";

    return s + "</svg>";
  }

  function rClass(r) {
    if (r == null) return "";
    return r > 0.05 ? "up" : r < -0.05 ? "dn" : "";
  }

  function tradeDetail(t) {
    var idx = jstate.entry[t.id];
    if (idx == null) idx = t.sim.entry_idx;
    var sim = simulateTrade(t, idx) || t.sim;
    var n = t.c.length;
    var moved = idx !== t.sim.entry_idx;

    var h = '<div class="tdetail">';
    h += chart(t, sim);

    h += '<div class="slider-row">' +
      '<label>Entry day' +
      '<input type="range" class="entrySlider" data-id="' + esc(t.id) +
      '" min="1" max="' + (n - 1) + '" value="' + idx + '"></label>' +
      '<span class="entryDate">' + esc(t.dates[idx]) +
      " @ open $" + num(t.o[idx], 2) +
      (moved ? ' <em>(moved from ' + esc(t.dates[t.sim.entry_idx]) + ')</em>' : "") +
      "</span></div>";

    h += '<div class="tgrid">' +
      mcell("Status", STATUS_LABEL[sim.status] || sim.status) +
      mcell("Entry", "$" + num(sim.entry, 2)) +
      mcell("Stop", "$" + num(sim.stop, 2)) +
      mcell("Target", "$" + num(sim.target, 2)) +
      mcell(sim.exit_price != null ? "Exit" : "Last",
            "$" + num(sim.exit_price != null ? sim.exit_price : sim.mark, 2)) +
      mcell("Result", (sim.r_multiple == null ? "–"
             : signed(sim.r_multiple, 2) + "R")) +
      mcell("P&L", signed(sim.pnl_pct, 2) + "%") +
      mcell("Held", sim.bars_held + " sessions") +
      mcell("Best (MFE)", sim.mfe_r == null ? "–" : signed(sim.mfe_r, 2) + "R") +
      mcell("Worst (MAE)", sim.mae_r == null ? "–" : signed(sim.mae_r, 2) + "R") +
      "</div>";

    h += '<p class="tnote">Signalled ' + esc(t.suggested_on) + " on <strong>" +
         esc(t.setup_label) + "</strong> at $" + num(t.signal_price, 2) +
         ", ATR $" + num(t.atr, 2) + ". Move the slider to see how the same " +
         "setup would have gone entered on a different day — the stop and " +
         "target follow the entry, keeping the same ATR distances.</p>";

    return h + "</div>";
  }

  function tradeRow(t) {
    var open = !!jstate.open[t.id];
    var sim = t.sim;
    var r = sim.r_multiple;
    var h = '<article class="trade ' + sim.status + '" data-id="' + esc(t.id) + '">';

    h += '<div class="thead"><div class="tmain">' +
      '<div class="crow1"><span class="tick">' + esc(t.ticker) + "</span>" +
      '<span class="badge ' + t.direction + '">' + t.direction + "</span>" +
      '<span class="pill st ' + sim.status + '">' +
        (STATUS_LABEL[sim.status] || sim.status) + "</span>" +
      (t.seeded ? '<span class="pill seeded">replayed</span>' : "") +
      "</div>" +
      '<div class="cname">' + esc(t.setup_label) + " · " + esc(t.theme_label) +
      " · signalled " + esc(t.suggested_on) + "</div></div>";

    h += '<div class="tres"><div class="v ' + rClass(r) + '">' +
      (r == null ? "–" : signed(r, 2) + "R") + '</div>' +
      '<div class="l">' + signed(sim.pnl_pct, 1) + "% · " +
      sim.bars_held + "d</div></div></div>";

    if (open) h += tradeDetail(t);
    return h + "</article>";
  }

  function jMatches(t) {
    if (jstate.st !== "all" && t.sim.status !== jstate.st) return false;
    if (jstate.q) {
      var hay = (t.ticker + " " + t.name + " " + t.setup_label + " " +
                 t.theme_label).toLowerCase();
      if (hay.indexOf(jstate.q) < 0) return false;
    }
    return true;
  }

  function renderJournalStats() {
    var s = JOURNAL.stats;
    var wr = s.win_rate == null ? "–" : Math.round(s.win_rate * 100) + "%";
    $("jstats").innerHTML =
      jstat("Tracked", s.total) +
      jstat("Open", s.open) +
      jstat("Closed", s.closed) +
      jstat("Target hit", s.target_hit, "up") +
      jstat("Stopped", s.stopped, "dn") +
      jstat("Timed out", s.expired) +
      jstat("Win rate", wr) +
      jstat("Avg result", s.avg_r == null ? "–" : signed(s.avg_r, 2) + "R",
            rClass(s.avg_r)) +
      jstat("Total", s.total_r == null ? "–" : signed(s.total_r, 1) + "R",
            rClass(s.total_r));
    var tn = $("tabnFollow");
    if (tn) tn.textContent = s.total ? " " + s.total : "";
  }

  function jstat(k, v, cls) {
    return '<div class="jstat"><div class="k">' + esc(k) + '</div>' +
           '<div class="v ' + (cls || "") + '">' + esc(v) + "</div></div>";
  }

  function renderJournal() {
    if (!JOURNAL) return;
    renderJournalStats();

    var list = JOURNAL.trades.filter(jMatches);
    list.sort(function (a, b) {
      switch (jstate.sort) {
        case "r": return (b.sim.r_multiple || -99) - (a.sim.r_multiple || -99);
        case "rworst": return (a.sim.r_multiple || 99) - (b.sim.r_multiple || 99);
        case "conviction": return b.conviction - a.conviction;
        default: return a.suggested_on < b.suggested_on ? 1 : -1;
      }
    });

    $("jcount").textContent = list.length + " of " + JOURNAL.trades.length +
      " tracked trades";
    $("jempty").hidden = list.length > 0;
    $("jlist").innerHTML = list.map(function (t) {
      try { return tradeRow(t); }
      catch (e) { console.error("trade render failed", t.id, e); return ""; }
    }).join("");

    var seeded = JOURNAL.trades.filter(function (t) { return t.seeded; });
    var lf = $("liveFrom");
    if (lf && seeded.length) {
      lf.textContent = "the first live run";
    }
  }

  function renderSetupTable() {
    var seen = {}, rows = [];
    DATA.opportunities.forEach(function (o) {
      o.setups.forEach(function (s) {
        if (seen[s.id]) return;
        seen[s.id] = 1;
        rows.push({ label: s.label, dir: o.direction, thesis: s.thesis,
                    inval: s.invalidation });
      });
    });
    if (!rows.length) { $("setupTable").innerHTML = ""; return; }
    rows.sort(function (a, b) { return a.dir === b.dir ? 0 : a.dir === "long" ? -1 : 1; });
    $("setupTable").innerHTML =
      "<table><thead><tr><th>Setup</th><th>Side</th><th>What it claims</th>" +
      "<th>Invalidated by</th></tr></thead><tbody>" +
      rows.map(function (r) {
        return "<tr><td><strong>" + esc(r.label) + '</strong></td><td class="dir ' +
          r.dir + '">' + r.dir + "</td><td>" + esc(r.thesis) + "</td><td>" +
          esc(r.inval) + "</td></tr>";
      }).join("") + "</tbody></table>";
  }

  /* -------------------------------------------------------------- wire -- */
  function wire() {
    $("q").addEventListener("input", function (e) {
      state.q = e.target.value.trim().toLowerCase();
      renderGrid();
    });

    $("dirFilter").addEventListener("click", function (e) {
      var b = e.target.closest("button");
      if (!b) return;
      state.dir = b.dataset.dir;
      [].forEach.call(this.children, function (c) {
        c.classList.toggle("on", c === b);
      });
      renderGrid();
    });

    $("actionFilter").addEventListener("change", function (e) {
      state.action = e.target.value; renderGrid();
    });

    $("conv").addEventListener("input", function (e) {
      state.conv = +e.target.value;
      $("convOut").textContent = state.conv;
      renderGrid();
    });

    $("sort").addEventListener("change", function (e) {
      state.sort = e.target.value; renderGrid();
    });

    $("themebar").addEventListener("click", function (e) {
      var b = e.target.closest(".tchip");
      if (!b) return;
      state.theme = b.dataset.theme;
      renderThemes(); renderGrid();
    });

    $("grid").addEventListener("click", function (e) {
      var head = e.target.closest(".chead");
      if (!head) return;
      var t = head.closest(".card").dataset.t;
      state.open[t] = !state.open[t];
      renderGrid();
    });

    $("tabs").addEventListener("click", function (e) {
      var b = e.target.closest(".tab");
      if (!b) return;
      [].forEach.call(this.children, function (c) {
        c.classList.toggle("active", c === b);
      });
      ["ideas", "followup", "pairs", "method"].forEach(function (v) {
        $("view-" + v).hidden = v !== b.dataset.view;
      });
      if (b.dataset.view === "followup") loadJournal();
    });

    // ---- follow-up tab ----
    $("jq").addEventListener("input", function (e) {
      jstate.q = e.target.value.trim().toLowerCase();
      renderJournal();
    });

    $("jStatusFilter").addEventListener("click", function (e) {
      var b = e.target.closest("button");
      if (!b) return;
      jstate.st = b.dataset.st;
      [].forEach.call(this.children, function (c) {
        c.classList.toggle("on", c === b);
      });
      renderJournal();
    });

    $("jSort").addEventListener("change", function (e) {
      jstate.sort = e.target.value; renderJournal();
    });

    $("jlist").addEventListener("click", function (e) {
      if (e.target.closest(".tdetail")) return;   // don't collapse on slider
      var head = e.target.closest(".thead");
      if (!head) return;
      var id = head.closest(".trade").dataset.id;
      jstate.open[id] = !jstate.open[id];
      renderJournal();
    });

    // Redraw only the expanded trade as the slider moves, so dragging stays
    // smooth and the input keeps focus.
    $("jlist").addEventListener("input", function (e) {
      var sl = e.target.closest(".entrySlider");
      if (!sl) return;
      var id = sl.dataset.id;
      jstate.entry[id] = +sl.value;
      var t = JOURNAL.trades.find(function (x) { return x.id === id; });
      if (!t) return;
      var art = sl.closest(".trade");
      var detail = art.querySelector(".tdetail");
      if (!detail) return;
      detail.outerHTML = tradeDetail(t);
      var moved = art.querySelector(".entrySlider");
      if (moved) { moved.focus(); }
    });
  }

  /* -------------------------------------------------------------- boot -- */
  fetch("data/latest.json", { cache: "no-cache" })
    .then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    })
    .then(function (d) {
      DATA = d;
      renderMarket();
      renderThemes();
      renderGrid();
      renderPairs();
      renderSetupTable();
      wire();
    })
    .catch(function (err) {
      document.getElementById("grid").innerHTML =
        '<div class="empty">Could not load data/latest.json — ' + esc(err.message) +
        ".<br>If you are opening this file directly, serve the folder over " +
        "HTTP instead (<code>python -m http.server</code>).</div>";
    });
})();
