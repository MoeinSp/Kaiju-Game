/* Events and progression: the monthly festival, the weekly event, the season pass and the
   achievements. Every number comes from /app/api/events/… (game.festival / game.events /
   game.battlepass / game.achievements) — nothing is computed here.
   Every action answers with the new state of its screen, so a screen is fetched once and
   then redrawn in place from the POST response. */
(function (K) {
  "use strict";

  K.addIcons({
    tent: '<path d="M12 6 3.500 20h17zM12 2.500V6M12 3l3.200 1.200L12 5.400M12 20l-2.500-5.500L12 11l2.500 3.500z"/>',
    token: '<circle cx="12" cy="12" r="8.500"/><path d="m12 7.300 1.400 2.900 3.200.4-2.300 2.200.6 3.200L12 14.500 9.100 16l.6-3.200-2.300-2.200 3.200-.4z"/>',
    sliders: '<path d="M4 7h8M16 7h4M4 12h2M10 12h10M4 17h10M18 17h2"/><circle cx="14" cy="7" r="2"/><circle cx="8" cy="12" r="2"/><circle cx="16" cy="17" r="2"/>'
  });

  // ───────────────────────── shared bits ─────────────────────────
  function num(x) { return Number(x || 0).toLocaleString("en-US"); }
  function speedText(r) { return r.speedup_label ? "کارت سرعت " + K.esc(r.speedup_label) : "کارت سرعت (جمعاً " + K.n(r.speedup) + " دقیقه)"; }
  /* a game reward {coins, dna, diamonds, speedup, speedup_label} as inline chips */
  function rewardHtml(r, sep) {
    var parts = [], a = K.amounts(r || {}, sep);
    if (a) parts.push(a);
    if (r && r.speedup) parts.push('<span class="t-accent b">' + K.ic("hourglass") + " " + speedText(r) + "</span>");
    return parts.join(sep || ' <span class="faint">·</span> ') || '<span class="faint">—</span>';
  }
  /* the same reward for K.reward(...) */
  function reveal(r, o) {
    o = o || {}; o.coins = r.coins; o.dna = r.dna; o.diamonds = r.diamonds;
    if (r.speedup) o.extra = [K.ic("hourglass") + '<span style="color:var(--text)">' + speedText(r) + "</span>"];
    return K.reward(o);
  }
  function banner(img, title, subHtml, extra) {
    return '<div class="banner ev-banner"' + (img ? ' style="background-image:url(\'' + img + '&s=l\')"' : "") + '><div class="grow"><div class="ttl">' + K.esc(title) + "</div>" + (subHtml ? '<div class="ev-bsub">' + subHtml + "</div>" : "") + (extra || "") + "</div></div>";
  }
  /* countdown to an ABSOLUTE time, so a redraw long after the fetch still shows the right time */
  function timerHtml(until, done) { return '<span class="timer" data-fmt="long" data-until="' + Number(until) + '" data-done="' + K.esc(done || "تموم شد") + '"></span>'; }
  /* ONE interval per screen: it re-reads the timers on every tick, so redrawing a part of the
     screen never stacks intervals. Returns the tick function (call it after a redraw). */
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
  /* reload once when a countdown of the screen ends */
  function watch(root, ctx) { var fired = false; return ticker(root, function () { if (fired) return; fired = true; K.after(1500, ctx.reload); }); }
  function seg(items, cur, attr) {
    return '<div class="seg">' + items.map(function (s) { return '<button class="' + (s[0] === cur ? "on" : "") + '" data-' + attr + '="' + s[0] + '">' + (s[2] ? K.ic(s[2]) : "") + s[1] + (s[3] ? '<span class="cnt">' + s[3] + "</span>" : "") + "</button>"; }).join("") + "</div>";
  }

  // ───────────────────────── festival ─────────────────────────
  var EARN_ICON = { hunt: "target", arena_attack: "swords", dispatch: "compass", worldboss_hit: "skull", raid_attack: "shield", collect: "building", feed: "food", wheel_spin: "wheel" };
  var EARN_GO = { hunt: "hunt", arena_attack: "arena", dispatch: "dispatch", worldboss_hit: "worldboss", collect: "buildings", feed: "upgrade", wheel_spin: "wheel" };
  var SHOP_ICON = { capsule: "food", speedup: "hourglass", gold: "coin", dna: "dna", golden: "chest", magical: "box", grand: "crown", vip_chest: "gift", vip_mythic: "crystal" };
  var SHOP_TINT = { capsule: "var(--earth)", speedup: "var(--accent)", gold: "var(--coin)", dna: "var(--dna)", golden: "var(--gold)", magical: "var(--epic)", grand: "var(--legendary)", vip_chest: "var(--epic)", vip_mythic: "var(--mythic)" };
  var SHOP_GROUPS = [["res", "منابع", "box"], ["box", "جعبه‌ها", "chest"], ["grand", "جایزه‌ی بزرگ", "crown"], ["vip", "ویژه‌ی اشتراک", "star"]];
  var festTab = "shop";

  function prizeRange(prizes, i) {
    var from = i ? prizes[i - 1].max_rank + 1 : 1, to = prizes[i].max_rank;
    return from === to ? "رتبه‌ی " + K.n(to) : "رتبه‌ی " + K.n(from) + " تا " + K.n(to);
  }
  function prizesPanel(d) {
    return '<div class="h2">' + K.ic("trophy") + 'جایزه‌ی رتبه‌ها</div><div class="panel kv">' + d.prizes.map(function (p, i) {
      return "<div><span>" + prizeRange(d.prizes, i) + "</span><span>" + K.amounts({ diamonds: p.diamonds }) + "</span></div>";
    }).join("") + '</div><p class="note">آخر جشنواره به کسایی که بیشترین سکه رو «جمع کردن» الماس می‌رسه؛ خرج کردن سکه رتبه رو کم نمی‌کنه.</p>';
  }
  /* what a subscription adds — the bot's «ویژه‌ی اشتراک» block */
  function vipPanel(d) {
    var on = d.vip, mark = function (t) { return '<div><span class="ic" style="color:' + (on ? "var(--good)" : "var(--faint)") + '">' + K.ic(on ? "check" : "lock") + '</span><span class="t">' + t + "</span></div>"; };
    return '<div class="h2">' + K.ic("star") + "ویژه‌ی اشتراک" + (on ? "" : ' <span class="faint sm">(اشتراک نداری)</span>') + '</div><div class="panel list ev-vip' + (on ? " on" : "") + '">' +
      mark("سکه‌ی جشنواره <b class=\"num\">+" + Number(d.vip_bonus_pct) + "%</b> از همه‌ی کارها") + mark("یه جعبه‌ی جادویی رایگان در هر جشنواره") +
      mark("هیولای اساطیری کریستال با " + K.n(d.mythic_cost) + " سکه") + "</div>" +
      (on || !K.hasScreen("sh_vip") ? "" : '<button class="btn block mt" data-act="fvip">' + K.ic("crown") + "دیدن اشتراک‌ها</button>");
  }
  /* one source of festival coins: how much one action pays and how many are left today */
  function srcRow(r, icon, what, go) {
    var done = r.left <= 0;
    return "<" + (go && !done ? 'button data-act="fgo" data-go="' + go + '"' : "div") + ' class="ev-src' + (done ? " done" : "") + '"><span class="ic" style="color:' + (done ? "var(--good)" : "var(--gold)") + '">' + K.ic(done ? "check" : icon) + "</span>" +
      '<span class="t"><span class="ev-srcn">' + K.esc(r.label) + "</span><small>" + K.n(r.unit) + " سکه" + (r.cap ? " · " + K.n(r.done) + " از " + K.n(r.cap) : "") + "</small></span>" +
      (done ? '<span class="tag" style="color:var(--good)">' + K.ic("check") + "تکمیل</span>" : '<span class="ev-left"><b class="num">' + num(r.left) + "</b><small>" + what + " مانده</small></span>") +
      "</" + (go && !done ? "button" : "div") + ">";
  }
  function todayPanel(d) {
    var t = d.today;
    return '<div class="panel pad ev-today-sum"><div class="ev-sum3"><div><small>امروز گرفتی</small><b class="t-gold">' + K.n(t.earned) + "</b></div><div><small>امروز مانده</small><b>" + K.n(t.left) + "</b></div><div><small>سقف امروز</small><b>" + K.n(t.max) + "</b></div></div>" +
      K.bar(t.max ? t.earned / t.max : 0, "gold", "thick") + "</div>" +
      '<div class="panel list mt ev-srcs">' + t.rows.map(function (r) { return srcRow(r, EARN_ICON[r.key] || "token", "بار", EARN_GO[r.key]); }).join("") +
      srcRow(t.daily, "calcheck", "مأموریت", "missions") + "</div>" +
      '<div class="h2">' + K.ic("calendar") + 'این هفته</div><div class="panel list ev-srcs">' + srcRow(t.weekly, "flag", "مأموریت", "missions_w") + "</div>" +
      '<div class="callout mt' + (d.vip ? " good" : "") + '">' + K.ic("star") + "<div>" + (d.vip ? "اشتراکت فعاله؛ عددهای بالا با +" + Number(d.vip_bonus_pct) + "% حساب شدن." : "با اشتراک، هر کدوم از این‌ها +" + Number(d.vip_bonus_pct) + "% سکه‌ی بیشتر می‌ده.") + "</div></div>" +
      '<p class="note">هر کار سقف روزانه داره و سقف‌ها هر شب صفر می‌شن.</p>';
  }
  /* the table of sources while no festival is running */
  function earnTable(d) {
    return '<div class="panel list ev-srcs">' + d.earn.map(function (e) {
      return '<div class="ev-src"><span class="ic" style="color:var(--gold)">' + K.ic(EARN_ICON[e.key] || "token") + '</span><span class="t"><span class="ev-srcn">' + K.esc(e.label) + "</span><small>تا " + K.n(e.cap) + " بار در روز</small></span>" +
        '<span class="ev-left"><b class="num t-gold">' + num(e.unit) + "</b><small>سکه</small></span></div>";
    }).join("") + '<div class="ev-src"><span class="ic t-accent">' + K.ic("calcheck") + '</span><span class="t"><span class="ev-srcn">هر مأموریت</span><small>روزانه ' + K.n(d.mission_daily) + " سکه · هفتگی " + K.n(d.mission_weekly) + " سکه</small></span></div></div>" +
      '<p class="note">هر کار سقف روزانه داره (روزی حداکثر ' + num(d.day_max) + " سکه)؛ برای سکه‌ی بیشتر باید به همه‌ی بخش‌های بازی سر بزنی.</p>";
  }
  function shopCard(it, coins) {
    var out = it.left <= 0, poor = !out && !it.locked && coins < it.cost, big = it.group === "grand" || it.group === "vip";
    var tint = SHOP_TINT[it.key] || "var(--accent)", icon = SHOP_ICON[it.key] || "gift";
    var state = out ? '<span class="ev-cover">' + K.ic("check") + (it.limit === 1 ? "گرفتی" : "تموم شد") + "</span>" : it.locked ? '<span class="ev-cover">' + K.ic("lock") + "فقط با اشتراک</span>" : "";
    var btn = out ? '<button class="btn sm block" disabled>' + K.ic("check") + (it.limit === 1 ? "گرفتی" : "تموم شد") + "</button>"
      : '<button class="btn sm block ' + (it.locked || poor ? "" : "gold") + '" data-act="fbuy" data-key="' + K.esc(it.key) + '">' +
        (it.locked ? K.ic("lock") + "اشتراک" : it.cost ? K.ic("token") + '<span class="num">' + num(it.cost) + "</span>" : K.ic("gift") + "رایگان") + "</button>";
    return '<div class="panel ev-card' + (big ? " big" : "") + (out ? " out" : "") + (it.locked ? " locked" : "") + '" style="--tc:' + tint + '">' +
      '<div class="ev-art"' + (it.img ? ' style="background-image:url(\'' + it.img + '\')"' : "") + '><span class="ev-arti">' + K.ic(icon) + "</span>" +
      (it.vip ? '<span class="ev-vipb">' + K.ic("star") + "اشتراک</span>" : "") + state + "</div>" +
      '<div class="ev-cbody"><div class="ev-cn">' + K.esc(it.title) + '</div><div class="xs muted ev-clim">' + (it.limit > 1 ? K.n(it.bought) + " از " + K.n(it.limit) + " خریدی" : "یک بار در هر جشنواره") + "</div>" + btn + "</div></div>";
  }
  function shopPanel(d) {
    var html = "";
    SHOP_GROUPS.forEach(function (g) {
      var items = d.shop.filter(function (it) { return it.group === g[0]; });
      if (!items.length) return;
      html += '<div class="h2">' + K.ic(g[2]) + g[1] + '</div><div class="ev-shop' + (g[0] === "grand" || g[0] === "vip" ? " wide" : "") + '">' + items.map(function (it) { return shopCard(it, d.coins); }).join("") + "</div>";
    });
    return html + '<p class="note">سکه‌ها آخر جشنواره باطل می‌شن؛ قبلش خرجشون کن.</p>';
  }
  function topPanel(d) {
    return (d.top.length ? '<div class="panel ev-lb">' + d.top.map(function (r) {
      return '<div class="' + (r.me ? "me" : "") + '"><span class="rk num">' + r.rank + '</span><span class="nm">' + K.esc(r.name) + (r.me ? ' <span class="faint sm">(تو)</span>' : "") + "</span>" +
        '<span class="ev-topv"><span class="t-gold b">' + K.ic("token") + " " + K.n(r.earned) + "</span>" + (r.prize ? '<small class="t-diamond">' + K.ic("gem") + " " + K.n(r.prize) + "</small>" : "") + "</span></div>";
    }).join("") + "</div>" : '<div class="panel">' + K.state("podium", "هنوز کسی سکه جمع نکرده", "اولین نفر جدول تو باش.") + "</div>") +
      (d.rank && !d.top.some(function (r) { return r.me; }) ? '<div class="panel ev-mine mt"><span class="rk num">' + d.rank + '</span><span class="grow">رتبه‌ی تو</span><span class="t-gold b">' + K.ic("token") + " " + K.n(d.earned) + "</span></div>" : "") + prizesPanel(d);
  }
  function festGo(action) {
    if (action === "missions_w") { K.go("dy_missions", { tab: "w" }); return; }
    if (K.dy && K.dy.go) K.dy.go(action); else if (K.hasScreen(action)) K.go(action);
  }

  K.screen("ev_festival", {
    title: "جشنواره", tab: "more",
    render: function (root, params, ctx) {
      return K.api.get("events/festival/").then(function (d) {
        var now = Date.now() / 1000, th = d.theme, elTag = K.elTag(th.element);
        K.on(root, "fvip", function () { if (K.hasScreen("sh_vip")) K.go("sh_vip"); });
        if (!d.active) {
          root.innerHTML = '<div class="banner ev-fest"' + (d.banner ? ' style="background-image:url(\'' + d.banner + '&s=l\')"' : "") + '><div class="grow"><div class="ev-fkick">' + K.ic("tent") + "جشنواره‌ی بعدی</div>" +
            '<div class="ttl">«' + K.esc(th.title) + '»</div><div class="ev-bsub">' + elTag + (d.seconds_until_next > 0 ? timerHtml(now + d.seconds_until_next, "شروع شد") : "") + "</div></div></div>" +
            '<div class="callout mt">' + K.ic("info") + "<div>هر ماه از روز " + K.n(d.start_day) + " تا " + K.n(d.end_day) + " جشنواره‌ست: از همه‌ی کارهات «سکه‌ی جشنواره» می‌افته و می‌تونی از فروشگاه مخصوصش خرید کنی. سکه‌ها آخر جشنواره باطل می‌شن. جایزه‌ی بزرگ یه هیولای افسانه‌ایِ " + K.esc(th.element_label) + " با " + K.n(d.grand_cost) + " سکه‌ست.</div></div>" +
            '<div class="h2">' + K.ic("token") + "چطور سکه می‌گیری؟</div>" + earnTable(d) + vipPanel(d) + prizesPanel(d);
          watch(root, ctx);
          return;
        }
        var ends = now + d.seconds_left, busy = false;
        root.innerHTML = '<div id="ev-fhead"></div><div id="ev-fseg"></div><div id="ev-fbody"></div>';
        var headEl = root.querySelector("#ev-fhead"), segEl = root.querySelector("#ev-fseg"), body = root.querySelector("#ev-fbody");

        function head() {
          var grand = d.shop.filter(function (it) { return it.key === "grand"; })[0], got = grand && grand.left <= 0;
          var ratio = d.grand_cost ? Math.min(1, d.coins / d.grand_cost) : 0, t = d.today;
          headEl.innerHTML = '<div class="banner ev-fest"' + (d.banner ? ' style="background-image:url(\'' + d.banner + '&s=l\')"' : "") + '><div class="grow"><div class="ev-fkick">' + K.ic("tent") + "جشنواره‌ی ماهانه</div>" +
            '<div class="ttl">«' + K.esc(th.title) + '»</div><div class="ev-bsub">' + elTag + (d.seconds_left > 0 ? timerHtml(ends, "تموم شد") : "") + "</div>" +
            '<div class="ev-grand' + (got ? " got" : "") + '"><div class="ev-grandh"><span>' + K.ic("crown") + "جایزه‌ی بزرگ: هیولای افسانه‌ایِ " + K.esc(th.element_label) + "</span>" +
            (got ? '<b class="t-good">' + K.ic("check") + "گرفتی</b>" : '<b class="num">' + num(d.coins) + " / " + num(d.grand_cost) + "</b>") + "</div>" +
            K.bar(got ? 1 : ratio, got ? "good" : "gold", "thick") + "</div></div></div>" +
            '<div class="ev-stats mt">' +
            '<div class="panel ev-st t-gold"><span class="ic">' + K.ic("token") + "</span><div><small>سکه‌ی تو</small><b>" + K.n(d.coins) + "</b></div></div>" +
            '<div class="panel ev-st t-cup"><span class="ic">' + K.ic("podium") + "</span><div><small>رتبه · جمع‌شده " + num(d.earned) + "</small><b>" + (d.rank ? K.n(d.rank) : '<span class="faint">—</span>') + "</b></div></div>" +
            '<div class="panel ev-st t-accent"><span class="ic">' + K.ic("calendar") + "</span><div><small>سکه‌ی امروز</small><b>" + K.n(t.earned) + '<span class="faint sm num"> / ' + num(t.max) + "</span></b></div></div>" +
            '<div class="panel ev-st t-warn"><span class="ic">' + K.ic("hourglass") + "</span><div><small>مانده‌ی امروز</small><b>" + K.n(t.left) + "</b></div></div></div>";
        }
        function draw() {
          segEl.innerHTML = '<div class="mt"></div>' + seg([["shop", "فروشگاه", "cart"], ["today", "سکه‌ی امروز", "token"], ["top", "جدول", "podium"]], festTab, "ftab");
          body.innerHTML = festTab === "today" ? todayPanel(d) : festTab === "top" ? topPanel(d) : shopPanel(d) + vipPanel(d);
        }
        if (festTab !== "shop" && festTab !== "today" && festTab !== "top") festTab = "shop";
        head(); draw();
        var tick = watch(root, ctx);
        segEl.addEventListener("click", function (ev) { var b = ev.target.closest("[data-ftab]"); if (!b) return; K.haptic(); festTab = b.dataset.ftab; draw(); });
        K.on(root, "fgo", function (el) { festGo(el.dataset.go); });
        K.on(root, "fbuy", function (el) {
          var it = d.shop.filter(function (x) { return x.key === el.dataset.key; })[0]; if (!it || busy) return;
          if (it.locked) { K.haptic("err"); K.toast("این آیتم فقط برای کساییه که اشتراک فعال دارن.", "err", K.hasScreen("sh_vip") ? { label: "اشتراک", run: function () { K.go("sh_vip"); } } : null); return; }
          if (d.coins < it.cost) { K.haptic("err"); K.toast("سکه‌ی جشنواره کافی نداری (" + num(d.coins) + " از " + num(it.cost) + ").", "err"); return; }
          K.confirm({ title: it.cost ? "خرید از جشنواره" : "دریافت هدیه", icon: SHOP_ICON[it.key] || "cart", ok: it.cost ? "بخر" : "بگیر", cancel: "بی‌خیال",
            html: it.cost ? "<p>«" + K.esc(it.title) + "» رو با <b>" + K.n(it.cost) + "</b> سکه‌ی جشنواره می‌خری؟<br>الان " + K.n(d.coins) + " سکه داری.</p>" : "<p>«" + K.esc(it.title) + "» رایگانه و همین الان باز می‌شه.</p>" }).then(function (yes) {
            if (!yes || busy) return;
            busy = true;
            K.api.post("events/festival/buy/", { key: it.key }, el).then(function (r) {
              busy = false;
              if (r.creatures.length) K.invalidate("profile/creatures/");
              if (r.items.length) K.invalidate("profile/equipment/");
              d.coins = r.coins; if (r.shop) d.shop = r.shop;
              if (ctx.alive()) { head(); draw(); tick(); }
              K.reward({ title: "گرفتی!", text: r.got, icon: SHOP_ICON[it.key] || "gift", creatures: r.creatures, items: r.items });
            }, function () { busy = false; ctx.reload(); });
          });
        });
      });
    }
  });

  // ───────────────────────── weekly event ─────────────────────────
  var WEEK_ICON = { double_xp: "up", double_pass: "ticket", bounty: "gift", golden: "star", element_week: "atom", fast_dispatch: "compass", cave_luck: "egg", surprise_week: "spark" };
  function eventTags(e) {
    var t = "";
    if (e.xp_mult > 1) t += K.tag("XP آزمایشگاه ×" + K.n(e.xp_mult), "var(--xp)", "up");
    if (e.pass_mult > 1) t += K.tag("امتیاز پاس ×" + K.n(e.pass_mult), "var(--accent)", "ticket");
    if (e.reward_mult > 1) t += K.tag("جایزه‌ی روزانه ×" + K.n(e.reward_mult), "var(--gold)", "gift");
    if (e.element) t += K.elTag(e.element);
    return t;
  }

  K.screen("ev_week", {
    title: "رویداد هفته", tab: "more",
    render: function (root, params, ctx) {
      return K.api.get("events/week/").then(function (d) {
        var at = Date.now() / 1000, busy = false;
        function draw() {
          var e = d.event, tags = eventTags(e);
          var html = banner(d.banner, e.title, d.seconds_left > 0 ? timerHtml(at + d.seconds_left, "تموم شد") : "") +
            '<div class="panel pad mt ev-rule"><span class="ico-box lg t-accent">' + K.ic(WEEK_ICON[e.key] || "calendar") + '</span><div class="grow"><div class="b">' + (e.rule ? "قانون این هفته" : "بونوس این هفته") + '</div><div class="sm" style="color:#cfd8ee">' + K.esc(e.desc) + "</div>" +
            (tags ? '<div class="ev-tags">' + tags + "</div>" : "") + "</div></div>" +
            '<div class="h2">' + K.ic("gift") + "جایزه‌ی روزانه‌ی رویداد</div>" +
            '<div class="panel pad ' + (d.can_claim ? "gold" : "") + '"><div class="flex between wrapx"><div><div class="sm muted">جایزه‌ی امروز · روز ' + K.n(d.day) + " از " + K.n(d.days.length) + '</div><div class="ev-today">' + rewardHtml(d.today_reward) + "</div></div>" +
            (d.can_claim ? '<button class="btn gold" data-act="wclaim">' + K.ic("gift") + "دریافت</button>" : '<span class="tag" style="color:var(--good)">' + K.ic("check") + "گرفتی</span>") + "</div>" +
            (d.can_claim ? "" : '<div class="sm muted" style="margin-top:8px">جایزه‌ی امروزو گرفتی. فردا دوباره بیا.</div>') + "</div>" +
            '<div class="ev-days">' + d.days.map(function (x) {
              var cls = x.day < d.day ? "past" : x.day === d.day ? (d.can_claim ? "now" : "now got") : "";
              return '<div class="ev-day ' + cls + '"><small>روز ' + K.n(x.day) + '</small><span class="ev-dayic">' + K.ic(x.day === d.day && !d.can_claim ? "check" : x.reward.diamonds ? "gem" : "gift") + "</span>" +
                '<span class="ev-dayrw">' + rewardHtml(x.reward, "<br>") + "</span></div>";
            }).join("") + "</div>" +
            '<p class="note">جایزه در روز ۴ و روز ۷ بزرگ‌تر می‌شه — روز آخر جک‌پات الماس! جایزه‌ی هر روز فقط همون روز قابل دریافته.</p>';
          if (d.upcoming.length) {
            html += '<div class="h2">' + K.ic("calendar") + 'هفته‌های بعد</div><div class="panel list">' + d.upcoming.map(function (u) {
              return '<div><span class="ic t-accent">' + K.ic(WEEK_ICON[u.key] || "calendar") + '</span><span class="t">' + K.esc(u.title) + (u.element_label ? " · " + K.esc(u.element_label) : "") + "<small>" + K.esc(u.desc) + "</small></span>" +
                '<span class="v sm muted">' + K.dur(u.starts_in).split(" و ")[0] + " دیگه</span></div>";
            }).join("") + "</div>";
          }
          root.innerHTML = html;
          var now = root.querySelector(".ev-day.now"); if (now && now.scrollIntoView) { try { now.scrollIntoView({ block: "nearest", inline: "center" }); } catch (e2) {} }
        }
        draw();
        var tick = watch(root, ctx);
        K.on(root, "wclaim", function (el) {
          if (busy) return; busy = true;
          K.api.post("events/week/claim/", {}, el).then(function (r) {
            busy = false; d = r.state; at = Date.now() / 1000;
            if (ctx.alive()) { draw(); tick(); }
            reveal(r.reward, { title: "جایزه‌ی رویداد دریافت شد!" });
          }, function () { busy = false; ctx.reload(); });
        });
      });
    }
  });

  // ───────────────────────── season pass ─────────────────────────
  var passAll = false;
  function passCell(reward, state, kind) {
    var ic = state === "got" ? "check" : state === "ready" ? "gift" : state === "nopass" ? "lock" : "";
    return '<div class="ev-cell ' + kind + " " + state + '">' + (ic ? '<span class="ev-cellic">' + K.ic(ic) + "</span>" : "") + '<span class="ev-cellrw">' + rewardHtml(reward, "<br>") + "</span></div>";
  }

  K.screen("ev_pass", {
    title: "پاس فصلی", tab: "more",
    render: function (root, params, ctx) {
      return K.api.get("events/pass/").then(function (d) {
        var at = Date.now() / 1000, busy = false;
        function track() {
          var done = Math.min(d.free_claimed, d.premium ? d.premium_claimed : d.free_claimed);   // tiers with nothing left to take
          var rows = d.tiers.filter(function (t) { return passAll || t.tier > done; });
          return '<div class="h2">' + K.ic("ticket") + "مسیر جوایز" + (done >= 1 ? '<button class="more" data-act="pall">' + (passAll ? "فقط مانده‌ها" : "همه‌ی مرحله‌ها") + "</button>" : "") + "</div>" +
            '<div class="ev-trackhead"><span>رایگان</span><span></span><span>' + K.ic("crown") + "ویژه</span></div>" +
            (rows.length ? '<div class="ev-track">' + rows.map(function (t) {
              var reached = t.tier <= d.tier;
              var fs = t.tier <= d.free_claimed ? "got" : reached ? "ready" : "far";
              var ps = !d.premium ? "nopass" : t.tier <= d.premium_claimed ? "got" : reached ? "ready" : "far";
              return '<div class="ev-trow' + (reached ? " reached" : "") + (t.tier === d.tier + 1 ? " next" : "") + '">' + passCell(t.free, fs, "free") + '<span class="ev-tnum num">' + t.tier + "</span>" + passCell(t.premium, ps, "prem") + "</div>";
            }).join("") + "</div>" : '<div class="panel">' + K.state("trophy", "همه رو گرفتی", "جایزه‌ی همه‌ی مرحله‌ها دریافت شده.") + "</div>");
        }
        function draw() {
          var maxed = d.tier >= d.max_tier;
          var ready = Math.max(0, d.tier - d.free_claimed) + (d.premium ? Math.max(0, d.tier - d.premium_claimed) : 0);
          root.innerHTML = banner(d.banner, "پاس ماهانه" + (d.month ? " · " + d.month + " " + d.year : ""), (d.seconds_left > 0 ? timerHtml(at + d.seconds_left, "فصل تموم شد") + ' <span class="sm" style="color:#c5cee2">تا ریست</span>' : "")) +
            '<div class="panel pad mt ' + (d.premium ? "gold" : "glow") + '"><div class="flex between"><div class="flex"><span class="ev-tier num">' + d.tier + '</span><div><div class="b">سطح ' + K.n(d.tier) + " از " + K.n(d.max_tier) + '</div><div class="sm muted">' + K.n(d.points) + " امتیاز</div></div></div>" +
            (d.premium ? K.tag("مسیر ویژه", "var(--gold)", "crown") : '<span class="tag plain">مسیر رایگان</span>') + "</div>" +
            '<div style="margin:12px 0 6px">' + K.bar(d.span ? d.into / d.span : 1, d.premium ? "gold" : "", "thick") + "</div>" +
            '<div class="flex between sm muted"><span>' + (maxed ? "به آخرین مرحله‌ی پاس رسیدی!" : "تا سطح " + K.n(d.tier + 1)) + '</span><span class="num">' + num(d.into) + " / " + num(d.span) + "</span></div></div>" +
            (d.has_claimable || !d.premium ? '<div class="btns mt">' + (d.has_claimable ? '<button class="btn gold" data-act="pclaim">' + K.ic("gift") + "دریافت جوایز (" + ready + ")</button>" : "") +
              (d.premium ? "" : '<button class="btn" data-act="pbuy">' + K.ic("crown") + 'پاس ویژه <span class="cost t-diamond">' + K.ic("gem") + K.n(d.premium_cost) + "</span></button>") + "</div>" : "") +
            (!d.has_claimable && d.premium ? '<div class="callout good mt">' + K.ic("check") + "<div>همه‌ی جایزه‌های مرحله‌هایی که رسیدی رو گرفتی.</div></div>" : "") +
            '<div id="ev-track">' + track() + "</div>" +
            '<div class="callout mt">' + K.ic("info") + "<div>کسب امتیاز از طریق: شکار، پیروزی در آرنا، ارتقای ساختمان و ورود روزانه. " + (d.premium ? "" : "پاس ویژه جایزه‌ی ویژه‌ی همه‌ی مرحله‌هایی که رسیدی رو هم باز می‌کنه.") + "</div></div>";
        }
        function update(state) { d = state; at = Date.now() / 1000; if (ctx.alive()) { draw(); tick(); } }
        draw();
        var tick = watch(root, ctx);
        K.on(root, "pall", function () { K.haptic(); passAll = !passAll; root.querySelector("#ev-track").innerHTML = track(); });
        K.on(root, "pclaim", function (el) {
          if (busy) return; busy = true;
          K.api.post("events/pass/claim/", {}, el).then(function (r) {
            busy = false; update(r.state);
            reveal(r.reward, { title: "جوایز پاس دریافت شد!", text: "جایزه‌ی " + r.tiers + " مرحله", icon: "ticket" });
          }, function () { busy = false; ctx.reload(); });
        });
        K.on(root, "pbuy", function (el) {
          var have = K.res ? K.res.diamonds : 0;
          K.confirm({ title: "خرید پاس ویژه", icon: "crown", ok: "تأیید و خرید", cancel: "انصراف",
            html: "<p>هزینه: <b>" + K.n(d.premium_cost) + "</b> الماس · موجودی تو: " + K.n(have) + " الماس<br>جایزه‌ی ویژه‌ی همه‌ی مرحله‌هایی که تا حالا رسیدی هم قابل دریافت می‌شه. پاس ویژه فقط برای همین ماهه.</p>" }).then(function (yes) {
            if (!yes || busy) return;
            busy = true;
            K.api.post("events/pass/premium/", {}, el).then(function (r) { busy = false; K.haptic("ok"); K.toast("پاس ویژه فعال شد!", "ok"); update(r.state); }, function () { busy = false; });
          });
        });
      });
    }
  });

  // ───────────────────────── achievements ─────────────────────────
  var ACH_ICON = { first_raid: "sword", collector_5: "claw", collector_15: "grid", epic_owner: "gem", legendary_owner: "crown", star_3: "star", creature_lv20: "up",
                   lab_10: "flask", lab_25: "vial", hall_max: "hall", all_buildings_max: "building", arena_wins_10: "swords", arena_wins_50: "shield", cup_300: "trophy", streak_7: "flame" };
  var ACH_CAT = { first_raid: "arena", arena_wins_10: "arena", arena_wins_50: "arena", cup_300: "arena",
                  collector_5: "creatures", collector_15: "creatures", epic_owner: "creatures", legendary_owner: "creatures", star_3: "creatures", creature_lv20: "creatures",
                  lab_10: "lab", lab_25: "lab", streak_7: "lab", hall_max: "base", all_buildings_max: "base" };
  var ACH_CATS = [["creatures", "هیولاها", "claw"], ["arena", "آرنا", "swords"], ["lab", "آزمایشگاه", "flask"], ["base", "پایگاه", "hall"], ["other", "سایر", "medal"]];
  var achCat = "";

  K.screen("ev_achievements", {
    title: "دستاوردها", tab: "more",
    render: function (root, params, ctx) {
      return K.api.get("events/achievements/").then(function (d) {
        var busy = false;
        function rank(i) { return i.claimed ? 2 : i.earned ? 0 : 1; }
        function cat(i) { return ACH_CAT[i.key] || "other"; }
        function list() {
          var cats = ACH_CATS.filter(function (c) { return d.items.some(function (i) { return cat(i) === c[0]; }); });
          if (achCat && !cats.some(function (c) { return c[0] === achCat; })) achCat = "";
          var rows = d.items.filter(function (i) { return !achCat || cat(i) === achCat; });
          rows.sort(function (a, b) { return rank(a) - rank(b); });
          return '<div class="row mt">' + '<button class="chip ' + (achCat === "" ? "on" : "") + '" data-cat="">همه</button>' + cats.map(function (c) {
            var n = d.items.filter(function (i) { return cat(i) === c[0] && i.earned && !i.claimed; }).length;
            return '<button class="chip ' + (achCat === c[0] ? "on" : "") + '" data-cat="' + c[0] + '">' + K.ic(c[2]) + c[1] + (n ? ' <span class="ev-dot num">' + n + "</span>" : "") + "</button>";
          }).join("") + "</div>" + (rows.map(function (i) {
            var st = i.claimed ? "got" : i.earned ? "ready" : "";
            return '<div class="panel ev-ach ' + st + '"><span class="ico-box lg">' + K.ic(i.claimed ? "check" : (ACH_ICON[i.key] || "medal")) + "</span>" +
              '<div class="grow"><div class="flex between"><span class="b">' + K.esc(i.title) + "</span>" +
              (i.claimed ? '<span class="tag plain">دریافت‌شده</span>' : i.earned ? '<button class="tag ev-ready" data-act="aclaim">' + K.ic("gift") + "آماده‌ی دریافت</button>" : '<span class="sm muted num">' + num(i.current) + " / " + num(i.target) + "</span>") + "</div>" +
              '<div class="sm muted">' + K.esc(i.desc) + "</div>" + (i.claimed || i.earned ? "" : '<div class="ev-abar">' + K.bar(i.current / Math.max(1, i.target)) + "</div>") +
              '<div class="sm ev-arw">' + K.ic("gift") + " " + rewardHtml(i.reward) + "</div></div></div>";
          }).join("") || K.state("medal", "چیزی اینجا نیست", ""));
        }
        function draw() {
          root.innerHTML = banner(d.banner, "دستاوردها", '<span class="sm" style="color:#c5cee2">هدف‌های بلندمدت؛ با تکمیل هر مرحله، پاداشش رو دریافت کن.</span>') +
            '<div class="panel pad mt"><div class="flex between"><span class="b">' + K.ic("medal") + " " + K.n(d.done) + " از " + K.n(d.total) + ' دریافت‌شده</span><span class="sm muted">' + (d.claimable ? K.n(d.claimable) + " آماده" : "") + "</span></div>" +
            '<div style="margin-top:10px">' + K.bar(d.done / Math.max(1, d.total), "gold", "thick") + "</div>" +
            (d.claimable ? '<button class="btn gold block mt" data-act="aclaim">' + K.ic("gift") + "دریافت همه (" + d.claimable + ")</button>" : "") + "</div>" +
            '<div id="ev-alist">' + list() + "</div>";
        }
        draw();
        root.addEventListener("click", function (ev) { var b = ev.target.closest("[data-cat]"); if (!b || !root.contains(b)) return; K.haptic(); achCat = b.dataset.cat; root.querySelector("#ev-alist").innerHTML = list(); });
        K.on(root, "aclaim", function (el) {
          if (busy) return; busy = true;
          K.api.post("events/achievements/claim/", {}, el.classList.contains("btn") ? el : null).then(function (r) {
            busy = false; d = r.state; if (ctx.alive()) draw();
            reveal(r.reward, { title: r.claimed.length + " دستاورد دریافت شد!", icon: "medal", text: r.claimed.map(function (a) { return a.title; }).join("، ") });
          }, function () { busy = false; ctx.reload(); });
        });
      });
    }
  });

  // ───────────────────────── entry points ─────────────────────────
  K.hub("more", { id: "festival", title: "جشنواره", sub: "سکه‌ی جشنواره و فروشگاه ماهانه", icon: "tent", color: "var(--plasma)", go: "ev_festival", order: 30,
                  badge: function () { var h = K.dy && K.dy.last && K.dy.last(); return h && h.festival ? "live" : 0; } });
  K.hub("more", { id: "week", title: "رویداد هفته", sub: "قانون هفته و جایزه‌ی روزانه", icon: "calendar", color: "var(--accent)", go: "ev_week", order: 32, hall: 3 });
  K.hub("more", { id: "pass", title: "پاس فصلی", sub: "مسیر جوایز ماهانه", icon: "ticket", color: "var(--gold)", go: "ev_pass", order: 34, hall: 2 });
  K.hub("more", { id: "achievements", title: "دستاوردها", sub: "هدف‌های بلندمدت و پاداش", icon: "medal", color: "var(--epic)", go: "ev_achievements", order: 36 });
})(window.K);
