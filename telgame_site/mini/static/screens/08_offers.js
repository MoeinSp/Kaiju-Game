/* «پیشنهاد ویژه»: the owner's special packs (ShopItem.is_offer, set in the bot's pack builder)
   pushed where players see them — a mid-screen card once a day, a bubble with a countdown on the
   home screen, a strip at the top of the shop — plus the right moment: out of energy, short of
   gold, just lost a fight. Buying goes through the shop's own endpoint (shop/store/item/); a
   real-money pack (price_toman) opens an order instead and shows the card (K.paySheet, 61_store).

     K.offers.list(fresh) → Promise<[offer]>     K.offers.top() → the soonest-ending offer or null
     K.offers.open(offer)                          K.offers.bubble() → html for the home screen
     K.offers.maybePopup()                         K.offers.need("energy" | "gold", message)
     K.offers.set(list)    (the shop hands over the list it got with its own payload) */
(function (K) {
  "use strict";
  var cache = null, at = 0, flight = null;
  K.offers = {};
  K.offers.set = function (list) { cache = (list || []).map(stamp); at = Date.now(); };
  // ends_in is relative to the answer: pin it to a clock time once
  function stamp(o) { if (o.ends_in != null && !o.endAt) o.endAt = Date.now() + o.ends_in * 1000; return o; }
  K.offers.list = function (fresh) {
    if (!fresh && cache && Date.now() - at < 60000) return Promise.resolve(cache);
    if (flight) return flight;
    flight = K.api.get("shop/offers/").then(function (d) { flight = null; K.offers.set(d.offers); return cache; },
      function () { flight = null; return cache || []; });
    return flight;
  };
  K.offers.top = function () {
    var now = Date.now();
    return (cache || []).filter(function (o) { return !o.endAt || o.endAt > now; })[0] || null;
  };

  /* ── a live countdown on every [data-oend] (ms) in the page; one ticker for all ── */
  function left(ms) {
    var s = Math.max(0, Math.round((ms - Date.now()) / 1000)), d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
    function p(n) { return (n < 10 ? "0" : "") + n; }
    return d ? d + " روز و " + p(h) + ":" + p(m) : p(h) + ":" + p(m) + ":" + p(s % 60);
  }
  setInterval(function () {
    Array.prototype.forEach.call(document.querySelectorAll("[data-oend]"), function (el) { el.textContent = left(+el.dataset.oend); });
  }, 1000);
  function clock(o) { return o.endAt ? '<span class="num" data-oend="' + o.endAt + '">' + left(o.endAt) + "</span>" : ""; }
  function toman(o, was) {
    return (was && o.was_toman > o.price_toman ? '<s class="num">' + K.n(o.was_toman) + "</s>" : "") +
      '<span class="num">' + K.n(o.price_toman) + '</span> <small>تومان</small>';
  }
  K.offers.price = function (o, was) { return o.price_toman ? toman(o, was) : price(o, was); };
  function price(o, was) {
    if (o.price_toman) return toman(o, was);
    var cur = o.price_diamonds ? "diamond" : "coin", n = o.price_diamonds || o.price_coins, w = o.price_diamonds ? o.was_diamonds : o.was_coins;
    var both = o.price_diamonds && o.price_coins ? ' <span class="of-plus">+ ' + K.ic("coin") + K.n(o.price_coins) + "</span>" : "";
    return (was && w > n ? '<s class="num">' + K.n(w) + "</s>" : "") + K.ic(cur) + '<span class="num">' + K.n(n) + "</span>" + both;
  }

  /* ── the offer card: a modal in the middle of the screen ── */
  var openEl = null;
  K.offers.open = function (o) {
    if (!o || openEl) return;
    var el = openEl = document.createElement("div");
    el.className = "of-modal";
    el.innerHTML = '<div class="of-card" role="dialog" aria-modal="true">' +
      '<div class="of-art"><img src="' + K.asset(o.art) + '" alt="">' + (o.discount ? '<span class="of-off num">-' + o.discount + "%</span>" : "") +
      '<span class="of-kick">' + K.ic("spark") + "پیشنهاد ویژه</span></div>" +
      '<button class="of-x" aria-label="بستن">' + K.ic("close") + "</button>" +
      '<div class="of-body"><b class="of-title">' + K.esc(o.title) + '</b><p class="of-gets">' + K.esc(o.gets) + "</p>" +
      (o.endAt ? '<div class="of-time">' + K.ic("clock") + "تموم می‌شه تا " + clock(o) + "</div>" : "") +
      (o.max ? '<div class="of-lim">فقط ' + K.n(Math.max(0, o.max - o.bought)) + " بار دیگه می‌تونی بخری</div>" : "") +
      '<button class="of-buy">' + price(o, true) + "</button>" +
      '<div class="of-hint">برای خرید دوباره بزن</div></div></div>';
    document.body.appendChild(el);
    requestAnimationFrame(function () { el.classList.add("on"); });
    if (K.sfx) K.sfx("chest");
    setTimeout(function () { el.classList.add("late"); }, 1000);   // the close key shows after a second
    function close() { if (!openEl) return; openEl = null; el.classList.remove("on"); setTimeout(function () { el.remove(); }, 240); }
    el.querySelector(".of-x").onclick = close;
    el.addEventListener("click", function (ev) { if (ev.target === el && el.classList.contains("late")) close(); });
    var buy = el.querySelector(".of-buy"), armed = 0;
    buy.onclick = function () {
      // the first tap arms it (the price is right there on the key), the second one buys
      if (!armed || Date.now() - armed > 4000) { armed = Date.now(); el.classList.add("sure"); K.haptic(); return; }
      if (o.price_toman) {   // real money: an order and the card to pay; granted when the receipt is approved
        K.api.post("shop/paid/order/", { kind: "item", id: o.id }, buy).then(function (p) {
          close(); if (K.paySheet) K.paySheet(p);
        }, function () { el.classList.remove("sure"); armed = 0; });
        return;
      }
      K.api.post("shop/store/item/", { id: o.id }, buy).then(function (r) {
        close();
        if (r.store && r.store.special) K.offers.set(r.store.special);
        K.invalidate("profile/creatures/", "profile/equipment/");
        K.reward({ title: "خرید موفق!", text: r.title, icon: "gift", extra: (r.notes || []).map(function (n) { return K.esc(n); }) });
        if (K.hubRefresh) K.hubRefresh();
      }, function () { el.classList.remove("sure"); armed = 0; });
    };
  };

  /* ── the home-screen bubble ── */
  K.offers.bubble = function () {
    var o = K.offers.top();
    if (!o) return "";
    return '<button class="of-bubble" data-act="offer" aria-label="پیشنهاد ویژه"><span class="of-bimg"><img src="' + K.asset(o.art) + '" alt=""></span>' +
      (o.discount ? '<span class="of-boff num">-' + o.discount + "%</span>" : "") + (o.endAt ? '<span class="of-btime">' + clock(o) + "</span>" : "") + "</button>";
  };

  /* ── once a day, on the home screen, when an offer is on ── */
  function today() { var d = new Date(); return d.getFullYear() + "-" + (d.getMonth() + 1) + "-" + d.getDate(); }
  K.offers.maybePopup = function () {
    var o = K.offers.top(), key = "koffer:" + today();
    if (!o || openEl || K.sheetOpen() || document.querySelector(".rs, .coach")) return;
    try { if (localStorage.getItem(key) === "1") return; localStorage.setItem(key, "1"); } catch (e) { return; }
    K.offers.open(o);
  };

  /* ── the right moment: out of energy / short of gold → a sheet with the way out ── */
  var lastNeed = 0;
  K.offers.need = function (kind, message) {
    if (Date.now() - lastNeed < 45000 || K.sheetOpen()) return false;   // not every refused tap
    lastNeed = Date.now();
    var gold = kind === "gold", o = K.offers.top();
    var box = K.sheet('<div class="grab"></div><div class="of-need"><img class="of-nimg" src="' + K.asset(gold ? "img/gold_4.jpg" : "img/hub_energy.jpg") + '" alt="">' +
      '<div class="pad"><div class="ttl" style="font-size:21px">' + (gold ? "طلا کم داری" : "انرژیت تموم شد") + '</div><p class="lead" style="margin:6px 0 12px">' + K.esc(message || "") + "</p>" +
      '<button class="btn primary lg block" data-go="' + (gold ? "gold" : "energy") + '">' + K.ic(gold ? "coin" : "bolt") + (gold ? "خرید طلا" : "شارژ انرژی") + "</button>" +
      (o ? '<button class="of-mini mt" data-offer>' + '<img src="' + K.asset(o.art) + '" alt=""><span class="grow"><small>پیشنهاد ویژه</small><b>' + K.esc(o.title) + "</b></span>" +
        '<span class="of-mprice">' + price(o, false) + "</span></button>" : "") + "</div></div>");
    box.addEventListener("click", function (ev) {
      var b = ev.target.closest("[data-go], [data-offer]"); if (!b) return;
      K.closeSheet();
      if (b.hasAttribute("data-offer")) K.offers.open(o);
      else if (b.dataset.go === "gold") K.go("sh_shop", { tab: "gold" });
      else K.go("sh_energy");
    });
    return true;
  };

  /* ── just lost: the result screen gets one line pointing at the offer ── */
  var baseResult = K.result;
  if (baseResult) K.result = function (o) {
    var p = baseResult(o), offer = !(o && o.won) && K.offers.top();
    var box = offer && document.querySelector(".rs.lose .rs-box");
    if (box) {
      var b = document.createElement("button");
      b.className = "rs-offer";
      b.innerHTML = '<img src="' + K.asset(offer.art) + '" alt=""><span class="grow"><small>قوی‌تر برگرد</small><b>' + K.esc(offer.title) + "</b></span>" + K.ic("chevron");
      b.onclick = function () { var x = document.querySelector(".rs .rs-btn"); if (x) x.click(); setTimeout(function () { K.offers.open(offer); }, 300); };
      box.appendChild(b);
    }
    return p;
  };

  // the list is fetched once the app is up (quietly; a failure just means no offers)
  setTimeout(function () { if (K.me) K.offers.list().catch(function () {}); }, 2500);
})(window.K);
