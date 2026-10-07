/* The daily loop: «پاداش امروز» (dy_today), the missions and their weekly box track
   (dy_missions), the story quest «قدم بعدی» (dy_story) and the daily wheel (dy_wheel).
   Two blocks on the home screen (the story card, the «پاداش امروز» card) and two tiles in
   the «بیشتر» hub. API: daily/…
   A few helpers are shared with 43_dispatch.js through K.dy. */
(function (K) {
  "use strict";

  K.addIcons({
    dy_fast: '<path d="m4 6.500 7 5.500-7 5.500zM13 6.500l7 5.500-7 5.500z"/>'
  });

  var DOT = ' <span class="faint">·</span> ';
  function num(x) { return Number(x || 0).toLocaleString("en-US"); }
  function reduced() { return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches); }

  // ───────────────────────── rewards ─────────────────────────
  function speedupText(s) { var m = Number(s.minutes); return "کارت سرعت " + (m % 60 === 0 ? (m / 60) + " ساعته" : m + " دقیقه‌ای"); }
  function capsuleText(c) { return num(c.count) + " غذای " + c.label; }
  /* the parts of a reward that are not a currency (food, speed-up card, box tickets) → [html] */
  function extras(o) {
    var out = [];
    if (!o) return out;
    if (o.capsule) out.push(K.ic("food") + "<span>" + K.esc(capsuleText(o.capsule)) + "</span>");
    if (o.speedup) out.push(K.ic("dy_fast") + "<span>" + K.esc(speedupText(o.speedup)) + "</span>");
    if (o.tickets) out.push(K.ic("ticket") + "<span>" + num(o.tickets) + " بلیط باکس</span>");
    return out;
  }
  /* a whole reward on one (wrapping) line */
  function rewardLine(o) {
    var parts = [], a = K.amounts(o || {});
    if (a) parts.push(a);
    extras(o).forEach(function (h) { parts.push('<span class="b dy-x">' + h + "</span>"); });
    return parts.join(DOT);
  }
  function doneMissions(list) {
    if (!list || !list.length) return "";
    return '<div class="pad"><div class="dy-done"><div class="dy-done-h">' + K.ic("target") + (list.length > 1 ? num(list.length) + " مأموریت تکمیل شد" : "یه مأموریت تکمیل شد") + "</div>" +
      list.map(function (m) {
        return '<div class="dy-done-r"><b>' + K.esc(m.label) + "</b>" + (m.weekly ? ' <span class="xs muted">هفتگی</span>' : "") +
          '<div class="sm">' + rewardLine(m) + (m.points ? DOT + '<span class="t-gold b">' + K.ic("star") + " " + K.n(m.points) + " امتیاز</span>" : "") + "</div></div>";
      }).join("") + "</div></div>";
  }
  /* Reward reveal — K.reward plus: a block of extra html (o.html), the missions the action
     completed (o.missions) and a «جدید» flag on the creatures won (o.fresh). → Promise */
  function reveal(o) {
    return new Promise(function (resolve) {
      var chips = [];
      [["coins", "coin", "t-coin"], ["dna", "dna", "t-dna"], ["diamonds", "gem", "t-diamond"], ["xp", "up", "t-xp"]].forEach(function (c) {
        if (o[c[0]]) chips.push('<span class="it ' + c[2] + '">' + K.ic(c[1]) + '<span class="num" style="color:var(--text)">+' + num(o[c[0]]) + (c[0] === "xp" ? " XP" : "") + "</span></span>");
      });
      (o.extra || []).forEach(function (h) { chips.push('<span class="it">' + h + "</span>"); });
      var cards = (o.creatures || []).map(function (c) { return K.creatureTile(c, { tag: "div", flag: o.fresh ? "جدید" : "" }); })
        .concat((o.items || []).map(function (e) { return K.itemTile(e, { tag: "div" }); })).join("");
      K.sheet('<div class="grab"></div><div class="loot"><div class="burst">' + K.ic(o.icon || "gift") + "</div><h3>" + K.esc(o.title || "جایزه گرفتی!") + "</h3>" +
        (o.text ? "<p>" + K.esc(o.text) + "</p>" : "") + (chips.length ? '<div class="items">' + chips.join("") + "</div>" : "") + (cards ? '<div class="cards">' + cards + "</div>" : "") + "</div>" +
        (o.html || "") + doneMissions(o.missions) +
        '<div class="pad" style="margin-top:16px"><button class="btn primary block" data-close>' + K.esc(o.button || "عالیه") + "</button></div>", { onClose: resolve });
      K.haptic("ok");
    });
  }

  // ───────────────────────── navigation to other areas ─────────────────────────
  /* The bot's menu actions (a story quest's «menu:hunt», the quick buttons of «پاداش امروز»)
     → app screens. Other areas are written by other modules; the first registered name wins
     and an unregistered one gets the router's «هنوز آماده نیست» toast. */
  var ROUTES = {
    hunt: ["hunt", "hu_home", "hu_hunt", "hunt_home"],
    arena: ["arena", "ar_home", "ar_arena", "arena_home"],
    mugen_tower: ["tower", "hu_tower", "tw_home", "mugen_tower", "mugen"],
    biocrate: ["sh_boxes", "boxes", "sh_box", "sh_home", "biocrate"],
    alliance_info: ["alliance", "so_alliance", "al_home", "sc_alliance"],
    worldboss: ["wb_boss"], tournament: ["tr_home"], festival: ["ev_festival"], events: ["ev_week", "ev_home", "ev_events"],
    chests: ["ar_chests", "chests", "arena_chests"],
    missions: ["dy_missions"], wheel: ["dy_wheel"], dispatch: ["dp_home"], today: ["dy_today"], story: ["dy_story"]
  };
  var HUB_ID = { mugen_tower: "tower", biocrate: "boxes", alliance_info: "alliance", worldboss: "boss", events: "week" };
  function goAction(action, params) {
    if (action === "buildings") { if (K.hasScreen("bs_buildings")) K.go("bs_buildings"); else K.tab("base"); return; }
    if (action === "upgrade") { if (K.me && K.me.active) K.go("creature", { id: K.me.active.id }); else K.tab("creatures"); return; }
    var names = ROUTES[action] || [action];
    for (var i = 0; i < names.length; i++) if (K.hasScreen(names[i])) { K.go(names[i], params); return; }
    var tile = K.hubTile && K.hubTile(HUB_ID[action] || action);   // whatever screen that module registered its tile with
    if (tile && tile.go && K.hasScreen(tile.go)) { K.go(tile.go, tile.params); return; }
    K.go(names[0], params);
  }

  /* Countdowns: <span class="timer" data-until="<unix seconds>">. ONE interval per screen that
     re-reads the timers on every tick, so redrawing part of a screen never stacks intervals
     (and the text is there on the first paint). Returns the tick function. */
  function ticker(root, onDone) {
    function tick() {
      var now = Date.now() / 1000;
      Array.prototype.forEach.call(root.querySelectorAll("[data-until]"), function (el) {
        if (el._done) return;
        var left = Number(el.dataset.until) - now;
        if (left <= 0) { el._done = true; el.classList.add("done"); el.innerHTML = K.ic("check") + K.esc(el.dataset.done || "آماده"); if (onDone) onDone(el); }
        else el.innerHTML = K.ic("clock") + '<span class="num">' + (el.dataset.fmt === "long" ? K.dur(left) : K.clock(left)) + "</span>";
      });
    }
    tick(); K.every(1000, tick);
    return tick;
  }
  function untilOf(seconds) { return Math.round(Date.now() / 1000 + Number(seconds || 0)); }

  K.dy = { reveal: reveal, extras: extras, rewardLine: rewardLine, speedupText: speedupText, capsuleText: capsuleText, go: goAction, num: num, ticker: ticker };

  // ───────────────────────── home summary (one small request for both home blocks) ─────────────────────────
  var last = null, homeP = null;
  function homeData() {
    if (!homeP) {
      var p = homeP = K.api.get("daily/home/").then(function (d) { last = d; return d; });
      setTimeout(function () { if (homeP === p) homeP = null; }, 1500);
    }
    return homeP;
  }
  function touch(key, value) { if (last) last[key] = value; }
  K.dy.last = function () { return last; };
  K.dy.touch = touch;

  // ───────────────────────── story quest ─────────────────────────
  function questCard(q, big) {
    var ratio = q.target ? q.cur / q.target : 0;
    return '<div class="panel pad dy-quest' + (q.done ? " done" : "") + '">' +
      "<" + (big ? "div" : 'button data-act="dy-story"') + ' class="dy-quest-top"><span class="ico-box lg" style="color:' + (q.done ? "var(--good)" : "var(--accent)") + '">' + K.ic(q.done ? "check" : "flag") + "</span>" +
      '<span class="grow"><span class="xs muted dy-block">' + K.esc(q.chapter_name) + DOT + "قدم " + K.n(q.step) + " از " + K.n(q.total) + "</span>" +
      '<b class="dy-quest-t">' + K.esc(q.title) + "</b></span>" + (big ? "" : '<span class="faint">' + K.ic("chevron") + "</span>") + "</" + (big ? "div" : "button") + ">" +
      (big ? '<p class="dy-quest-d">' + K.esc(q.desc) + "</p>" : "") +
      '<div class="dy-quest-p">' + K.bar(ratio, q.done ? "good" : "") + '<span class="num xs b">' + num(q.cur) + " / " + num(q.target) + "</span></div>" +
      '<div class="dy-quest-r sm"><span class="muted">' + K.ic("gift") + " پاداش:</span> " + rewardLine(q.reward) + "</div>" +
      (q.done ? '<button class="btn gold block" data-act="dy-claim">' + K.ic("gift") + "دریافت پاداش</button>"
              : q.cta ? '<button class="btn primary block" data-act="dy-cta" data-go="' + K.esc(q.cta) + '">' + K.esc(q.cta_label || "بزن بریم") + K.ic("chevron") + "</button>" : "") +
      "</div>";
  }
  function bindQuest(root, after) {
    var busy = false;
    K.on(root, "dy-story", function () { K.go("dy_story"); });
    K.on(root, "dy-cta", function (el) { goAction(el.dataset.go); });
    K.on(root, "dy-claim", function (el) {
      if (busy) return; busy = true;
      K.api.post("daily/story/claim/", {}, el).then(function (r) {
        busy = false;
        if (last) last.quest = r.next;
        after(r);   // the screen shows the next step behind the reward sheet
        reveal({ icon: "flag", title: "مأموریت انجام شد!", text: r.title, coins: r.reward.coins, dna: r.reward.dna, diamonds: r.reward.diamonds,
                 extra: extras(r.reward), button: r.next ? "قدم بعدی" : "عالیه" });
      }, function () { busy = false; });
    });
  }

  K.homeSection({
    order: 20,
    render: function (el) {
      return homeData().then(function (d) {
        if (!d.quest) return;
        function draw(q) { el.innerHTML = q ? '<div class="h2">' + K.ic("flag") + "قدم بعدی</div>" + questCard(q, false) : ""; }
        draw(d.quest);
        bindQuest(el, function (r) { draw(r.next); });
      });
    }
  });

  K.screen("dy_story", {
    title: "قدم بعدی", tab: "home",
    render: function (root, params, ctx) {
      return K.api.get("daily/story/").then(function (d) {
        function draw(q) {
          if (!q) { root.innerHTML = K.state("flag", "داستان رو تموم کردی", "همه‌ی مأموریت‌های داستانی انجام شدن. از مأموریت‌های روزانه و هفتگی جا نمونی!", '<button class="btn primary" data-act="dy-missions" style="margin-top:16px">' + K.ic("target") + "مأموریت‌ها</button>"); return; }
          root.innerHTML = '<div class="panel pad dy-chapter"><div class="flex between"><b>' + K.esc(q.chapter_name) + '</b><span class="sm muted">قدم ' + K.n(q.step) + " از " + K.n(q.total) + "</span></div>" +
            K.bar((q.step - 1) / Math.max(1, q.total), "gold", "thick") + "</div>" +
            '<div class="mt"></div>' + questCard(q, true) +
            '<div class="callout mt">' + K.ic("info") + "<span>" + (q.done ? "این قدم رو انجام دادی؛ پاداشت رو بگیر تا قدم بعدی باز بشه." : "هر قدم که تموم بشه پاداشش رو می‌گیری و قدم بعدی باز می‌شه.") + "</span></div>";
        }
        draw(d.quest);
        K.on(root, "dy-missions", function () { K.replace("dy_missions"); });
        bindQuest(root, function (r) { if (ctx.alive()) { draw(r.next); window.scrollTo(0, 0); } });
      });
    }
  });

  // ───────────────────────── home: the «پاداش امروز» card ─────────────────────────
  function liveChips(d) {
    var out = [];
    if (d.boss) out.push('<button class="chip dy-live" style="--lc:var(--bad)" data-act="dy-go" data-go="worldboss">' + K.ic("skull") + "غول سرگردان" + DOT + K.n(d.boss.hits_left) + " ضربه</button>");
    if (d.tournament_open) out.push('<button class="chip dy-live" style="--lc:var(--gold)" data-act="dy-go" data-go="tournament">' + K.ic("trophy") + "ثبت‌نام جام بازه</button>");
    if (d.festival) out.push('<button class="chip dy-live" style="--lc:var(--' + (K.EL_ICON[d.festival.element] ? d.festival.element : "accent") + ')" data-act="dy-go" data-go="festival">' + K.ic("spark") + K.esc(d.festival.title) + "</button>");
    if (d.wheel) out.push('<button class="chip dy-live" style="--lc:var(--accent-2)" data-act="dy-go" data-go="wheel">' + K.ic("wheel") + "گردونه‌ی رایگان</button>");
    return out;
  }
  K.homeSection({
    order: 22,
    render: function (el) {
      return homeData().then(function (d) {
        var has = d.collectable > 0, chips = liveChips(d);
        el.innerHTML = '<div class="h2">' + K.ic("gift") + "پاداش امروز</div>" +
          '<div class="panel pad dy-card' + (has ? " gold" : "") + '"><button class="dy-card-top" data-act="dy-today">' +
          '<span class="ico-box lg" style="color:' + (has ? "var(--gold)" : "var(--muted)") + '">' + K.ic(has ? "gift" : "calcheck") + "</span>" +
          '<span class="grow"><b class="dy-block">' + (d.waiting ? K.n(d.waiting) + " چیز منتظرته" : "همه رو گرفتی") + "</b>" +
          '<span class="sm muted dy-block">' + (has ? (d.collectable === d.waiting ? "یک‌جا همه رو بگیر" : K.n(d.collectable) + " تاش آماده‌ی دریافته") : d.waiting ? "یه سر به رویدادهای زنده بزن" : "فعلاً چیزی برای جمع کردن نمونده") + "</span></span>" +
          '<span class="btn sm ' + (has ? "gold" : "") + '">' + (has ? "دریافت" : "ببین") + "</span></button>" +
          (chips.length ? '<div class="row dy-chips">' + chips.join("") + "</div>" : "") +
          (d.rule ? '<div class="xs muted dy-rule">' + K.ic("calendar") + " قانون این هفته: <b>" + K.esc(d.rule) + "</b></div>" : "") + "</div>";
        K.on(el, "dy-today", function () { K.go("dy_today"); });
        K.on(el, "dy-go", function (b) { goAction(b.dataset.go); });
      });
    }
  });

  // ───────────────────────── «پاداش امروز» ─────────────────────────
  var RES = { coins: ["coin", "var(--coin)"], dna: ["dna", "var(--dna)"], diamonds: ["gem", "var(--diamond)"] };
  var SEC_ICON = { building: "building", dispatch: "compass", gift: "gift", wheel: "wheel", box: "box" };
  var BOX_TIER = { bronze: "برنزی", silver: "نقره‌ای" };

  function row(icon, color, title, small, value, go) {
    return "<" + (go ? 'button data-act="dy-go" data-go="' + go + '"' : "div") + '><span class="ic" style="color:' + color + '">' + K.ic(icon) + "</span>" +
      '<span class="t">' + title + (small ? "<small>" + small + "</small>" : "") + "</span>" + (value ? '<span class="v">' + value + "</span>" : "") +
      (go ? '<span class="chev">' + K.ic("chevron") + "</span>" : "") + "</" + (go ? "button" : "div") + ">";
  }
  function readyRow(r) {
    if (r.kind === "building") { var s = RES[r.resource] || RES.coins; return row(s[0], s[1], K.esc(r.label), "", '<span class="num" style="color:' + s[1] + '">+' + num(r.amount) + "</span>"); }
    if (r.kind === "dispatch") return row("compass", "var(--accent)", K.n(r.count) + " هیولا از مأموریت برگشته", "جایزه‌شون آماده‌ست", "", "dispatch");
    if (r.kind === "event") return row("calendar", "var(--accent-2)", "جایزه‌ی روزانه‌ی رویداد", rewardLine(r.reward));
    if (r.kind === "story") return row("flag", "var(--good)", "پاداش مأموریت داستانی", K.esc(r.label) + (rewardLine(r.reward) ? "<br>" + rewardLine(r.reward) : ""), "", "story");
    if (r.kind === "wheel") return row("wheel", "var(--accent-2)", "گردونه‌ی رایگان امروز", "می‌تونی خودت هم بچرخونیش", "", "wheel");
    if (r.kind === "free_box") return row("box", "var(--rare)", K.n(r.count) + " باکس رایگان", K.esc((r.tiers || []).map(function (t) { return BOX_TIER[t] || t; }).join(" و ")));
    if (r.kind === "chest") return row("chest", "var(--gold)", K.n(r.count) + " جعبه‌ی آرنا", "آماده‌ی باز شدن");
    if (r.kind === "mission_box") return row("gift", "var(--gold)", K.n(r.count) + " باکس مأموریت", "از مسیر باکس‌های هفته", "", "boxes");
    return "";
  }
  function collectSheet(r) {
    var html = r.sections.map(function (s) {
      return '<div class="dy-sec"><div class="dy-sec-h">' + K.ic(SEC_ICON[s.kind] || "gift") + K.esc(s.title) + "</div>" +
        s.lines.map(function (l) { return '<div class="dy-ln' + (l.sub ? " sub" : "") + (l.tone ? " " + l.tone : "") + '">' + (l.tone === "bonus" ? K.ic("spark") : l.tone === "level" ? K.ic("up") : "") + K.esc(l.text) + "</div>"; }).join("") + "</div>";
    }).join("");
    return reveal({ title: "همه رو گرفتی!", coins: r.totals.coins, dna: r.totals.dna, diamonds: r.totals.diamonds, creatures: r.creatures, items: r.items, fresh: true,
                    html: html ? '<div class="pad dy-secs">' + html + "</div>" : "", missions: r.missions });
  }
  function todayHtml(d) {
    var art = (K.meta && K.meta.art && K.meta.art.missions) || "", has = d.ready.length > 0, html = "", live = "";
    var auto = d.ready.some(function (r) { return r.kind === "wheel" || r.kind === "free_box" || r.kind === "chest" || r.kind === "mission_box"; });
    html += '<div class="banner dy-hero' + (has ? " has" : "") + '"' + (art ? ' style="background-image:url(\'' + art + '\')"' : "") + '><div class="grow">' +
      '<div class="dy-hero-k">' + K.ic(has ? "gift" : "calcheck") + "پاداش امروز</div>" +
      '<div class="ttl">' + (has ? K.n(d.collectable) + " جایزه منتظرته" : "همه رو گرفتی") + "</div>" +
      '<div class="sm dy-hero-s">' + (has ? "یک‌جا همه رو بگیر" : "ساختمون‌ها که پر شدن یا هیولایی که برگشت، همین‌جا پیداش می‌شه.") + "</div>" +
      (has ? '<button class="btn gold lg block dy-hero-b" data-act="dy-collect">' + K.ic("gift") + 'دریافت همه<span class="cost num">' + num(d.collectable) + "</span></button>" : "") + "</div></div>";
    if (has) {
      html += '<div class="h2">' + K.ic("gift") + 'آماده‌ی دریافت</div><div class="panel list">' + d.ready.map(readyRow).join("") + "</div>" +
        (auto ? '<p class="note" style="margin-top:8px">«دریافت همه» گردونه رو هم می‌چرخونه و باکس‌ها و جعبه‌های آماده رو باز می‌کنه.</p>' : "");
    }
    if (d.boss) live += row("skull", "var(--bad)", "غول سرگردان اینجاست!", K.esc(d.boss.name) + DOT + K.n(d.boss.hits_left) + " ضربه داری", "", "worldboss");
    if (d.tournament_open) live += row("trophy", "var(--gold)", "ثبت‌نام جام آخر هفته بازه", "رایگانه", "", "tournament");
    if (d.festival) live += row("spark", "var(--" + (K.EL_ICON[d.festival.element] ? d.festival.element : "accent") + ")", "جشنواره‌ی «" + K.esc(d.festival.title) + "» در جریانه", "از همه‌ی کارهات سکه‌ی جشنواره می‌افته", "", "festival");
    if (d.rule) live += row("calendar", "var(--accent-2)", "قانون این هفته", K.esc(d.rule), "", "events");
    if (live) html += '<div class="h2">' + K.ic("bolt") + 'الان فعاله</div><div class="panel list glow">' + live + "</div>";

    var left = d.missions_total - d.missions_done, todo = "";
    todo += row("target", "var(--accent)", "مأموریت‌های امروز", left > 0 ? (d.next_box ? "تا باکس بعدی " + K.n(d.next_box.left) + " امتیاز" : "") : "همه رو انجام دادی",
                '<span class="num">' + num(d.missions_done) + " / " + num(d.missions_total) + "</span>", "missions");
    if (d.can_dispatch) todo += row("compass", "var(--accent)", "می‌تونی یه هیولا بفرستی مأموریت", "جایگاه خالی و مأموریت باز داری", "", "dispatch");
    todo += row("bolt", "var(--energy)", "انرژی", "", '<span class="num">' + num(d.energy) + " / " + num(d.max_energy) + "</span>");
    return html + '<div class="h2">' + K.ic("list") + 'کارهای امروز</div><div class="panel list">' + todo + "</div>" +
      '<div class="btns mt"><button class="btn" data-act="dy-go" data-go="hunt">' + K.ic("target") + 'شکار</button><button class="btn" data-act="dy-go" data-go="arena">' + K.ic("swords") + "آرنا</button></div>";
  }
  /* keep the home card / hub badges in step with what the screen just learned */
  function syncHome(d) {
    if (!last) return;
    last.collectable = d.collectable; last.waiting = d.waiting; last.wheel = d.ready.some(function (r) { return r.kind === "wheel"; });
    var mb = d.ready.filter(function (r) { return r.kind === "mission_box"; })[0], fb = d.ready.filter(function (r) { return r.kind === "free_box"; })[0];
    last.boxes_ready = mb ? mb.count : 0; last.free_boxes = fb ? fb.count : 0;
  }

  K.screen("dy_today", {
    title: "پاداش امروز", tab: "home",
    render: function (root, params, ctx) {
      return K.api.get("daily/today/").then(function (d) {
        var busy = false;
        function draw() { root.innerHTML = todayHtml(d); syncHome(d); }
        draw();
        K.on(root, "dy-go", function (el) {
          if (el.dataset.go === "boxes") K.go("dy_missions", { tab: "b" }); else goAction(el.dataset.go);
        });
        K.on(root, "dy-collect", function (el) {
          if (busy) return; busy = true;
          K.api.post("daily/today/collect/", {}, el).then(function (r) {
            busy = false;
            K.invalidate("profile/creatures/", "profile/equipment/");
            if (r.today) { d = r.today; if (ctx.alive()) { draw(); window.scrollTo(0, 0); } }   // the new screen came with the answer
            else ctx.reload();
            if (r.empty) { K.toast("چیزی برای دریافت نبود."); return; }
            collectSheet(r);
          }, function () { busy = false; ctx.reload(); });
        });
      });
    }
  });

  // ───────────────────────── missions ─────────────────────────
  var mTab = "d";
  var TIER = { silver: ["chest", "#c9d3e6"], golden: ["chest", "var(--gold)"], magical: ["crystal", "var(--epic)"], mega: ["crown", "var(--mythic)"] };

  function missionRow(m) {
    return '<div class="dy-m' + (m.done ? " done" : "") + '"><span class="ico-box" style="color:' + (m.done ? "var(--good)" : "var(--accent)") + '">' + K.ic(m.done ? "check" : "target") + "</span>" +
      '<div class="grow"><div class="dy-m-h"><b>' + K.esc(m.label) + "</b>" +
      (m.done ? '<span class="tag" style="color:var(--good)">انجام شد</span>' : '<span class="num sm b">' + num(m.progress) + " / " + num(m.target) + "</span>") + "</div>" +
      (m.done ? "" : K.bar(m.target ? m.progress / m.target : 0)) +
      '<div class="dy-m-r sm">' + rewardLine(m) + DOT + '<span class="t-gold b">' + K.ic("star") + " " + K.n(m.points) + " امتیاز</span></div></div></div>";
  }
  function missionList(list) {
    var todo = list.filter(function (m) { return !m.done; }), done = list.filter(function (m) { return m.done; });
    return (todo.length ? "" : '<div class="callout good mb">' + K.ic("check") + "<span>همه رو انجام دادی!</span></div>") +
      '<div class="panel dy-ms">' + todo.concat(done).map(missionRow).join("") + "</div>";
  }
  function boxRow(b, points) {
    var t = TIER[b.tier] || TIER.silver, ready = b.reached && !b.opened;
    return '<div class="dy-box' + (b.opened ? " opened" : ready ? " ready" : " locked") + (b.tier === "mega" ? " mega" : "") + '" style="--bc:' + t[1] + '">' +
      '<span class="dy-box-ic">' + K.ic(b.opened ? "check" : b.reached ? t[0] : "lock") + "</span>" +
      '<div class="grow"><b>' + K.esc(b.name || "باکس") + '</b><small class="dy-block muted">پله‌ی ' + K.n(b.index) + DOT + K.n(b.need) + " امتیاز</small></div>" +
      (b.opened ? '<span class="tag" style="color:var(--good)">' + K.ic("check") + "باز شد</span>"
        : ready ? '<button class="btn gold sm" data-act="dy-box" data-i="' + b.index + '">' + K.ic("gift") + "باز کن</button>"
        : '<span class="sm muted">' + K.n(b.need - points) + " امتیاز مونده</span>") + "</div>";
  }

  K.screen("dy_missions", {
    title: "مأموریت‌ها", tab: "more",
    render: function (root, params, ctx) {
      if (params.tab) { mTab = params.tab; params.tab = ""; }
      return K.api.get("daily/missions/").then(function (d) {
        var weekEnd = untilOf(d.reset_in), dayEnd = untilOf(d.day_reset_in), busy = false;
        function draw() {
          var ready = d.boxes.filter(function (b) { return b.reached && !b.opened; });
          var next = d.boxes.filter(function (b) { return !b.reached; })[0];
          var target = next ? next.need : (d.boxes.length ? d.boxes[d.boxes.length - 1].need : 1);
          var dLeft = d.daily.filter(function (m) { return !m.done; }).length, wLeft = d.weekly.filter(function (m) { return !m.done; }).length;
          touch("boxes_ready", ready.length);

          var html = '<div class="panel pad dy-pts"><div class="flex between"><b>' + K.ic("star") + ' امتیاز این هفته</b><span class="b t-gold num">' + num(d.points) + '<small class="muted"> / ' + num(target) + "</small></span></div>" +
            '<div class="dy-rail">' + K.bar(Math.min(d.points, target) / Math.max(1, target), "gold", "thick") + "</div>" +
            '<div class="dy-steps">' + d.boxes.map(function (b) {
              var t = TIER[b.tier] || TIER.silver;
              return '<span class="dy-step' + (b.opened ? " opened" : b.reached ? " ready" : "") + '" style="--bc:' + t[1] + '">' + K.ic(b.opened ? "check" : t[0]) + '<small class="num">' + num(b.need) + "</small></span>";
            }).join("") + "</div>" +
            '<div class="sm muted" style="margin-top:8px">' + (next ? "تا «" + K.esc(next.name) + "»: <b style=\"color:var(--text)\">" + K.n(next.need - d.points) + "</b> امتیاز" : "به همه‌ی باکس‌های این هفته رسیدی") + "</div>" +
            (ready.length ? '<button class="btn gold block mt" data-act="dy-tab" data-t="b">' + K.ic("gift") + K.n(ready.length) + " باکس آماده‌ی باز شدنه</button>" : "") + "</div>" +
            '<div class="seg mt">' + [["d", "روزانه", dLeft], ["w", "هفتگی", wLeft], ["b", "باکس‌ها", ready.length]].map(function (t) {
              return '<button data-act="dy-tab" data-t="' + t[0] + '">' + t[1] + (t[0] === "b" && t[2] ? '<span class="cnt">' + t[2] + "</span>" : "") + "</button>";
            }).join("") + "</div>";

          html += '<div data-pane="d"><div class="dy-ph"><b>مأموریت‌های امروز</b><span class="sm muted">' + K.n(d.today_points) + " / " + K.n(d.today_max) + " امتیاز</span></div>" + missionList(d.daily) +
            '<p class="note">ریست روزانه (نیمه‌شب تهران): <span class="timer" data-until="' + dayEnd + '" data-done="ریست شد"></span></p></div>';
          html += '<div data-pane="w"><div class="dy-ph"><b>مأموریت‌های هفتگی</b><span class="sm muted">کل هفته وقت داری</span></div>' + missionList(d.weekly) +
            '<p class="note">ریست هفتگی (دوشنبه، نیمه‌شب تهران): <span class="timer" data-until="' + weekEnd + '" data-fmt="long" data-done="ریست شد"></span></p></div>';
          html += '<div data-pane="b"><div class="callout mb">' + K.ic("info") + "<span>با هر مأموریت امتیاز می‌گیری؛ به هر پله که برسی یه باکس باز می‌کنی. آخرین پله «باکس امگا»ست: برای کسی که تقریباً همه‌ی مأموریت‌های هفته رو انجام بده.</span></div>" +
            '<div class="panel dy-track">' + d.boxes.map(function (b) { return boxRow(b, d.points); }).join("") + "</div>" +
            '<p class="note">سقف امتیاز هفته: ' + K.n(d.max_points) + DOT + 'ریست: <span class="timer" data-until="' + weekEnd + '" data-fmt="long" data-done="ریست شد"></span></p></div>';
          root.innerHTML = html;
          show();
        }
        function show() {
          Array.prototype.forEach.call(root.querySelectorAll("[data-pane]"), function (p) { p.hidden = p.dataset.pane !== mTab; });
          Array.prototype.forEach.call(root.querySelectorAll(".seg button"), function (b) { b.classList.toggle("on", b.dataset.t === mTab); });
        }
        if (mTab !== "d" && mTab !== "w" && mTab !== "b") mTab = "d";
        draw();
        var fired = false, tick = ticker(root, function () { if (fired) return; fired = true; K.after(1500, ctx.reload); });
        K.on(root, "dy-tab", function (el) { K.haptic(); mTab = el.dataset.t; show(); });
        K.on(root, "dy-box", function (el) {
          if (busy) return; busy = true;
          K.api.post("daily/missions/box/", { index: +el.dataset.i }, el).then(function (r) {
            busy = false;
            K.invalidate("profile/creatures/", "profile/equipment/");
            // the only thing an opened box changes on this screen is its own «opened» mark
            d.boxes.forEach(function (b) { if (b.index === r.index) b.opened = true; });
            mTab = "b";
            if (ctx.alive()) { draw(); tick(); }
            reveal({ icon: "chest", title: r.name + " باز شد!", coins: r.coins, dna: r.dna, diamonds: r.diamonds, creatures: r.creatures, items: r.items, fresh: true });
          }, function () { busy = false; ctx.reload(); });
        });
      });
    }
  });

  // ───────────────────────── wheel ─────────────────────────
  /* The slices show the KINDS of prize the wheel can give (game.wheel) — the amount is rolled
     by the server for this player, and the wheel simply stops on the kind it returned. */
  var SEGS = [["coins", "coin", "طلا", "#ffc857"], ["creature", "claw", "هیولا", "#ff6b3d"], ["dna", "dna", "DNA", "#5ee6a8"], ["speedup", "dy_fast", "کارت سرعت", "#a98bff"],
              ["diamonds", "gem", "الماس", "#6fd3ff"], ["xp_capsule", "food", "غذا", "#ff62d0"], ["coins", "coin", "طلا", "#ffc857"], ["jackpot", "crown", "جک‌پات", "#ff4d6d"]];
  var STEP = 360 / SEGS.length;

  function wheelSvg() {
    var R = 90, s = '<svg viewBox="-100 -100 200 200" aria-hidden="true"><defs><radialGradient id="dy-wg" cx="50%" cy="50%" r="50%"><stop offset="55%" stop-color="#000" stop-opacity="0"/><stop offset="100%" stop-color="#000" stop-opacity=".45"/></radialGradient></defs>';
    function pt(deg, r) { var a = deg * Math.PI / 180; return (r * Math.sin(a)).toFixed(2) + " " + (-r * Math.cos(a)).toFixed(2); }
    SEGS.forEach(function (g, i) {
      var a0 = i * STEP - STEP / 2;
      s += '<path class="dy-slice" data-i="' + i + '" d="M0 0L' + pt(a0, R) + "A" + R + " " + R + " 0 0 1 " + pt(a0 + STEP, R) + 'Z" fill="' + g[3] + '" fill-opacity="' + (i % 2 ? ".2" : ".36") + '" stroke="rgba(255,255,255,.16)" stroke-width=".8"/>';
    });
    s += '<circle r="' + R + '" fill="url(#dy-wg)"/>';
    SEGS.forEach(function (g, i) {
      s += '<g transform="rotate(' + (i * STEP) + ')"><g transform="translate(-11 -82) scale(.92)" fill="none" stroke="' + g[3] + '" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + (K.icons[g[1]] || "") + "</g>" +
        '<text x="0" y="-46" text-anchor="middle" font-size="8.6" font-weight="700" fill="#eaf0ff">' + g[2] + "</text></g>";
    });
    return s + '<circle r="' + R + '" fill="none" stroke="rgba(255,200,87,.8)" stroke-width="3"/><circle r="19" fill="#131927" stroke="rgba(255,200,87,.8)" stroke-width="2.5"/>' +
      '<g transform="translate(-10 -10) scale(.84)" fill="none" stroke="#ffc857" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + K.icons.star + "</g></svg>";
  }
  /* the ring of bulbs around the wheel (it does not turn) */
  function bulbs() {
    var out = "", n = 16;
    for (var i = 0; i < n; i++) { var a = i * 2 * Math.PI / n; out += '<i style="left:' + (50 + 48.2 * Math.sin(a)).toFixed(2) + "%;top:" + (50 - 48.2 * Math.cos(a)).toFixed(2) + '%"></i>'; }
    return '<div class="dy-wheel-bulbs">' + out + "</div>";
  }
  function wheelReveal(r) {
    return reveal({
      icon: r.kind === "creature" ? "claw" : r.kind === "jackpot" ? "crown" : "wheel",
      title: r.kind === "jackpot" ? "جک‌پات!" : r.kind === "creature" ? "یه هیولای جدید!" : "جایزه‌ی گردونه",
      text: r.label, coins: r.coins, dna: r.dna, diamonds: r.diamonds, extra: extras(r), creatures: r.creatures, fresh: true, missions: r.missions
    });
  }

  K.screen("dy_wheel", {
    title: "گردونه‌ی شانس", tab: "more",
    render: function (root, params, ctx) {
      return K.api.get("daily/wheel/").then(function (d) {
        var busy = false, resetAt = untilOf(d.reset_in);
        touch("wheel", d.available);
        root.innerHTML = '<div class="dy-wheel' + (d.available ? "" : " off") + '">' + bulbs() + '<span class="dy-wheel-pin"></span><button class="dy-wheel-disc" data-act="dy-spin" aria-label="بچرخون">' + wheelSvg() + "</button></div>" +
          '<div id="dy-wfoot"></div>' +
          '<div class="callout mt">' + K.ic("info") + "<span>گردونه می‌تونه طلا، DNA، الماس، غذای هیولا، کارت سرعت، یه هیولای تصادفی یا جک‌پات بده. هر چی هیولای فعالت قوی‌تر و کاپت بیشتر باشه، جایزه‌ها بزرگ‌ترن.</span></div>";
        var wrap = root.querySelector(".dy-wheel"), disc = root.querySelector(".dy-wheel-disc"), foot = root.querySelector("#dy-wfoot");
        function drawFoot() {
          wrap.classList.toggle("off", !d.available);
          foot.innerHTML = d.available
            ? '<button class="btn gold lg block" data-act="dy-spin">' + K.ic("wheel") + "بچرخون</button>" +
              '<p class="note" style="margin-top:10px">هر روز ' + K.n(d.limit) + " چرخش رایگان داری.</p>"
            : '<div class="panel pad center"><b class="dy-block">چرخش امروزت رو زدی</b><span class="sm muted">چرخش بعدی: </span><span class="timer" data-until="' + resetAt + '" data-done="آماده‌ست"></span></div>';
        }
        drawFoot();
        var fired = false, tick = ticker(root, function () { if (fired) return; fired = true; K.after(1200, ctx.reload); });

        K.on(root, "dy-spin", function (el) {
          if (busy || !d.available) return;
          busy = true;
          var btn = foot.querySelector(".btn");
          K.api.post("daily/wheel/spin/", {}, btn).then(function (r) {
            touch("wheel", false);
            if (r.creatures && r.creatures.length) K.invalidate("profile/creatures/");
            var hits = [];
            SEGS.forEach(function (g, i) { if (g[0] === r.kind) hits.push(i); });
            var i = hits.length ? hits[Math.floor(Math.random() * hits.length)] : 0;
            var deg = 360 * 6 - i * STEP + (Math.random() - 0.5) * STEP * 0.6, quick = reduced(), spinMs = quick ? 300 : 5000;
            if (btn) btn.disabled = true;
            wrap.classList.add("spin");
            disc.style.transform = "rotate(" + deg.toFixed(1) + "deg)";
            K.haptic("hit");
            // a few light ticks while it slows down (cleared by the router if the player leaves)
            if (!quick) [350, 750, 1200, 1750, 2400, 3150, 4000].forEach(function (ms) { K.after(ms, function () { K.haptic(); }); });
            setTimeout(function () {
              if (!ctx.alive()) { K.toast("جایزه‌ی گردونه: " + r.label, "ok"); return; }
              wrap.classList.remove("spin"); wrap.classList.add("won");
              var slice = root.querySelector('.dy-slice[data-i="' + i + '"]');
              if (slice) slice.classList.add("hit");
              K.after(quick ? 0 : 550, function () {
                // the spin's own answer says when the next one is — no second request
                d.available = false; resetAt = untilOf(r.reset_in);
                wrap.classList.remove("won"); drawFoot(); tick();
                wheelReveal(r);
              });
            }, spinMs);
          }, function () { busy = false; ctx.reload(); });
        });
      });
    }
  });

  // ───────────────────────── hub tiles ─────────────────────────
  K.hub("more", { id: "missions", title: "مأموریت‌ها", sub: "روزانه، هفتگی و باکس‌ها", icon: "target", color: "var(--accent)", go: "dy_missions", order: 20,
                  badge: function () { return last ? last.boxes_ready : 0; } });
  K.hub("more", { id: "wheel", title: "گردونه", sub: "روزی یه چرخش رایگان", icon: "wheel", color: "var(--gold)", go: "dy_wheel", order: 22,
                  badge: function () { return last && last.wheel ? 1 : 0; } });
})(window.K);
