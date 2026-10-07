/* Home: the lab card, the active creature and whatever blocks other modules add
   through K.homeSection (daily rewards, live events, …). */
(function (K) {
  "use strict";

  function labCard(me) {
    var lp = me.lab_progress || {}, ratio = Math.max(0, Math.min(1, lp.ratio || 0)), C = 2 * Math.PI * 30, a = me.active;
    var league = me.league || {}, tier = String(league.key || "bronze").split("_")[0];
    return '<button class="panel home-lab" data-act="profile" style="--art:url(\'' + (a ? a.img : "") + '\')">' +
      '<span class="ring"><svg viewBox="0 0 70 70"><defs><linearGradient id="rg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#3ad6ff"/><stop offset="1" stop-color="#8f6bff"/></linearGradient></defs>' +
      '<circle cx="35" cy="35" r="30" fill="none" stroke="rgba(255,255,255,.09)" stroke-width="5"/>' +
      '<circle cx="35" cy="35" r="30" fill="none" stroke="url(#rg)" stroke-width="5" stroke-linecap="round" stroke-dasharray="' + (C * ratio).toFixed(1) + " " + C.toFixed(1) + '"/></svg>' +
      '<span class="lv"><span><b class="num">' + me.lab_level + "</b><small>LEVEL</small></span></span></span>" +
      '<span class="grow"><span class="nm cut">' + K.esc(me.lab_name) + "</span>" +
      '<span class="muted sm" style="display:block">' + (lp.span ? '<span class="num">' + Number(lp.into).toLocaleString("en-US") + " / " + Number(lp.span).toLocaleString("en-US") + " XP</span> تا سطح بعد" : "بالاترین سطح آزمایشگاه") + "</span>" +
      '<span class="home-chips">' +
      (league.name ? '<span class="home-chip lg-' + tier + '">' + K.ic("medal") + K.esc(league.name) + "</span>" : "") +
      (me.subscription ? '<span class="home-chip vip">' + K.ic("crown", "f") + K.esc(me.subscription) + "</span>" : "") +
      "</span></span>" +
      '<span class="faint">' + K.ic("chevron") + "</span></button>";
  }

  function heroCard(a) {
    return '<div class="h2">' + K.ic("claw") + "هیولای فعال</div>" +
      '<button class="panel hero" data-act="creature" data-id="' + a.id + '">' +
      '<div class="art"><img src="' + a.img + '&s=l" alt="" decoding="async"><div class="over"><div><div class="ttl">' + K.esc(a.name) + "</div>" + K.stars(a.star) + "</div>" +
      '<div class="pow"><b>' + K.n(a.power) + "</b><small>قدرت</small></div></div></div>" +
      '<div class="meta">' + K.rarTag(a.rarity) + K.elTag(a.element) + K.tag("سطح " + K.n(a.level)) + "</div>" + K.statGrid(a) + "</button>";
  }

  K.screen("home", {
    tab: "home",
    render: function (root, params, ctx) {
      /* The profile we already have is drawn at once; K.meFast re-reads it in the background
         when it is old and only the two cards that show it are replaced (the blocks other
         modules added keep their own state and are not asked again). */
      function cards(me) {
        var lab = root.querySelector("#home-lab"), hero = root.querySelector("#home-hero");
        if (lab) lab.innerHTML = labCard(me);
        if (hero) hero.innerHTML = me.active ? heroCard(me.active) : "";
      }
      return K.meFast(function (me) { if (ctx.alive()) cards(me); }).then(function (me) {
        root.innerHTML = '<div class="home-top" style="background-image:url(' + K.asset("img/bg_home.jpg") + ')"><img src="' + K.asset("img/logo.png") + '" alt="Kaiju Legends" width="255" height="96"></div>' +
          '<div id="home-lab"></div>' +
          '<div id="home-sections"></div>' +
          '<div id="home-hero"></div>' +
          '<p class="note">نسخه‌ی آزمایشی مینی‌اپ</p>';
        cards(me);
        K.on(root, "profile", function () { K.go("profile"); });
        K.on(root, "creature", function (el) { K.go("creature", { id: +el.dataset.id }); });
        var host = root.querySelector("#home-sections");
        K.homeSections.slice().sort(function (a, b) { return (a.order || 50) - (b.order || 50); }).forEach(function (def) {
          var el = document.createElement("section");
          host.appendChild(el);
          try { Promise.resolve(def.render(el, me)).catch(function () { el.remove(); }); } catch (e) { el.remove(); }
        });
      });
    }
  });
})(window.K);
