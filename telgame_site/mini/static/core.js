/* Kaiju Legends Mini App — the shared front-end layer (global `K`).

   A SCREEN MODULE is one file in static/screens/<name>.js (plus optional <name>.css). It is
   loaded automatically; it only talks to `K`:

     (function (K) {
       K.addIcons({ myicon: '<path d="..."/>' });            // optional, 24×24 stroke icons

       K.hub("battle", {                                       // a tile in a hub menu
         id: "hunt", title: "شکار", sub: "طلا و DNA", icon: "target", color: "var(--fire)",
         go: "hunt", order: 10,
         badge: function (me) { return 0; }                    // optional number/"live" on the tile
       });

       K.screen("hunt", {
         title: "شکار",                                        // shown in the top bar
         tab: "battle",                                        // which bottom tab stays lit
         render: function (root, params, ctx) {                // root = empty <main>; return a Promise or nothing
           return K.api.get("hunt/").then(function (d) {
             root.innerHTML = "...";                           // build with K.* helpers
             K.on(root, "attack", function (el) {              // <button data-act="attack" data-id="3">
               K.api.post("hunt/attack/", { id: +el.dataset.id }, el).then(function (r) { ctx.reload(); });
             });
           });
         }
       });
     })(window.K);

   HUBS (bottom tabs): "home" (screen "home"), "creatures", "battle", "base", "more".
   Rules: NO emoji — use K.ic(name). Always escape game/player text with K.esc(). Numbers
   through K.n(). All requests through K.api (it adds auth, updates the top bar from
   `res`, and shows the server's error text as a toast). Persian UI text, informal tone.

   SPEED / ROBUSTNESS — what the core does for you, and what to prefer:
     * K.meFast(onChange) instead of K.refreshMe() at the top of a screen: it answers from memory
       and re-reads the profile in the background (K.refreshMe() waits for the network).
     * ctx.reload() re-draws IN PLACE: the old content stays (untouchable) until the new one is
       ready and the scroll position is kept. «back» returns to where the player was.
     * K.api.post ignores a second identical POST while the first is running (double tap);
       K.api.get retries a dropped connection once and gives up after 15 s; K.api.cached shares
       one request between simultaneous callers.
     * K.on(root, "act", fn) keeps ONE handler per action per root — binding again replaces it.
     * A K.every poll or a late callback must check ctx.alive() before touching the page.
     * Countdowns to a server timestamp use K.now() (server clock), not Date.now().
     * K.grid(list, fn, size, memo) remembers how far a list was opened; K.asset("img/x.jpg") gives
       a cache-for-ever URL of a static picture; K.hubRefresh() re-draws the open hub when your
       badge data arrives; a tile's `hall` may be a function(me) and `open(me)` exempts a player.   */

