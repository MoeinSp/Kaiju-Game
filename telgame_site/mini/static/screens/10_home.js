/* Home: a game «lobby» — the active kaiju on a glowing stage in the middle, round shortcut
   buttons down both sides (daily rewards, missions, wheel, events on one side; shop, chests,
   dungeon, alliance on the other), a strip for whatever is live right now, the story quest as
   a slim banner and a big «نبرد» button. Everything that used to stack down the page (the
   «پاداش امروز» card, the quest card) is one tap away instead. */
(function (K) {
  "use strict";

  function badgeOf(id) { var v = K.badges && K.badges[id]; return v === "live" ? "live" : Number(v) || 0; }
  function tileGo(id) { var t = K.hubTile(id); return t ? t.go : null; }
  function dot(n) { return n === "live" ? '<i class="lb-dot live"></i>' : n ? '<i class="lb-dot num">' + (n > 99 ? "99+" : n) + "</i>" : ""; }

  function plate(me) {
    var lp = me.lab_progress || {}, ratio = Math.max(0, Math.min(1, lp.ratio || 0)), C = 2 * Math.PI * 21;
    var league = me.league || {}, tier = String(league.key || "bronze").split("_")[0];
    return '<button class="lb-plate" data-act="profile">' +
      '<span class="lb-ring"><svg viewBox="0 0 50 50"><defs><linearGradient id="lbrg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#3ad6ff"/><stop offset="1" stop-color="#8f6bff"/></linearGradient></defs>' +
      '<circle cx="25" cy="25" r="21" fill="rgba(8,11,18,.7)" stroke="rgba(255,255,255,.1)" stroke-width="4"/>' +
      '<circle cx="25" cy="25" r="21" fill="none" stroke="url(#lbrg)" stroke-width="4" stroke-linecap="round" stroke-dasharray="' + (C * ratio).toFixed(1) + " " + C.toFixed(1) + '"/></svg>' +
      '<b class="num">' + me.lab_level + "</b></span>" +
      '<span class="lb-who"><span class="nm cut">' + K.esc(me.lab_name) + "</span>" +
      '<span class="lb-tags">' + (league.name ? '<span class="lb-tag lg-' + tier + '">' + K.ic("medal") + K.esc(league.name) + "</span>" : "") +
      (me.subscription ? '<span class="lb-tag vip">' + K.ic("crown", "f") + K.esc(me.subscription) + "</span>" : "") + "</span></span></button>";
  }

  function sideBtn(act, icon, label, color, badge, go) {
    return '<button class="lb-side-btn" data-act="' + act + '"' + (go ? ' data-go="' + go + '"' : "") + ' style="--c:' + color + '">' +
      '<span class="lb-orb">' + K.ic(icon) + dot(badge) + "</span><span class=\"lb-lbl\">" + label + "</span></button>";
  }

  function stage(a) {
    if (!a) return '<div class="lb-kaiju empty"><span class="muted">هنوز هیولای فعال نداری</span></div>';
    return '<button class="lb-kaiju ' + a.rarity + K.shadowCls(a) + '" data-act="creature" data-id="' + a.id + '" style="--elc:var(--' + a.element + ')">' +
      '<span class="lb-pedestal"></span>' +
      '<span class="lb-card"><img src="' + a.img + '&s=l" alt="" decoding="async">' + K.shadowFx(a) + "</span>" +
      '<span class="lb-name"><b class="cut">' + K.esc(a.name) + "</b>" + K.stars(a.star) + "</span>" +
      '<span class="lb-power">' + K.ic("power") + '<b class="num">' + K.n(a.power) + "</b>" + K.elTag(a.element) + "</span></button>";
  }

  function liveStrip(d) {
    var out = [];
    if (d.boss) out.push('<button class="lb-live" style="--c:var(--bad)" data-act="go" data-go="' + (tileGo("boss") || "") + '">' + K.ic("skull") + "غول سرگردان · " + K.n(d.boss.hits_left) + " ضربه</button>");
    if (d.tournament_open) out.push('<button class="lb-live" style="--c:var(--gold)" data-act="go" data-go="' + (tileGo("tournament") || "") + '">' + K.ic("trophy") + "ثبت‌نام جام بازه</button>");
    if (d.festival) out.push('<button class="lb-live" style="--c:var(--accent-2)" data-act="go" data-go="' + (tileGo("festival") || "") + '">' + K.ic("spark") + K.esc(d.festival.title) + "</button>");
    return out.length ? '<div class="lb-strip">' + out.join("") + "</div>" : "";
  }

  /* «دریافت همه»: whenever something is waiting (buildings, finished missions, free boxes, the
     wheel, ready chests …) one golden banner takes all of it — the «امروز» screen's button, one tap from home */
  var CHEST = '<svg viewBox="0 0 32 32" aria-hidden="true"><path d="M4 13h24v13a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z" fill="#a8641c"/><path d="M4 13v-3a6 6 0 0 1 6-6h12a6 6 0 0 1 6 6v3z" fill="#d9892a"/>' +
    '<path d="M4 13h24v3H4z" fill="#ffc53d"/><path d="M9 4.6V28M23 4.6V28" stroke="#ffc53d" stroke-width="2"/><path d="M13.6 11h4.8v8h-4.8z" fill="#ffe08a" stroke="#8a4b00" stroke-width="1.2"/></svg>';
  function collect(d) {
    if (!(d.collectable > 0)) return "";
    return '<button class="lb-collect" data-act="collect"><span class="lb-c-ic">' + CHEST + '</span><span class="grow"><b>دریافت همه</b>' +
      "<small>" + K.n(d.collectable) + ' جایزه منتظرته</small></span><span class="lb-c-go">بگیر</span></button>';
  }

  function quest(q) {
    if (!q) return "";
    var ratio = q.target ? Math.min(1, q.cur / q.target) : 0;
    return '<button class="lb-quest' + (q.done ? " done" : "") + '" data-act="quest">' +
      '<span class="lb-q-ic">' + K.ic(q.done ? "gift" : "flag") + "</span>" +
      '<span class="grow"><span class="xs muted cut" style="display:block">' + (q.done ? "پاداشت آماده‌ست!" : "قدم بعدی · " + K.esc(q.chapter_name)) + "</span>" +
      '<b class="cut" style="display:block">' + K.esc(q.title) + "</b>" + K.bar(ratio, q.done ? "good" : "") + "</span>" +
      '<span class="lb-q-go">' + (q.done ? "دریافت" : K.ic("chevron")) + "</span></button>";
  }

  /* the season (or the running festival) as a small chip across from the player's plate */
  function seasonChip(d) {
    if (d.festival) return '<button class="lb-season fest" data-act="go" data-go="' + (tileGo("festival") || "") + '">' + K.ic("spark") + K.esc(d.festival.title) + "</button>";
    return K.season ? '<span class="lb-season s-' + K.season.key + '"><i></i>' + K.season.label + "</span>" : "";
  }

  function lobby(me, d) {
    d = d || {};
    var left = [
      sideBtn("today", "gift", "پاداش امروز", "var(--gold)", d.collectable ? d.collectable : (d.waiting ? "live" : 0)),
      sideBtn("go", "target", "مأموریت‌ها", "var(--good)", badgeOf("missions"), tileGo("missions")),
      sideBtn("go", "wheel", "گردونه", "var(--accent-2)", d.wheel ? "live" : badgeOf("wheel"), tileGo("wheel")),
      sideBtn("go", "calendar", "رویدادها", "var(--accent)", badgeOf("week") || badgeOf("festival"), tileGo(d.festival ? "festival" : "week")),
    ];
    var right = [
      sideBtn("go", "cart", "فروشگاه", "var(--coin)", badgeOf("shop") || badgeOf("boxes") || (d.free_boxes || 0), tileGo("shop")),
      sideBtn("go", "chest", "جعبه‌ها", "var(--gold)", badgeOf("chests"), tileGo("chests")),
      sideBtn("go", "skull", "سیاه‌چال", "var(--mythic)", badgeOf("dungeon"), tileGo("dungeon")),
      sideBtn("go", "users", "اتحاد", "var(--rare)", badgeOf("alliance"), tileGo("alliance")),
    ];
    return '<div class="lobby">' +
      '<div class="lb-bg" style="background-image:url(' + K.asset("img/bg_home.jpg") + ')"><i class="lb-fog"></i><i class="lb-motes"></i>' + (K.seasonFx ? K.seasonFx() : "") + "</div>" +
      // across from the plate: the season chip and the «پیشنهاد ویژه» bubble with its countdown (08_offers.js)
      '<div class="lb-top">' + plate(me) + '<div class="lb-tl">' + seasonChip(d) + (K.offers ? K.offers.bubble() : "") + "</div></div>" +
      liveStrip(d) +
      '<div class="lb-stage"><div class="lb-side">' + left.join("") + "</div>" + stage(me.active) + '<div class="lb-side">' + right.join("") + "</div></div>" +
      collect(d) + quest(d.quest) +
      '<div class="lb-cta"><button class="lb-fight" data-act="battle">' + K.ic("swords") + "<span>نبرد</span></button>" +
      '<button class="lb-hunt" data-act="go" data-go="' + (tileGo("hunt") || "") + '">' + K.ic("target") + "<span>شکار</span></button></div>" +
      "</div>";
  }

  K.screen("home", {
    tab: "home",
    render: function (root, params, ctx) {
      var me0 = null, daily = null;
      /* The lobby is drawn once the home summary is here (or after a short wait), and re-drawn only
         when its HTML really changed: every re-draw rebuilt the whole scene (pictures, particles) and
         the parts that arrived late (banner, bubble) pushed the screen down — it «jumped». */
      var shown = "", ready = false, painted, firstDraw = new Promise(function (ok) { painted = ok; });
      function draw() {
        if (!me0 || !ctx.alive() || !ready) return;
        var html = lobby(me0, daily);
        if (html === shown) return;
        shown = html; root.innerHTML = html; painted();   // the screen shows its skeleton until this first draw
        if (K.coach) setTimeout(function () {
          if (ctx.alive()) K.coach(root.querySelector(".lb-collect"), "همه‌ی جایزه‌هات رو با یه لمس بگیر", "lb_collect");
        }, 900);
      }
      setTimeout(function () { ready = true; draw(); }, 700);   // a slow summary must not leave the screen empty
      (K.dy && K.dy.home ? K.dy.home() : K.api.get("daily/home/")).then(function (d) {
        daily = d; ready = true; if (K.setFestival) K.setFestival(d.festival && d.festival.element); draw();
        // the offers come after the summary: the bubble is slotted in place (no re-draw), and once a day the card
        if (K.offers) K.offers.list().then(function () {
          if (!ctx.alive()) return;
          var slot = root.querySelector(".lb-tl");
          if (slot && !slot.querySelector(".of-bubble")) { slot.insertAdjacentHTML("beforeend", K.offers.bubble()); shown = lobby(me0, daily); }
          setTimeout(function () { if (ctx.alive()) K.offers.maybePopup(); }, 1400);
        });
      }).catch(function () { ready = true; draw(); });
      return K.meFast(function (me) { me0 = me; draw(); }).then(function (me) {
        me0 = me; draw();
        K.on(root, "profile", function () { K.go("profile"); });
        K.on(root, "creature", function (el) { K.go("creature", { id: +el.dataset.id }); });
        K.on(root, "today", function () { K.go("dy_today"); });
        K.on(root, "quest", function () { K.go("dy_story"); });
        K.on(root, "battle", function () { K.tab("battle"); });
        K.on(root, "offer", function () { if (K.offers) K.offers.open(K.offers.top()); });
        var collecting = false;
        K.on(root, "collect", function (el) {
          if (collecting || !K.dy || !K.dy.collectAll) return; collecting = true;
          K.dy.collectAll(el).then(function () { return K.dy.home(); }).then(function (d) { collecting = false; daily = d; draw(); },
            function () { collecting = false; });
        });
        K.on(root, "go", function (el) { if (el.dataset.go) K.go(el.dataset.go); });
        return firstDraw;
      });
    }
  });
})(window.K);
