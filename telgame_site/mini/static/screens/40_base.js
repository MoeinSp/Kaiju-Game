/* The base: buildings (collect / build / upgrade / speed-up / workers) and the research lab.
   Screens: bs_buildings (the visual list), bs_building (one building), bs_research (the lab).
   API: base/…  Every number shown here comes from the server (game.buildings / game.research).
   Every action answers with the screen it was sent from, so a screen redraws from the POST instead of
   asking again; countdowns keep ONE interval per screen however often it redraws. */
(function (K) {
  "use strict";

  K.addIcons({
    bs_card: '<path d="M6 3.500h12v17H6zM13 7l-3.500 5.500h5L11 17"/>',
    bs_worker: '<circle cx="12" cy="9" r="3.300"/><path d="M5 20a7 7 0 0 1 14 0M7.500 7.500a4.500 4.500 0 0 1 9 0M6.500 7.500h11"/>',
    bs_silo: '<path d="M6 21V8.500a6 6 0 0 1 12 0V21zM4 21h16M6 12h12M6 16.500h12"/>'
  });

  var RESEARCH_HALL = 5;   // SECTION_HALL_REQ["research"] — the server enforces it
  var RES = { coins: { name: "طلا", icon: "coin", cls: "t-coin" }, dna: { name: "DNA", icon: "dna", cls: "t-dna" }, diamonds: { name: "الماس", icon: "gem", cls: "t-diamond" } };
  var TYPE_ICON = { main_hall: "hall", gold_collector: "coin", diamond_collector: "gem", dna_lab: "dna", blacksmith: "hammer", fusion_lab: "star",
                    trade_hall: "swap", research_lab: "flask", dispatch_hq: "compass" };
  /* a building card links to the section it runs, like the bot's card does: [hub tile id, title] */
  var SECTION = { dispatch_hq: ["dispatch", "مأموریت‌های اعزامی"], blacksmith: ["forge", "آهنگری"], fusion_lab: ["fusion", "تالار ادغام"] };
  function sectionOf(type) {
    if (type === "research_lab") return { go: "bs_research", title: "پژوهش‌ها", icon: "flask" };
    var s = SECTION[type], t = s && K.hubTile ? K.hubTile(s[0]) : null;
    if (!t || !t.go || !K.hasScreen(t.go)) return null;
    var need = 0; try { need = typeof t.hall === "function" ? t.hall(K.me) : t.hall; } catch (e) { need = 0; }
    if (need && K.me && K.me.hall_level < need) return null;   // its own gate would refuse: no dead-end button
    return { go: t.go, params: t.params, title: s[1], icon: TYPE_ICON[type] };
  }

  // ───────────────────────── small helpers ─────────────────────────
  function noop() {}
  function fmt(x) { return Number(x || 0).toLocaleString("en-US"); }
  function num(x) { return '<span class="num">' + Number(x || 0).toLocaleString("en-US", { maximumFractionDigits: 2 }) + "</span>"; }
  function plusPct(x) { return '<span class="num">+' + Math.round(Number(x || 0) * 100) + "%</span>"; }
  function amount(res, n) { var r = RES[res] || RES.coins; return '<span class="' + r.cls + ' b">' + K.ic(r.icon) + " " + K.n(n) + "</span>"; }
  function resName(res) { return (RES[res] || RES.coins).name; }
  /* K.res is the latest balance the server reported (it rides on every answer) */
  function have(res) { return K.res ? Number(K.res[res] || 0) : Infinity; }
  function pips(level, max, cap) {
    var s = '<span class="bs-pips" aria-label="سطح ' + level + '">';
    for (var i = 1; i <= max; i++) s += '<i class="' + (i <= level ? "on" : cap != null && i > cap ? "off" : "") + '"></i>';
    return s + "</span>";
  }
  /* one click listener per root; the INNERMOST [data-act] wins (a card holds its own buttons) */
  function acts(root, map) {
    root.addEventListener("click", function (ev) {
      var el = ev.target.closest("[data-act]");
      if (el && root.contains(el) && !el.disabled && map[el.dataset.act]) map[el.dataset.act](el, ev);
    });
  }
  function progress(left, total, kind) {
    var ratio = total > 0 ? 1 - left / total : 1;
    return '<div class="progress ' + (kind || "gold") + '" data-bs-left="' + Number(left) + '" data-bs-total="' + Number(total) + '"><i style="width:' + (Math.max(0, Math.min(1, ratio)) * 100).toFixed(1) + '%"></i></div>';
  }
  /* Countdowns + live bars that survive in-place redraws: each call replaces the previous interval of its
     channel ("page" / "sheet"). onDone(el) fires once, for the first countdown that reaches zero. */
  var ticking = {};
  function live(root, channel, onDone) {
    if (ticking[channel]) clearInterval(ticking[channel]);
    ticking[channel] = null;
    var els = Array.prototype.slice.call(root.querySelectorAll(".timer[data-left]")), bars = Array.prototype.slice.call(root.querySelectorAll("[data-bs-left]"));
    if (!els.length && !bars.length) return;
    var t0 = Date.now() / 1000, fired = false;
    function tick() {
      var gone = Date.now() / 1000 - t0;
      els.forEach(function (el) {
        if (el._done) return;
        var left = Number(el.dataset.left) - gone;
        if (left <= 0) { el._done = true; el.classList.add("done"); el.innerHTML = K.ic("check") + (el.dataset.done || "آماده"); if (!fired && onDone) { fired = true; onDone(el); } }
        else el.innerHTML = K.ic("clock") + '<span class="num">' + (left >= 86400 ? K.dur(left) : K.clock(left)) + "</span>";
      });
      bars.forEach(function (el) {
        var total = Number(el.dataset.bsTotal), left = Number(el.dataset.bsLeft) - gone;
        if (total > 0 && el.firstChild) el.firstChild.style.width = (Math.max(0, Math.min(1, 1 - left / total)) * 100).toFixed(1) + "%";
      });
    }
    tick(); ticking[channel] = K.every(1000, tick);
  }
  /* a timer ran out: nothing is left to speed up or pay for — take those buttons away at once */
  function expire(root) {
    Array.prototype.forEach.call(root.querySelectorAll("[data-timed]"), function (b) { b.disabled = true; });
  }
  function showMissions(ms) {
    if (!ms || !ms.length) return Promise.resolve();
    var sum = { coins: 0, dna: 0, diamonds: 0 }, extra = [];
    ms.forEach(function (m) { sum.coins += m.coins || 0; sum.dna += m.dna || 0; sum.diamonds += m.diamonds || 0; (m.extra || []).forEach(function (e) { extra.push(K.esc(e)); }); });
    return K.reward({ icon: "calcheck", title: ms.length > 1 ? "چند مأموریت تکمیل شد!" : "مأموریت تکمیل شد!", text: ms.map(function (m) { return "«" + m.label + "»"; }).join("، "),
                      coins: sum.coins, dna: sum.dna, diamonds: sum.diamonds, extra: extra });
  }

  // ───────────────────────── shared actions ─────────────────────────
  /* Each screen passes `io`: {view: "list"|"detail", apply: fn(response), alive: fn} — `apply` redraws it
     from the `list` / `detail` the server sent back with the action. */
  function send(path, body, el, io) {
    body.view = io.view;
    return K.api.post(path, body, el).then(function (r) { if (io.alive()) io.apply(r); return r; });
  }
  function doCollect(id, el, io) {
    return send("base/collect/", { id: id }, el, io).then(function (r) {
      K.haptic("ok"); K.toast("+" + fmt(r.amount) + " " + resName(r.resource) + " برداشت شد", "ok");
      return showMissions(r.missions);
    }).catch(noop);
  }
  function askUpgrade(b, el, io) {
    var nx = b.next; if (!nx) return;
    var build = b.level === 0;
    if (!nx.lab_ok) { K.haptic("err"); K.toast("برای سطح " + nx.target + " این ساختمون باید سطح آزمایشگاهت حداقل " + nx.lab_req + " باشه. با بازی‌کردن بالا می‌ره.", "err"); return; }
    if (have("coins") < nx.cost) { K.haptic("err"); K.toast("طلا کم داری — " + (build ? "ساخت" : "ارتقا") + " " + fmt(nx.cost) + " طلا می‌خواد.", "err"); return; }
    K.confirm({
      icon: "hammer", title: build ? "ساخت " + b.label : "ارتقای " + b.label + " به سطح " + nx.target,
      html: '<div class="bs-bill"><div><span>هزینه</span>' + amount("coins", nx.cost) + "</div><div><span>زمان " + (build ? "ساخت" : "ارتقا") + '</span><b>' + K.ic("clock") + " " + K.dur(nx.seconds) + "</b></div></div>",
      ok: build ? "شروع ساخت" : "شروع ارتقا", cancel: "بی‌خیال"
    }).then(function (yes) {
      if (!yes) return;
      send("base/upgrade/", { id: b.id }, el, io).then(function (r) {
        K.haptic("ok"); K.toast(r.construct ? "ساخت شروع شد!" : "ارتقا شروع شد!", "ok");
      }, noop);
    });
  }
  function buyBuilder(cost, el, io, id) {
    K.confirm({
      icon: "bs_worker", title: "خرید بنّای دوم",
      html: "<p>با بنّای دوم می‌تونی هم‌زمان دو ساختمون رو ارتقا بدی و سرعت ساخت‌وسازت دو برابر می‌شه.</p>" +
            '<div class="bs-bill"><div><span>هزینه (دائمی)</span>' + amount("diamonds", cost) + "</div></div>",
      ok: "تأیید و خرید", cancel: "انصراف"
    }).then(function (yes) {
      if (!yes) return;
      var body = {}; if (id) body.id = id;
      send("base/builder/", body, el, io).then(function () { K.haptic("ok"); K.toast("بنّای دوم فعال شد!", "ok"); }, noop);
    });
  }
  function buildersLine(bl) {
    return '<span class="bs-builders">' + K.ic("bs_worker") + "بنّاها " + '<b class="num">' + bl.busy + "/" + bl.slots + "</b> مشغول</span>";
  }
  /* Speed-up cards for the upgrade of building `b`. `st` = {cards, left} is updated from each answer;
     when the upgrade completes the answer carries the fresh screen (io.apply). */
  function speedSheet(b, st, io) {
    var rows = st.cards.map(function (c) {
      return '<div><span class="ic" style="color:var(--gold)">' + K.ic("bs_card") + '</span><span class="t">' + K.esc(c.label) + "<small>" + K.n(c.count) + " تا داری</small></span>" +
        '<button class="btn sm gold" data-m="' + c.minutes + '">' + '<span class="num">1×</span></button>' +
        (c.count > 1 ? '<button class="btn sm" data-m="' + c.minutes + '" data-all="1">همه <span class="num">(' + c.count + "×)</span></button>" : "") + "</div>";
    }).join("");
    var box = K.sheet('<div class="bs-sheet"><div class="grab"></div><div class="pad"><div class="ttl" style="font-size:18px">تسریع با کارت سرعت</div>' +
      '<p class="lead" style="margin:4px 0 12px">هر کارت همون‌قدر از زمان باقی‌مونده کم می‌کنه. «همه» فقط به اندازه‌ی لازم خرج می‌کنه.</p>' +
      '<div class="panel pad bs-left"><span class="muted">زمان باقی‌مونده</span><span class="timer" data-left="' + st.left + '" data-done="تموم شد"></span></div>' +
      (rows ? '<div class="panel list mt">' + rows + "</div>" : K.state("bs_card", "کارت سرعت نداری", "کارت سرعت از مأموریت‌ها، گردونه و جایزه‌ها می‌رسه.")) + "</div></div>");
    // the timer ran out while the sheet was open: no card may be spent on a finished upgrade
    live(box, "sheet", function () { K.closeSheet(); });
    box.querySelector(".bs-sheet").onclick = function (ev) {
      var btn = ev.target.closest("button[data-m]"); if (!btn) return;
      var body = { id: b.id, minutes: +btn.dataset.m, view: io.view }; if (btn.dataset.all) body.all = true;
      K.api.post("base/speedup/", body, btn).then(function (r) {
        K.haptic("ok");
        if (r.completed) {
          K.toast(r.used ? (b.level === 0 ? "ساخت تموم شد!" : "ارتقا تموم شد!") : "خودش تموم شده بود؛ کارتی خرج نشد.", "ok");
          K.closeSheet(); if (io.alive()) io.apply(r);
          return;
        }
        K.toast(r.used > 1 ? r.used + " کارت استفاده شد!" : "سرعت گرفت!", "ok");
        st.cards = r.cards; st.left = r.left;
        if (io.alive()) io.sped(b.id, r);
        speedSheet(b, st, io);
      }, function () { K.closeSheet(); if (io.alive()) io.refresh(); });
    };
  }

  // ═════════════════════════ the buildings list ═════════════════════════
  function statusLine(b) {
    if (b.state === "upgrading") {
      return '<span class="timer" data-left="' + b.upgrade.left + '" data-done="تموم شد"></span><span class="muted">' + (b.level === 0 ? "در حال ساخت" : "تا سطح " + K.n(b.upgrade.target)) + "</span>";
    }
    if (b.state === "locked") return '<span class="bs-dim">' + K.ic("lock") + "از سطح " + K.n(b.unlock_hall) + " تالار مِهر باز می‌شه</span>";
    if (b.level === 0) return '<span class="bs-dim">' + K.ic("hammer") + "هنوز ساخته نشده</span>";
    if (b.produces) {
      var r = RES[b.resource];
      return '<span class="' + r.cls + '">' + K.ic(r.icon) + "</span><span><b>" + num(b.rate) + '</b> در ساعت</span><span class="faint">·</span><span class="bs-dim">' + K.ic("bs_worker") + '<span class="num">' + b.workers + "/" + b.slots + "</span></span>";
    }
    return '<span class="muted bs-two">' + K.esc(b.desc) + "</span>";
  }
  function storeLine(b) {
    if (!(b.produces && b.level > 0)) return "";
    var full = b.store_cap > 0 && b.pending >= b.store_cap;
    return '<div class="bs-store">' + K.bar(b.store_cap > 0 ? b.pending / b.store_cap : 0, full ? "bad" : "good") +
      '<span class="xs ' + (full ? "t-warn b" : "muted") + '">' + (full ? "مخزن پره" : "مخزن") + ' <span class="num">' + fmt(b.pending) + " / " + fmt(b.store_cap) + "</span></span></div>";
  }
  /* ONE action per card — the most useful thing to do with this building right now */
  function cardAction(b) {
    if (b.produces && b.level > 0 && b.pending > 0) {
      return '<button class="btn sm good" data-act="collect" data-id="' + b.id + '">' + K.ic(RES[b.resource].icon) + "برداشت " + '<span class="num">+' + fmt(b.pending) + "</span></button>";
    }
    if (b.state === "upgrading") return '<button class="btn sm gold" data-timed data-act="speed" data-id="' + b.id + '">' + K.ic("bolt") + "تسریع</button>";
    if (b.state === "ready") {
      var ok = b.next.lab_ok && have("coins") >= b.next.cost;
      return '<button class="btn sm ' + (ok ? "primary" : "bs-off") + '" data-act="upgrade" data-id="' + b.id + '">' + K.ic(b.level === 0 ? "hammer" : "up") + (b.level === 0 ? "ساخت" : "ارتقا") +
        '<span class="cost">' + K.ic("coin") + K.short(b.next.cost) + "</span></button>";
    }
    if (b.state === "capped") return '<span class="bs-hint">' + K.ic("lock") + "اول تالار مِهر رو ارتقا بده</span>";
    if (b.state === "busy") return '<span class="bs-hint">' + K.ic("hourglass") + "بنّاها مشغولن</span>";
    if (b.state === "max") return '<span class="bs-hint t-gold">' + K.ic("trophy") + "سقف سطح</span>";
    return "";
  }
  function card(b) {
    var pic = b.img ? '<img loading="lazy" decoding="async" src="' + b.img + '" alt="">' : '<span class="bs-noimg">' + K.ic(TYPE_ICON[b.type] || "building") + "</span>";
    return '<div class="panel bs-card s-' + b.state + (b.main ? " main" : "") + (b.level === 0 ? " unbuilt" : "") + '" data-act="open" data-id="' + b.id + '" role="button">' +
      '<div class="bs-pic">' + pic + (b.state === "locked" ? '<span class="bs-lock">' + K.ic("lock") + "</span>" : b.level > 0 ? '<span class="bs-lv">سطح <span class="num">' + b.level + "</span></span>" : "") + "</div>" +
      '<div class="bs-body"><div class="bs-name"><b class="cut">' + K.esc(b.label) + "</b>" + pips(b.level, b.max_level, b.cap) + "</div>" +
      '<div class="bs-status">' + statusLine(b) + "</div>" + storeLine(b) +
      '<div class="bs-acts">' + cardAction(b) + "</div></div>" +
      (b.state === "upgrading" ? '<div class="bs-strip">' + progress(b.upgrade.left, b.upgrade.total) + "</div>" : "") + "</div>";
  }

  K.screen("bs_buildings", {
    title: "ساختمان‌ها", tab: "base",
    render: function (root, params, ctx) {
      return K.api.get("base/").then(function (d) {
        var byId = {}, drawnAt = 0, refreshing = false, plunderShown = false;
        function refresh() {
          if (refreshing) return; refreshing = true;
          K.api.get("base/").then(function (f) { refreshing = false; if (ctx.alive()) { d = f; draw(); } }, function () { refreshing = false; });
        }
        var io = {
          view: "list", alive: ctx.alive, refresh: refresh,
          apply: function (r) { if (r && r.list) { d = r.list; draw(); } else refresh(); },
          sped: function (id, r) { var b = byId[id]; if (b && b.upgrade) { b.upgrade.left = r.left; b.upgrade.finish_price = r.finish_price; } d.cards = r.cards; draw(); }
        };
        function draw() {
          byId = {}; d.buildings.forEach(function (b) { byId[b.id] = b; }); drawnAt = Date.now();
          var w = d.waiting, anyWaiting = w.coins || w.dna || w.diamonds, bl = d.builders, hall = d.buildings[0] || {};
          var cardsTotal = d.cards.reduce(function (s, c) { return s + c.count; }, 0);
          var html = "";
          if (d.plunder) {
            html += '<div class="callout bad mb">' + K.ic("warn") + "<span><b>معدنت غارت شد.</b> توی حمله‌ی اخیر آرنا بخشی از منابع ذخیره‌شده رفت: " + K.amounts(d.plunder) + "</span></div>";
            if (!plunderShown) { plunderShown = true; K.api.post("base/plunder_seen/", {}).catch(noop); }   // shown once, like the bot
          }
          html += '<div class="banner bs-hallhead"' + (hall.img ? ' style="background-image:url(\'' + hall.img + "&s=l')\"" : "") + '><div class="grow"><div class="ttl">تالار مِهر · سطح ' + K.n(d.hall_level) + ' <span class="sm" style="color:#c5cee2;font-weight:400">از ' + K.n(d.max_level) + "</span></div>" +
            pips(d.hall_level, d.max_level) + '<div class="bs-top-meta">' + buildersLine(bl) + (cardsTotal ? '<span class="bs-builders">' + K.ic("bs_card") + "کارت سرعت " + '<b class="num">' + cardsTotal + "</b></span>" : "") + "</div></div></div>" +
            '<p class="note bs-rule">هیچ ساختمونی از سطح تالار مِهر جلو نمی‌زنه.</p>' +
            (bl.second_cost ? '<button class="btn sm block mb" data-act="builder">' + K.ic("plus") + "بنّای دوم (دو ارتقای هم‌زمان)" + '<span class="cost t-diamond">' + K.ic("gem") + K.n(bl.second_cost) + "</span></button>" : "");
          if (anyWaiting) {
            html += '<div class="panel pad glow bs-wait"><div class="grow"><div class="sm muted">آماده‌ی برداشت</div><div class="bs-wait-sum">' + K.amounts(w) + '</div></div><button class="btn good" data-act="collect-all">' + K.ic("check") + "جمع‌آوری همه</button></div>";
          }
          html += '<div class="bs-list mt">' + d.buildings.map(card).join("") + "</div>";
          root.innerHTML = html;
          live(root, "page", function () { expire(root); K.after(900, refresh); });
        }
        draw();
        acts(root, {
          open: function (el) { K.go("bs_building", { id: +el.dataset.id }); },
          collect: function (el) { doCollect(+el.dataset.id, el, io); },
          upgrade: function (el) { askUpgrade(byId[+el.dataset.id], el, io); },
          builder: function (el) { buyBuilder(d.builders.second_cost, el, io); },
          speed: function (el) {
            var b = byId[+el.dataset.id]; if (!b || !b.upgrade) return;
            speedSheet(b, { cards: d.cards, left: Math.max(1, b.upgrade.left - Math.round((Date.now() - drawnAt) / 1000)) }, io);
          },
          "collect-all": function (el) {
            send("base/collect_all/", {}, el, io).then(function (r) {
              return K.reward({ icon: "bs_silo", title: "برداشت شد!", coins: r.got.coins, dna: r.got.dna, diamonds: r.got.diamonds, button: "خوبه" }).then(function () { return showMissions(r.missions); });
            }).catch(noop);
          }
        });
      });
    }
  });

  // ═════════════════════════ one building ═════════════════════════
  function upgradeBlock(b, bl) {
    var up = b.upgrade, nx = b.next, build = b.level === 0, h = "";
    if (up) {
      h += '<div class="panel pad gold bs-up"><div class="flex between"><b>' + K.ic("hammer") + " " + (build ? "در حال ساخت" : "در حال ارتقا تا سطح " + K.n(up.target)) + '</b><span class="timer" data-left="' + up.left + '" data-done="تموم شد"></span></div>' +
        '<div class="mt">' + progress(up.left, up.total) + "</div>" +
        '<div class="btns mt"><button class="btn gold" data-timed data-act="speed">' + K.ic("bs_card") + 'کارت سرعت</button><button class="btn" data-timed data-act="finish">' + K.ic("bolt") + "اتمام فوری" + '<span class="cost t-diamond">' + K.ic("gem") + K.n(up.finish_price) + "</span></button></div>" +
        '<button class="btn ghost block sm mt t-bad" data-timed data-act="cancel">' + K.ic("close") + (build ? "لغو ساخت" : "لغو ارتقا") + "</button></div>";
      return h;
    }
    if (b.state === "locked") return '<div class="callout warn">' + K.ic("lock") + "<span>هنوز قفله؛ از سطح " + K.n(b.unlock_hall) + " تالار مِهر باز می‌شه.</span></div>";
    if (b.state === "max") return '<div class="callout good">' + K.ic("trophy") + "<span>این ساختمون به سقف سطح رسیده.</span></div>";
    if (b.state === "capped") return '<div class="callout warn">' + K.ic("lock") + "<span>برای ادامه اول باید تالار مِهر رو ارتقا بدی (سقف فعلی: سطح " + K.n(b.cap) + ").</span></div>";
    if (!nx) return "";
    var poor = have("coins") < nx.cost;
    h += '<div class="panel bs-next"><div class="kv"><div><span>' + (build ? "هزینه‌ی ساخت" : "هزینه‌ی ارتقا به سطح " + K.n(nx.target)) + '</span><span class="' + (poor ? "t-bad" : "") + '">' + (poor ? K.ic("coin") + " " + K.n(nx.cost) : amount("coins", nx.cost)) + "</span></div>" +
      "<div><span>مدت زمان</span><span>" + K.dur(nx.seconds) + "</span></div>" +
      (nx.lab_req ? "<div><span>سطح آزمایشگاه لازم</span><span class=\"" + (nx.lab_ok ? "t-good" : "t-bad") + '">' + K.n(nx.lab_req) + "</span></div>" : "") + "</div>";
    if (b.state === "busy") h += '<div class="pad-in"><div class="callout warn">' + K.ic("hourglass") + "<span>بنّاهات مشغول ساختمون‌های دیگه‌ان (" + '<span class="num">' + bl.busy + "/" + bl.slots + "</span>). صبر کن یکی تموم بشه" + (bl.second_cost ? " یا بنّای دوم بخر" : "") + ".</span></div>" +
      (bl.second_cost ? '<button class="btn block mt" data-act="builder">' + K.ic("plus") + "بنّای دوم" + '<span class="cost t-diamond">' + K.ic("gem") + K.n(bl.second_cost) + "</span></button>" : "") + "</div>";
    else h += '<div class="pad-in">' + (nx.lab_ok ? "" : '<div class="callout warn mb">' + K.ic("flask") + "<span>سطح آزمایشگاهت هنوز به " + K.n(nx.lab_req) + " نرسیده. با بازی‌کردن و فعالیت بالا می‌ره.</span></div>") +
      (poor && nx.lab_ok ? '<div class="callout warn mb">' + K.ic("coin") + "<span>طلا کم داری؛ " + K.n(nx.cost - have("coins")) + " طلای دیگه لازمه.</span></div>" : "") +
      '<button class="btn ' + (poor || !nx.lab_ok ? "bs-off" : "primary") + ' block lg" data-act="upgrade">' + K.ic(build ? "hammer" : "up") + (build ? "شروع ساخت" : "شروع ارتقا") + '<span class="cost">' + K.ic("coin") + K.short(nx.cost) + "</span></button></div>";
    return h + "</div>";
  }
  function producerBlock(b, d) {
    var r = RES[b.resource], ratio = b.store_cap > 0 ? b.pending / b.store_cap : 0, full = ratio >= 1;
    var h = '<div class="panel pad bs-silo"><div class="flex between"><span class="muted">مخزن</span><span class="b">' + K.n(b.pending) + ' <span class="muted">/ ' + K.n(b.store_cap) + "</span></span></div>" +
      '<div class="mt">' + K.bar(ratio, full ? "bad" : "good", "thick") + "</div>" +
      (full ? '<div class="sm t-warn mt">مخزن پره و تولید وایساده؛ برداشت کن.</div>' : "") +
      '<button class="btn good block mt" data-act="collect"' + (b.pending > 0 ? "" : " disabled") + ">" + K.ic(r.icon) + "جمع‌آوری " + r.name + (b.pending > 0 ? ' <span class="num">+' + fmt(b.pending) + "</span>" : "") + "</button></div>" +
      '<div class="tiles c3 mt">' +
      '<div class="panel bs-fig"><small>تولید کل</small><b class="' + r.cls + '">' + num(b.rate) + "</b><small>در ساعت</small></div>" +
      '<div class="panel bs-fig"><small>تولید پایه</small><b>' + num(b.base_rate) + "</b><small>در ساعت</small></div>" +
      '<div class="panel bs-fig"><small>بونوس کارگرها</small><b class="t-good">' + plusPct(b.bonus) + "</b><small>" + (d.bonus_cap ? "سقف " + plusPct(d.bonus_cap) : "بدون سقف") + "</small></div></div>";
    // workers
    h += '<div class="h2">' + K.ic("bs_worker") + "کارگرها " + '<span class="muted num">' + d.workers.length + "/" + b.slots + "</span></div>";
    h += '<div class="panel bs-workers">' + d.workers.map(function (c) {
      return '<div class="bs-wrow">' + K.fighter(c, '<div class="sm t-good b">' + plusPct(c.influence) + " تولید</div>") +
        '<button class="iconbtn t-bad" data-act="unassign" data-id="' + c.id + '" aria-label="ترک کار">' + K.ic("close") + "</button></div>";
    }).join("");
    if (d.workers.length < b.slots) {
      h += '<button class="bs-add" data-act="assign"><span class="ico-box">' + K.ic("plus") + '</span><span class="grow"><b>فرستادن هیولا سر کار</b><small>' +
        (d.workers.length ? "جایگاه خالی داری" : "خالیه؛ هیولا بذار تا تولید بیشتر بشه") + "</small></span>" + K.ic("chevron") + "</button>";
    } else {
      h += '<div class="bs-full muted sm">' + K.ic("info") + "جایگاه خالی نداری. برای جای بیشتر ساختمون رو ارتقا بده.</div>";
    }
    return h + "</div>";
  }

  K.screen("bs_building", {
    title: "ساختمان", tab: "base",
    render: function (root, params, ctx) {
      return K.api.get("base/building/?id=" + encodeURIComponent(params.id)).then(function (d) {
        var b, bl, link, drawnAt = 0, refreshing = false;
        function refresh() {
          if (refreshing) return; refreshing = true;
          K.api.get("base/building/?id=" + encodeURIComponent(params.id)).then(function (f) { refreshing = false; if (ctx.alive()) { d = f; draw(); } }, function () { refreshing = false; });
        }
        var io = {
          view: "detail", alive: ctx.alive, refresh: refresh,
          apply: function (r) { if (r && r.detail) { d = r.detail; draw(); } else refresh(); },
          sped: function (id, r) { if (b.upgrade) { b.upgrade.left = r.left; b.upgrade.finish_price = r.finish_price; } d.cards = r.cards; draw(); }
        };
        function elapsed() { return Math.round((Date.now() - drawnAt) / 1000); }
        function draw() {
          b = d.building; bl = d.builders; drawnAt = Date.now();
          link = b.level > 0 || b.type === "dispatch_hq" ? sectionOf(b.type) : null;
          ctx.setTitle(b.label);
          var tags = (b.level === 0 ? K.tag(b.state === "locked" ? "قفل" : "ساخته‌نشده", "var(--warn)", b.state === "locked" ? "lock" : "hammer")
                                    : K.tag("سطح " + K.n(b.level) + " از " + K.n(b.cap))) +
            (b.level > 0 && !b.main && b.cap < b.max_level ? K.tag("سقف با تالار مِهر", "var(--muted)", "hall") : "") +
            (b.state === "upgrading" ? K.tag("در حال " + (b.level === 0 ? "ساخت" : "ارتقا"), "var(--gold)", "hammer") : "");
          var html = '<div class="panel hero bs-hero' + (b.level === 0 ? " unbuilt" : "") + '"><div class="art">' +
            (b.img ? '<img src="' + b.img + '&s=l" alt="">' : '<span class="bs-noimg">' + K.ic(TYPE_ICON[b.type] || "building") + "</span>") +
            '<div class="over"><div><div class="ttl">' + K.esc(b.label) + "</div>" + pips(b.level, b.max_level, b.cap) + "</div></div></div>" +
            '<div class="meta">' + tags + '</div><p class="bs-desc">' + K.esc(b.desc) + "</p></div>";

          if (b.produces && b.level > 0) html += '<div class="mt">' + producerBlock(b, d) + "</div>";

          html += '<div class="h2">' + K.ic("hammer") + (b.level === 0 ? "ساخت" : "ارتقا") + '<span class="more">' + buildersLine(bl) + "</span></div>" + upgradeBlock(b, bl);

          // what it gives
          var info = "";
          if (b.state === "locked") info += '<div style="color:var(--warn)"><span class="ic">' + K.ic("lock") + '</span><span class="t">تالار مِهر سطح ' + K.n(b.unlock_hall) + "<small>پیش‌نیاز باز شدن</small></span></div>";
          if (b.benefit) info += '<div style="color:var(--good)"><span class="ic">' + K.ic("up") + '</span><span class="t">' + K.esc(b.benefit) + "<small>مزیت ارتقا</small></span></div>";
          if (b.equip_cap != null) info += '<div style="color:var(--accent)"><span class="ic">' + K.ic("hammer") + '</span><span class="t"><span class="num b">+' + Number(b.equip_cap) + "</span><small>سقف فعلی سطح تجهیزات</small></span></div>";
          (b.extras || []).forEach(function (e) { info += '<div style="color:var(--accent)"><span class="ic">' + K.ic("info") + '</span><span class="t">' + K.esc(e) + "</span></div>"; });
          if (b.note) info += '<div style="color:var(--accent-2)"><span class="ic">' + K.ic("doc") + '</span><span class="t">' + K.esc(b.note) + "</span></div>";
          if (info) html += '<div class="h2">' + K.ic("info") + "این ساختمون چی می‌ده</div>" + '<div class="panel list bs-info">' + info + "</div>";
          if (b.unlocks && b.unlocks.items.length) {
            html += '<div class="h2">' + K.ic("spark") + "با سطح " + K.n(b.unlocks.level) + " باز می‌شه</div>" + '<div class="panel pad bs-unlocks">' +
              b.unlocks.items.map(function (t) { return "<div>" + K.ic("check") + "<span>" + K.esc(t) + "</span></div>"; }).join("") + "</div>";
          }
          if (link) html += '<button class="btn block mt" data-act="section">' + K.ic(link.icon || "chevron") + K.esc(link.title) + "</button>";
          root.innerHTML = html;
          live(root, "page", function () { expire(root); K.after(900, refresh); });
        }
        draw();

        acts(root, {
          collect: function (el) { doCollect(b.id, el, io); },
          upgrade: function (el) { askUpgrade(b, el, io); },
          builder: function (el) { buyBuilder(bl.second_cost, el, io, b.id); },
          section: function () { if (link) K.go(link.go, link.params); },
          speed: function () { if (b.upgrade) speedSheet(b, { cards: d.cards, left: Math.max(1, b.upgrade.left - elapsed()) }, io); },
          finish: function (el) {
            var up = b.upgrade; if (!up) return;
            // the price only falls while the timer runs: the server charges what it is at that moment and
            // refuses anything above the price shown here
            K.confirm({
              icon: "gem", title: "اتمام فوری با الماس",
              html: '<div class="bs-bill"><div><span>هزینه (حداکثر)</span>' + amount("diamonds", up.finish_price) + "</div><div><span>زمان باقی‌مونده</span><b>" + K.dur(Math.max(0, up.left - elapsed())) + "</b></div></div><p>هرچی بیشتر صبر کنی ارزون‌تر می‌شه؛ همون قیمتِ لحظه‌ی تأیید حساب می‌شه.</p>",
              ok: "تأیید", cancel: "انصراف"
            }).then(function (yes) {
              if (!yes) return;
              send("base/finish/", { id: b.id, price: up.finish_price }, el, io).then(function (r) {
                K.haptic("ok"); K.toast(r.cost ? "با " + fmt(r.cost) + " الماس تموم شد!" : "خودش تموم شده بود؛ الماسی کم نشد.", "ok");
              }, function () { if (ctx.alive()) refresh(); });
            });
          },
          cancel: function (el) {
            var up = b.upgrade; if (!up) return;
            K.confirm({
              danger: true, title: b.level === 0 ? "لغو ساخت" : "لغو ارتقا",
              html: "<p>اگه لغو کنی پیشرفت ساخت (تا سطح " + K.n(up.target) + ") از بین می‌ره و فقط نصف طلای پرداختی برمی‌گرده.</p>" +
                    '<div class="bs-bill"><div><span>مبلغ برگشتی</span>' + amount("coins", up.refund) + "</div></div>",
              ok: "تأیید لغو", cancel: "انصراف"
            }).then(function (yes) {
              if (!yes) return;
              send("base/cancel/", { id: b.id }, el, io).then(function (r) { K.toast("لغو شد؛ " + fmt(r.refund) + " طلا برگشت.", "ok"); }, function () { if (ctx.alive()) refresh(); });
            });
          },
          unassign: function (el) {
            send("base/unassign/", { creature: +el.dataset.id }, el, io).then(function () {
              K.invalidate("profile/creatures/"); K.haptic("ok"); K.toast("برگشت؛ تولید جمع‌شده سر جاشه.", "ok");
            }, noop);
          },
          assign: function () {
            var inf = d.influence || {};
            K.pickCreature({
              title: "کی بره سر کار؟", sub: "هرچی هیولا نایاب‌تر و قوی‌تر باشه، تولید بیشتر می‌شه. تولید جمع‌شده سر جاش می‌مونه.",
              filter: function (c) { return inf[c.id] != null; },
              note: function (c) { return "+" + Math.round(inf[c.id] * 100) + "%"; },
              empty: "هیولای آزادی نداری. هیولای فعال و اون‌هایی که سر کار، توی غار هیولا یا مأموریت اعزامی‌ان نمی‌تونن کار کنن."
            }).then(function (c) {
              if (!c) return;
              return send("base/assign/", { id: b.id, creature: c.id }, null, io).then(function () {
                K.invalidate("profile/creatures/"); K.haptic("ok"); K.toast(c.name + " سر کار رفت.", "ok");
              }, noop);
            }, function (err) { K.toast(err.message, "err"); });
          }
        });
      });
    }
  });

  // ═════════════════════════ research ═════════════════════════
  function trackIcon(t) { return t.kind === "element" ? (K.EL_ICON[t.element] || "atom") : t.kind === "atk" ? "sword" : "heart"; }
  function trackColor(t) { return t.kind === "element" ? "var(--" + t.element + ")" : t.kind === "atk" ? "#ff9a3d" : "#ff5f7e"; }
  function trackStatus(t, d) {
    if (t.state === "running") return '<span class="timer" data-left="' + t.upgrade.left + '" data-done="تموم شد"></span>';
    if (t.state === "max") return '<span class="t-gold b sm">' + K.ic("trophy") + " نهایی</span>";
    if (t.state === "capped") return '<span class="muted sm">' + K.ic("lock") + " سقف آزمایشگاه</span>";
    if (t.state === "busy") return '<span class="sm muted">' + K.ic("hourglass") + " نوبت بعدی</span>";
    return '<span class="sm t-accent b">' + K.ic("flask") + " آماده‌ی پژوهش</span>";
  }

  K.screen("bs_research", {
    title: "پژوهش", tab: "base",
    render: function (root, params, ctx) {
      return K.api.get("base/research/").then(function (d) {
        var byKey = {}, drawnAt = 0, refreshing = false;
        function elapsed() { return Math.round((Date.now() - drawnAt) / 1000); }
        function refresh() {
          if (refreshing) return; refreshing = true;
          K.api.get("base/research/").then(function (f) { refreshing = false; if (ctx.alive()) { d = f; draw(); } }, function () { refreshing = false; });
        }
        function applied(r) {
          K.invalidate("profile/creatures/");   // a finished research changes every creature's power
          if (!ctx.alive()) return;
          if (r && r.panel) { d = r.panel; draw(); } else refresh();
        }
        function finishResearch(key, el) {
          var t = byKey[key]; if (!t || !t.upgrade) return;
          K.confirm({
            icon: "gem", title: "اتمام فوری پژوهش",
            html: "<p>«" + K.esc(t.label) + "» تا سطح " + K.n(t.upgrade.target) + " همین الان تموم می‌شه.</p>" +
                  '<div class="bs-bill"><div><span>هزینه (حداکثر)</span>' + amount("diamonds", t.upgrade.finish_price) + "</div><div><span>زمان باقی‌مونده</span><b>" + K.dur(Math.max(0, t.upgrade.left - elapsed())) + "</b></div></div><p>هرچی بیشتر صبر کنی ارزون‌تر می‌شه؛ همون قیمتِ لحظه‌ی تأیید حساب می‌شه.</p>",
            ok: "تأیید", cancel: "نه"
          }).then(function (yes) {
            if (!yes) return;
            K.api.post("base/research/finish/", { key: key, price: t.upgrade.finish_price }, el).then(function (r) {
              K.haptic("ok"); K.toast(r.cost ? "با " + fmt(r.cost) + " الماس تموم شد!" : "خودش تموم شده بود؛ الماسی کم نشد.", "ok"); applied(r);
            }, function () { if (ctx.alive()) refresh(); });
          });
        }
        function trackSheet(t) {
          var col = trackColor(t), h = '<div class="bs-sheet"><div class="grab"></div><div class="pad"><div class="flex"><span class="ico-box lg" style="color:' + col + '">' + K.ic(trackIcon(t)) + '</span><div class="grow"><div class="ttl" style="font-size:19px">' + K.esc(t.label) + "</div>" +
            pips(t.level, t.max_level, d.lab_level) + '</div></div><p class="lead" style="margin:12px 0">' + K.esc(t.desc) + "</p>";
          h += '<div class="panel kv"><div><span>سطح</span><span>' + K.n(t.level) + " از " + K.n(t.max_level) + "</span></div><div><span>اثر فعلی</span><span class=\"" + (t.level ? "t-good" : "muted") + '">' + (t.level ? '<span class="num">+' + t.effect + "%</span>" : "هنوز هیچی") + "</span></div>";
          var poor = "";
          if (t.next && t.state !== "running") {
            if (have("coins") < t.next.coins) poor = "طلا کم داری — این پژوهش " + fmt(t.next.coins) + " طلا می‌خواد.";
            else if (have("dna") < t.next.dna) poor = "DNA کم داری — این پژوهش " + fmt(t.next.dna) + " DNA می‌خواد.";
            h += "<div><span>اثر در سطح " + K.n(t.next.target) + '</span><span class="t-good num">+' + t.next.effect + "%</span></div>" +
              "<div><span>هزینه</span><span>" + K.amounts({ coins: t.next.coins, dna: t.next.dna }) + "</span></div><div><span>زمان پژوهش</span><span>" + K.dur(t.next.seconds) + "</span></div>";
          }
          h += "</div>";
          if (t.state === "running") {
            var left = Math.max(1, t.upgrade.left - elapsed());
            h += '<div class="panel pad gold mt"><div class="flex between"><b>در حال پژوهش تا سطح ' + K.n(t.upgrade.target) + '</b><span class="timer" data-left="' + left + '" data-done="تموم شد"></span></div><div class="mt">' + progress(left, t.upgrade.total) + "</div>" +
              '<div class="sm muted mt">پژوهش فقط با الماس زودتر تموم می‌شه؛ کارت سرعت اینجا کار نمی‌کنه.</div></div>' +
              '<div class="foot"><button class="btn gold block lg" data-go="finish">' + K.ic("bolt") + "تمومش کن" + '<span class="cost">' + K.ic("gem") + K.n(t.upgrade.finish_price) + "</span></button></div>";
          } else if (t.state === "max") h += '<div class="callout good mt">' + K.ic("trophy") + "<span>به سقف نهایی رسیده.</span></div>";
          else if (t.state === "capped") {
            h += '<div class="callout warn mt">' + K.ic("lock") + "<span>" + (d.lab_level > 0 ? "سطح پژوهش از سطح ساختمون آزمایشگاه (" + K.n(d.lab_level) + ") جلو نمی‌زنه؛ اول خود آزمایشگاه رو ارتقا بده."
              : "اول باید ساختمون آزمایشگاه رو بسازی.") + "</span></div>" +
              (d.lab_id ? '<div class="foot"><button class="btn block" data-go="lab">' + K.ic("building") + "رفتن به ساختمون آزمایشگاه</button></div>" : "");
          } else if (t.state === "busy") h += '<div class="callout warn mt">' + K.ic("hourglass") + "<span>هم‌زمان فقط یه پژوهش می‌شه انجام داد. اول پژوهش در حال انجام تموم بشه (یا با الماس تمومش کن).</span></div>";
          else h += (poor ? '<div class="callout warn mt">' + K.ic("warn") + "<span>" + K.esc(poor) + "</span></div>" : "") +
            '<div class="foot"><button class="btn ' + (poor ? "bs-off" : "primary") + ' block lg" data-go="start">' + K.ic("flask") + "شروع پژوهش</button></div>";
          var box = K.sheet(h + "</div></div>");
          // the timer ran out with the sheet open: nothing to pay for any more
          live(box, "sheet", function () { K.closeSheet(); });
          box.querySelector(".bs-sheet").onclick = function (ev) {
            var btn = ev.target.closest("[data-go]"); if (!btn) return;
            if (btn.dataset.go === "lab") { K.closeSheet(); K.go("bs_building", { id: d.lab_id }); }
            else if (btn.dataset.go === "finish") finishResearch(t.key, btn);
            else if (poor) { K.haptic("err"); K.toast(poor, "err"); }
            else K.api.post("base/research/start/", { key: t.key }, btn).then(function (r) { K.closeSheet(); K.haptic("ok"); K.toast("پژوهش شروع شد!", "ok"); applied(r); }, noop);
          };
        }
        function draw() {
          byKey = {}; d.tracks.forEach(function (t) { byKey[t.key] = t; }); drawnAt = Date.now();
          var html = '<div class="banner bs-rhead"' + (d.img ? ' style="background-image:url(\'' + d.img + "&s=l')\"" : "") + '><div><div class="ttl">آزمایشگاه پژوهش</div><div class="sm" style="color:#c5cee2">' +
            (d.lab_level > 0 ? "سطح ساختمون " + K.n(d.lab_level) + " از " + K.n(d.max_level) : "هنوز ساخته نشده") + "</div></div></div>";
          if (d.lab_level <= 0) {
            root.innerHTML = html + K.state("flask", "اول آزمایشگاه رو بساز", "اینجا با پژوهش روی عناصر و توانایی‌ها به همه‌ی هیولاهات تقویت همیشگی می‌دی. ساختمونش از سطح " + d.hall_req + " تالار مِهر باز می‌شه.",
              d.lab_id ? '<button class="btn primary" data-act="lab" style="margin-top:16px">' + K.ic("building") + "رفتن به ساختمون آزمایشگاه</button>" : "");
            return;
          }
          html += '<div class="callout mt">' + K.ic("info") + "<span>هر پژوهش به <b>همه‌ی هیولاهات</b> تقویت همیشگی می‌ده. سطح هر پژوهش از سطح ساختمون (" + K.n(d.lab_level) + ") جلو نمی‌زنه و هم‌زمان فقط یکی انجام می‌شه.</span></div>";
          var run = d.running && byKey[d.running];
          if (run) {
            html += '<div class="panel pad gold bs-run mt" data-act="track" data-key="' + run.key + '" role="button"><div class="flex"><span class="ico-box lg" style="color:' + trackColor(run) + '">' + K.ic(trackIcon(run)) + '</span><div class="grow"><div class="b cut">' + K.esc(run.label) + ' <span class="muted sm">تا سطح ' + K.n(run.upgrade.target) + "</span></div>" +
              '<span class="timer" data-left="' + run.upgrade.left + '" data-done="تموم شد"></span></div><button class="btn sm gold" data-timed data-act="finish" data-key="' + run.key + '">' + K.ic("bolt") + '<span class="cost">' + K.ic("gem") + K.n(run.upgrade.finish_price) + "</span></button></div>" +
              '<div class="mt">' + progress(run.upgrade.left, run.upgrade.total) + "</div></div>";
          }
          html += '<div class="h2">' + K.ic("flask") + "رشته‌های پژوهش</div>" + '<div class="bs-tracks">' + d.tracks.map(function (t) {
            return '<button class="panel bs-track s-' + t.state + '" data-act="track" data-key="' + t.key + '" style="--tc:' + trackColor(t) + '">' +
              '<span class="bs-thead"><span class="ico-box">' + K.ic(trackIcon(t)) + '</span><span class="bs-eff">' + (t.level ? '<b class="t-good num">+' + t.effect + "%</b>" : '<span class="faint num">+' + t.per_level + "%</span>") + "</span></span>" +
              '<b class="cut">' + K.esc(t.label) + "</b>" + pips(t.level, t.max_level, d.lab_level) +
              '<span class="bs-tst">' + trackStatus(t, d) + "</span></button>";
          }).join("") + "</div>" +
            (d.lab_id ? '<button class="btn block mt" data-act="lab">' + K.ic("building") + "ساختمون آزمایشگاه" + (d.lab_level < d.max_level ? " (ارتقا)" : "") + "</button>" : "");
          root.innerHTML = html;
          live(root, "page", function () { expire(root); K.after(900, function () { K.invalidate("profile/creatures/"); refresh(); }); });
        }
        draw();
        acts(root, {
          track: function (el) { var t = byKey[el.dataset.key]; if (t) trackSheet(t); },
          finish: function (el) { finishResearch(el.dataset.key, el); },
          lab: function () { K.go("bs_building", { id: d.lab_id }); }
        });
      });
    }
  });

  // ═════════════════════════ entry points ═════════════════════════
  K.hub("base", { id: "buildings", title: "ساختمان‌ها", sub: "بساز، ارتقا بده، منابع جمع کن", icon: "building", color: "var(--gold)", go: "bs_buildings", order: 1 });
  K.hub("base", { id: "research", title: "پژوهش", sub: "تقویت همیشگی همه‌ی هیولاها", icon: "flask", color: "var(--accent)", go: "bs_research", order: 2, hall: RESEARCH_HALL });
})(window.K);