window.K = (function () {
  "use strict";
  var K = {};

  // ───────────────────────── icons ─────────────────────────
  var ICONS = {
    coin: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5v9M14.5 9.6c-.5-.9-1.400-1.400-2.600-1.400-1.500 0-2.500.8-2.500 1.900 0 1.200.9 1.700 2.600 1.900 1.800.2 2.700.8 2.700 2s-1.100 1.900-2.700 1.900c-1.300 0-2.300-.6-2.800-1.500"/>',
    dna: '<path d="M7 3c0 5 10 5 10 9s-10 4-10 9M17 3c0 5-10 5-10 9s10 4 10 9M8.600 6.500h6.800M8.600 17.500h6.800M10 12h4"/>',
    gem: '<path d="M6.500 4h11L21 9 12 20 3 9z"/><path d="M3 9h18M9 4 7.500 9 12 20l4.500-11L15 4"/>',
    bolt: '<path d="M13.500 2.500 5 13.500h6l-1 8 8.500-11h-6z"/>',
    trophy: '<path d="M8 4h8v5a4 4 0 0 1-8 0zM8 5.500H5a3 3 0 0 0 3 4.300M16 5.500h3a3 3 0 0 1-3 4.300M12 13v4M8.500 20.500h7M10 17h4l.6 3.500H9.400z"/>',
    heart: '<path d="M12 20s-7.500-4.600-7.500-10A4.300 4.300 0 0 1 12 7.300 4.300 4.300 0 0 1 19.500 10c0 5.400-7.500 10-7.500 10z"/>',
    sword: '<path d="M19.500 3.500 9 14M19.500 3.500l-4.500.6-8.200 8.200L9 14.500l1.700 2.200 8.200-8.200zM6 13l5 5M4 20l3.500-3.500"/>',
    swords: '<path d="M4 4l9 9M4 4l.5 4L13 16.500l1.500-1.500L16.500 13 8 4.500zM20 4l-9 9M20 4l-4 .5-2.500 2.500M4 20l3-3M20 20l-3-3M15.500 14.500l2.500 2.500M8.500 14.500 6 17"/>',
    shield: '<path d="M12 3 5 5.800v5.700c0 4.500 3 7.900 7 9.500 4-1.600 7-5 7-9.500V5.800z"/>',
    shieldcheck: '<path d="M12 3 5 5.800v5.700c0 4.500 3 7.900 7 9.500 4-1.600 7-5 7-9.500V5.800z"/><path d="m9.200 12 2 2 3.600-4"/>',
    wind: '<path d="M3 9h10.500a2.500 2.500 0 1 0-2.500-2.500M3 13h15a2.500 2.500 0 1 1-2.500 2.500M3 17h7.500a2 2 0 1 1-2 2"/>',
    power: '<path d="m6 13 6-6 6 6M6 18.500l6-6 6 6"/>',
    flask: '<path d="M9.500 3h5M10.500 3v6L5 18.500A1.700 1.700 0 0 0 6.500 21h11a1.700 1.700 0 0 0 1.500-2.500L13.500 9V3M7.800 14.500h8.400"/>',
    home: '<path d="M4 11 12 4l8 7v8.500a.5.500 0 0 1-.5.500H15v-6H9v6H4.500a.5.500 0 0 1-.5-.5z"/>',
    claw: '<path d="M6 4c1.500 4.500 1.500 9.500-1 15M12 3c1.500 5 1.500 11-.5 17M18 4c1.200 4.500 1 9.500-1.500 15"/>',
    chest: '<path d="M4 10.500a4 4 0 0 1 4-4h8a4 4 0 0 1 4 4V19H4zM4 12.500h16M10 12.500V15h4v-2.500"/>',
    podium: '<path d="M9 21V8h6v13M3 21v-8h6M15 13h6v8M2.500 21h19"/>',
    star: '<path d="m12 3.200 2.700 5.500 6.100.9-4.400 4.300 1 6.100L12 17.100 6.600 20l1-6.100L3.200 9.600l6.100-.9z"/>',
    search: '<circle cx="10.800" cy="10.800" r="6.300"/><path d="m20 20-4.600-4.600"/>',
    close: '<path d="M6 6l12 12M18 6 6 18"/>',
    check: '<path d="m5 12.500 4.500 4.500L19 7.500"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    minus: '<path d="M5 12h14"/>',
    back: '<path d="M9 5l7 7-7 7"/>',
    chevron: '<path d="M15 5l-7 7 7 7"/>',
    up: '<path d="M12 19V5M5.500 11.500 12 5l6.500 6.500"/>',
    refresh: '<path d="M20 11a8 8 0 0 0-14.500-4.500L4 8M4 4v4h4M4 13a8 8 0 0 0 14.500 4.500L20 16M20 20v-4h-4"/>',
    clock: '<circle cx="12" cy="12" r="8.500"/><path d="M12 7.500V12l3 2"/>',
    hourglass: '<path d="M7 3.500h10M7 20.500h10M8 3.500c0 5 8 5 8 8.500s-8 3.500-8 8.500M16 3.500c0 5-8 5-8 8.500s8 3.500 8 8.500"/>',
    flame: '<path d="M12 21a6.300 6.300 0 0 0 6.300-6.300c0-3-1.800-4.700-3-6.700-.9-1.500-1.200-3-1.200-5-3 1.500-4.800 4-4.800 6.700 0 .9.200 1.700.5 2.400-1.300-.3-2.300-1.500-2.300-3.100-1.200 1.500-1.800 3.200-1.800 5.700A6.300 6.300 0 0 0 12 21z"/>',
    drop: '<path d="M12 3.200s6.300 6.400 6.300 11.300a6.300 6.300 0 0 1-12.600 0C5.700 9.600 12 3.200 12 3.200z"/>',
    mountain: '<path d="m3 19.500 6.500-12 3.500 6 2.500-3.500 5.500 9.500zM7.800 10.700l1.700 1.500 1.700-1.500"/>',
    crystal: '<path d="M12 2.500 17.500 9 12 21.500 6.500 9zM6.500 9h11M12 2.500v19"/>',
    atom: '<circle cx="12" cy="12" r="1.600"/><ellipse cx="12" cy="12" rx="9.500" ry="3.800"/><ellipse cx="12" cy="12" rx="9.500" ry="3.800" transform="rotate(60 12 12)"/><ellipse cx="12" cy="12" rx="9.500" ry="3.800" transform="rotate(120 12 12)"/>',
    ring: '<circle cx="12" cy="14.500" r="6"/><path d="m9.500 4 2.500-1.500L14.500 4 12 8.500z"/>',
    vial: '<path d="M9 3h6M10 3v5.500L6.500 17a3 3 0 0 0 2.800 4h5.400a3 3 0 0 0 2.800-4L14 8.500V3M8.300 13.500h7.400"/>',
    tower: '<path d="M7 21V8L5.500 6.500v-3h3V5h2V3.500h3V5h2V3.500h3v3L17 8v13zM10.500 21v-4.500a1.500 1.500 0 0 1 3 0V21M4 21h16"/>',
    hall: '<path d="M3.500 9.500 12 4l8.500 5.500M5 9.500V19M9.700 9.500V19M14.300 9.500V19M19 9.500V19M3 19.500h18"/>',
    building: '<path d="M5 21V6l7-3 7 3v15M3 21h18M9 9h1.500M13.500 9H15M9 13h1.500M13.500 13H15M10.500 21v-4h3v4"/>',
    hammer: '<path d="M14 4l6 6-2.500 2.500-6-6zM12.500 8.500 4 17l3 3 8.500-8.500M9.500 3.500 14 4"/>',
    calendar: '<path d="M4 6.500h16v13H4zM4 10.500h16M8 4v4M16 4v4"/>',
    calcheck: '<path d="M4 6.500h16v13H4zM4 10.500h16M8 4v4M16 4v4M9.500 15.500l1.800 1.800 3.400-3.600"/>',
    users: '<circle cx="9" cy="8.500" r="3.300"/><path d="M2.800 19.500a6.200 6.200 0 0 1 12.400 0M16 5.500a3.300 3.300 0 0 1 0 6.200M18 14.300a6 6 0 0 1 3.300 5.200"/>',
    user: '<circle cx="12" cy="8.500" r="3.800"/><path d="M4.500 20a7.500 7.500 0 0 1 15 0"/>',
    medal: '<circle cx="12" cy="14.500" r="5.500"/><path d="m8.500 10.500-3-7h4L12 7l2.500-3.500h4l-3 7"/>',
    crown: '<path d="m3.500 8 4 4L12 5l4.500 7 4-4-1.800 10.500H5.300z"/>',
    wifi: '<path d="M3 9.500a13 13 0 0 1 18 0M6 13a8.700 8.700 0 0 1 12 0M9 16.300a4.300 4.300 0 0 1 6 0"/><circle cx="12" cy="19.300" r=".8"/>',
    lock: '<path d="M6.500 11h11v9h-11zM8.500 11V8a3.500 3.500 0 0 1 7 0v3"/>',
    gift: '<path d="M4 11h16v9.500H4zM3 7.500h18V11H3zM12 7.500v13M12 7.500c-1-3-5.500-3.500-5.500-1 0 1 1 1 5.500 1zM12 7.500c1-3 5.500-3.500 5.500-1 0 1-1 1-5.500 1z"/>',
    box: '<path d="m3.500 7.500 8.500-4 8.500 4v9l-8.500 4-8.500-4zM3.500 7.500l8.500 4 8.500-4M12 11.500v9"/>',
    egg: '<path d="M12 3c3.500 0 6.500 5.500 6.500 10a6.500 6.500 0 0 1-13 0C5.500 8.500 8.500 3 12 3z"/>',
    target: '<circle cx="12" cy="12" r="8.500"/><circle cx="12" cy="12" r="4.500"/><circle cx="12" cy="12" r=".9"/>',
    skull: '<path d="M5 11a7 7 0 0 1 14 0c0 2.500-1 4-2.500 5v3.500h-9V16C6 15 5 13.500 5 11zM10 19.500v-2M14 19.500v-2"/><circle cx="9.300" cy="11.500" r="1.400"/><circle cx="14.700" cy="11.500" r="1.400"/>',
    compass: '<circle cx="12" cy="12" r="8.500"/><path d="m15.500 8.500-2 5-5 2 2-5z"/>',
    flag: '<path d="M5 21V4M5 4.500c4-2 6 2 10 0v9c-4 2-6-2-10 0"/>',
    cart: '<path d="M3 4h2.500l2.200 11h10.600L20 7H7M9.500 19.500a1 1 0 1 0 0-.1M17 19.500a1 1 0 1 0 0-.1"/>',
    ticket: '<path d="M3.500 8a2 2 0 0 1 2-2h13a2 2 0 0 1 2 2v2a2 2 0 0 0 0 4v2a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2v-2a2 2 0 0 0 0-4zM14 6v12"/>',
    wheel: '<circle cx="12" cy="12" r="8.500"/><circle cx="12" cy="12" r="2"/><path d="M12 3.500V10M12 14v6.500M3.500 12H10M14 12h6.500M6 6l4.600 4.600M13.400 13.400 18 18M18 6l-4.600 4.600M10.600 13.400 6 18"/>',
    info: '<circle cx="12" cy="12" r="8.500"/><path d="M12 11v5.500M12 7.800v.2"/>',
    warn: '<path d="M12 4 2.800 19.500h18.400zM12 10v4.500M12 17.200v.2"/>',
    list: '<path d="M8.500 6.500h11M8.500 12h11M8.500 17.500h11M4.500 6.500h.1M4.500 12h.1M4.500 17.500h.1"/>',
    grid: '<path d="M4 4h6.500v6.500H4zM13.500 4H20v6.500h-6.500zM4 13.500h6.500V20H4zM13.500 13.500H20V20h-6.500z"/>',
    more: '<circle cx="5.500" cy="12" r="1.300"/><circle cx="12" cy="12" r="1.300"/><circle cx="18.500" cy="12" r="1.300"/>',
    swap: '<path d="M7 4v15M7 19l-3.500-3.500M7 19l3.500-3.500M17 20V5M17 5l-3.500 3.500M17 5l3.500 3.500"/>',
    food: '<path d="M6 3.500v7a3 3 0 0 0 3 3V21M6 3.500V9M9 3.500V9M12 3.500V10.500a3 3 0 0 1-3 3M17.500 21v-8M17.500 13c-2 0-3-2-3-5s1.500-4.500 3-4.500z"/>',
    send: '<path d="M20.500 3.500 3.500 10.500l6.500 3 3 6.500zM10 13.500l4.500-4.500"/>',
    map: '<path d="m3.500 6 5.500-2 6 2 5.500-2v14L15 20l-6-2-5.500 2zM9 4v14M15 6v14"/>',
    globe: '<circle cx="12" cy="12" r="8.500"/><path d="M3.500 12h17M12 3.500c2.500 2.500 3.800 5.300 3.800 8.500s-1.300 6-3.800 8.500c-2.500-2.500-3.800-5.300-3.800-8.500s1.300-6 3.800-8.500z"/>',
    spark: '<path d="M12 3v5M12 16v5M3 12h5M16 12h5M6 6l3 3M15 15l3 3M18 6l-3 3M9 15l-3 3"/>',
    doc: '<path d="M6 3.500h8l4 4v13H6zM14 3.500v4h4M9 12h6M9 15.500h6"/>',
    bell: '<path d="M6 16.500V11a6 6 0 0 1 12 0v5.500l1.500 2h-15zM10 20.500a2 2 0 0 0 4 0"/>',
    play: '<path d="M7.500 4.500v15l12-7.500z"/>'
  };
  K.icons = ICONS;
  K.addIcons = function (map) { for (var k in map) ICONS[k] = map[k]; };
  K.ic = function (name, cls) { return '<svg class="i ' + (cls || "") + '" viewBox="0 0 24 24" aria-hidden="true">' + (ICONS[name] || ICONS.info) + "</svg>"; };
  K.EL_ICON = { fire: "flame", water: "drop", earth: "mountain", electric: "bolt", crystal: "crystal", plasma: "atom" };
  K.SLOT_ICON = { weapon: "sword", armor: "shield", rune: "ring", offhand: "vial" };

  // ───────────────────────── formatting ─────────────────────────
  K.esc = function (s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); };
  K.n = function (x) { return '<span class="num">' + Number(x || 0).toLocaleString("en-US") + "</span>"; };
  K.short = function (x) { x = Number(x || 0); return '<span class="num">' + (x >= 1e9 ? (x / 1e9).toFixed(1) + "B" : x >= 1e6 ? (x / 1e6).toFixed(1) + "M" : x >= 1e5 ? Math.round(x / 1e3) + "K" : x.toLocaleString("en-US")) + "</span>"; };
  K.pct = function (x) { return '<span class="num">' + Math.round(Number(x || 0) * 100) + "%</span>"; };
  /* seconds → «۲ ساعت و ۵ دقیقه» / «۴۵ ثانیه» */
  K.dur = function (sec) {
    sec = Math.max(0, Math.round(sec || 0));
    var d = Math.floor(sec / 86400), h = Math.floor(sec % 86400 / 3600), m = Math.floor(sec % 3600 / 60), s = sec % 60;
    if (d) return d + " روز" + (h ? " و " + h + " ساعت" : "");
    if (h) return h + " ساعت" + (m ? " و " + m + " دقیقه" : "");
    if (m) return m + " دقیقه" + (m < 5 && s ? " و " + s + " ثانیه" : "");
    return s + " ثانیه";
  };
  /* compact clock «01:23:45» */
  K.clock = function (sec) {
    sec = Math.max(0, Math.round(sec || 0));
    var h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60), s = sec % 60, p = function (v) { return (v < 10 ? "0" : "") + v; };
    return (h ? p(h) + ":" : "") + p(m) + ":" + p(s);
  };
  K.stars = function (k) { var s = '<span class="stars">'; for (var i = 1; i <= 5; i++) s += '<svg class="i f' + (i <= k ? " on" : "") + '" viewBox="0 0 24 24">' + ICONS.star + "</svg>"; return s + "</span>"; };
  K.rarLabel = function (r) { var m = K.meta && K.meta.rarities[r]; return m ? m.label : r; };
  K.elLabel = function (e) { var m = K.meta && K.meta.elements[e]; return m ? m.label : e; };
  K.slotLabel = function (s) { var m = K.meta && K.meta.slots[s]; return m ? m.label : s; };
  K.rarTag = function (r) { return '<span class="tag c-' + r + '">' + K.ic("gem") + K.esc(K.rarLabel(r)) + "</span>"; };
  K.elTag = function (e) { return '<span class="tag e-' + e + '">' + K.ic(K.EL_ICON[e] || "atom") + K.esc(K.elLabel(e)) + "</span>"; };
  K.tag = function (html, color, icon) { return '<span class="tag' + (color ? '" style="color:' + color + '"' : ' plain"') + ">" + (icon ? K.ic(icon) : "") + html + "</span>"; };
  /* amounts of the three currencies as coloured inline chips: K.amounts({coins:10,dna:2,diamonds:1,xp:5}) */
  K.amounts = function (o, sep) {
    var out = [];
    if (o.coins) out.push('<span class="t-coin b">' + K.ic("coin") + " " + K.n(o.coins) + "</span>");
    if (o.dna) out.push('<span class="t-dna b">' + K.ic("dna") + " " + K.n(o.dna) + "</span>");
    if (o.diamonds) out.push('<span class="t-diamond b">' + K.ic("gem") + " " + K.n(o.diamonds) + "</span>");
    if (o.energy) out.push('<span class="t-energy b">' + K.ic("bolt", "f") + " " + K.n(o.energy) + "</span>");
    if (o.xp) out.push('<span class="t-xp b">' + K.ic("up") + " " + K.n(o.xp) + " XP</span>");
    if (o.cup) out.push('<span class="t-cup b">' + K.ic("trophy") + " " + (o.cup > 0 ? "+" : "") + Number(o.cup) + "</span>");
    return out.join(sep || ' <span class="faint">·</span> ');
  };

  // ───────────────────────── Telegram shell ─────────────────────────
  var tg = window.Telegram && window.Telegram.WebApp;
  K.tg = tg;
  try { if (tg) { tg.ready(); tg.expand(); tg.setHeaderColor("#080b12"); tg.setBackgroundColor("#080b12"); if (tg.disableVerticalSwipes) tg.disableVerticalSwipes(); } } catch (e) {}
  K.haptic = function (kind) {
    try {
      if (!tg || !tg.HapticFeedback) return;
      if (kind === "ok") tg.HapticFeedback.notificationOccurred("success");
      else if (kind === "err") tg.HapticFeedback.notificationOccurred("error");
      else if (kind === "hit") tg.HapticFeedback.impactOccurred("medium");
      else tg.HapticFeedback.selectionChanged();
    } catch (e) {}
  };
  var initData = tg ? (tg.initData || "") : "";
  var devUser = new URLSearchParams(location.search).get("dev_user");

  // ───────────────────────── API ─────────────────────────
  K.cache = {};
  var inflight = {}, posting = {}, postSeq = 0;
  var GET_TIMEOUT = 15000, POST_TIMEOUT = 30000;
  /* Server clock − phone clock, in seconds (from the HTTP Date header). Countdowns to a server
     timestamp use K.now(), so a phone with a wrong clock still shows the right time left. */
  K.skew = 0;
  K.now = function () { return Date.now() / 1000 + K.skew; };
  function noteDate(header) {
    var t = header ? Date.parse(header) : NaN; if (isNaN(t)) return;
    var s = t / 1000 - Date.now() / 1000;
    K.skew = Math.abs(s) < 5 ? 0 : s;   // the header has 1 s resolution and arrives a little late: ignore small differences
  }
  /* One HTTP exchange → {r: Response, j: parsed body}. Rejects with err.network = true when the
     connection failed, the body was cut off, or nothing came back in `ms` (err.timeout = true). */
  function send(url, opts, ms) {
    var ctl = window.AbortController ? new AbortController() : null, timer = null, timedOut = false;
    if (ctl) { opts.signal = ctl.signal; timer = setTimeout(function () { timedOut = true; ctl.abort(); }, ms); }
    function fail() { clearTimeout(timer); var err = new Error("network"); err.network = true; err.timeout = timedOut; throw err; }
    return fetch(url, opts).then(function (r) {
      noteDate(r.headers.get("Date"));
      return r.json().then(function (j) { clearTimeout(timer); return { r: r, j: j || {} }; }, function () {
        if (timedOut || r.ok) fail();            // a 200 whose JSON did not arrive whole is a network failure
        clearTimeout(timer); return { r: r, j: {} };   // an error page from the web server (502 …): not JSON
      });
    }, fail);
  }
  function request(method, path, body, btn) {
    var url = "/app/api/" + path + (devUser ? (path.indexOf("?") >= 0 ? "&" : "?") + "dev_user=" + encodeURIComponent(devUser) : "");
    var tries = 0;
    if (btn) btn.classList.add("busy");
    function attempt() {
      var opts = { method: method, headers: { "X-Tg-Init-Data": initData } };
      if (method === "POST") { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body || {}); }
      return send(url, opts, method === "GET" ? GET_TIMEOUT : POST_TIMEOUT).catch(function (err) {
        // a read is safe to repeat: one silent retry hides a dropped connection (never for POST)
        if (method === "GET" && !err.timeout && tries++ < 1) return new Promise(function (ok) { setTimeout(ok, 600); }).then(attempt);
        throw err;
      });
    }
    return attempt().then(function (x) {
      var r = x.r, j = x.j;
      if (j && j.res) K.setRes(j.res);
      if (!r.ok) {
        var e = new Error(j.error && r.status !== 401 && r.status !== 404 && r.status !== 405 ? j.error
          : r.status === 401 ? (initData ? "نشستت تموم شده. مینی‌اپ رو ببند و دوباره از ربات بازش کن." : "این صفحه فقط از داخل تلگرام باز می‌شه.")
          : r.status >= 500 ? "سرور الان جواب نمی‌ده. کمی بعد دوباره امتحان کن." : "این کار الان ممکن نیست.");
        e.status = r.status; e.code = j.code; e.data = j; throw e;
      }
      if (method === "POST") { postSeq++; meStale = true; }   // an action may have changed the profile
      return j;
    }, function (err) {
      err.message = !err.timeout ? "اتصال برقرار نشد. اینترنتت رو چک کن."
        : method === "GET" ? "اتصال خیلی کنده. دوباره امتحان کن."
        : "جواب سرور نرسید. صفحه رو تازه کن تا نتیجه رو ببینی.";
      if (method === "POST") { postSeq++; meStale = true; }   // it may have gone through all the same
      throw err;
    }).finally(function () { if (btn) btn.classList.remove("busy"); });
  }
  K.api = {
    /* GET (no toast on error — screens show an error state through the router). A dropped
       connection is retried once by itself; it gives up after 15 s. */
    get: function (path) { return request("GET", path); },
    /* GET with a per-session cache (use for lists that only change through your own actions;
       call K.invalidate(path) after changing them). Two callers asking at the same moment
       share one request. */
    cached: function (path) {
      if (K.cache[path]) return Promise.resolve(K.cache[path]);
      if (inflight[path]) return inflight[path];
      var p = request("GET", path).then(function (j) { if (inflight[path] === p) { K.cache[path] = j; delete inflight[path]; } return j; },
                                         function (err) { if (inflight[path] === p) delete inflight[path]; throw err; });
      inflight[path] = p; return p;
    },
    /* POST. `btn` (optional) gets a spinner while it runs. A failed action shows the server's
       message as a toast AND rejects, so `.then` only runs on success.
       A second identical POST (same path + body, or the same button) fired while the first is
       still running is IGNORED — its promise never settles — so a double tap cannot buy twice. */
    post: function (path, body, btn) {
      var key; try { key = path + "\n" + JSON.stringify(body || {}); } catch (e) { key = path; }
      if (posting[key] || (btn && btn._kbusy)) return new Promise(function () {});
      posting[key] = true; if (btn) btn._kbusy = true;
      return request("POST", path, body, btn).catch(function (err) {
        K.haptic("err");
        // out of energy → the toast offers the refill screen (registered by the shop module)
        if (err.code === "energy" && K.hasScreen("sh_energy")) K.toast(err.message, "err", { label: "شارژ انرژی", run: function () { K.go("sh_energy"); } });
        else K.toast(err.message, "err");
        throw err;
      }).finally(function () { delete posting[key]; if (btn) btn._kbusy = false; });
    }
  };
  /* Forget cached lists: K.invalidate("profile/creatures/", "profile/equipment/"). A request for
     that path that is still on its way is not stored either. */
  K.invalidate = function () { for (var i = 0; i < arguments.length; i++) { delete K.cache[arguments[i]]; delete inflight[arguments[i]]; } };

  // ───────────────────────── HUD ─────────────────────────
  var hudIn = document.getElementById("hud-in");
  K.res = null;
  var resShown = "", energyTimer = null;
  var RES_TITLE = { coins: "طلا", dna: "DNA", diamonds: "الماس", energy: "انرژی", cup: "کاپ" };
  K.setRes = function (res) {
    if (!res) return;
    var prev = K.res; K.res = res;
    // every response carries `res`; the bar is only rebuilt when a number really changed
    var shown = [res.coins, res.dna, res.diamonds, res.energy, res.max_energy, res.cup].join("|");
    if (shown !== resShown) {
      resShown = shown;
      var items = [["coin", "coins", "coin", K.short(res.coins)], ["dna", "dna", "dna", K.short(res.dna)], ["gem", "diamonds", "diamond", K.short(res.diamonds)],
                   ["bolt", "energy", "energy", '<span class="num">' + Number(res.energy) + "<small>/" + res.max_energy + "</small></span>"], ["trophy", "cup", "cup", K.n(res.cup)]];
      hudIn.innerHTML = items.map(function (it) {
        var changed = prev && prev[it[1]] !== res[it[1]];
        return '<span class="res t-' + it[2] + (changed ? " bump" : "") + '" title="' + RES_TITLE[it[1]] + '">' + K.ic(it[0], it[0] === "bolt" ? "f" : "") + '<span style="color:var(--text)">' + it[3] + "</span></span>";
      }).join("");
    }
    // energy refills by itself: ask again when the server said the next point is due
    clearTimeout(energyTimer);
    if (res.energy < res.max_energy && res.energy_in > 0) {
      energyTimer = setTimeout(function () { if (!document.hidden && K.me) K.refreshMe(true).catch(function () {}); }, (Number(res.energy_in) + 1) * 1000);
    }
  };

  // ───────────────────────── toasts, sheet, dialogs ─────────────────────────
  var toasts = document.getElementById("toasts");
  /* K.toast("متن", "ok"|"err", {label: "شارژ", run: fn}) — the optional third argument adds a tappable action. */
  var lastToast = "", lastToastAt = 0;
  K.toast = function (text, kind, action) {
    text = String(text == null ? "" : text); if (!text) return;
    // the same message twice in a row (two failed taps, two modules reporting one error) shows once
    var sig = kind + "|" + text, now = Date.now();
    if (sig === lastToast && now - lastToastAt < 1500) return;
    lastToast = sig; lastToastAt = now;
    var el = document.createElement("div");
    el.className = "toast " + (kind === "err" ? "err" : "ok");
    el.innerHTML = K.ic(kind === "err" ? "warn" : "check") + "<span>" + K.esc(text) + "</span>" + (action ? '<button class="tact">' + K.esc(action.label) + "</button>" : "");
    el.style.pointerEvents = "auto";
    el.onclick = function () { el.remove(); };   // tap to dismiss
    if (action) el.querySelector(".tact").onclick = function (ev) { ev.stopPropagation(); el.remove(); action.run(); };
    toasts.appendChild(el);
    while (toasts.children.length > 3) toasts.removeChild(toasts.firstChild);
    setTimeout(function () { el.classList.add("out"); setTimeout(function () { el.remove(); }, 260); }, action ? 5000 : kind === "err" ? 3600 : 2200);
  };
  var sheet = document.getElementById("sheet"), sheetBox = document.getElementById("sheet-box"), sheetClose = null;
  /* Bottom sheet. `html` is the content; returns the box element so you can K.on(box, ...).
     opts.onClose runs when it is dismissed. Close it with K.closeSheet(). */
  K.sheet = function (html, opts) {
    // a FRESH inner element per sheet: listeners a caller adds to the returned node die with it
    // (they used to pile up on the one shared box and fire in every later sheet)
    // a sheet opened over a waiting K.confirm / K.pickCreature / K.reward: that promise is settled
    // (as "dismissed") instead of hanging for ever. A module's own onClose keeps the old
    // behaviour: it only runs when its sheet is really closed.
    var prev = sheetClose; sheetClose = null;
    if (prev && prev.settle) { try { prev(); } catch (e) { console.error(e); } }
    sheetBox.innerHTML = '<button class="x" data-close aria-label="بستن">' + K.ic("close") + '</button><div class="sheet-in"></div>';
    var inner = sheetBox.lastChild; inner.innerHTML = html;
    sheetBox.scrollTop = 0; sheet.classList.add("open"); sheet.setAttribute("aria-hidden", "false"); document.body.classList.add("sheet-open");
    sheetClose = opts && opts.onClose; K.haptic();
    return inner;
  };
  function settle(fn) { fn.settle = true; return fn; }
  K.sheetOpen = function () { return sheet.classList.contains("open"); };
  K.closeSheet = function () {
    if (!sheet.classList.contains("open")) return;
    sheet.classList.remove("open"); sheet.setAttribute("aria-hidden", "true"); document.body.classList.remove("sheet-open");
    var f = sheetClose; sheetClose = null; if (f) f();
  };
  sheet.addEventListener("click", function (ev) { if (ev.target.closest("[data-close]")) K.closeSheet(); });
  document.addEventListener("keydown", function (ev) { if (ev.key === "Escape") K.closeSheet(); });
  /* K.confirm({title, text, ok: "بفرست", cancel: "نه", danger: false, icon: "warn"}) → Promise<boolean> */
  K.confirm = function (o) {
    return new Promise(function (resolve) {
      var done = false;
      var box = K.sheet('<div class="grab"></div><div class="loot"><div class="burst" style="color:' + (o.danger ? "var(--bad)" : "var(--accent)") + ';background:none;border-color:currentColor">' + K.ic(o.icon || (o.danger ? "warn" : "info")) + "</div>" +
        "<h3>" + K.esc(o.title || "مطمئنی؟") + "</h3>" + (o.html || (o.text ? "<p>" + K.esc(o.text) + "</p>" : "")) + "</div>" +
        '<div class="pad btns" style="margin-top:14px"><button class="btn" data-no>' + K.esc(o.cancel || "نه") + '</button><button class="btn ' + (o.danger ? "danger" : "primary") + '" data-yes>' + K.esc(o.ok || "آره") + "</button></div>",
        { onClose: settle(function () { if (!done) resolve(false); }) });
      box.querySelector("[data-yes]").onclick = function () { done = true; K.closeSheet(); resolve(true); };
      box.querySelector("[data-no]").onclick = function () { K.closeSheet(); };
    });
  };
  /* Reward reveal. K.reward({title, text, coins, dna, diamonds, xp, energy, cup, extra: ["…html…"],
     creatures: [creatureDict], items: [{name,rarity,level,img}], button: "عالیه"}) → Promise (resolves when closed) */
  K.reward = function (o) {
    return new Promise(function (resolve) {
      var chips = [];
      [["coins", "coin", "t-coin"], ["dna", "dna", "t-dna"], ["diamonds", "gem", "t-diamond"], ["energy", "bolt", "t-energy"], ["xp", "up", "t-xp"]].forEach(function (c) {
        if (o[c[0]]) chips.push('<span class="it ' + c[2] + '">' + K.ic(c[1]) + '<span style="color:var(--text)">+' + Number(o[c[0]]).toLocaleString("en-US") + (c[0] === "xp" ? " XP" : "") + "</span></span>");
      });
      if (o.cup) chips.push('<span class="it t-cup">' + K.ic("trophy") + '<span style="color:var(--text)" class="num">' + (o.cup > 0 ? "+" : "") + o.cup + "</span></span>");
      (o.extra || []).forEach(function (h) { chips.push('<span class="it">' + h + "</span>"); });
      var cards = (o.creatures || []).map(function (c) { return K.creatureTile(c, { tag: "div" }); }).concat((o.items || []).map(function (e) { return K.itemTile(e, { tag: "div" }); })).join("");
      var box = K.sheet('<div class="grab"></div><div class="loot"><div class="burst">' + K.ic(o.icon || "gift") + "</div><h3>" + K.esc(o.title || "جایزه گرفتی!") + "</h3>" +
        (o.text ? "<p>" + K.esc(o.text) + "</p>" : "") + (chips.length ? '<div class="items">' + chips.join("") + "</div>" : "") + (cards ? '<div class="cards">' + cards + "</div>" : "") + "</div>" +
        '<div class="pad" style="margin-top:16px"><button class="btn primary block" data-close>' + K.esc(o.button || "عالیه") + "</button></div>", { onClose: settle(function () { resolve(); }) });
      K.haptic("ok"); return box;
    });
  };

  // ───────────────────────── shared components ─────────────────────────
  K.statGrid = function (c) {
    return '<div class="stats">' +
      '<div class="stat hp">' + K.ic("heart", "f") + "<b>" + K.n(c.hp) + "</b><small>جان</small></div>" +
      '<div class="stat atk">' + K.ic("sword") + "<b>" + K.n(c.atk) + "</b><small>حمله</small></div>" +
      '<div class="stat def">' + K.ic("shield") + "<b>" + K.n(c.def) + "</b><small>دفاع</small></div>" +
      '<div class="stat spd">' + K.ic("wind") + "<b>" + K.n(c.spd) + "</b><small>سرعت</small></div></div>";
  };
  /* A creature in the image grid. opts: {tag:"button"|"div", attrs:'data-act="x"', sel:bool, dim:bool, flag:"متن"} */
  K.creatureTile = function (c, opts) {
    opts = opts || {}; var tag = opts.tag || "button";
    var flag = opts.flag ? '<span class="flag new">' + K.esc(opts.flag) + "</span>" : c.active ? '<span class="flag act">' + K.ic("check") + "فعال</span>" : c.busy ? '<span class="flag busy">' + K.ic("clock") + "مشغول</span>" : "";
    return "<" + tag + ' class="tile ' + c.rarity + (opts.sel ? " sel" : "") + (opts.dim ? " dim" : "") + '" data-creature="' + (c.id || "") + '" ' + (opts.attrs || "") + ">" +
      '<img class="pic" loading="lazy" decoding="async" src="' + c.img + '" alt=""><span class="shade"></span>' + flag +
      '<span class="el e-' + c.element + '">' + K.ic(K.EL_ICON[c.element] || "atom") + "</span>" +
      '<span class="body"><span class="nm">' + K.esc(c.name) + '</span><span class="sub">' + K.stars(c.star) + (c.power != null ? '<span class="p">' + K.ic("power") + K.n(c.power) + "</span>" : "") + "</span></span></" + tag + ">";
  };
  /* An equipment piece in the image grid. e: {id,name,slot,rarity,level,power?,img,on?} */
  K.itemTile = function (e, opts) {
    opts = opts || {}; var tag = opts.tag || "button";
    return "<" + tag + ' class="tile ' + e.rarity + (opts.sel ? " sel" : "") + (opts.dim ? " dim" : "") + '" data-equip="' + (e.id || "") + '" ' + (opts.attrs || "") + ">" +
      '<img class="pic" loading="lazy" decoding="async" src="' + e.img + '" alt=""><span class="shade"></span>' +
      (e.on ? '<span class="flag act">' + K.ic("check") + "پوشیده</span>" : "") + '<span class="lvl num">+' + e.level + "</span>" +
      '<span class="body"><span class="nm">' + K.esc(e.name) + '</span><span class="sub"><span>' + K.esc(K.slotLabel(e.slot)) + "</span>" + (e.power != null ? '<span class="p">' + K.ic("power") + K.n(e.power) + "</span>" : "") + "</span></span></" + tag + ">";
  };
  /* Compact row: thumbnail + name + tags + power. c = creatureDict (or {name,img,rarity,element,star,level,power}) */
  K.fighter = function (c, sub) {
    return '<div class="fighter ' + (c.rarity || "") + '"><img class="tile ' + (c.rarity || "") + '" style="box-shadow:0 0 0 1.5px var(--rc,var(--line))" loading="lazy" decoding="async" src="' + c.img + '" alt=""><div class="grow"><div class="nm cut">' + K.esc(c.name) + "</div>" +
      '<div class="sm muted">' + (c.star ? K.stars(c.star) + " " : "") + (c.level ? "سطح " + K.n(c.level) : "") + "</div>" + (sub || "") + "</div>" +
      (c.power != null ? '<span class="pw">' + K.ic("power") + K.n(c.power) + "</span>" : "") + "</div>";
  };
  /* Element-advantage line for a duel: returns {html, mine:bool, theirs:bool}. Same rule as the bot:
     advantage = exactly +20% power. */
  K.advantage = function (mine, theirs) {
    var s = (K.meta && K.meta.strong_against) || {}, m = (s[mine] || []).indexOf(theirs) >= 0, t = (s[theirs] || []).indexOf(mine) >= 0;
    return { mine: m, theirs: t, html: m ? '<span class="tag" style="color:var(--good)">' + K.ic("up") + "برتری با تو: +۲۰٪ قدرت</span>"
      : t ? '<span class="tag" style="color:var(--bad)">' + K.ic("warn") + "برتری با حریف: +۲۰٪ قدرت</span>" : '<span class="tag plain">بدون برتری عنصری</span>' };
  };
  /* Progress bar: K.bar(0.4, "gold"|"good"|"bad"|"", "thick") */
  K.bar = function (ratio, kind, extra) { return '<div class="progress ' + (kind || "") + " " + (extra || "") + '"><i style="width:' + Math.max(0, Math.min(100, (ratio || 0) * 100)).toFixed(1) + '%"></i></div>'; };
  /* Empty / error state */
  K.state = function (icon, title, text, btnHtml) { return '<div class="state"><div class="big">' + K.ic(icon) + "</div><b>" + K.esc(title) + "</b>" + K.esc(text || "") + (btnHtml ? "<br>" + btnHtml : "") + "</div>"; };
  /* Loading placeholder shown while a screen's render() is running. A screen picks one with
     `skeleton: "grid" | "hub" | "detail" | "list"` in its K.screen definition (default: three blocks). */
  K.skeleton = function (kind) {
    var i, g;
    if (kind === "grid") { g = '<div class="sk" style="height:44px;margin-bottom:10px"></div><div class="grid">'; for (i = 0; i < 9; i++) g += '<div class="sk" style="aspect-ratio:1/1;border-radius:14px"></div>'; return g + "</div>"; }
    if (kind === "hub") { g = '<div class="sk" style="height:132px;margin-bottom:12px"></div><div class="hub">'; for (i = 0; i < 6; i++) g += '<div class="sk" style="height:112px"></div>'; return g + "</div>"; }
    if (kind === "detail") return '<div class="sk" style="aspect-ratio:16/10;margin-bottom:12px"></div><div class="sk" style="height:74px;margin-bottom:12px"></div><div class="sk" style="height:150px"></div>';
    if (kind === "list") { g = ""; for (i = 0; i < 6; i++) g += '<div class="sk" style="height:62px;margin-bottom:9px"></div>'; return g; }
    return '<div class="sk" style="height:110px;margin-bottom:12px"></div><div class="sk" style="height:220px;margin-bottom:12px"></div><div class="sk" style="height:140px"></div>';
  };
  /* Live countdown: put <span class="timer" data-until="<unix seconds>"></span> (or data-left="<seconds from now>")
     in your HTML and call K.timers(root, onDone). Text updates every second; onDone(el) fires once at zero. */
  K.timers = function (root, onDone) {
    var els = Array.prototype.slice.call(root.querySelectorAll("[data-until],[data-left]"));
    if (!els.length) return;
    var now0 = K.now(), id = null, seen = false, born = Date.now();
    els.forEach(function (el) { if (el.dataset.left != null && el.dataset.until == null) el.dataset.until = now0 + Number(el.dataset.left); });
    function tick(first) {
      var now = K.now(), live = 0;
      els.forEach(function (el) {
        if (el._done) return;
        // the first pass also FILLS elements that are not in the page yet (a screen is built
        // off-screen and attached when ready) — they used to stay empty for a second
        var conn = el.isConnected;
        if (conn) { seen = true; live++; } else if (first !== true) return;
        var left = Number(el.dataset.until) - now;
        if (left <= 0) {
          el.classList.add("done"); el.innerHTML = K.ic("check") + (el.dataset.done || "آماده");
          if (conn) { el._done = true; live--; if (onDone) onDone(el); }   // onDone only once it is on screen, as before
        } else el.innerHTML = K.ic("clock") + '<span class="num">' + (el.dataset.fmt === "long" ? K.dur(left) : K.clock(left)) + "</span>";
      });
      // nothing left to count (all done, or the block was re-drawn / closed): stop this interval
      // instead of letting one pile up per re-draw until the player leaves the screen
      if (id !== null && !live && (seen || Date.now() - born > 30000)) clearInterval(id);
    }
    tick(true); id = K.every(1000, tick);
  };
  /* A tile grid that draws `size` tiles and a «نمایش بیشتر» button for the rest (big accounts own thousands of pieces):
     html = K.grid(list, function (x) { return K.itemTile(x); })
     Optional 4th argument: an object you keep (e.g. per screen). K.grid stores in `memo.at` how many
     tiles are open and starts from there next time — so «back» shows the list as far as it was opened.
     Set memo.at = 0 when the list itself changes (another filter). */
  var grids = {}, gridSeq = 0;
  function moreLabel(n) { return 'نمایش بیشتر <span class="num">(' + Number(n).toLocaleString("en-US") + ")</span>"; }
  K.grid = function (list, fn, size, memo) {
    size = size || 90; var id = ++gridSeq, at = Math.min(list.length, Math.max(size, (memo && memo.at) || 0));
    grids[id] = { list: list, fn: fn, at: at, size: size, memo: memo || null }; delete grids[id - 24];
    if (memo) memo.at = at;
    return '<div class="grid" id="kg' + id + '">' + list.slice(0, at).map(fn).join("") + "</div>" +
      (list.length > at ? '<button class="btn ghost block mt" data-grid-more="' + id + '">' + moreLabel(list.length - at) + "</button>" : "");
  };
  document.addEventListener("click", function (ev) {
    var b = ev.target.closest && ev.target.closest("[data-grid-more]"); if (!b) return;
    var g = grids[b.dataset.gridMore], host = g && document.getElementById("kg" + b.dataset.gridMore); if (!host) return;
    host.insertAdjacentHTML("beforeend", g.list.slice(g.at, g.at + g.size).map(g.fn).join("")); g.at = Math.min(g.list.length, g.at + g.size);
    if (g.memo) g.memo.at = g.at;
    // (the counter used to be written with textContent and showed raw «<span …>» markup from the second page on)
    if (g.at >= g.list.length) b.remove(); else b.innerHTML = moreLabel(g.list.length - g.at);
  });
  /* Delegated clicks: K.on(root, "name", fn(el, ev)) handles <… data-act="name"> */
  K.on = function (root, act, fn) {
    // ONE click listener per root and one handler per action name: calling K.on again for the
    // same root + action (a screen that re-draws itself and binds again) REPLACES the handler.
    // It used to add another listener each time, so one tap ran the action two, three … times.
    var map = root._kon;
    if (!map) {
      map = root._kon = {};
      root.addEventListener("click", function (ev) {
        var el = ev.target.closest && ev.target.closest("[data-act]");
        if (!el || !root.contains(el)) return;
        var h = map[el.dataset.act]; if (h) h(el, ev);
      });
    }
    map[act] = fn;
  };
  /* Creature picker sheet → Promise<creatureDict|null>.
     opts: {title, sub, filter: fn(c)→bool (false = hidden), disabled: fn(c)→"reason"|"" (shown dimmed), exclude:[ids],
            note: fn(c)→"short plain text" shown as a badge on the tile (e.g. "+12%"), empty: "text when nothing matches"} */
  K.pickCreature = function (opts) {
    opts = opts || {};
    return K.api.cached("profile/creatures/").then(function (d) {
      return new Promise(function (resolve) {
        var done = false, ex = opts.exclude || [];
        var list = d.creatures.filter(function (c) { return ex.indexOf(c.id) < 0 && (!opts.filter || opts.filter(c)); });
        var html = '<div class="grab"></div><div class="pad"><div class="ttl" style="font-size:18px">' + K.esc(opts.title || "یه هیولا انتخاب کن") + "</div>" + (opts.sub ? '<p class="lead" style="margin:4px 0 12px">' + K.esc(opts.sub) + "</p>" : '<div style="height:10px"></div>');
        // K.grid: a big collection is drawn 60 at a time instead of thousands of tiles at once
        html += list.length ? K.grid(list, function (c) { var why = opts.disabled ? opts.disabled(c) : ""; var note = opts.note ? opts.note(c) : ""; return K.creatureTile(c, { attrs: why ? 'data-why="' + K.esc(why) + '"' : 'data-pick="' + c.id + '"', dim: !!why, flag: note || undefined }); }, 60)
                            : K.state("claw", "هیولای مناسبی نداری", opts.empty || "");
        var box = K.sheet(html + "</div>", { onClose: settle(function () { if (!done) resolve(null); }) });
        box.addEventListener("click", function (ev) {
          var t = ev.target.closest(".tile"); if (!t) return;
          if (t.dataset.why) { K.toast(t.dataset.why, "err"); return; }
          if (!t.dataset.pick) return;
          done = true; var id = Number(t.dataset.pick); K.closeSheet(); resolve(list.filter(function (c) { return c.id === id; })[0]);
        });
      });
    }, function (err) {
      // the list could not be loaded: say so and behave like «nothing picked» (callers only handle that)
      K.toast(err && err.message ? err.message : "لیست هیولاها باز نشد.", "err"); return null;
    });
  };

  // ───────────────────────── router ─────────────────────────
  var view = document.getElementById("view"), topbar = document.getElementById("topbar"), titleEl = document.getElementById("title"), topact = document.getElementById("topact"), backEl = document.getElementById("back");
  var tabsEl = document.getElementById("tabs");
  var screens = {}, hubs = { battle: [], base: [], more: [] }, stack = [], timers = [], renderSeq = 0, hubRedraw = null;
  K.TABS = [["home", "home", "خانه"], ["creatures", "claw", "هیولاها"], ["battle", "swords", "نبرد"], ["base", "hall", "پایگاه"], ["more", "grid", "بیشتر"]];
  backEl.innerHTML = K.ic("back");

  /* Register a screen. def: {title: "…" | fn(params), tab: "home|creatures|battle|base|more",
     skeleton: "grid|hub|detail|list" (optional loading placeholder), render(root, params, ctx)} */
  K.screen = function (name, def) { screens[name] = def; };
  K.hasScreen = function (name) { return !!screens[name]; };
  /* Add a tile to a hub menu ("battle" | "base" | "more"). def: {id, title, sub, icon, color, go, params, order, img, wide,
     badge(me)  → number | "live" | 0   (without it the tile shows K.badges[id], see core.py `hub_badges`),
     hall: 3 | function (me) → level    (locked below that main-hall level; a function may return 0 = open, e.g. for an exemption),
     open(me) → true                    (optional: this player is exempt from `hall`)} */
  K.hub = function (hub, def) { (hubs[hub] = hubs[hub] || []).push(def); };
  /* The registered tile with this id (any hub), or null — e.g. to link to another module's screen: K.hubTile("dispatch").go */
  K.hubTile = function (id) { for (var h in hubs) for (var i = 0; i < hubs[h].length; i++) if (hubs[h][i].id === id) return hubs[h][i]; return null; };
  /* Run `fn` every `ms` while the CURRENT screen is shown (cleared automatically on navigation). */
  K.every = function (ms, fn) { var id = setInterval(fn, ms); timers.push(id); return id; };
  K.after = function (ms, fn) { var id = setTimeout(fn, ms); timers.push(id); return id; };

  function current() { return stack[stack.length - 1]; }
  function scrollTop() { return window.scrollY || window.pageYOffset || 0; }
  function showError(err) {
    var st = err && err.status, expired = st === 401 && !!initData, s;
    if (st === 401) s = expired ? ["clock", "نشستت تموم شده", "مینی‌اپ رو ببند و دوباره از ربات بازش کن."] : ["lock", "از داخل تلگرام بازش کن", "این صفحه فقط از دکمه‌ی ربات باز می‌شه."];
    else if (st === 404 && err.data && err.data.error === "not_started") s = ["flask", "هنوز بازی رو شروع نکردی", "اول توی ربات /start بزن و اولین هیولات رو بگیر."];
    else if (st === 400) s = ["lock", "الان در دسترس نیست", err.message];
    else if (err && err.network) s = ["wifi", err.timeout ? "اتصال خیلی کنده" : "اتصال برقرار نشد", "اینترنتت رو چک کن و دوباره امتحان کن."];
    else if (st >= 500) s = ["warn", "سرور جواب نداد", "کمی بعد دوباره امتحان کن."];
    else s = ["warn", "یه مشکلی پیش اومد", "دوباره امتحان کن. اگه درست نشد مینی‌اپ رو ببند و باز کن."];
    view.innerHTML = K.state(s[0], s[1], s[2], '<button class="btn primary" id="retry" style="margin-top:16px">' + (expired ? K.ic("close") + "بستن" : K.ic("refresh") + "تلاش دوباره") + "</button>");
    var b = document.getElementById("retry");
    if (b) b.onclick = function () { if (expired && tg && tg.close) { try { tg.close(); return; } catch (e) {} } paint(); };
  }
  /* Draw the top screen of the stack. `soft` (ctx.reload / K.reload on a screen that is already
     showing): the old content stays visible, untouchable, until the new one is ready and the
     scroll position is kept — no skeleton flash and no jump to the top after every action. */
  function paint(soft) {
    var cur = current(), def = screens[cur.name], seq = ++renderSeq;
    timers.forEach(function (id) { clearInterval(id); clearTimeout(id); }); timers = [];
    K.closeSheet(); hubRedraw = null;
    soft = soft === true && cur.shown === true;
    var tab = def.tab || cur.name;
    Array.prototype.forEach.call(tabsEl.children, function (b) { b.classList.toggle("on", b.dataset.tab === tab); });
    var inner = stack.length > 1, acted = false;
    topbar.hidden = !inner;
    if (!soft) { topact.innerHTML = ""; if (inner) titleEl.textContent = typeof def.title === "function" ? def.title(cur.params) : (def.title || ""); }
    try { if (tg && tg.BackButton) { if (inner) tg.BackButton.show(); else tg.BackButton.hide(); } } catch (e) {}
    if (soft) view.classList.add("reloading");
    else { view.classList.remove("reloading"); view.innerHTML = K.skeleton(def.skeleton); }
    view.classList.remove("enter");
    var ctx = {
      reload: function () { if (current() === cur) paint(true); },
      back: K.back,
      setTitle: function (t) { if (seq === renderSeq) titleEl.textContent = t; },
      /* put buttons in the top bar: ctx.actions('<button class="iconbtn" data-act="x">…</button>') */
      actions: function (html) { if (seq === renderSeq) { acted = true; topact.innerHTML = html; } },
      /* false once the player has moved on — check it before touching the page from a late callback or a K.every poll */
      alive: function () { return seq === renderSeq; }
    };
    var root = document.createElement("div");
    K.ensure().then(function () { return def.render(root, cur.params || {}, ctx); }).then(function () {
      if (seq !== renderSeq) return;
      var y = soft ? scrollTop() : (cur.scrollY || 0);
      if (soft && !acted) topact.innerHTML = "";
      view.classList.remove("reloading");
      view.innerHTML = ""; view.appendChild(root);
      if (!soft) { void view.offsetWidth; view.classList.add("enter"); }
      window.scrollTo(0, y);
      cur.scrollY = 0; cur.shown = true;
    }).catch(function (err) {
      if (seq !== renderSeq) return;
      console.error(err);
      view.classList.remove("reloading"); cur.shown = false;
      showError(err);
    });
  }
  /* Open a screen on top of the current one. */
  K.go = function (name, params) {
    if (!screens[name]) { K.toast("این بخش هنوز آماده نیست.", "err"); return; }
    var top = current(), sig; try { sig = name + "|" + JSON.stringify(params || {}); } catch (e) { sig = name + "|" + Math.random(); }
    if (top && top.sig === sig && Date.now() - top.at < 700) return;   // a double tap would open it twice (and need two «back»)
    if (top) top.scrollY = scrollTop();                                // «back» returns to where the player was
    K.haptic(); stack.push({ name: name, params: params || {}, sig: sig, at: Date.now() }); paint();
  };
  /* Replace the current screen (no extra «back» step). */
  K.replace = function (name, params) { if (!screens[name]) return; stack[stack.length - 1] = { name: name, params: params || {} }; paint(); };
  K.back = function () { if (stack.length > 1) { stack.pop(); paint(); } };
  /* Switch bottom tab (resets the stack). */
  K.tab = function (name) { if (!screens[name]) return; stack = [{ name: name, params: {} }]; paint(); };
  /* Re-draw the current screen in place (same as ctx.reload()). */
  K.reload = function () { if (stack.length) paint(true); };
  /* The «back» the PLAYER presses (top bar, Telegram's BackButton): an open sheet closes first. */
  function backPressed() { if (K.sheetOpen()) { K.closeSheet(); return; } K.back(); }
  backEl.onclick = backPressed;
  try { if (tg && tg.BackButton) tg.BackButton.onClick(backPressed); } catch (e) {}

  /* URL of a file in static/ with its version, so it is cached for good: K.asset("img/bg_home.jpg") */
  var KV = window.__KV || {};
  K.asset = function (rel) { return "/app/s/" + rel + (KV[rel] ? "?v=" + KV[rel] : ""); };
  /* A picture that failed to load on a bad connection is tried once more; a second failure
     hides the browser's broken-image mark (the dark placeholder stays). */
  document.addEventListener("error", function (ev) {
    var el = ev.target; if (!el || el.tagName !== "IMG") return;
    var src = el.getAttribute("src") || ""; if (src.indexOf("/app/") !== 0) return;
    if (el._kretry) { el.classList.add("broken"); return; }
    el._kretry = 1;
    setTimeout(function () { if (el.isConnected) el.src = src + (src.indexOf("?") >= 0 ? "&" : "?") + "r=1"; }, 1500);
  }, true);

  /* Generic hub renderer, used by the built-in "battle" / "base" / "more" screens. */
  /* Tile background: the tile's own `img`, else the section art the bot uses for it
     (K.meta.art, keyed by feature name; HUB_ART maps tile ids that are named differently). */
  var HUB_ART = { tower: "mugen_tower", boss: "worldboss", chests: "arena_chests", forge: "blacksmith", boxes: "diamond_box", market: "blackmarket",
                  vip: "subscription", week: "events", pass: "battlepass", leaderboard: "rank", ranks: "rank", settings: "profile", fusion: "fusion",
                  cave: "cave", missions: "missions", wheel: "wheel", dispatch: "dispatch", hunt: "hunt", arena: "arena", tournament: "tournament",
                  league: "league", shop: "shop", exchange: "exchange", festival: "festival", achievements: "achievements", alliance: "alliance",
                  guide: "guide", profile: "profile", recycle: "equip_exchange", gold_shop: "gold_shop", shield: "shield_shop", items: "item_shop" };
  var HUB_STATIC = { buildings: "img/bg_base.jpg", research: "img/bg_research.jpg", energy: "img/bg_energy.jpg" };
  /* Section headings inside a hub: [first order, title, icon] — a tile belongs to the last group whose order it reaches. */
  var HUB_GROUPS = { more: [[0, "فروشگاه و بازار", "cart"], [20, "روزانه", "target"], [30, "رویدادها", "calendar"], [40, "اتحاد و جدول‌ها", "podium"], [85, "حساب و راهنما", "user"]] };
  function hubArt(t) {
    var art = (K.meta && K.meta.art) || {};
    return t.img || (HUB_STATIC[t.id] ? K.asset(HUB_STATIC[t.id]) : "") || art[t.art] || art[HUB_ART[t.id]] || art[t.id] || "";
  }
  /* The main-hall level a tile still needs for this player (0 = open). */
  function hallNeed(t, me) {
    var need = 0;
    try {
      need = typeof t.hall === "function" ? t.hall(me) : t.hall;
      if (need && typeof t.open === "function" && t.open(me)) need = 0;
    } catch (e) { need = 0; }
    return need && me && me.hall_level < need ? need : 0;
  }
  /* Server-side badges ({tile id: number | "live"}, from /profile/me/ — see core.py `hub_badges`). */
  K.badges = {};
  function tileBadge(t, me) { try { return (t.badge ? t.badge(me) : K.badges[t.id]) || 0; } catch (e) { return 0; } }
  /* A dot on the bottom tab whose hub has a server-side badge (tiles with their own `badge`
     function are not asked here: that could start their polling before the hub is opened). */
  function paintDots() {
    Array.prototype.forEach.call(tabsEl.children, function (b) {
      var list = hubs[b.dataset.tab] || [], n = 0, live = false, me = K.me || {};
      list.forEach(function (t) { var v = t.badge ? 0 : K.badges[t.id]; if (!v || hallNeed(t, me)) return; if (v === "live") live = true; else n += Number(v) || 0; });
      var dot = b.querySelector(".dot");
      if (!n && !live) { if (dot) dot.remove(); return; }
      if (!dot) { dot = document.createElement("i"); dot.className = "dot num"; b.appendChild(dot); }
      dot.classList.toggle("live", !n); dot.textContent = n ? (n > 99 ? "99+" : n) : "";
    });
  }
  K.renderHub = function (root, hub, intro) {
    var me = K.me || {}, items = (hubs[hub] || []).slice().sort(function (a, b) { return (a.order || 50) - (b.order || 50); });
    var html = intro || "", groups = HUB_GROUPS[hub] || [], gi = -1;
    html += '<div class="hub">' + items.map(function (t) {
      var head = "";
      while (gi + 1 < groups.length && (t.order || 50) >= groups[gi + 1][0]) { gi++; head = '<div class="h2 hubgroup">' + K.ic(groups[gi][2]) + groups[gi][1] + "</div>"; }
      return head + tileHtml(t);
    }).join("") + "</div>";
    function tileHtml(t) {
      var badge = tileBadge(t, me), need = hallNeed(t, me), art = hubArt(t);
      // the art is a lazy <img> (it was a CSS background, which downloads every tile of a long
      // menu at once, also the ones far below the screen)
      return '<button class="hubtile' + (t.wide ? " wide" : "") + (need ? " locked" : "") + '" data-hub="' + K.esc(t.id) + '" style="--tc:' + (t.color || "var(--accent)") + '">' +
        (art ? '<img class="bg" loading="lazy" decoding="async" src="' + K.esc(art) + '" alt="">' : "") +
        '<span class="hi">' + K.ic(need ? "lock" : t.icon) + "</span>" + (badge && !need ? '<span class="badge' + (badge === "live" ? " live" : "") + '">' + (badge === "live" ? "فعال" : K.esc(badge)) + "</span>" : "") +
        "<b>" + K.esc(t.title) + "</b><small>" + K.esc(need ? "از سطح " + need + " تالار مِهر" : (t.sub || "")) + "</small></button>";
    }
    if (!items.length) html += K.state("hourglass", "به‌زودی", "این بخش داره آماده می‌شه.");
    root.innerHTML = html;
    var bound = !!root._khub; root._khub = items;
    if (bound) return;                       // re-drawn in place: the click handler is already there
    root.addEventListener("click", function (ev) {
      var b = ev.target.closest("[data-hub]"); if (!b) return;
      var t = root._khub.filter(function (x) { return x.id === b.dataset.hub; })[0]; if (!t) return;
      var need = hallNeed(t, K.me || {});    // asked NOW: the profile may have been refreshed since the tile was drawn
      if (need) { K.haptic("err"); K.toast("این بخش از سطح " + need + " تالار مِهر باز می‌شه.", "err"); return; }
      if (t.go) K.go(t.go, t.params); else if (t.run) t.run();
    });
  };
  /* Re-draw the hub that is on screen (no-op elsewhere). A module calls it when its own badge
     data arrives, so the tile shows the number without waiting for the next visit. */
  K.hubRefresh = function () { if (hubRedraw) hubRedraw(); };
  function hubBanner(img, title, sub) {
    return '<div class="banner hubhead" style="background-image:url(' + K.asset("img/" + img) + ')"><div><div class="ttl">' + title + '</div><div class="sm" style="color:#c5cee2">' + sub + "</div></div></div>";
  }
  /* A hub opens at once from the profile we already have; if that is old (or an action
     happened since) it is refreshed in the background and the tiles are re-drawn only when
     something changed. */
  function hubScreen(hub, img, title, sub) {
    K.screen(hub, { skeleton: "hub", render: function (root, params, ctx) {
      function draw() { K.renderHub(root, hub, hubBanner(img, title, sub)); }
      return K.meFast(function () { if (ctx.alive()) draw(); }).then(function () { draw(); hubRedraw = function () { if (ctx.alive()) draw(); }; });
    } });
  }
  hubScreen("battle", "bg_battle.jpg", "نبرد", "بجنگ، غارت کن و بالا برو");
  hubScreen("base", "bg_base.jpg", "پایگاه", "بساز، ارتقا بده و منابع جمع کن");
  hubScreen("more", "bg_more.jpg", "بیشتر", "فروشگاه، رویدادها، جدول‌ها و تنظیمات");

  /* Profile (`K.me`): lab name/level, league, hall_level, active creature … Refreshed on demand.
     `K.meta` (label tables) is written into the page by the server. */
  K.me = null; K.meta = window.__KMETA || null;
  var meAt = 0, meStale = false, meFlight = null, meFlightSeq = -1, meReq = 0;
  function applyMe(me) {
    K.me = me; if (me.meta) K.meta = me.meta;
    K.badges = me.badges || {}; meAt = Date.now(); paintDots();
    return me;
  }
  /* Fetch the profile from the server → Promise<me>. Calls made at the same moment share one
     request, and an answer that is less than 1.5 s old is reused (K.refreshMe(true) always asks). */
  K.refreshMe = function (force) {
    if (meFlight && meFlightSeq === postSeq) return meFlight;          // already on its way, and no action since it left
    if (force !== true && K.me && !meStale && Date.now() - meAt < 1500) return Promise.resolve(K.me);
    var id = ++meReq, seq = postSeq;
    var p = request("GET", "profile/me/" + (K.meta ? "?meta=0" : "")).then(function (me) {
      if (id !== meReq) return K.me || me;                             // a newer request overtook this one
      if (seq === postSeq) meStale = false;
      return applyMe(me);
    });
    meFlight = p; meFlightSeq = seq;
    function clear() { if (meFlight === p) meFlight = null; }
    p.then(clear, clear);
    return p;
  };
  /* The profile WITHOUT waiting when we already have one → Promise<me> (resolves at once).
     If it is older than 20 s, or an action happened since it was read, it is re-read in the
     background and `onChange(me)` is called if anything differs — re-draw the part that shows it.
     Use this instead of K.refreshMe() at the top of a screen so navigation never waits. */
  K.meFast = function (onChange) {
    if (!K.me) return K.refreshMe();
    if (meStale || Date.now() - meAt > 20000) {
      var before = JSON.stringify(K.me);
      K.refreshMe(true).then(function (me) { if (onChange && JSON.stringify(me) !== before) onChange(me); }).catch(function () {});
    }
    return Promise.resolve(K.me);
  };

  /* Resolves once the profile + label tables are loaded (retries after a failed boot). The
     first call uses the request the shell page started before this file had even loaded. */
  var bootP = null;
  function bootMe() {
    var b = window.__KBOOT; window.__KBOOT = null;
    if (!b || !b.p || !K.meta || (b.dev || null) !== (devUser || null) || (!b.dev && b.d !== initData)) return K.refreshMe(true);
    return b.p.then(function (x) {
      if (!x || !x.ok || !x.j || x.j.id == null) return K.refreshMe(true);   // failed early: ask again the normal way (with its error handling)
      noteDate(x.date); if (x.j.res) K.setRes(x.j.res);
      return applyMe(x.j);
    });
  }
  K.ensure = function () { if (!bootP) bootP = bootMe().catch(function (err) { bootP = null; throw err; }); return bootP; };
  /* Extra blocks on the home screen: K.homeSection({order: 20, render: function (el, me) { el.innerHTML = "…"; }})
     `el` is an empty <section>; return a Promise if you load data (a failure just hides the block). */
  K.homeSections = [];
  K.homeSection = function (def) { K.homeSections.push(def); };

  K.started = false;
  K.start = function () {
    if (K.started) return; K.started = true;
    tabsEl.innerHTML = K.TABS.map(function (t) { return '<button data-tab="' + t[0] + '">' + K.ic(t[1]) + "<span>" + t[2] + "</span></button>"; }).join("");
    tabsEl.addEventListener("click", function (ev) { var b = ev.target.closest("button[data-tab]"); if (!b) return; K.haptic(); K.tab(b.dataset.tab); });
    // back from another app / a locked phone: numbers may be old
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden && K.me && Date.now() - meAt > 60000) K.refreshMe(true).then(K.hubRefresh).catch(function () {});
    });
    K.tab(screens.home ? "home" : "creatures");
  };
  return K;
})();
