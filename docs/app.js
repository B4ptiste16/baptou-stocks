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
      ["ideas", "pairs", "method"].forEach(function (v) {
        $("view-" + v).hidden = v !== b.dataset.view;
      });
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
