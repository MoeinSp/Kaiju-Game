/* Arena («آرنا»), arena chests («جعبه‌ها») and the league («لیگ»).
   Screens: ar_home, ar_opponent, ar_revenge, ar_chests, ar_league.

   The opponent being looked at lives HERE (`held`): the server gives a signed, single-use
   token with every card and the app sends it back on attack / swap / details. The server
   decides everything; a used or expired card simply comes back as a fresh opponent. */
(function (K) {
  "use strict";

  K.addIcons({
    revenge: '<path d="M4 12a8 8 0 1 0 2.600-5.900M4 4.500v4h4M9.500 9.500l5 5M14.500 9.500l-5 5"/>',
    hpbar: '<path d="M3.500 9h17v6h-17zM3.500 9v6M12 9v6"/>'
  });

  var held = null;      // the opponent card on screen (token, seen, …)
  var seen = [];        // real opponents shown lately — «حریف بعدی» skips them
  var readyChests = 0;  // hub badge
  var chestGuide = null;

  function refreshBadge() {
    return K.api.get("arena/badges/").then(function (d) { readyChests = d.ready_chests || 0; }).catch(function () {});
  }
  refreshBadge();

  K.hub("battle", { id: "arena", title: "آرنا", sub: "غارت، کاپ و لیگ", icon: "swords", color: "var(--bad)", go: "ar_home", order: 2 });
  K.hub("battle", { id: "chests", title: "جعبه‌ها", sub: "جایزه‌ی بردهای آرنا", icon: "chest", color: "var(--gold)", go: "ar_chests", order: 3, badge: function () { return readyChests; } });
  K.hub("more", { id: "league", title: "لیگ", sub: "پاداش آخر هفته و جایزه‌ی رتبه", icon: "medal", color: "var(--cup)", go: "ar_league", hall: 3, order: 58 });

  // ───────────────────────── small builders ─────────────────────────
  function tier(key) { return String(key || "bronze").split("_")[0]; }
  function medal(key, cls) { return '<svg class="i ar-lg ar-lg-' + tier(key) + " " + (cls || "") + '" viewBox="0 0 24 24">' + K.icons.medal + "</svg>"; }
  function signed(x) { x = Number(x || 0); return '<span class="num">' + (x > 0 ? "+" : x < 0 ? "−" : "") + Math.abs(x).toLocaleString("en-US") + "</span>"; }
  function pic(src, rarity) {
    var rc = rarity ? ' style="--rc:var(--' + rarity + ')"' : "";
    return src ? '<img src="' + src + '" alt=""' + rc + ">" : '<div class="ar-nopic"' + rc + ">" + K.ic("skull") + "</div>";
  }
  function side(o, mine) {
    return '<div class="side">' + pic(o.img, o.rarity) +
      '<div class="ar-who">' + (mine ? "تو" : "حریف") + "</div>" +
      '<div class="b cut">' + K.esc(o.name) + "</div>" +
      '<div class="ar-pw">' + K.ic("power") + K.n(o.power) + "</div>" +
      '<div class="ar-sub">' + (o.star ? K.stars(o.star) : "") + '<span class="t-cup">' + K.ic("trophy") + K.n(o.cup) + "</span></div>" +
      (o.element ? '<div class="ar-el">' + K.elTag(o.element) + "</div>" : "") + "</div>";
  }
  function versus(my, myCup, opp) {
    return '<div class="versus">' +
      side({ img: my.img, rarity: my.rarity, name: my.name, power: my.power, star: my.star, cup: myCup, element: my.element }, true) +
      '<div class="vs">VS</div>' +
      side({ img: opp.img, rarity: opp.rarity, name: opp.creature, power: opp.power, star: opp.star, cup: opp.cup, element: opp.element }, false) + "</div>";
  }
  function swing(win, loss) {
    return '<div class="ar-swing"><div class="w"><small>' + K.ic("trophy") + "اگه ببری</small><b>" + signed(win) + '</b></div><div class="l"><small>' + K.ic("trophy") + "اگه ببازی</small><b>" + signed(loss) + "</b></div></div>";
  }
  function attackBtn(act, label, c, cls) {
    return '<button class="btn ' + (cls || "danger") + ' lg block" data-act="' + act + '">' + K.ic("swords") + label + '<span class="cost">' + K.ic("bolt", "f") + K.n(c.energy_cost) + "</span></button>" +
      '<div class="xs muted center" style="margin-top:6px">انرژی: ' + K.n(c.energy) + " از " + K.n(c.max_energy) + "</div>";
  }
  function shieldNote(c) {
    return c.shield > 0 ? '<div class="callout warn mt">' + K.ic("shield") + "<div>الان سپر داری (" + K.esc(K.dur(c.shield)) + " مونده). هر حمله " + K.n(c.shield_cost_hours) + " ساعت ازش کم می‌کنه.</div></div>" : "";
  }

  /* POST an attack; asks first when the server says the attacker holds a shield. Resolves with the
     final response, or null if the player kept the shield. */
  function fight(path, body, btn) {
    return K.api.post(path, body, btn).then(function (r) {
      if (!r.need_confirm) return r;
      return K.confirm({
        title: "الان سپر داری", icon: "shield", danger: true, ok: "حمله کن", cancel: "سپر بمونه",
        text: K.dur(r.shield) + " از سپرت مونده. اگه حمله کنی " + r.shield_cost_hours + " ساعت ازش کم می‌شه و ممکنه دوباره غارت بشی."
      }).then(function (yes) {
        if (!yes) return null;
        body.confirm = true;
        return K.api.post(path, body, btn);
      });
    });
  }

  function hpRow(s, mine) {
    var dead = s.hp <= 0;
    return '<div class="ar-hp"><div class="flex between"><span class="cut ' + (dead ? "muted" : "b") + '">' + K.ic(dead ? "skull" : "heart", dead ? "" : "f") + " " + K.esc(s.name) + '</span><span class="num sm">' + Math.round(100 * s.hp / s.max_hp) + "%</span></div>" +
      K.bar(s.hp / s.max_hp, dead ? "bad" : mine ? "good" : "gold") + "</div>";
  }
  /* The result of one raid (arena attack or revenge). `tail` = the buttons under it. */
  function resultHtml(r, tail) {
    var rows = "";
    function row(icon, color, title, amountsHtml, small) {
      return '<div style="color:' + color + '"><span class="ic">' + K.ic(icon) + '</span><span class="t">' + title + (small ? "<small>" + small + "</small>" : "") + '</span><span class="v">' + amountsHtml + "</span></div>";
    }
    if (r.won) {
      rows += row("gift", "var(--gold)", "غنیمت", K.amounts({ coins: r.coins, dna: r.dna }) || K.n(0));
      if (r.plunder.coins || r.plunder.dna) rows += row("building", "var(--warn)", "از معدن‌های حریف", K.amounts(r.plunder));
      if (r.league.coins || r.league.dna) rows += row("medal", "var(--cup)", "پاداش لیگ " + K.esc(r.league.name), K.amounts(r.league));
    }
    rows += row("trophy", "var(--cup)", "کاپ", '<span class="' + (r.cup_delta >= 0 ? "t-good" : "t-bad") + '">' + signed(r.cup_delta) + "</span>", "الان " + K.n(r.new_cup));
    var chest = r.chest ? '<button class="panel ar-won-chest mt" data-act="to-chests"><img src="' + (r.chest.img || "") + '" alt=""><span class="grow"><b>جعبه‌ی جدید: ' + K.esc(r.chest.name) + '</b><small class="muted">جایگاه ' + K.n(r.chest.slot) + " — برو بازش کن</small></span>" + K.ic("chevron") + "</button>"
      : r.slots_full ? '<div class="callout warn mt">' + K.ic("chest") + "<div>جایگاه‌های جعبه‌ات پر بود؛ جعبه‌ی جدیدی نگرفتی.</div></div>" : "";
    var missions = r.missions.length ? '<div class="h2">' + K.ic("calcheck") + 'مأموریت‌های کامل‌شده</div><div class="panel list">' + r.missions.map(function (m) {
      return '<div style="color:var(--good)"><span class="ic">' + K.ic("check") + '</span><span class="t">' + K.esc(m.label) + "<small>" + (m.weekly ? "هفتگی" : "روزانه") + '</small></span><span class="v">' + K.amounts(m) + "</span></div>";
    }).join("") + "</div>" : "";
    return '<div class="panel ar-res ' + (r.won ? "win" : "lose") + '"><div class="ar-res-ic">' + K.ic(r.won ? "trophy" : "skull") + "</div><h3>" + (r.won ? "پیروزی!" : "شکست") + "</h3>" +
      '<div class="muted sm">' + K.esc(r.opponent.label) + (r.opponent.alliance ? " · اتحاد " + K.esc(r.opponent.alliance) : "") + "</div>" +
      (r.opponent.element ? '<div style="margin-top:6px">' + K.elTag(r.opponent.element) + "</div>" : "") +
      '<div class="ar-hps">' + hpRow(r.me, true) + hpRow(r.foe, false) + "</div></div>" +
      '<div class="panel list mt">' + rows + "</div>" + chest + missions + '<div class="mt">' + tail + "</div>";
  }
  function showDetail(text) {
    K.sheet('<div class="grab"></div><div class="pad"><div class="ttl" style="font-size:18px">جزئیات نبرد</div><pre class="ar-log">' + K.esc(text || "جزئیاتی ثبت نشده.") + "</pre></div>");
  }
  function afterFight(r) {
    K.invalidate("profile/creatures/");
    K.haptic(r.won ? "ok" : "err");
    if (r.won) K.reward({
      title: "بردی!", icon: "trophy", button: "ادامه",
      coins: r.coins + r.plunder.coins + r.league.coins, dna: r.dna + r.plunder.dna + r.league.dna, cup: r.cup_delta,
      extra: r.chest ? [K.ic("chest") + '<span style="color:var(--text)">' + K.esc(r.chest.name) + "</span>"] : []
    });
  }

  function detailsSheet(body, btn) {
    return K.api.post("arena/details/", body, btn).then(function (res) {
      var d = res.details, h = '<div class="grab"></div><div class="pad"><div class="ttl" style="font-size:18px">' + K.esc(d.label) + '</div><div class="meta">' +
        (d.alliance ? K.tag("اتحاد " + K.esc(d.alliance), "", "users") : K.tag("بدون اتحاد")) + "</div>";
      if (d.is_fake) {
        h += (d.img ? '<div class="ar-dpic"><img src="' + d.img + '" alt="" style="--rc:var(--' + d.rarity + ')"></div>' : "") +
          '<div class="panel kv mt">' + (d.name ? "<div><span>هیولا</span><span>" + K.esc(d.name) + "</span></div>" : "") +
          (d.rarity ? "<div><span>نایابی</span><span>" + K.rarTag(d.rarity) + "</span></div><div><span>ستاره</span><span>" + K.stars(d.star) + "</span></div>" : "") +
          "<div><span>قدرت کل</span><span>" + K.n(d.power) + "</span></div>" +
          (d.element ? "<div><span>عنصر</span><span>" + K.elTag(d.element) + "</span></div>" : "") + "</div>" +
          '<div class="callout mt">' + K.ic("info") + "<div>این یه آزمایشگاه هوش‌مصنوعیِ هم‌رده‌ی کاپ توئه؛ هرچی کاپت بالاتر بره قوی‌تر می‌شه و نزدیک کاپ " + K.n(4000) + " شکست‌ناپذیره.</div></div>";
      } else {
        var c = d.creature;
        h += '<div class="mt">' + K.fighter(c, '<div style="margin-top:4px">' + K.rarTag(c.rarity) + " " + K.elTag(c.element) + "</div>") + "</div>" +
          (c.species && c.species !== c.name ? '<div class="sm muted mt">نژاد: ' + K.esc(c.species) + "</div>" : "") +
          '<div class="mt">' + K.statGrid(c) + "</div>" +
          '<div class="panel kv mt"><div><span>قدرت تجهیزات</span><span>+' + K.n(c.gear_power) + "</span></div>" + (c.poison ? "<div><span>زهر</span><span>" + K.n(c.poison) + "</span></div>" : "") +
          c.parts.map(function (p) { return "<div><span>" + K.esc(p.label) + "</span><span>سطح " + K.n(p.level) + "</span></div>"; }).join("") + "</div>" +
          '<div class="h2">' + K.ic("chest") + 'تجهیزات</div><div class="panel list">' + (c.gear.length ? c.gear.map(function (g) {
            return '<div class="c-' + g.rarity + '"><span class="ic">' + K.ic(K.SLOT_ICON[g.slot] || "chest") + '</span><span class="t">' + K.esc(g.name) + ' <span class="num b">+' + g.level + "</span><small>" + K.esc(K.slotLabel(g.slot)) + " · " + K.esc(K.rarLabel(g.rarity)) + "</small></span></div>";
          }).join("") : '<div><span class="t muted">هیچ تجهیزاتی نداره.</span></div>') + "</div>";
      }
      K.sheet(h + "</div>");
    }).catch(function () {});
  }

  // ───────────────────────── arena home ─────────────────────────
  function seasonSheet(btn) {
    btn.classList.add("busy");
    K.api.get("arena/season/").then(function (d) {
      var tab = "now";
      function body() {
        var rows = tab === "now" ? d.table.map(function (r) {
          return '<div class="' + (r.me ? "me" : "") + '"><span class="rk num">' + r.rank + '</span><span class="nm">' + K.esc(r.name) + '<small class="muted"> ریست به ' + K.n(r.reset_to) + '</small></span><span class="cp">' + K.ic("trophy") + K.n(r.cup) + "</span></div>";
        }) : d.last.map(function (r) {
          return '<div class="' + (r.me ? "me" : "") + '"><span class="rk num">' + r.rank + '</span><span class="nm">' + K.esc(r.name) + '</span><span class="cp">' + K.n(r.cup_before) + ' <span class="faint">' + K.ic("chevron") + "</span> " + K.n(r.cup_after) + "</span></div>";
        });
        return '<div class="grab"></div><div class="pad"><div class="ttl" style="font-size:18px">' + (tab === "now" ? "جدول فصل " : "نتایج فصل ") + '<span class="num">' + K.esc(tab === "now" ? d.week : (d.last_week || "")) + "</span></div>" +
          '<div class="seg mt"><button data-tab="now" class="' + (tab === "now" ? "on" : "") + '">جدول هفته</button><button data-tab="last" class="' + (tab === "last" ? "on" : "") + '">فصل قبل</button></div>' +
          (tab === "now" ? '<p class="lead">' + K.esc(K.dur(d.left)) + " تا ریست کاپ‌ها</p>" : "") +
          (rows.length ? '<div class="panel ar-table">' + rows.join("") + "</div>" : K.state("trophy", tab === "now" ? "هنوز کسی کاپ نگرفته" : "هنوز فصلی تموم نشده", tab === "now" ? "اولین نفر باش!" : "اولین ریست آخر همین هفته‌ست.")) + "</div>";
      }
      function open() {
        var box = K.sheet(body());
        box.querySelectorAll("[data-tab]").forEach(function (b) { b.onclick = function () { tab = b.dataset.tab; open(); }; });
      }
      open();
    }).catch(function (e) { K.toast(e.message, "err"); }).finally(function () { btn.classList.remove("busy"); });
  }

  K.screen("ar_home", {
    title: "آرنا", tab: "battle",
    render: function (root, params, ctx) {
      return K.api.get("arena/").then(function (d) {
        readyChests = d.chests.ready;
        var lg = d.league, nx = d.next_league, ch = d.chests, now = Date.now() / 1000;
        var chestSub = !ch.count ? "با برد در آرنا می‌گیری" : ch.ready ? K.n(ch.ready) + " جعبه آماده‌ی باز کردن" : ch.opening_left != null ? '<span class="timer" data-left="' + ch.opening_left + '"></span> تا باز شدن' : "منتظر بازگشایی";
        root.innerHTML = '<div class="banner"' + (d.img ? ' style="background-image:url(\'' + d.img + "')\"" : "") + '><div class="grow"><div class="ttl">آرنا</div><div class="sm" style="color:#c5cee2">فصل <span class="num">' + K.esc(d.week) + '</span> · <span class="timer" data-left="' + d.season_left + '" data-fmt="long" data-done="ریست شد"></span> تا ریست کاپ‌ها</div></div></div>' +
          '<div class="tiles mt">' +
          '<div class="panel info" style="color:var(--cup)"><span class="ic">' + K.ic("trophy") + "</span><span><small>کاپ تو</small><b>" + K.n(d.cup) + "</b></span></div>" +
          '<div class="panel info" style="color:var(--accent)"><span class="ic">' + K.ic("power") + "</span><span><small>قدرت هیولای فعال</small><b>" + K.n(d.power) + "</b></span></div></div>" +
          '<div class="panel pad mt ar-league"><div class="flex">' + medal(lg.key, "big") + '<div class="grow"><div class="b">لیگ ' + K.esc(lg.name) + '</div><div class="sm muted">پاداش هر برد: ' + K.amounts({ coins: lg.coins, dna: lg.dna }) + "</div></div></div>" +
          (nx ? '<div class="mt">' + K.bar((d.cup - lg.min_cup) / Math.max(1, nx.min_cup - lg.min_cup), "gold") + '<div class="xs muted" style="margin-top:5px">لیگ بعدی «' + K.esc(nx.name) + "» در کاپ " + K.n(nx.min_cup) + "</div></div>" : '<div class="xs t-gold mt">توی بالاترین لیگی!</div>') + "</div>" +
          (d.overcap ? '<div class="callout warn mt">' + K.ic("warn") + "<div>کاپت از قدرت هیولات جلو زده؛ تا قوی‌تر نشه بردها کاپ کمتری می‌دن.</div></div>" : "") +
          (d.shield > 0 ? '<div class="callout good mt">' + K.ic("shieldcheck") + '<div>سپر: <span class="timer" data-left="' + d.shield + '" data-fmt="long" data-done="تموم شد"></span> مونده. هر حمله ' + K.n(d.shield_cost_hours) + " ساعت ازش کم می‌کنه.</div></div>"
                        : '<div class="callout mt">' + K.ic("shield") + "<div>سپر نداری؛ ممکنه بهت حمله بشه.</div></div>") +
          '<button class="btn danger lg block mt" data-act="find">' + K.ic("search") + 'جستجوی حریف<span class="cost">' + K.ic("bolt", "f") + K.n(d.energy_cost) + "</span></button>" +
          '<div class="panel list mt">' +
          '<button data-act="chests" style="color:var(--gold)"><span class="ic">' + K.ic("chest") + '</span><span class="t">جعبه‌ها <span class="num muted">(' + ch.count + "/" + ch.max + ")</span><small>" + chestSub + '</small></span><span class="chev">' + K.ic("chevron") + "</span></button>" +
          '<button data-act="revenges" style="color:var(--bad)"><span class="ic">' + K.ic("revenge") + '</span><span class="t">انتقام‌ها<small>' + (d.revenges ? K.n(d.revenges) + " انتقام در انتظار · مهلت " + K.n(3) + " روزه" : "کسی اخیراً بهت حمله نکرده") + "</small></span>" + (d.revenges ? '<span class="ar-cnt num">' + d.revenges + "</span>" : "") + '<span class="chev">' + K.ic("chevron") + "</span></button>" +
          '<button data-act="season" style="color:var(--accent)"><span class="ic">' + K.ic("podium") + '</span><span class="t">جدول هفته و فصل قبل<small>ده نفر اول و کاپ بعد از ریست</small></span><span class="chev">' + K.ic("chevron") + "</span></button>" +
          '<button data-act="league" style="color:var(--cup)"><span class="ic">' + K.ic("medal") + '</span><span class="t">لیگ<small>پاداش آخر هفته و جایزه‌ی رتبه</small></span><span class="chev">' + K.ic("chevron") + "</span></button></div>" +
          '<div class="h2">' + K.ic("shield") + "آخرین حمله‌ها به تو</div>" +
          (d.history.length ? '<div class="panel list">' + d.history.map(function (h) {
            var lost = K.amounts({ coins: h.coins, dna: h.dna });
            return '<div style="color:' + (h.won ? "var(--bad)" : "var(--good)") + '"><span class="ic">' + K.ic(h.won ? "warn" : "shieldcheck") + '</span><span class="t">' + K.esc(h.name) + "<small>" + (h.won ? "غارتت کرد" : "دفاع کردی") + " · قدرت " + K.n(h.power) + " · " + K.esc(K.dur(Math.max(60, now - h.at))) + ' پیش</small></span><span class="v">' + (lost ? '<span class="faint">−</span> ' + lost : "") + "</span></div>";
          }).join("") + "</div>" : '<div class="panel pad muted sm center">هنوز کسی بهت حمله نکرده.</div>') +
          '<p class="note">هر حمله ' + K.n(d.energy_cost) + " انرژی · برد = " + K.n(d.loot_percent) + "٪ طلا و DNA حریف</p>";
        K.timers(root);
        K.on(root, "find", function () { K.go("ar_opponent", { fresh: true }); });
        K.on(root, "chests", function () { K.go("ar_chests"); });
        K.on(root, "revenges", function () { K.go("ar_revenge"); });
        K.on(root, "league", function () { if (K.me && K.me.hall_level < 3) K.toast("لیگ از سطح 3 تالار مِهر باز می‌شه.", "err"); else K.go("ar_league"); });
        K.on(root, "season", function (el) { seasonSheet(el); });
      });
    }
  });

  // ───────────────────────── the opponent card ─────────────────────────
  function cardHtml(c) {
    var o = c.opponent, adv = K.advantage(c.my.element, o.element);
    return (c.note ? '<div class="callout warn mb">' + K.ic("info") + "<div>" + K.esc(c.note) + "</div></div>" : "") +
      '<div class="panel ar-card">' + versus(c.my, c.my_cup, o) +
      '<div class="ar-lab">' + K.ic(o.is_fake ? "flask" : "user") + '<span class="cut">' + K.esc(o.label) + "</span>" + (o.alliance ? '<span class="muted sm cut">· اتحاد ' + K.esc(o.alliance) + "</span>" : "") + "</div>" +
      '<div class="center" style="padding:0 12px 12px">' + adv.html + "</div></div>" +
      '<div class="panel pad mt"><div class="flex between"><span class="muted">' + K.ic("gift") + " جایزه‌ی برد</span><span>" + (K.amounts(c.loot) || K.n(0)) + "</span></div>" + swing(c.cup_win, c.cup_loss) + "</div>" +
      shieldNote(c) +
      '<div class="mt">' + attackBtn("attack", "حمله", c) + "</div>" +
      '<div class="btns mt"><button class="btn" data-act="next">' + K.ic("refresh") + 'حریف بعدی</button><button class="btn" data-act="swap">' + K.ic("swap") + "تعویض هیولا</button></div>" +
      '<button class="btn ghost block mt" data-act="details">' + K.ic("search") + "جزئیات حریف</button>";
  }

  K.screen("ar_opponent", {
    title: "حریف", tab: "battle",
    render: function (root, params, ctx) {
      var last = null;  // the last result (for «جزئیات نبرد»)
      function show(c) {
        held = c; seen = c.seen || [];
        root.innerHTML = cardHtml(c);
        window.scrollTo(0, 0);
      }
      function find(btn) {
        return K.api.post("arena/find/", { seen: seen }, btn).then(function (c) { if (ctx.alive()) show(c); });
      }
      function showResult(r) {
        held = null; last = r;
        root.innerHTML = resultHtml(r, '<button class="btn danger lg block" data-act="next">' + K.ic("swords") + "حریف بعدی</button>" +
          '<div class="btns mt"><button class="btn" data-act="log">' + K.ic("list") + "جزئیات نبرد</button><button class=\"btn\" data-act=\"home\">" + K.ic("chevron") + "بازگشت به آرنا</button></div>");
        window.scrollTo(0, 0);
        afterFight(r);
      }
      K.on(root, "attack", function (el) {
        if (!held) return;
        fight("arena/attack/", { token: held.token, seen: seen }, el).then(function (r) {
          if (!r || !ctx.alive()) return;
          if (r.rematch) { show(r); K.toast(r.note || "حریف عوض شد.", "err"); return; }
          showResult(r.result);
        }).catch(function () {});
      });
      K.on(root, "next", function (el) { find(el).catch(function () {}); });
      K.on(root, "details", function (el) { if (held) detailsSheet({ token: held.token }, el); });
      K.on(root, "log", function () { showDetail(last && last.detail); });
      K.on(root, "home", function () { K.back(); });
      K.on(root, "to-chests", function () { K.go("ar_chests"); });
      K.on(root, "swap", function (el) {
        if (!held) return;
        el.classList.add("busy");
        K.api.get("arena/team/").then(function (d) {
          var box = K.sheet('<div class="grab"></div><div class="pad"><div class="ttl" style="font-size:18px">کدوم هیولا با این حریف بجنگه؟</div><p class="lead" style="margin:4px 0 12px">حریف عوض نمی‌شه؛ فقط هیولای خودت. برتری عنصری یعنی +۲۰٪ قدرت.</p>' +
            d.creatures.map(function (c) {
              var adv = K.advantage(c.element, held.opponent.element);
              return '<button class="panel ar-pick' + (c.active ? " on" : "") + (c.busy ? " dim" : "") + '" data-pick="' + c.id + '"' + (c.busy ? ' data-busy="1"' : "") + ">" +
                K.fighter(c, '<div style="margin-top:4px">' + K.elTag(c.element) + " " + (c.active ? K.tag("فعال", "var(--good)", "check") : c.busy ? K.tag("مشغول", "var(--warn)", "clock") : adv.mine || adv.theirs ? adv.html : "") + "</div>") + "</button>";
            }).join("") + "</div>");
          box.querySelectorAll("[data-pick]").forEach(function (b) {
            b.onclick = function () {
              if (b.dataset.busy) { K.toast("این هیولا مشغوله و نمی‌شه فعالش کرد.", "err"); return; }
              if (b.classList.contains("on")) { K.closeSheet(); return; }
              K.api.post("arena/swap/", { token: held.token, creature_id: +b.dataset.pick, seen: seen }, b).then(function (c) {
                K.invalidate("profile/creatures/"); K.closeSheet(); K.haptic("ok");
                if (ctx.alive()) { show(c); K.toast(c.note || "هیولات عوض شد."); }
              }).catch(function () {});
            };
          });
        }).catch(function (e) { K.toast(e.message, "err"); }).finally(function () { el.classList.remove("busy"); });
      });

      var fresh = params.fresh && !params.used;
      params.used = true;
      var p = (held && !fresh) ? K.api.post("arena/card/", { token: held.token, seen: seen }) : K.api.post("arena/find/", { seen: seen });
      return p.then(show);
    }
  });

  // ───────────────────────── revenge ─────────────────────────
  K.screen("ar_revenge", {
    title: function (p) { return p && p.id ? "انتقام" : "انتقام‌ها"; }, tab: "battle",
    render: function (root, params, ctx) {
      if (params.id) return revengeCard(root, params, ctx);
      return K.api.get("arena/revenges/").then(function (d) {
        if (!d.items.length) { root.innerHTML = K.state("revenge", "لیست انتقام خالیه", "هر حمله‌ای که بهت بشه (چه ببازی چه دفاع کنی) تا " + d.days + " روز اینجا قابل انتقامه."); return; }
        var ready = d.items.filter(function (i) { return i.shield <= 0; }), shielded = d.items.filter(function (i) { return i.shield > 0; });
        function item(i, can) {
          var lost = K.amounts({ coins: i.coins, dna: i.dna });
          return '<div class="panel pad ar-rev"><div class="flex"><span class="ico-box" style="color:' + (i.won ? "var(--bad)" : "var(--good)") + '">' + K.ic(i.won ? "warn" : "shieldcheck") + '</span><div class="grow"><div class="b cut">' + K.esc(i.name) + '</div><div class="sm muted">' + (i.won ? "غارتت کرد" : "دفاع کردی") + (lost ? ' · <span class="faint">−</span> ' + lost : "") + '</div></div><span class="ar-pw">' + K.ic("power") + K.n(i.power) + "</span></div>" +
            '<div class="flex between mt sm"><span class="muted">' + (can ? "مهلت انتقام" : "سپر داره") + '</span><span class="timer" data-left="' + (can ? i.left : i.shield) + '" data-fmt="long" data-done="' + (can ? "تموم شد" : "سپرش تموم شد") + '"></span></div>' +
            '<div class="btns mt">' + (can ? '<button class="btn danger" data-act="go" data-id="' + i.log_id + '">' + K.ic("revenge") + "انتقام</button>" : "") +
            '<button class="btn" data-act="details" data-id="' + i.attacker_id + '">' + K.ic("search") + "جزئیات</button></div></div>";
        }
        root.innerHTML = '<p class="lead">' + K.n(d.items.length) + " مورد · " + K.n(ready.length) + " آماده. هر انتقام " + K.n(d.energy_cost) + " انرژی می‌بره.</p>" +
          ready.map(function (i) { return item(i, true); }).join("") +
          (shielded.length ? '<div class="h2">' + K.ic("shield") + 'سپر دارن</div><p class="lead">تا سپرشون تموم نشه نمی‌شه انتقام گرفت.</p>' + shielded.map(function (i) { return item(i, false); }).join("") : "");
        K.timers(root, function () { K.after(1500, ctx.reload); });
        K.on(root, "go", function (el) { K.go("ar_revenge", { id: +el.dataset.id }); });
        K.on(root, "details", function (el) { detailsSheet({ attacker_id: +el.dataset.id }, el); });
      });
    }
  });

  function revengeCard(root, params, ctx) {
    return K.api.get("arena/revenge/card/?id=" + params.id).then(function (c) {
      var o = c.opponent, adv = K.advantage(c.my.element, o.element), last = null, taken = K.amounts(c.taken);
      root.innerHTML = '<div class="panel ar-card">' + versus(c.my, c.my_cup, o) +
        '<div class="ar-lab">' + K.ic("user") + '<span class="cut">' + K.esc(o.label) + "</span></div>" +
        '<div class="center" style="padding:0 12px 12px">' + adv.html + "</div></div>" +
        '<div class="panel pad mt">' + (taken ? '<div class="flex between"><span class="muted">' + K.ic("revenge") + " ازت برده بود</span><span>" + taken + "</span></div>" : "") +
        '<div class="sm muted" style="margin-top:' + (taken ? 6 : 0) + 'px">اگه ببری ۱۰٪ طلا و DNA الانش رو می‌گیری.</div>' + swing(c.cup_win, c.cup_loss) + "</div>" +
        (c.opp_shield > 0 ? '<div class="callout warn mt">' + K.ic("shield") + "<div>این بازیکن الان سپر داره (" + K.esc(K.dur(c.opp_shield)) + "). تا تموم نشه نمی‌شه بهش حمله کرد.</div></div>" : "") +
        shieldNote(c) +
        '<div class="mt">' + (c.opp_shield > 0 ? '<button class="btn lg block" disabled>' + K.ic("shield") + "سپر داره</button>" : attackBtn("revenge", "شروع انتقام", c)) + "</div>" +
        '<button class="btn ghost block mt" data-act="details">' + K.ic("search") + "جزئیات حریف</button>";
      K.on(root, "details", function (el) { detailsSheet({ attacker_id: o.id }, el); });
      K.on(root, "log", function () { showDetail(last && last.detail); });
      K.on(root, "back", function () { K.back(); });
      K.on(root, "to-chests", function () { K.go("ar_chests"); });
      K.on(root, "revenge", function (el) {
        fight("arena/revenge/", { log_id: c.log_id }, el).then(function (r) {
          if (!r || !ctx.alive()) return;
          last = r.result;
          ctx.setTitle(last.won ? "انتقام گرفتی" : "انتقام نگرفتی");
          root.innerHTML = resultHtml(last, '<div class="btns"><button class="btn" data-act="log">' + K.ic("list") + 'جزئیات نبرد</button><button class="btn primary" data-act="back">' + K.ic("chevron") + "انتقام‌ها</button></div>");
          window.scrollTo(0, 0);
          afterFight(last);
        }).catch(function () {});
      });
    });
  }

  // ───────────────────────── chests ─────────────────────────
  var STATUS = { locked: ["lock", "قفل", "var(--muted)"], unlocking: ["hourglass", "در حال باز شدن", "var(--warn)"], queued: ["list", "در صف", "var(--accent)"], ready: ["gift", "آماده", "var(--good)"] };

  function chestLines(c) {
    return '<div class="panel kv mt"><div><span>لیگِ زمان دریافت</span><span>' + K.esc(c.league) + ' <span class="muted">(کاپ ' + K.n(c.cup) + ")</span></span></div>" +
      "<div><span>زمان بازگشایی</span><span>" + K.n(c.unlock_hours) + " ساعت</span></div>" +
      "<div><span>طلا و DNA</span><span>" + K.amounts({ coins: c.coins, dna: c.dna }) + "</span></div>" +
      (c.has_diamonds ? '<div><span>الماس</span><span class="t-diamond">' + K.ic("gem") + " بونس ویژه</span></div>" : "") +
      "<div><span>هیولا یا تجهیزات</span><span>" + (c.creature_chance >= 1 ? "همیشه هیولا" : K.pct(c.creature_chance) + " هیولا، وگرنه تجهیزات") + "</span></div>" +
      "<div><span>حداقل نایابی</span><span>" + K.rarTag(c.min_rarity) + "</span></div>" +
      (c.mythic_chance != null ? "<div><span>شانس " + K.esc(K.rarLabel("mythic")) + "</span><span>" + K.pct(c.mythic_chance) + "</span></div>" : "") + "</div>";
  }
  function guideSheet(guide, mine) {
    var cur = guide[0].tier;
    function open() {
      var g = guide.filter(function (x) { return x.tier === cur; })[0];
      var box = K.sheet('<div class="grab"></div><div class="pad"><div class="ttl" style="font-size:18px">راهنمای جوایز جعبه‌ها</div>' +
        '<div class="seg mt">' + guide.map(function (x) { return '<button data-tier="' + x.tier + '" class="' + (x.tier === cur ? "on" : "") + '">' + K.esc(x.name.replace("جعبه‌ی ", "").replace("جعبه ", "")) + "</button>"; }).join("") + "</div>" +
        '<div class="flex">' + (g.img ? '<img class="ar-gimg" src="' + g.img + '" alt="">' : "") + '<div class="grow sm"><div class="b" style="font-size:15px">' + K.esc(g.name) + '</div><div class="muted">بازگشایی: ' + K.n(g.unlock_hours) + " ساعت</div><div class=\"muted\">" + (g.creature_chance >= 1 ? "همیشه یه هیولا" : K.pct(g.creature_chance) + " هیولا، وگرنه یه تجهیزات") + "</div>" +
        (g.has_diamonds ? '<div class="t-diamond">' + K.ic("gem") + " الماس بونس داره</div>" : "") +
        (g.mythic_min != null ? '<div class="muted">شانس ' + K.esc(K.rarLabel("mythic")) + ": از " + K.pct(g.mythic_min) + " تا " + K.pct(g.mythic_max) + " (بسته به لیگ)</div>" : "") + "</div></div>" +
        '<div class="panel ar-guide mt">' + g.rows.map(function (r) {
          return '<div class="' + (r.key === mine ? "me" : "") + '">' + medal(r.key) + '<span class="grow"><b>' + K.esc(r.league) + '</b><small class="muted"> کاپ ' + K.n(r.min_cup) + '+ · حداقل <span class="c-' + r.min_rarity + '">' + K.esc(K.rarLabel(r.min_rarity)) + '</span></small></span><span class="sm">' + K.amounts({ coins: r.coins, dna: r.dna }, " ") + "</span></div>";
        }).join("") + '</div><p class="note">جایزه بر اساس لیگت در لحظه‌ی برد حساب می‌شه.</p></div>');
      box.querySelectorAll("[data-tier]").forEach(function (b) { b.onclick = function () { cur = b.dataset.tier; open(); }; });
    }
    open();
  }

  K.screen("ar_chests", {
    title: "جعبه‌های آرنا", tab: "battle",
    render: function (root, params, ctx) {
      var data = null;
      function byId(id) { var out = null; data.slots.forEach(function (s) { if (s.chest && s.chest.id === id) out = s.chest; }); return out; }
      function draw(d) {
        data = d;
        readyChests = d.slots.filter(function (s) { return s.chest && s.chest.status === "ready"; }).length;
        root.innerHTML = '<p class="lead">با هر برد توی آرنا یه جعبه می‌گیری (تا ' + K.n(d.slots.length) + " جایگاه). هر بار فقط یه جعبه باز می‌شه.</p>" +
          '<div class="ar-chests">' + d.slots.map(function (s) {
            var c = s.chest;
            if (!c) return '<div class="ar-chest empty"><span class="ar-slot num">' + s.slot + "</span>" + K.ic("chest") + "<b>خالی</b><small>با برد در آرنا</small></div>";
            var st = STATUS[c.status] || STATUS.locked;
            return '<button class="ar-chest ' + c.status + " t-" + c.tier + '" data-act="chest" data-id="' + c.id + '"><span class="ar-slot num">' + s.slot + "</span>" +
              (c.img ? '<img src="' + c.img + '" alt="">' : K.ic("chest")) + "<b>" + K.esc(c.name) + "</b>" +
              (c.status === "unlocking" ? '<span class="timer" data-left="' + c.left + '"></span>' : '<span class="ar-st" style="color:' + st[2] + '">' + K.ic(st[0]) + st[1] + (c.status === "locked" ? " · " + K.n(c.unlock_hours) + " ساعت" : "") + "</span>") +
              (c.status === "ready" ? '<span class="btn gold sm">' + K.ic("gift") + "باز کن</span>" : "") + "</button>";
          }).join("") + "</div>" +
          '<div class="callout mt">' + K.ic(d.has_sub ? "crown" : "info") + "<div>" + (d.has_sub ? "اشتراک ویژه‌ات فعاله: می‌تونی یه جعبه رو توی صف بذاری تا خودکار بعد از جعبه‌ی فعلی باز بشه." : "با اشتراک ویژه می‌تونی یه جعبه رو توی صف بذاری تا خودکار باز بشه.") + "</div></div>" +
          '<button class="btn block mt" data-act="guide">' + K.ic("list") + "راهنمای جوایز لیگ‌ها</button>";
        K.timers(root, function (el) { if (root.contains(el)) K.api.get("arena/chests/").then(function (x) { if (ctx.alive()) draw(x); }).catch(function () {}); });
      }
      function act(path, body, btn, okText) {
        return K.api.post(path, body, btn).then(function (d) {
          K.closeSheet(); K.haptic("ok"); if (okText) K.toast(okText);
          if (ctx.alive()) draw(d);
          return d;
        });
      }
      function openChest(c, btn) {
        act("arena/chests/open/", { id: c.id }, btn).then(function (d) {
          var rw = d.reward;
          K.invalidate("profile/creatures/", "profile/equipment/");
          K.reward({
            title: rw.name + " باز شد!", icon: "chest", coins: rw.coins, dna: rw.dna, diamonds: rw.diamonds,
            text: rw.creature ? "یه هیولای " + K.rarLabel(rw.rarity) + " آزاد شد." : rw.item ? "یه تجهیزات " + K.rarLabel(rw.rarity) + " گرفتی." : "",
            creatures: rw.creature ? [rw.creature] : [], items: rw.item ? [rw.item] : []
          }).then(function () {
            if (rw.next_started) K.toast("«" + rw.next_started.name + "» از صف شروع به باز شدن کرد.");
          });
        }).catch(function () {});
      }
      function chestSheet(c) {
        var st = STATUS[c.status] || STATUS.locked, btns = "";
        if (c.status === "ready") btns = '<button class="btn gold lg block" data-do="open">' + K.ic("gift") + "باز کردن جعبه</button>";
        else {
          if (c.can_start) btns += '<button class="btn primary block" data-do="start">' + K.ic("play") + "شروع بازگشایی (" + c.unlock_hours + " ساعت)</button>";
          else if (c.can_queue) btns += '<button class="btn primary block" data-do="queue">' + K.ic("list") + "بذار توی صف</button>";
          btns += '<button class="btn block' + (btns ? " mt" : "") + '" data-do="speed">' + K.ic("bolt") + 'بازگشایی فوری<span class="cost t-diamond">' + K.ic("gem") + K.n(c.speedup_cost) + "</span></button>";
        }
        var box = K.sheet((c.img ? '<div class="art"><img src="' + c.img + '&s=l" alt=""></div>' : '<div class="grab"></div>') + '<div class="pad"><div class="ttl">' + K.esc(c.name) + '</div><div class="meta">' +
          K.tag("جایگاه " + K.n(c.slot)) + '<span class="tag" style="color:' + st[2] + '">' + K.ic(st[0]) + st[1] + "</span>" +
          (c.status === "unlocking" ? '<span class="timer" data-left="' + c.left + '"></span>' : "") + "</div>" +
          (c.status === "queued" ? '<p class="sm muted">بعد از تموم شدن جعبه‌ی فعلی خودکار باز می‌شه.</p>' : "") +
          (c.status === "locked" && !c.can_start && !c.can_queue ? '<div class="callout warn mt">' + K.ic("hourglass") + "<div>یه جعبه‌ی دیگه داره باز می‌شه." + (data.has_sub ? " صفت هم پره." : " با اشتراک ویژه می‌تونی این یکی رو توی صف بذاری.") + "</div></div>" : "") +
          chestLines(c) + '<div class="mt">' + btns + "</div></div>");
        K.timers(box);
        box.querySelectorAll("[data-do]").forEach(function (b) {
          b.onclick = function () {
            var what = b.dataset.do;
            if (what === "open") openChest(c, b);
            else if (what === "start") act("arena/chests/start/", { id: c.id }, b, "بازگشایی جعبه شروع شد.").catch(function () {});
            else if (what === "queue") act("arena/chests/queue/", { id: c.id }, b, "جعبه رفت توی صف.").catch(function () {});
            else if (what === "speed") {
              K.confirm({ title: "بازگشایی فوری", icon: "gem", ok: "باز کن", cancel: "نه",
                          text: "«" + c.name + "» با " + c.speedup_cost + " الماس همین الان آماده می‌شه. الان " + data.diamonds + " الماس داری." }).then(function (yes) {
                if (!yes) return;
                K.api.post("arena/chests/speedup/", { id: c.id, cost: c.speedup_cost }).then(function (d) {
                  K.haptic("ok"); K.toast("جعبه آماده‌ی باز کردن شد.");
                  if (!ctx.alive()) return;
                  draw(d);
                  var fresh = byId(c.id); if (fresh) chestSheet(fresh);
                }).catch(function () { K.api.get("arena/chests/").then(function (x) { if (ctx.alive()) draw(x); }).catch(function () {}); });
              });
            }
          };
        });
      }
      K.on(root, "chest", function (el) {
        var c = byId(+el.dataset.id); if (!c) return;
        if (c.status === "ready") openChest(c, el.querySelector(".btn")); else chestSheet(c);
      });
      K.on(root, "guide", function () { if (chestGuide) guideSheet(chestGuide, data.my_league); });
      return K.api.get("arena/chests/").then(function (d) { chestGuide = d.guide; draw(d); });
    }
  });

  // ───────────────────────── league ─────────────────────────
  var leagueTab = "divs";
  K.screen("ar_league", {
    title: "لیگ", tab: "more",
    render: function (root, params, ctx) {
      return K.api.get("arena/league/").then(function (d) {
        function list() {
          if (leagueTab === "ranks") return '<p class="lead">علاوه بر پاداش لیگ، به ' + K.n(d.rank_limit) + ' نفر اول جدول کاپ داده می‌شه.</p><div class="panel ar-guide">' + d.ranks.map(function (r) {
            var mine = d.cup > 0 && d.rank >= r.from && d.rank <= r.to;
            return '<div class="' + (mine ? "me" : "") + '"><span class="ar-rk num">' + (r.from === r.to ? r.from : r.from + "–" + r.to) + '</span><span class="grow"><b>' + (r.from === r.to ? "رتبه‌ی " + r.from : "رتبه‌ی " + r.from + " تا " + r.to) + '</b></span><span class="sm">' + K.amounts(r.reward, " ") + "</span></div>";
          }).join("") + "</div>";
          if (leagueTab === "top") return d.standings.length ? '<div class="panel ar-table">' + d.standings.map(function (r) {
            return '<div class="' + (r.me ? "me" : "") + '"><span class="rk num">' + r.rank + "</span>" + medal(r.league) + '<span class="nm">' + K.esc(r.name) + '</span><span class="cp">' + K.ic("trophy") + K.n(r.cup) + "</span></div>";
          }).join("") + "</div>" : K.state("trophy", "هنوز کسی کاپ نگرفته", "");
          return '<p class="lead">آخر هفته پاداش لیگی که توش تموم کنی رو می‌گیری.</p><div class="panel ar-guide">' + d.divisions.map(function (v) {
            return '<div class="' + (v.mine ? "me" : "") + '">' + medal(v.key) + '<span class="grow"><b>' + K.esc(v.title) + '</b><small class="muted"> کاپ ' + K.n(v.min_cup) + "+" + (v.mine ? " · اینجایی" : "") + '</small></span><span class="sm">' + K.amounts(v.reward, " ") + "</span></div>";
          }).join("") + "</div>";
        }
        function draw() {
          var dv = d.division, nx = d.next;
          root.innerHTML = '<div class="banner"' + (d.img ? ' style="background-image:url(\'' + d.img + "')\"" : "") + '><div class="grow"><div class="ttl">لیگ رتبه‌بندی</div><div class="sm" style="color:#c5cee2">پایان فصل: <span class="timer" data-left="' + d.left + '" data-fmt="long" data-done="تموم شد"></span></div></div></div>' +
            '<div class="panel pad mt ar-league"><div class="flex">' + medal(dv.key, "big") + '<div class="grow"><div class="b" style="font-size:16px">' + K.esc(dv.title) + '</div><div class="sm t-cup">' + K.ic("trophy") + " " + K.n(d.cup) + ' کاپ</div></div></div>' +
            (nx ? '<div class="mt">' + K.bar((d.cup - dv.min_cup) / Math.max(1, nx.min_cup - dv.min_cup), "gold") + '<div class="xs muted" style="margin-top:5px">تا «' + K.esc(nx.title) + "»: " + K.n(nx.min_cup - d.cup) + " کاپ دیگه</div></div>" : '<div class="xs t-gold mt">توی بالاترین سطح لیگی!</div>') + "</div>" +
            '<div class="panel list mt"><div style="color:var(--gold)"><span class="ic">' + K.ic("gift") + '</span><span class="t">پاداش آخر هفته‌ی لیگت</span><span class="v">' + K.amounts(d.reward, " ") + "</span></div>" +
            (d.cup > 0 ? '<div style="color:var(--accent)"><span class="ic">' + K.ic("podium") + '</span><span class="t">رتبه‌ی فعلی تو: ' + K.n(d.rank) + "<small>" + (d.rank_reward ? "جایزه‌ی این رتبه" : "جایزه‌ی رتبه از " + K.n(d.rank_limit) + " نفر اول شروع می‌شه") + '</small></span><span class="v">' + (d.rank_reward ? K.amounts(d.rank_reward, " ") : "") + "</span></div>" : "") + "</div>" +
            '<div class="seg mt">' + [["divs", "جوایز لیگ‌ها"], ["ranks", "جوایز رتبه"], ["top", "صدرنشین‌ها"]].map(function (t) { return '<button data-tab="' + t[0] + '" class="' + (leagueTab === t[0] ? "on" : "") + '">' + t[1] + "</button>"; }).join("") + "</div>" + list();
          K.timers(root);
        }
        draw();
        root.addEventListener("click", function (ev) { var b = ev.target.closest("[data-tab]"); if (!b) return; K.haptic(); leagueTab = b.dataset.tab; draw(); });
      });
    }
  });
})(window.K);
