/* Economy: monster boxes (sh_boxes), the shop (sh_shop: daily offers, packs, shields, gold),
   the gold/DNA exchange + gear recycling (sh_exchange), the energy refill (sh_energy) and the
   read-only VIP page (sh_vip). Back end: api/shop.py. Every price, chance and preview on these
   screens comes from the server. */
(function (K) {
  "use strict";

  K.addIcons({
    recycle: '<path d="M7 19H4.500l3-5M17 19h2.500l-3-5M12 4 9.500 8.500M12 4l2.500 4.500M7.500 14 10 9.500M16.500 14 14 9.500M9 19h6"/>',
    tagp: '<path d="M4 4h7.500l8.500 8.500-7.500 7.500L4 11.500zM8.500 8.500h.1"/>'
  });

  var RAR_BACK = ["mythic", "legendary", "epic", "rare", "common"];
  var tabs = { boxes: "gold", shop: "daily", shield: "arena", ex: "swap", exDir: "buy_dna", rec: "" };
  var freeBoxes = null;   // known once the boxes screen was opened; before that the home summary answers

  function money(cur, n) {
    var d = cur === "diamonds";
    return '<span class="b ' + (d ? "t-diamond" : "t-coin") + '">' + K.ic(d ? "gem" : "coin") + " " + K.n(n) + "</span>";
  }
  function pct(s) { return '<span class="num">' + K.esc(s) + "</span>٪"; }
  function seg(items, current, attr) {
    return '<div class="seg">' + items.map(function (it) {
      return '<button class="' + (it[0] === current ? "on" : "") + '" data-' + attr + '="' + it[0] + '">' + (it[2] ? K.ic(it[2]) : "") + it[1] + (it[3] ? '<span class="cnt">' + it[3] + "</span>" : "") + "</button>";
    }).join("") + "</div>";
  }
  function banner(img, title, sub) {
    return '<div class="banner mb"' + (img ? ' style="background-image:url(\'' + img + '\')"' : "") + '><div><div class="ttl">' + title + '</div><div class="sm" style="color:#c5cee2">' + sub + "</div></div></div>";
  }
  function kv(rows) {
    return '<div class="panel kv sh-kv">' + rows.filter(Boolean).map(function (r) { return "<div><span>" + r[0] + "</span><span>" + r[1] + "</span></div>"; }).join("") + "</div>";
  }
  /* digits typed with a Persian/Arabic keyboard, thousands separators, spaces → integer (0 if invalid) */
  function toInt(v) {
    var s = String(v == null ? "" : v).replace(/[۰-۹]/g, function (c) { return String(c.charCodeAt(0) - 1776); })
      .replace(/[٠-٩]/g, function (c) { return String(c.charCodeAt(0) - 1632); }).replace(/[,٬،_\s]/g, "");
    return /^\d{1,13}$/.test(s) ? parseInt(s, 10) : 0;
  }
  function hallLocked(req) {
    return K.state("lock", "هنوز قفله", "این بخش از سطح " + req + " تالار مِهر باز می‌شه.");
  }
  /* in-place screen: `load()` fetches + draws into root without a full repaint (so an open
     sheet — the box reveal — survives and the scroll position is kept) */
  function live(root, ctx, path, draw) {
    var self = { data: null, at: 0 }, tick = null, fired = false;
    /* seconds-from-the-fetch → absolute time, so a redraw later still counts down right */
    self.until = function (seconds) { return Math.round(self.at + Number(seconds || 0)); };
    self.draw = function () {
      if (!ctx.alive()) return;
      draw(self.data);
      // ONE interval per screen (it re-reads the timers each tick) — redraws never stack intervals
      if (!tick) tick = ticker(root, function () { if (fired) return; fired = true; K.after(1500, function () { fired = false; self.refresh(); }); });
      else tick();
    };
    /* an action answered with the new state of the screen: show it without another request */
    self.set = function (d) { if (!d) return self.refresh(); self.data = d; self.at = Date.now() / 1000; self.draw(); };
    self.load = function () { return K.api.get(path).then(function (d) { self.data = d; self.at = Date.now() / 1000; self.draw(); return d; }); };
    self.refresh = function () { return self.load().catch(function () { ctx.reload(); }); };
    return self;
  }
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

  // ───────────────────────── hub tiles ─────────────────────────
  K.hub("more", { id: "boxes", title: "باکس‌ها", sub: "هیولا و تجهیزات شانسی", icon: "box", color: "var(--gold)", go: "sh_boxes", order: 10,
                  badge: function () { var h = K.dy && K.dy.last && K.dy.last(); return freeBoxes != null ? freeBoxes : (h && h.free_boxes) || 0; } });
  K.hub("more", { id: "shop", title: "فروشگاه", sub: "آفر روزانه، سپر، طلا", icon: "cart", color: "var(--good)", go: "sh_shop", order: 11 });
  K.hub("more", { id: "exchange", title: "صرافی", sub: "طلا، DNA و بازیافت", icon: "swap", color: "var(--dna)", go: "sh_exchange", order: 13, hall: 2 });
  K.hub("more", { id: "energy", title: "شارژ انرژی", sub: "پر کردن انرژی با الماس", icon: "bolt", color: "var(--energy)", go: "sh_energy", order: 14 });
  K.hub("more", { id: "vip", title: "اشتراک VIP", sub: "نقره‌ای و طلایی", icon: "crown", color: "var(--legendary)", go: "sh_vip", order: 15 });


  // ───────────────────────── boxes ─────────────────────────
  function oddsRows(list) {
    return '<div class="panel kv sh-kv">' + list.map(function (o) {
      return '<div><span class="c-' + o.rarity + '">' + K.ic("gem") + " " + K.esc(K.rarLabel(o.rarity)) + "</span><span>" + pct(o.pct) + "</span></div>";
    }).join("") + "</div>";
  }
  function showOdds(box, t) {
    var html = '<div class="grab"></div><div class="pad"><div class="ttl" style="font-size:18px">' + K.esc(t.label) + '</div><p class="lead" style="margin:4px 0 12px">شانس هر رده</p>';
    if (box === "gold") {
      html += '<div class="h2">' + K.ic("chest") + "تجهیزات (مجموعاً " + pct(t.equip_total) + ")</div>" + oddsRows(t.equip) +
              '<div class="h2">' + K.ic("claw") + "هیولا (مجموعاً " + pct(t.creature_total) + ")</div>" + oddsRows(t.creature);
    } else {
      html += oddsRows(t.odds) + '<p class="note">این باکس همیشه یه هیولای جدید می‌ده.</p>';
    }
    K.sheet(html + '<div style="height:6px"></div><button class="btn block" data-close>باشه</button></div>');
  }

  function rollTile(roll, i, best) {
    var flag = best ? "بهترین" : "";
    var inner = roll.kind === "creature" ? K.creatureTile(roll.creature, { tag: "div", flag: flag }) : K.itemTile(roll.item, { tag: "div" });
    return '<div class="shb-cell' + (best ? " best" : "") + '" style="animation-delay:' + Math.min(i * 55, 700) + 'ms">' + inner + "</div>";
  }
  function payLine(r) {
    var bits = [];
    if (r.box === "gold") {
      if (r.from_tickets) bits.push(K.ic("ticket") + " " + K.n(r.from_tickets) + " تا با بلیط");
      if (r.paid_boxes) bits.push(K.n(r.paid_boxes) + " تا با " + K.amounts({ coins: r.gold_spent, dna: r.dna_spent }, " + "));
      bits.push("بلیط باقی‌مونده: " + K.n(r.tickets_left));
    } else if (r.is_free) bits.push("هدیه‌ی رایگان امروزت بود");
    else bits.push("پرداختی: " + money("diamonds", r.diamonds_spent) + (r.paid && r.opened > r.paid ? " · " + K.n(r.opened - r.paid) + " باکس هدیه" : ""));
    return '<div class="shb-pay sm muted">' + bits.join(' <span class="faint">·</span> ') + "</div>";
  }
  function resultHtml(r) {
    var best = r.rolls[r.best] || r.rolls[0], html = "";
    if (r.rolls.length === 1) {
      var roll = r.rolls[0], o = roll.kind === "creature" ? roll.creature : roll.item;
      html += '<div class="shb-big ' + roll.rarity + '"><img src="' + o.img + '&s=l" alt=""><div class="cap"><div class="ttl">' + K.esc(o.name) + "</div>" +
              (roll.kind === "creature" ? K.stars(o.star) : '<span class="sm">' + K.esc(K.slotLabel(o.slot)) + ' <span class="num b">+' + o.level + "</span></span>") + "</div></div>" +
              '<div class="shb-meta">' + K.rarTag(roll.rarity) + (roll.kind === "creature" ? K.elTag(o.element) : "") + K.tag(K.ic("power") + " " + K.n(o.power), "var(--accent)") + "</div>" +
              (roll.kind === "creature" ? K.statGrid(o) : "") +
              '<p class="note" style="margin-top:12px">' + (roll.kind === "creature" ? "به کلکسیونت اضافه شد؛ از «هیولاها» می‌تونی فعالش کنی." : "به کوله‌ت اضافه شد؛ از «تجهیزات» می‌تونی بپوشونیش.") + "</p>";
    } else {
      var bo = best.kind === "creature" ? best.creature : best.item;
      var tally = RAR_BACK.filter(function (k) { return r.by_rarity[k]; }).map(function (k) {
        return '<span class="tag c-' + k + '">' + K.esc(K.rarLabel(k)) + " " + K.n(r.by_rarity[k]) + "</span>";
      }).join("");
      html += '<div class="shb-best c-' + best.rarity + '">' + K.ic("trophy") + '<span>ارزشمندترین: <b>' + K.esc(bo.name) + "</b> · " + K.esc(K.rarLabel(best.rarity)) + "</span></div>" +
              '<div class="shb-grid">' + r.rolls.map(function (x, i) { return rollTile(x, i, i === r.best); }).join("") + "</div>" +
              '<div class="shb-meta">' + tally + "</div>";
    }
    return html + payLine(r);
  }
  /* The reveal: a shaking crate first, then the big card (one box) or the grid (a batch). */
  function reveal(r, again) {
    var best = r.rolls[r.best] || r.rolls[0];
    var title = r.rolls.length === 1 ? K.esc(r.label) + " باز شد" : K.n(r.opened) + " " + K.esc(r.label) + " باز شد";
    var box = K.sheet('<div class="grab"></div><div class="shb-rv"><div class="shb-crate ' + best.rarity + '">' + K.ic(r.box === "diamond" ? "gem" : "box") + "</div>" +
      '<div class="muted sm" style="margin-top:14px">داره باز می‌شه…</div></div>');
    var stage = box.querySelector(".shb-rv");
    K.haptic("hit");
    setTimeout(function () {
      if (!document.body.contains(stage) || box.querySelector(".shb-rv") !== stage) return;
      stage.classList.add("done");
      stage.innerHTML = '<h3 class="shb-ttl">' + title + "</h3>" + resultHtml(r) +
        '<div class="btns" style="margin-top:14px">' + (again ? '<button class="btn" data-again>' + K.ic("refresh") + "یکی دیگه</button>" : "") +
        '<button class="btn primary" data-close>عالیه</button></div>';
      K.haptic("ok");
      var b = stage.querySelector("[data-again]");
      if (b) b.onclick = function () { K.closeSheet(); again(); };
    }, 950);
  }

  K.screen("sh_boxes", {
    title: "باکس‌ها", tab: "more",
    render: function (root, params, ctx) {
      if (params.tab) { tabs.boxes = params.tab; params.tab = null; }
      var scr = live(root, ctx, "shop/boxes/", function (d) {
        freeBoxes = d.free_boxes; if (K.dy && K.dy.touch) K.dy.touch("free_boxes", d.free_boxes);
        var html = seg([["gold", "باکس ژنتیکی", "box"], ["diamond", "باکس هیولا", "gem", d.free_boxes || ""]], tabs.boxes, "tab");
        if (tabs.boxes === "gold") {
          html += banner(d.img_gold, "باکس ژنتیکی", "تجهیزات و هیولای شانسی با طلا و DNA") +
            '<p class="lead">هرچی سطح باکس بالاتر باشه، شانس هیولا و نایابیش بیشتره.</p>' +
            (d.tickets ? '<div class="callout good mb">' + K.ic("ticket") + "<div><b>" + K.n(d.tickets) + " بلیط داری.</b> اول بلیط‌ها مصرف می‌شن (هر بلیط یه باکس) و برای بقیه طلا و DNA کم می‌شه.</div></div>" : "");
          html += d.gold.map(function (t) {
            var c1 = t.cost["1"], c10 = t.cost["10"];
            function price(c) {
              return c.paid ? K.amounts({ coins: c.gold, dna: c.dna }, " ") + (c.from_tickets ? ' <span class="t-good b">' + K.ic("ticket") + " " + K.n(c.from_tickets) + "</span>" : "")
                            : '<span class="t-good b">' + K.ic("ticket") + " " + K.n(c.from_tickets) + " بلیط</span>";
            }
            return '<div class="panel pad shb-box t-' + t.tier + '"><div class="flex"><span class="ico-box lg">' + K.ic("box") + '</span><div class="grow"><div class="b">' + K.esc(t.label) + "</div>" +
              '<div class="sm">' + K.amounts({ coins: t.gold, dna: t.dna }) + '</div></div><button class="btn sm ghost" data-act="odds" data-box="gold" data-tier="' + t.tier + '">' + K.ic("info") + "شانس‌ها</button></div>" +
              '<div class="sm muted shb-sum">هیولا ' + pct(t.creature_total) + " · تجهیزات " + pct(t.equip_total) + "</div>" +
              '<div class="shb-acts"><button class="btn primary" data-act="open" data-tier="' + t.tier + '" data-count="1"><span>باز کردن ۱×</span><span class="shb-price">' + price(c1) + "</span></button>" +
              '<button class="btn gold" data-act="open" data-tier="' + t.tier + '" data-count="10"><span>باز کردن ۱۰×</span><span class="shb-price">' + price(c10) + "</span></button></div></div>";
          }).join("");
        } else {
          html += banner(d.img_diamond, "باکس هیولا", "همیشه یه هیولای جدید می‌ده") +
            (d.free_boxes ? '<div class="callout good mb">' + K.ic("gift") + "<div><b>باکس رایگان امروزت آماده‌ست.</b> هر روز یه برنزی و یه نقره‌ای رایگان داری.</div></div>"
                          : '<p class="lead">هرچی سطح باکس بالاتر باشه، شانس نایاب بودن هیولا بیشتره. برنزی و نقره‌ای روزی یه بار رایگانن.</p>');
          html += d.diamond.map(function (t) {
            var top = t.odds.slice().reverse().slice(0, 2).map(function (o) { return '<span class="c-' + o.rarity + '">' + K.esc(K.rarLabel(o.rarity)) + " " + pct(o.pct) + "</span>"; }).join(" · ");
            return '<div class="panel pad shb-box d-' + t.tier + (t.free ? " free" : "") + '"><div class="flex"><span class="ico-box lg">' + K.ic("gem") + '</span><div class="grow"><div class="b">' + K.esc(t.label) + "</div>" +
              '<div class="sm">' + (t.free ? '<span class="t-good b">' + K.ic("gift") + " رایگان امروز</span>" : money("diamonds", t.cost) + (t.daily_free ? ' <span class="muted">(رایگان امروز مصرف شده)</span>' : "")) + "</div></div>" +
              '<button class="btn sm ghost" data-act="odds" data-box="diamond" data-tier="' + t.tier + '">' + K.ic("info") + "شانس‌ها</button></div>" +
              '<div class="sm muted shb-sum">' + top + "</div>" +
              '<div class="shb-acts">' + (t.free ? '<button class="btn good" data-act="dfree" data-tier="' + t.tier + '">' + K.ic("gift") + "باز کردن رایگان</button>"
                : '<button class="btn primary" data-act="dbuy" data-tier="' + t.tier + '"><span>خرید و باز کردن</span><span class="shb-price">' + money("diamonds", t.cost) + "</span></button>") +
              '<button class="btn" data-act="dbulk" data-tier="' + t.tier + '"><span>بسته‌ای ' + K.n(d.bulk_pay) + "+" + K.n(d.bulk_open - d.bulk_pay) + '</span><span class="shb-price">' + money("diamonds", t.bulk_cost) + "</span></button></div></div>";
          }).join("");
        }
        root.innerHTML = html;
      });
      function tier(list, key) { return list.filter(function (t) { return t.tier === key; })[0]; }
      var busy = false;
      function opened(r, again) {
        busy = false;
        K.invalidate("profile/creatures/", "profile/equipment/");
        reveal(r, again);
        scr.set(r.boxes);
      }
      function failed(reload) { return function () { busy = false; if (reload) scr.refresh(); }; }
      function openGold(key, count, el) {
        var t = tier(scr.data.gold, key); if (!t) return;
        var c = t.cost[String(count)];
        var go = function () {
          if (busy) return; busy = true;
          K.api.post("shop/boxes/open/", { tier: key, count: count }, el).then(function (r) { opened(r, function () { openGold(key, count, null); }); }, failed(false));
        };
        if (count === 1) return go();
        K.confirm({ title: "باز کردن " + count + " باکس", icon: "box", ok: "باز کن",
          html: "<p>" + K.esc(t.label) + "</p>" + kv([c.from_tickets ? ["با بلیط", K.n(c.from_tickets) + " باکس"] : null,
            c.paid ? ["با هزینه", K.n(c.paid) + " باکس"] : null, c.paid ? ["از حسابت کم می‌شه", K.amounts({ coins: c.gold, dna: c.dna }, " + ")] : null]) })
          .then(function (yes) { if (yes) go(); });
      }
      function openDiamond(key, mode, el) {
        var t = tier(scr.data.diamond, key); if (!t) return;
        var again = function () { openDiamond(key, mode === "free" ? "buy" : mode, null); };
        if (mode === "free") {
          if (busy) return; busy = true;
          K.api.post("shop/boxes/diamond/", { tier: key, free: true }, el).then(function (r) { opened(r, again); }, failed(true));
          return;
        }
        var bulk = mode === "bulk", d = scr.data;
        K.confirm({ title: bulk ? "خرید بسته‌ای" : "خرید و باز کردن", icon: "gem", ok: bulk ? "تأیید و خرید" : "تأیید و باز کردن",
          html: "<p>" + K.esc(t.label) + "</p>" + kv([bulk ? ["تعداد", K.n(d.bulk_pay) + " + " + K.n(d.bulk_open - d.bulk_pay) + " رایگان (" + K.n(d.bulk_open) + " باکس)"] : null,
            ["هزینه", money("diamonds", bulk ? t.bulk_cost : t.cost)], ["الماس تو", money("diamonds", K.res ? K.res.diamonds : 0)]]) })
          .then(function (yes) {
            if (!yes || busy) return;
            busy = true;
            K.api.post(bulk ? "shop/boxes/diamond_bulk/" : "shop/boxes/diamond/", { tier: key }, el).then(function (r) { opened(r, again); }, failed(false));
          });
      }
      root.addEventListener("click", function (ev) {
        var t = ev.target.closest("[data-tab]"); if (t && root.contains(t)) { K.haptic(); tabs.boxes = t.dataset.tab; scr.draw(); }
      });
      K.on(root, "odds", function (el) { var t = tier(scr.data[el.dataset.box], el.dataset.tier); if (t) showOdds(el.dataset.box, t); });
      K.on(root, "open", function (el) { openGold(el.dataset.tier, +el.dataset.count, el); });
      K.on(root, "dfree", function (el) { openDiamond(el.dataset.tier, "free", el); });
      K.on(root, "dbuy", function (el) { openDiamond(el.dataset.tier, "buy", el); });
      K.on(root, "dbulk", function (el) { openDiamond(el.dataset.tier, "bulk", el); });
      return scr.load();
    }
  });

  // ───────────────────────── shop ─────────────────────────
  var GROUPS = [["food", "غذای هیولا (XP)", "food"], ["speed", "کارت‌های سرعت", "hourglass"], ["other", "آفرهای دیگه", "tagp"]];
  var QTY_PRESETS = [1, 5, 10, 25, 50, 100];

  function bought(title, notes) {
    K.haptic("ok");
    K.toast("خریدی: " + (notes && notes.length ? notes.join(" + ") : title), "ok");
  }

  K.screen("sh_shop", {
    title: "فروشگاه", tab: "more",
    render: function (root, params, ctx) {
      if (params.tab) { tabs.shop = params.tab; params.tab = null; }
      var scr = live(root, ctx, "shop/store/", function (d) {
        var html = seg([["daily", "روزانه", "cart"], ["packs", "بسته‌ها", "gift", d.items.length || ""], ["shield", "سپر", "shield"], ["gold", "طلا", "coin"]], tabs.shop, "tab");
        if (tabs.shop === "daily") html += daily(d);
        else if (tabs.shop === "packs") html += packs(d);
        else if (tabs.shop === "shield") html += shield(d);
        else html += gold(d);
        root.innerHTML = html;
      });
      var busy = false;

      /* an offer as a card: picture, what you get, today's limit, the price */
      function offerRow(o) {
        var out = o.remaining === 0, cut = o.price < o.base, dia = o.currency === "diamonds";
        var tint = o.group === "food" ? "var(--earth)" : o.group === "speed" ? "var(--accent)" : dia ? "var(--diamond)" : "var(--coin)";
        return '<button class="panel shs-card' + (out ? " out" : "") + (o.featured ? " hot" : "") + '" data-act="offer" data-key="' + K.esc(o.key) + '"' + (out ? " disabled" : "") + ' style="--tc:' + tint + '">' +
          '<span class="shs-art"' + (o.img ? ' style="background-image:url(\'' + o.img + '\')"' : "") + '><span class="shs-ai">' + K.ic(o.group === "food" ? "food" : o.group === "speed" ? "hourglass" : "tagp") + "</span>" +
          (o.featured ? '<span class="shs-hot">' + K.ic("flame") + "ویژه‌ی امروز</span>" : "") + (out ? '<span class="shs-cover">' + K.ic("check") + "سقف امروز پر شد</span>" : "") + "</span>" +
          '<span class="shs-cb"><b>' + K.esc(o.title) + "</b><small>" + K.esc(o.gets) + "</small>" +
          '<small class="shs-lim">' + (o.remaining != null ? (out ? "فردا دوباره" : K.n(o.remaining) + " عدد مانده") : o.qty ? "هر تعداد که بخوای" : "") + "</small>" +
          '<span class="shs-pr">' + (cut ? '<s class="faint num">' + Number(o.base).toLocaleString("en-US") + "</s>" : "") + money(o.currency, o.price) + "</span></span></button>";
      }
      function daily(d) {
        var html = banner(d.img, "فروشگاه روزانه", "آفرها هر روز عوض می‌شن");
        if (d.gem) {
          var g = d.gem;
          html += '<div class="h2">' + K.ic("gem") + 'کایجوی جمی امروز</div><div class="panel pad glow"><div class="fighter ' + g.rarity + '">' + (g.img ? '<img src="' + g.img + '" alt="">' : "") +
            '<div class="grow"><div class="nm cut">' + K.esc(g.name) + '</div><div class="shs-tags">' + K.rarTag(g.rarity) + K.elTag(g.element) + '</div><div class="sm muted">هم‌گونه و هم‌رده‌ی یکی از قوی‌ترین هیولاهات؛ روزی یه بار.</div></div></div>' +
            (g.claimed ? '<button class="btn block mt" disabled>' + K.ic("check") + "امروز خریدی</button>"
                       : '<button class="btn primary block mt" data-act="gem"><span>خرید</span><span class="cost">' + K.ic("gem") + " " + K.n(g.price) + "</span></button>") + "</div>";
        }
        var any = false;
        GROUPS.forEach(function (gr) {
          var list = d.offers.filter(function (o) { return o.group === gr[0]; });
          if (!list.length) return;
          any = true;
          html += '<div class="h2">' + K.ic(gr[2]) + gr[1] + '</div><div class="shs-grid">' + list.map(offerRow).join("") + "</div>";
        });
        if (!any && !d.gem) html += K.state("cart", "الان آفری موجود نیست", "بعداً سر بزن.");
        return html;
      }
      function packs(d) {
        if (!d.items.length) return K.state("gift", "فعلاً بسته‌ای نیست", "بسته‌های ویژه هر از گاهی اینجا می‌آن. بعداً سر بزن.");
        return banner(d.img_pack || d.img, "بسته‌های ویژه", "بعضی‌هاشون سقف خرید دارن") + d.items.map(function (it) {
          var full = it.max_per_user > 0 && it.bought >= it.max_per_user;
          var price = [it.price_coins ? money("coins", it.price_coins) : "", it.price_diamonds ? money("diamonds", it.price_diamonds) : ""].filter(Boolean).join(" + ") || '<span class="t-good b">رایگان</span>';
          return '<div class="panel pad shs-pack' + (full ? " out" : "") + '"><div class="flex"><span class="ico-box lg t-gold">' + K.ic(full ? "check" : "gift") + '</span><div class="grow"><div class="b">' + K.esc(it.title) + '</div><div class="sm muted">' + K.esc(it.gets) + "</div></div></div>" +
            '<div class="flex between mt"><div>' + price + (it.max_per_user > 0 ? '<div class="xs muted">خریدهای تو: ' + K.n(it.bought) + " از " + K.n(it.max_per_user) + "</div>" : "") + "</div>" +
            '<button class="btn primary sm" data-act="pack" data-id="' + it.id + '"' + (full ? " disabled" : "") + ">" + (full ? "سقفت پر شد" : "خرید") + "</button></div></div>";
        }).join("");
      }
      function shield(d) {
        var s = d.shield;
        if (s.locked) return hallLocked(s.req);
        var kind = tabs.shield, cur = s[kind];
        var html = banner(d.img_shield, "سپر محافظ", "با الماس؛ خریدها روی هم جمع می‌شن") +
          seg([["arena", "سپر آرنا", "shield"], ["group", "سپر گروه", "users"]], kind, "shield") +
          '<div class="panel pad ' + (cur.left > 0 ? "glow" : "") + '"><div class="flex"><span class="ico-box lg ' + (cur.left > 0 ? "t-good" : "faint") + '">' + K.ic(cur.left > 0 ? "shieldcheck" : "shield") + '</span><div class="grow"><div class="sm muted">وضعیت الان</div>' +
          (cur.left > 0 ? '<span class="timer" data-fmt="long" data-until="' + scr.until(cur.left) + '" data-done="تموم شد"></span>' : '<div class="b">سپر فعالی نداری</div>') + "</div></div></div>" +
          '<div class="callout mt mb">' + K.ic("info") + "<div>" + (kind === "arena"
            ? "تا وقتی سپر داری کسی نمی‌تونه توی آرنا بهت حمله کنه. هر حمله‌ای که خودت بزنی " + K.n(s.attack_cost_hours) + " ساعت از سپرت کم می‌کنه."
            : "تا وقتی سپر گروه داری کسی نمی‌تونه توی گروه با «اتک» بهت حمله کنه. از سپر آرنا جداست و ارزون‌تره.") + "</div></div>" +
          '<div class="panel list">' + cur.tiers.map(function (t) {
            return '<button data-act="shield" data-tier="' + K.esc(t.tier) + '"><span class="ic t-accent">' + K.ic("shield") + '</span><span class="t">' + K.esc(t.label) + "<small>" + K.n(t.hours) + " ساعت محافظت</small></span>" +
              '<span class="v">' + money("diamonds", t.diamonds) + "</span></button>";
          }).join("") + "</div>";
        return html;
      }
      function gold(d) {
        return banner(d.img_gold, "خرید طلا با الماس", "همیشه در دسترس") +
          '<div class="callout mb">' + K.ic("info") + "<div>بسته‌های بزرگ‌تر به‌صرفه‌ترن.</div></div>" +
          '<div class="shs-grid">' + d.gold_packs.map(function (p, i) {
            return '<button class="panel shs-card shs-gold" data-act="gold" data-idx="' + p.idx + '" style="--tc:var(--coin)"><span class="shs-art"><span class="shs-coins s' + Math.min(i, 4) + '">' + K.ic("coin") + K.ic("coin") + K.ic("coin") + "</span></span>" +
              '<span class="shs-cb"><b class="t-coin num">' + Number(p.gold).toLocaleString("en-US") + "</b><small>طلا</small>" +
              '<span class="shs-pr">' + money("diamonds", p.diamonds) + "</span></span></button>";
          }).join("") + "</div>";
      }

      function buyOffer(o, count, el) {
        if (busy) return; busy = true;
        return K.api.post("shop/store/buy/", { token: o.token, count: count }, el).then(function (r) {
          busy = false; K.closeSheet(); bought(r.title, r.notes); scr.set(r.store);
        }, function () { busy = false; scr.refresh(); });
      }
      function qtySheet(o) {
        var bal = K.res ? (o.currency === "diamonds" ? K.res.diamonds : K.res.coins) : 0;
        var max = Math.floor(bal / Math.max(1, o.price));
        if (o.remaining != null) max = Math.min(max, o.remaining);
        var count = 1;
        var box = K.sheet('<div class="grab"></div><div class="pad"><div class="ttl" style="font-size:18px">' + K.esc(o.title) + '</div><p class="lead" style="margin:4px 0 12px">' + K.esc(o.gets) + "</p>" +
          kv([["قیمت هر عدد", money(o.currency, o.price)], ["موجودی تو", money(o.currency, bal)], ["حداکثر قابل خرید", K.n(Math.max(0, max)) + " عدد" + (o.remaining != null ? " (سقف امروز: " + K.n(o.remaining) + ")" : "")]]) +
          '<div class="shs-qty"><div class="stepper"><button data-q="-1" aria-label="کمتر">' + K.ic("minus") + '</button><input class="shs-num num" id="shs-n" inputmode="numeric" value="1" autocomplete="off"><button data-q="1" aria-label="بیشتر">' + K.ic("plus") + "</button></div>" +
          '<div class="row" style="padding-bottom:0">' + QTY_PRESETS.filter(function (p) { return p <= Math.max(1, max); }).map(function (p) { return '<button class="chip" data-set="' + p + '">' + p + "</button>"; }).join("") +
          (max > 1 && QTY_PRESETS.indexOf(max) < 0 ? '<button class="chip" data-set="' + max + '">حداکثر</button>' : "") + "</div></div>" +
          '<button class="btn primary block lg" id="shs-buy"></button></div>');
        var input = box.querySelector("#shs-n"), buy = box.querySelector("#shs-buy");
        function paint(fromInput) {
          count = Math.max(1, Math.min(count, 100000));
          if (!fromInput) input.value = count;
          buy.innerHTML = "<span>خرید " + K.n(count) + ' عدد</span><span class="cost">' + K.ic(o.currency === "diamonds" ? "gem" : "coin") + " " + K.n(o.price * count) + "</span>";
          buy.disabled = max < 1 || count > max;
        }
        input.oninput = function () { count = toInt(input.value) || 1; paint(true); };
        input.onblur = function () { paint(false); };
        box.addEventListener("click", function (ev) {
          var q = ev.target.closest("[data-q]"), s = ev.target.closest("[data-set]");
          if (q) { count += +q.dataset.q; K.haptic(); paint(false); }
          else if (s) { count = +s.dataset.set; K.haptic(); paint(false); }
        });
        buy.onclick = function () {
          var go = function () { buyOffer(o, count, buy); };
          if (o.currency !== "diamonds") return go();
          // a sheet is already open: ask inline instead of stacking a second sheet
          if (buy.dataset.sure) return go();
          buy.dataset.sure = "1"; buy.classList.add("gold"); buy.classList.remove("primary");
          buy.innerHTML = "<span>مطمئنی؟ " + K.n(o.price * count) + " الماس کم می‌شه</span>";
          input.oninput = function () { delete buy.dataset.sure; buy.classList.remove("gold"); buy.classList.add("primary"); count = toInt(input.value) || 1; paint(true); };
        };
        paint(false);
      }

      root.addEventListener("click", function (ev) {
        var t = ev.target.closest("[data-tab]"), s = ev.target.closest("[data-shield]");
        if (t && root.contains(t)) { K.haptic(); tabs.shop = t.dataset.tab; scr.draw(); }
        else if (s && root.contains(s)) { K.haptic(); tabs.shield = s.dataset.shield; scr.draw(); }
      });
      K.on(root, "offer", function (el) {
        var o = scr.data.offers.filter(function (x) { return x.key === el.dataset.key; })[0]; if (!o) return;
        if (o.qty) return qtySheet(o);
        if (o.currency !== "diamonds") return buyOffer(o, 1, el);
        K.confirm({ title: "تأیید خرید", icon: "gem", ok: "تأیید و خرید",
          html: kv([["آیتم", K.esc(o.title)], ["می‌گیری", K.esc(o.gets)], ["مبلغ", money("diamonds", o.price)], ["الماس تو", money("diamonds", K.res ? K.res.diamonds : 0)]]) })
          .then(function (yes) { if (yes) buyOffer(o, 1, el); });
      });
      K.on(root, "gem", function (el) {
        var g = scr.data.gem;
        K.confirm({ title: "خرید کایجوی جمی", icon: "gem", ok: "تأیید و خرید",
          html: kv([["هیولا", K.esc(g.name) + " (" + K.esc(K.rarLabel(g.rarity)) + ")"], ["قیمت", money("diamonds", g.price)], ["الماس تو", money("diamonds", K.res ? K.res.diamonds : 0)]]) })
          .then(function (yes) {
            if (!yes || busy) return;
            busy = true;
            K.api.post("shop/store/gem/", {}, el).then(function (r) {
              busy = false;
              K.invalidate("profile/creatures/");
              scr.set(r.store);
              K.reward({ title: "کایجوی جمی خریده شد", text: "به کلکسیونت اضافه شد؛ می‌تونی فعالش کنی یا برای ادغام استفاده‌ش کنی.", creatures: [r.creature], icon: "gem" });
            }, function () { busy = false; scr.refresh(); });
          });
      });
      K.on(root, "pack", function (el) {
        var it = scr.data.items.filter(function (x) { return x.id === +el.dataset.id; })[0]; if (!it) return;
        K.confirm({ title: "خرید بسته‌ی ویژه", icon: "gift", ok: "تأیید و خرید",
          html: kv([["بسته", K.esc(it.title)], ["محتویات", K.esc(it.gets)],
            ["قیمت", [it.price_coins ? money("coins", it.price_coins) : "", it.price_diamonds ? money("diamonds", it.price_diamonds) : ""].filter(Boolean).join(" + ") || "رایگان"]]) })
          .then(function (yes) {
            if (!yes || busy) return;
            busy = true;
            K.api.post("shop/store/item/", { id: it.id }, el).then(function (r) {
              busy = false;
              K.invalidate("profile/creatures/", "profile/equipment/");
              scr.set(r.store);
              K.reward({ title: "خرید موفق", text: r.title, extra: r.notes.map(function (n) { return K.esc(n); }) });
            }, function () { busy = false; scr.refresh(); });
          });
      });
      K.on(root, "shield", function (el) {
        var kind = tabs.shield, t = scr.data.shield[kind].tiers.filter(function (x) { return x.tier === el.dataset.tier; })[0]; if (!t) return;
        K.confirm({ title: kind === "arena" ? "خرید سپر آرنا" : "خرید سپر گروه", icon: "shield", ok: "تأیید و خرید",
          html: kv([["نوع سپر", K.esc(t.label)], ["مدت", K.n(t.hours) + " ساعت"], ["هزینه", money("diamonds", t.diamonds)], ["الماس تو", money("diamonds", K.res ? K.res.diamonds : 0)]]) })
          .then(function (yes) {
            if (!yes || busy) return;
            busy = true;
            K.api.post("shop/store/shield/", { kind: kind, tier: t.tier }, el).then(function (r) {
              busy = false; K.haptic("ok"); K.toast("سپر فعال شد. الان " + K.dur(r.left) + " محافظت داری.", "ok"); scr.set(r.store);
            }, function () { busy = false; });
          });
      });
      K.on(root, "gold", function (el) {
        var p = scr.data.gold_packs.filter(function (x) { return x.idx === +el.dataset.idx; })[0]; if (!p) return;
        K.confirm({ title: "خرید طلا با الماس", icon: "coin", ok: "تأیید و خرید",
          html: kv([["می‌گیری", money("coins", p.gold)], ["می‌دی", money("diamonds", p.diamonds)], ["الماس تو", money("diamonds", K.res ? K.res.diamonds : 0)]]) })
          .then(function (yes) {
            if (!yes || busy) return;
            busy = true;
            K.api.post("shop/store/gold/", { idx: p.idx }, el).then(function (r) {
              busy = false; K.reward({ title: "طلا گرفتی", coins: r.gold, icon: "coin" });
            }, function () { busy = false; });
          });
      });
      return scr.load();
    }
  });

  // ───────────────────────── exchange + recycling ─────────────────────────
  K.screen("sh_exchange", {
    title: "صرافی", tab: "more",
    render: function (root, params, ctx) {
      if (params.tab) { tabs.ex = params.tab; params.tab = null; }
      var amount = { buy_dna: 0, buy_gold: 0 }, deal = null, seq = 0, timer = null, rec = null, picked = {}, swapping = false;
      var scr = live(root, ctx, "shop/exchange/", function (d) {
        var html = seg([["swap", "طلا و DNA", "swap"], ["recycle", "بازیافت تجهیزات", d.recycle.locked ? "lock" : "recycle"]], tabs.ex, "tab");
        root.innerHTML = html + '<div id="shx-body"></div>';
        if (tabs.ex === "swap") drawSwap(d); else drawRecycle(d);
      });
      function body() { return root.querySelector("#shx-body"); }

      // ── gold ↔ DNA ──
      function drawSwap(d) {
        var dir = tabs.exDir, buyDna = dir === "buy_dna";
        if (!amount[dir]) amount[dir] = buyDna ? d.presets.buy_dna[0].dna : d.presets.buy_gold[0].gold;
        var presets = buyDna ? d.presets.buy_dna.map(function (p) { return [p.dna, K.n(p.dna) + " DNA"]; }) : d.presets.buy_gold.map(function (p) { return [p.gold, K.n(p.gold) + " طلا"]; });
        body().innerHTML = banner(d.img, "صرافی طلا و DNA", "رفت و برگشتش ضرر داره؛ فقط وقتی لازمه تبدیل کن") +
          seg([["buy_dna", "خرید DNA", "dna"], ["buy_gold", "خرید طلا", "coin"]], dir, "dir") +
          '<div class="callout mb">' + K.ic("info") + "<div>" + (buyDna ? "برای هر ۱ DNA، " + K.n(d.rate_buy) + " طلا می‌دی." : "برای هر ۱ DNA که بدی، " + K.n(d.rate_sell) + " طلا می‌گیری.") + "</div></div>" +
          '<div class="panel pad"><div class="sm muted">' + (buyDna ? "چند DNA می‌خوای بگیری؟" : "چند طلا می‌خوای بگیری؟") + "</div>" +
          '<div class="shx-amt"><button class="iconbtn" data-step="-1" aria-label="کمتر">' + K.ic("minus") + '</button><input class="input num" id="shx-in" inputmode="numeric" autocomplete="off" value="' + amount[dir] + '">' +
          '<button class="iconbtn" data-step="1" aria-label="بیشتر">' + K.ic("plus") + "</button></div>" +
          '<div class="row" style="padding-bottom:0">' + presets.map(function (p) { return '<button class="chip" data-amt="' + p[0] + '">' + p[1] + "</button>"; }).join("") + "</div></div>" +
          '<div class="panel pad mt" id="shx-prev"></div>' +
          '<button class="btn primary block lg mt" id="shx-go" disabled>' + K.ic("swap") + "مبادله</button>";
        var input = root.querySelector("#shx-in");
        input.oninput = function () { amount[dir] = toInt(input.value); preview(260); };
        preview(0);
      }
      function setAmount(v) {
        var dir = tabs.exDir, input = root.querySelector("#shx-in");
        amount[dir] = Math.max(1, v); if (input) input.value = amount[dir];
        preview(180);
      }
      function preview(delay) {
        var dir = tabs.exDir, box = root.querySelector("#shx-prev"), go = root.querySelector("#shx-go"), mine = ++seq;
        if (!box) return;
        deal = null; go.disabled = true;
        clearTimeout(timer);
        if (!amount[dir]) { box.innerHTML = '<div class="muted sm center">یه عدد وارد کن.</div>'; return; }
        box.classList.add("shx-wait");
        timer = setTimeout(function () {
          if (mine !== seq || !ctx.alive()) return;
          K.api.get("shop/exchange/preview/?direction=" + dir + "&amount=" + amount[dir]).then(function (p) {
            if (mine !== seq || !ctx.alive() || !document.body.contains(box)) return;
            deal = p; box.classList.remove("shx-wait");
            var buyDna = p.direction === "buy_dna";
            box.innerHTML = '<div class="shx-deal"><div><small>می‌دی</small>' + (buyDna ? K.amounts({ coins: p.gold }) : K.amounts({ dna: p.dna })) + '</div><span class="faint">' + K.ic("chevron") + "</span>" +
              "<div><small>می‌گیری</small>" + (buyDna ? K.amounts({ dna: p.dna }) : K.amounts({ coins: p.gold })) + "</div></div>" +
              (p.enough ? "" : '<div class="t-bad sm center" style="margin-top:8px">' + K.ic("warn") + " " + (buyDna ? "طلای کافی نداری." : "DNA کافی نداری.") + "</div>");
            go.disabled = !p.enough;
          }).catch(function (err) {
            if (mine !== seq || !document.body.contains(box)) return;
            box.classList.remove("shx-wait");
            box.innerHTML = '<div class="t-bad sm center">' + K.ic("warn") + " " + K.esc(err.message) + "</div>";
          });
        }, delay);
      }
      function doSwap(el) {
        var p = deal; if (!p) return;
        var buyDna = p.direction === "buy_dna";
        K.confirm({ title: "تأیید مبادله", icon: "swap", ok: "مبادله کن",
          html: kv([["می‌دی", buyDna ? K.amounts({ coins: p.gold }) : K.amounts({ dna: p.dna })], ["می‌گیری", buyDna ? K.amounts({ dna: p.dna }) : K.amounts({ coins: p.gold })]]) })
          .then(function (yes) {
            if (!yes || swapping) return;
            swapping = true;
            K.api.post("shop/exchange/do/", { direction: p.direction, dna: p.dna }, el).then(function (r) {
              swapping = false;
              var o = { title: "مبادله انجام شد", icon: "swap" };
              if (r.direction === "buy_dna") o.dna = r.dna; else o.coins = r.gold;
              scr.set(r.exchange); K.reward(o);
            }, function () { swapping = false; preview(0); });
          });
      }

      // ── gear → tickets ──
      function drawRecycle(d) {
        if (d.recycle.locked) { body().innerHTML = hallLocked(d.recycle.req); return; }
        if (!rec) {
          body().innerHTML = K.skeleton("grid");
          K.api.get("shop/recycle/").then(function (r) { rec = r; if (ctx.alive() && tabs.ex === "recycle") drawRecycle(d); })
            .catch(function (err) { if (ctx.alive() && tabs.ex === "recycle") body().innerHTML = K.state("warn", "باز نشد", err.message); });
          return;
        }
        var ids = {}; rec.items.forEach(function (it) { ids[it.id] = it; });
        Object.keys(picked).forEach(function (id) { if (!ids[id]) delete picked[id]; });
        var shown = tabs.rec ? rec.items.filter(function (it) { return it.rarity === tabs.rec; }) : rec.items;
        var sel = rec.items.filter(function (it) { return picked[it.id]; }), gain = sel.reduce(function (a, it) { return a + it.tickets; }, 0);
        var html = banner(rec.img, "بازیافت تجهیزات", "تجهیزات اضافه رو با بلیط باکس ژنتیکی عوض کن") +
          '<div class="tiles mb"><div class="panel info"><span class="ic t-good">' + K.ic("ticket") + "</span><div><small>بلیط‌های تو</small><b>" + K.n(rec.tickets) + "</b></div></div>" +
          '<div class="panel info"><span class="ic t-accent">' + K.ic("recycle") + "</span><div><small>" + rec.values.map(function (v) { return K.esc(K.rarLabel(v.rarity)) + " = " + v.tickets; }).join(" · ") + "</small><b>بلیط</b></div></div></div>" +
          '<p class="lead">هر بلیط یه باکس ژنتیکی رایگانه. تجهیزاتی که پوشیده شدن اینجا نیستن.</p>';
        if (!rec.items.length) { body().innerHTML = html + K.state("chest", "چیزی برای بازیافت نداری", "فقط تجهیزات افسانه‌ای و اساطیری که پوشیده نشدن قابل بازیافتن."); return; }
        html += '<div class="row"><button class="chip ' + (tabs.rec ? "" : "on") + '" data-rar="">همه (' + rec.items.length + ")</button>" + rec.values.map(function (v) {
          var n = rec.items.filter(function (it) { return it.rarity === v.rarity; }).length;
          return '<button class="chip ' + (tabs.rec === v.rarity ? "on" : "c-" + v.rarity) + '" data-rar="' + v.rarity + '">' + K.esc(K.rarLabel(v.rarity)) + " (" + n + ")</button>";
        }).join("") + "</div>" +
          '<div class="btns mb"><button class="btn sm" data-act="rec-all">' + K.ic("check") + 'انتخاب همه</button><button class="btn sm" data-act="rec-clear">' + K.ic("close") + "پاک‌کردن</button></div>" +
          '<div class="grid">' + shown.map(function (it) {
            return '<div class="shx-pick">' + K.itemTile(it, { sel: !!picked[it.id], attrs: 'data-act="rec-pick"' }) + '<span class="shx-tk">' + K.ic("ticket") + it.tickets + "</span></div>";
          }).join("") + "</div>" +
          '<div class="shx-foot"><button class="btn gold block lg" data-act="rec-go"' + (sel.length ? "" : " disabled") + ">" + K.ic("recycle") +
          (sel.length ? "تبدیل " + K.n(sel.length) + " تجهیز به " + K.n(gain) + " بلیط" : "چند تا تجهیز انتخاب کن") + "</button></div>";
        body().innerHTML = html;
      }
      function redrawRec() { var y = window.scrollY; drawRecycle(scr.data); window.scrollTo(0, y); }

      root.addEventListener("click", function (ev) {
        var el = ev.target.closest("[data-tab],[data-dir],[data-step],[data-amt],[data-rar],#shx-go"); if (!el || !root.contains(el)) return;
        var d = el.dataset;
        if (el.id === "shx-go") return doSwap(el);
        K.haptic();
        if ("tab" in d) { tabs.ex = d.tab; scr.draw(); }
        else if ("dir" in d) { tabs.exDir = d.dir; scr.draw(); }
        else if ("amt" in d) setAmount(+d.amt);
        else if ("step" in d) {
          var cur = amount[tabs.exDir] || 0, unit = cur >= 10000 ? 1000 : cur >= 1000 ? 100 : cur >= 100 ? 10 : 1;
          if (tabs.exDir === "buy_gold") unit = Math.max(unit, scr.data.rate_sell);
          setAmount(cur + unit * +d.step);
        }
        else if ("rar" in d) { tabs.rec = d.rar; redrawRec(); }
      });
      K.on(root, "rec-pick", function (el) { var id = el.dataset.equip; if (picked[id]) delete picked[id]; else picked[id] = 1; K.haptic(); redrawRec(); });
      K.on(root, "rec-all", function () { rec.items.forEach(function (it) { if (!tabs.rec || it.rarity === tabs.rec) picked[it.id] = 1; }); redrawRec(); });
      K.on(root, "rec-clear", function () { picked = {}; redrawRec(); });
      K.on(root, "rec-go", function (el) {
        var sel = rec.items.filter(function (it) { return picked[it.id]; }); if (!sel.length) return;
        var gain = sel.reduce(function (a, it) { return a + it.tickets; }, 0), up = sel.filter(function (it) { return it.level > 1; });
        var warn = up.length ? '<div class="callout warn" style="text-align:right;margin-bottom:10px">' + K.ic("warn") + "<div>این‌ها ارتقا داده شدن: " +
          up.slice(0, 8).map(function (it) { return "«" + K.esc(it.name) + ' <span class="num">+' + it.level + "</span>»"; }).join("، ") + (up.length > 8 ? " و " + (up.length - 8) + " مورد دیگه" : "") + "</div></div>" : "";
        K.confirm({ title: "تبدیل به بلیط", danger: true, icon: "recycle", ok: "بله، تبدیل کن",
          html: warn + "<p>" + K.n(sel.length) + " تجهیز برای همیشه حذف می‌شه و " + K.n(gain) + " بلیط می‌گیری.</p>" })
          .then(function (yes) {
            if (!yes || swapping) return;
            swapping = true;
            K.api.post("shop/recycle/do/", { ids: sel.map(function (it) { return it.id; }) }, el).then(function (r) {
              swapping = false;
              K.invalidate("profile/equipment/"); picked = {}; rec = r.recycle || null;   // the list after the exchange came with the answer
              K.haptic("ok"); K.toast(r.tickets + " بلیط گرفتی. الان " + r.total + " بلیط داری.", "ok");
              scr.draw();
            }, function () { swapping = false; rec = null; scr.draw(); });
          });
      });
      return scr.load();
    }
  });

  // ───────────────────────── energy ─────────────────────────
  K.screen("sh_energy", {
    title: "شارژ انرژی", tab: "more",
    render: function (root, params, ctx) {
      return K.api.get("shop/energy/").then(function (d) {
        var busy = false, fired = false, tick = null;
        function draw() {
          var full = d.energy >= d.max_energy, nextAt = Math.round(Date.now() / 1000 + Number(d.next_in || 0));
          root.innerHTML = '<div class="banner she-top" style="background-image:url(/app/s/img/bg_energy.jpg)"><div class="she-meter"><div class="she-num">' + K.ic("bolt", "f") +
            '<b class="num">' + d.energy + '</b><span class="num">/' + d.max_energy + "</span></div>" + K.bar(d.max_energy ? d.energy / d.max_energy : 0, "gold", "thick") +
            '<div class="sm she-next">' + (full ? '<span class="t-good b">' + K.ic("check") + " انرژیت پره</span>"
              : 'انرژی بعدی تا <span class="timer" data-until="' + nextAt + '" data-done="رسید"></span>') + "</div></div></div>" +
            '<div class="tiles mt"><div class="panel info"><span class="ic t-energy">' + K.ic("clock") + "</span><div><small>هر یک انرژی</small><b>" + K.dur(d.regen_seconds) + "</b></div></div>" +
            '<div class="panel info"><span class="ic t-diamond">' + K.ic("gem") + "</span><div><small>الماس تو</small><b>" + K.n(d.diamonds) + "</b></div></div></div>" +
            '<div class="panel pad mt"><div class="b">شارژ کامل انرژی</div><div class="sm muted" style="margin:2px 0 12px">انرژیت یک‌جا به ' + K.n(d.max_energy) + " می‌رسه.</div>" +
            '<button class="btn gold block lg" data-act="refill"' + (full ? " disabled" : "") + ">" + K.ic("bolt", "f") + "<span>" + (full ? "نیازی به شارژ نیست" : "شارژ کن") + '</span><span class="cost">' + K.ic("gem") + " " + K.n(d.cost) + "</span></button>" +
            (!full && d.diamonds < d.cost ? '<div class="t-bad sm center" style="margin-top:8px">' + K.ic("warn") + " الماس کافی نداری.</div>" : "") + "</div>" +
            (d.subscriber ? '<div class="callout good mt">' + K.ic("crown") + "<div>اشتراک VIP داری: سقف انرژیت " + K.n(d.max_energy) + " هست و سریع‌تر پر می‌شه.</div></div>"
              : '<button class="callout mt she-vip" data-act="vip">' + K.ic("crown") + '<div class="grow" style="text-align:right">با اشتراک VIP سقف انرژی ' + K.n(d.sub_max) + " می‌شه (به جای " + K.n(d.base_max) + ") و سریع‌تر پر می‌شه.</div>" + K.ic("chevron") + "</button>");
          if (tick) tick();
        }
        draw();
        tick = ticker(root, function () { if (fired) return; fired = true; K.after(800, ctx.reload); });
        K.on(root, "vip", function () { K.go("sh_vip"); });
        K.on(root, "refill", function (el) {
          K.confirm({ title: "شارژ کامل انرژی", icon: "bolt", ok: "بله، شارژ کن", cancel: "بی‌خیال",
            html: "<p>انرژیت به " + K.n(d.max_energy) + " پر می‌شه و " + K.n(d.cost) + " الماس ازت کم می‌شه.</p>" })
            .then(function (yes) {
              if (!yes || busy) return;
              busy = true;
              K.api.post("shop/energy/refill/", {}, el).then(function (r) {
                busy = false; K.haptic("ok"); K.toast("انرژی پر شد (" + r.cost + " الماس کم شد). برگرد و ادامه بده.", "ok");
                if (r.panel) { d = r.panel; if (ctx.alive()) draw(); } else ctx.reload();
              }, function () { busy = false; ctx.reload(); });
            });
        });
      });
    }
  });

  // ───────────────────────── VIP (read-only) ─────────────────────────
  K.screen("sh_vip", {
    title: "اشتراک VIP", tab: "more",
    render: function (root, params, ctx) {
      return K.api.get("shop/vip/").then(function (d) {
        var html = banner(d.img, "اشتراک‌های ویژه", "انرژی بیشتر، رشد سریع‌تر") +
          '<div class="panel pad ' + (d.active ? "gold" : "") + '"><div class="flex"><span class="ico-box lg ' + (d.active ? "t-gold" : "faint") + '">' + K.ic("crown") + '</span><div class="grow"><div class="sm muted">وضعیت اشتراک تو</div>' +
          '<div class="b" style="font-size:16px">' + (d.active ? K.esc(d.tier_name) : "عادی (بدون اشتراک فعال)") + "</div></div></div>" +
          (d.active ? '<div class="flex between mt sm"><span class="muted">زمان باقی‌مونده</span><span class="b">' + K.n(d.days_left) + " روز و " + K.n(d.hours_left) + " ساعت</span></div>" +
            '<div class="flex between sm" style="margin-top:4px"><span class="muted">تا پایان</span><span class="timer" data-fmt="long" data-until="' + d.until + '" data-done="تموم شد"></span></div>' : "") + "</div>";
        html += d.tiers.map(function (t) {
          var mine = d.active && d.tier === t.key;
          return '<div class="h2">' + K.ic("crown") + K.esc(t.name) + " (" + K.n(t.days) + " روزه)" + (mine ? ' <span class="tag" style="color:var(--good)">' + K.ic("check") + "فعال</span>" : "") + "</div>" +
            '<div class="panel shv-tier k-' + K.esc(t.key) + '"><div class="list">' + t.perks.map(function (p) {
              return '<div><span class="ic">' + K.ic("check") + '</span><span class="t">' + K.esc(p) + "</span></div>";
            }).join("") + '</div><div class="shv-price"><span class="muted sm">قیمت</span><span class="b">' + K.n(t.price_toman) + " تومان</span></div></div>";
        }).join("");
        html += '<div class="callout mt">' + K.ic("info") + "<div>خرید و تمدید اشتراک از داخل ربات انجام می‌شه: «فروشگاه» ← «اشتراک VIP». با خرید دوباره، مدتش به اشتراک فعلیت اضافه می‌شه.</div></div>";
        root.innerHTML = html;
        var fired = false;
        ticker(root, function () { if (fired) return; fired = true; K.after(1500, ctx.reload); });
      });
    }
  });
})(window.K);
