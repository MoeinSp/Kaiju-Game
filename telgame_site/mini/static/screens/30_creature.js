/* Looking after one creature and its gear.
   Screens: cr_parts (body parts), cr_feed (XP capsules), cr_devour (eat spare creatures), cr_gear (the 4
   equipment slots), cr_forge (the blacksmith list), cr_item (one piece: forge with gold / merge a twin /
   same-slot fusion).
   Entry points: the «upgrade paths» block on the creature screen, buttons on the item screen and the
   «آهنگری» tile in "base".
   API: creature/…  — every number shown here (costs, before → after, chances, XP) comes from the server.
   Every action answers with the fresh screen, so nothing is fetched twice; long lists are paged. */
(function (K) {
  "use strict";

  K.addIcons({
    wing: '<path d="M3 6c6-2 12 0 18 6-3 0-5 1-6 3-2-1-4-1-5 1-1-2-3-3-5-3 1-2 0-5-2-7z"/>',
    fang: '<path d="M6 4h12l-2 6-2 9-2-7-2 7-2-9z"/>',
    pencil: '<path d="M4 20l1-4.500L16 4.500l3.500 3.500L8.500 19zM14 6.500l3.500 3.500"/>',
    anvil: '<path d="M5 8h14v3c-3 0-5 1.500-5 4h-4c0-2.500-2-4-5-4zM8 19h8M10 15v4M14 15v4"/>',
    link: '<path d="M10 14a4 4 0 0 0 5.700 0l3-3a4 4 0 0 0-5.700-5.700l-1 1M14 10a4 4 0 0 0-5.700 0l-3 3a4 4 0 0 0 5.700 5.700l1-1"/>',
    jaws: '<path d="M3 9c3-4 15-4 18 0l-3 1-1.500 3-2-3-2.500 3-2.500-3-2 3L6 10zM3 15c3 4 15 4 18 0"/>'
  });

  var HALL_FORGE = 3;   // SECTION_HALL_REQ["blacksmith"] — the server enforces it
  var PART_ICON = { wings: "wing", armor: "shield", fangs: "fang", poison: "drop" };
  var PART_COLOR = { wings: "#62e6b4", armor: "#5aa9ff", fangs: "#ff9a3d", poison: "#b58cff" };
  var TIER_COLOR = { small: "var(--common)", medium: "var(--rare)", large: "var(--legendary)" };
  var LIST_PAGE = 40;      // rows of a long list drawn at a time
  var step = 1;            // ×1 / ×5 / ×10, remembered while the app is open
  var forgeSlot = "";      // slot filter of the blacksmith list
  var panels = {};         // creature id → last creature/panel/ payload (dropped whenever it may be stale)

  // ───────────────────────── small helpers ─────────────────────────
  function noop() {}
  function fmt(x) { return Number(x || 0).toLocaleString("en-US"); }
  /* the collection is big (hundreds of rows): after an action on ONE creature its row is patched in the
     cached list instead of throwing the whole list away */
  function patchCreature(cr) {
    var d = K.cache["profile/creatures/"], i, k;
    if (!d || !cr) return;
    for (i = 0; i < d.creatures.length; i++) {
      if (cr.active && d.creatures[i].id !== cr.id) d.creatures[i].active = false;
      if (d.creatures[i].id === cr.id) for (k in d.creatures[i]) if (cr[k] !== undefined) d.creatures[i][k] = cr[k];
    }
  }
  function keepPanel(d) { if (d && d.creature && d.parts) { d._at = Date.now(); panels[d.creature.id] = d; } }
  function panelOf(id) { var d = panels[id]; return d && Date.now() - d._at < 60000 ? d : null; }
  /* gold changes on every screen of the app; K.res is always the latest balance the server reported */
  function canPay(cost, fallback) { return K.res ? Number(K.res.coins) >= Number(cost) : !!fallback; }
  /* gear changed hands or a creature is gone: both cached lists are stale */
  function touched() { panels = {}; K.invalidate("profile/creatures/", "profile/equipment/"); }
  /* before → after (RTL: the chevron points at the new value) */
  function ba(before, after, cls) {
    return '<span class="cr-ba"><span>' + before + "</span>" + K.ic("chevron") + '<b class="' + (cls || "t-good") + '">' + after + "</b></span>";
  }
  function plus(x) { return '<span class="num">+' + fmt(x) + "</span>"; }
  function powerText(a, b) { return a === b ? "قدرت عوض نشد" : "قدرت از " + fmt(a) + " شد " + fmt(b); }
  function lv(x) { return '<span class="num">+' + Number(x) + "</span>"; }
  function cost(x) { return '<span class="cost">' + K.ic("coin") + K.short(x) + "</span>"; }
  function offBtn(cls, label, why) { return '<button class="btn ' + cls + ' cr-off" data-act="why" data-why="' + K.esc(why) + '">' + label + "</button>"; }
  function bindWhy(root) { K.on(root, "why", function (el) { K.haptic("err"); K.toast(el.dataset.why, "err"); }); }
  function xpBlock(c) {
    return '<div class="cr-xp"><div class="flex between xs"><span class="t-xp b">' + K.ic("up") + " تجربه</span><span class=\"muted\">" +
      (c.maxed ? "به سقف سطح رسیده" : K.n(c.xp) + " / " + K.n(c.xp_need) + " XP") + "</span></div>" + K.bar(c.maxed ? 1 : (c.xp_need ? c.xp / c.xp_need : 0), c.maxed ? "gold" : "") + "</div>";
  }
  /* the creature on top of every screen of this module: art, level against its ceiling, power */
  function creatureHead(c, extra, flash) {
    return '<div class="panel pad cr-head' + (flash ? " cr-flash" : "") + '"><div class="fighter ' + c.rarity + '"><img class="tile ' + c.rarity + '" src="' + c.img + '" alt=""><div class="grow"><div class="nm cut">' + K.esc(c.name) + "</div>" +
      '<div class="sm muted">' + K.stars(c.star) + ' سطح <b class="num" style="color:var(--text)">' + Number(c.level) + '</b><span class="num"> / ' + Number(c.max_level) + "</span></div></div>" +
      '<span class="pw">' + K.ic("power") + K.n(c.power) + "</span></div>" + (extra || "") + "</div>";
  }
  function itemRow(it, sub, right) {
    return '<img class="th cr-th c-' + it.rarity + '" loading="lazy" decoding="async" src="' + it.img + '" alt=""><span class="t"><span class="cut" style="display:block">' + K.esc(it.name) + ' <b class="c-' + it.rarity + '">' + lv(it.level) + "</b></span><small>" + sub + "</small></span>" + (right || "");
  }
  function itemSub(it) { return K.esc(K.rarLabel(it.rarity)) + (it.bonus ? " · " + K.esc(it.bonus) : "") + (it.on ? " · روی " + K.esc(it.on) : ""); }
  function rarChips(counts, current, act, allCount) {
    var ro = (K.meta.rarity_order || []).slice().reverse().filter(function (r) { return counts[r]; });
    if (ro.length < 2) return "";
    return '<div class="row"><button class="chip ' + (current ? "" : "on") + '" data-act="' + act + '" data-r="">همه <span class="num">(' + fmt(allCount) + ")</span></button>" + ro.map(function (r) {
      return '<button class="chip ' + (current === r ? "on" : "c-" + r) + '" data-act="' + act + '" data-r="' + r + '">' + K.ic("gem") + K.esc(K.rarLabel(r)) + ' <span class="num">(' + fmt(counts[r]) + ")</span></button>";
    }).join("") + "</div>";
  }
  function moreBtn(left) { return '<button class="btn ghost block mt" data-act="more">نمایش بیشتر <span class="num">(' + fmt(left) + ")</span></button>"; }
  /* a "something good happened" sheet: {icon, title, text, big, chips:[html], rows:[[label, before, after]], button} */
  function celebrate(o) {
    K.sheet('<div class="grab"></div><div class="loot cr-cele' + (o.bad ? " bad" : "") + '">' +
      (o.big != null ? '<div class="cr-big"><small>' + K.esc(o.bigLabel || "سطح") + "</small><b class=\"num\">" + o.big + "</b></div>" : '<div class="burst">' + K.ic(o.icon || "up") + "</div>") +
      "<h3>" + K.esc(o.title) + "</h3>" + (o.text ? "<p>" + K.esc(o.text) + "</p>" : "") +
      ((o.chips || []).length ? '<div class="items">' + o.chips.map(function (h) { return '<span class="it">' + h + "</span>"; }).join("") + "</div>" : "") + "</div>" +
      ((o.rows || []).length ? '<div class="pad" style="margin-top:12px"><div class="panel kv">' + o.rows.map(function (r) { return "<div><span>" + r[0] + "</span><span>" + ba(r[1], r[2]) + "</span></div>"; }).join("") + "</div></div>" : "") +
      '<div class="pad" style="margin-top:16px"><button class="btn primary block" data-close>' + K.esc(o.button || "عالیه") + "</button></div>");
    K.haptic(o.bad ? "err" : "ok");
  }
  function levelUp(done, extraChips) {
    celebrate({ big: Number(done.level_after), title: "رسید به سطح " + done.level_after + "!", text: done.maxed ? "به سقف سطحش رسید. با ادغام و ستاره‌ی بالاتر، سقف هم بالا می‌ره." : "",
      chips: ['<span class="t-xp">' + K.ic("up") + "</span>" + plus(done.xp) + " XP"].concat(extraChips || []),
      rows: [["سطح", K.n(done.level_before), K.n(done.level_after)], ["قدرت", K.n(done.power_before), K.n(done.power_after)]] });
  }
  /* GET creature/panel/ once per creature while it is fresh */
  function loadPanel(id) {
    return panelOf(id) ? Promise.resolve(panelOf(id)) : K.api.get("creature/panel/?id=" + Number(id)).then(function (d) { keepPanel(d); return d; });
  }

  // ═════════════════════════ body parts ═════════════════════════
  K.screen("cr_parts", {
    title: "ارتقای اندام‌ها", tab: "creatures",
    render: function (root, params) {
      return loadPanel(params.id).then(function (d) {
        if (d.steps.indexOf(step) < 0) step = d.steps[0];
        function card(p, flash) {
          var o = null, i, color = PART_COLOR[p.part] || "var(--accent)";
          for (i = 0; i < p.options.length; i++) if (p.options[i].step === step) o = p.options[i];
          var foot, stat, afford = o && canPay(o.cost, o.afford);
          if (p.maxed || !o) {
            stat = '<b class="num">' + K.n(p.stat_now) + "</b>";
            foot = '<span class="tag" style="color:var(--warn)">' + K.ic("lock") + "به سقف رسیده</span>" +
              '<span class="xs muted grow">' + (d.next_star ? "با " + K.n(d.next_star.star) + " ستاره سقف می‌شه " + K.n(d.next_star.part_cap) : "این آخرین سقف این هیولاست") + "</span>";
          } else {
            stat = ba(K.n(p.stat_now), K.n(o.stat_after));
            var label = K.ic("up") + "ارتقا <span class=\"num\">×" + o.buy + "</span>" + cost(o.cost);
            foot = '<span class="tag" style="color:var(--accent)">' + K.ic("power") + plus(o.power_gain) + "</span>" +
              '<span class="xs grow ' + (afford ? "muted" : "t-bad") + '">' + (afford ? (o.buy < o.step ? "فقط " + K.n(o.buy) + " سطح تا سقف مونده" : "") : "طلا کم داری") + "</span>" +
              (afford ? '<button class="btn gold sm" data-act="up" data-part="' + p.part + '">' + label + "</button>"
                        : offBtn("sm", label, "طلا کم داری — این ارتقا " + fmt(o.cost) + " طلا می‌خواد."));
          }
          return '<div class="panel cr-part' + (flash ? " cr-flash" : "") + '" style="--pc:' + color + '">' +
            '<div class="cr-part-top"><span class="ico-box" style="color:' + color + '">' + K.ic(PART_ICON[p.part] || "up") + "</span>" +
            '<div class="grow"><div class="b">' + K.esc(p.label) + '</div><div class="xs muted">سطح <b class="num" style="color:var(--text)">' + Number(p.level) + '</b> از <span class="num">' + Number(p.cap) + "</span></div></div>" +
            '<div class="cr-part-stat"><small>' + K.esc(p.stat_label) + "</small>" + stat + "</div></div>" +
            K.bar(p.cap ? p.level / p.cap : 0, p.maxed ? "gold" : "") +
            '<div class="cr-part-foot">' + foot + "</div></div>";
        }
        function draw(flashPart) {
          var c = d.creature;
          root.innerHTML = creatureHead(c, "", !!flashPart) +
            '<div class="seg mt">' + d.steps.map(function (s) { return '<button class="' + (s === step ? "on" : "") + '" data-act="step" data-s="' + s + '">هر بار <span class="num">×' + s + "</span></button>"; }).join("") + "</div>" +
            d.parts.map(function (p) { return card(p, p.part === flashPart); }).join("") +
            '<div class="callout mt">' + K.ic("info") + "<span>سقف هر اندام برای این هیولا <b>" + K.n(d.part_cap) + "</b> ـه و با طلا بالا می‌ره (انرژی نمی‌خواد). " +
            (d.next_star ? "هر ستاره‌ی بالاتر سقف رو بیشتر می‌کنه: با " + K.n(d.next_star.star) + " ستاره، اندام‌ها تا " + K.n(d.next_star.part_cap) + " و سطح تا " + K.n(d.next_star.max_level) + " می‌رن." : "این هیولا به آخرین ستاره رسیده.") + "</span></div>";
        }
        draw();
        bindWhy(root);
        K.on(root, "step", function (el) { K.haptic(); step = +el.dataset.s; draw(); });
        K.on(root, "up", function (el) {
          var part = el.dataset.part;
          K.api.post("creature/part/", { id: d.creature.id, part: part, count: step }, el).then(function (r) {
            var done = r.done, label = "";
            d = r; keepPanel(r); patchCreature(r.creature); draw(part); K.haptic("ok");
            d.parts.forEach(function (p) { if (p.part === part) label = p.label; });
            K.toast(label + " رسید به سطح " + done.new_level + " · قدرت +" + fmt(done.power_after - done.power_before), "ok");
          }).catch(noop);
        });
      });
    }
  });

  // ═════════════════════════ feeding (XP capsules) ═════════════════════════
  K.screen("cr_feed", {
    title: "تغذیه", tab: "creatures",
    render: function (root, params) {
      return K.api.get("creature/feed/?id=" + Number(params.id)).then(function (d) {
        function hint(p) { return !p ? "" : p.levels ? "می‌رسه به سطح " + K.n(p.level_after) : "+" + fmt(p.xp) + " XP"; }
        function draw(flash) {
          var c = d.creature, html;
          html = creatureHead(c, xpBlock(c) + (c.maxed ? "" : '<div class="xs muted" style="margin-top:6px">تا سقف سطح ' + K.n(c.xp_to_max) + " XP دیگه لازمه.</div>"), flash);
          if (c.maxed) html += '<div class="callout warn mt">' + K.ic("lock") + "<span>این هیولا به سقف سطحش رسیده — تغذیه بی‌فایده‌ست. اول با ادغام ستاره‌ش رو بالا ببر.</span></div>";
          else if (!d.total) html += '<div class="callout mt">' + K.ic("info") + "<span>هیچ غذایی برای تغذیه نداری. از «فروشگاه روزانه» بخر یا از جایزه‌ها بگیر. با «بلعیدن» هم می‌شه هیولاهای اضافه رو به XP تبدیل کرد.</span></div>";
          html += '<div class="h2">' + K.ic("food") + "غذاها</div>";
          html += d.capsules.map(function (row) {
            var can = !c.maxed && row.count > 0, color = TIER_COLOR[row.tier] || "var(--accent)";
            return '<div class="panel cr-cap' + (row.count ? "" : " cr-none") + '"><div class="cr-cap-top"><span class="ico-box" style="color:' + color + '">' + K.ic("food") + "</span>" +
              '<div class="grow"><div class="b">' + K.esc(row.label) + '</div><div class="xs t-xp">' + plus(row.xp) + " XP برای هر کدوم</div></div>" +
              '<div class="cr-cap-n"><b class="num">' + Number(row.count) + "</b><small>عدد</small></div></div>" +
              (can ? '<div class="cr-cap-btns"><div><button class="btn sm block" data-act="feed" data-kind="one" data-tier="' + row.tier + '">' + K.ic("plus") + 'یکی بده</button><small>' + hint(row.one) + "</small></div>" +
                     '<div><button class="btn sm block" data-act="feed" data-kind="allt" data-tier="' + row.tier + '">همه <span class="num">(' + Number(row.count) + ")</span></button><small>" +
                     (row.all ? (row.all.eaten < row.count ? K.n(row.all.eaten) + " تا می‌خوره · " : "") + hint(row.all) : "") + "</small></div></div>" : "") + "</div>";
          }).join("");
          if (d.all) {
            var a = d.all;
            html += '<div class="panel glow pad mt"><div class="b">' + K.ic("food") + " تغذیه با همه</div>" +
              '<div class="panel kv mt"><div><span>غذایی که می‌خوره</span><span>' + K.n(a.eaten) + " از " + K.n(d.total) + "</span></div>" +
              '<div><span>تجربه</span><span class="t-xp">' + plus(a.xp) + " XP</span></div>" +
              "<div><span>سطح</span><span>" + (a.levels ? ba(K.n(c.level), K.n(a.level_after)) : K.n(c.level)) + "</span></div>" +
              "<div><span>قدرت</span><span>" + (a.power_after !== c.power ? ba(K.n(c.power), K.n(a.power_after)) : K.n(c.power)) + "</span></div></div>" +
              '<button class="btn primary block mt" data-act="feed" data-kind="all">' + K.ic("food") + "تغذیه با همه</button>" +
              '<p class="note" style="margin-top:10px">بزرگ‌ها رو اول می‌ده و به سقف سطح که رسید متوقف می‌شه؛ هیچ غذایی هدر نمی‌ره.</p></div>';
          }
          root.innerHTML = html;
        }
        draw();
        K.on(root, "feed", function (el) {
          var body = { id: d.creature.id, kind: el.dataset.kind };
          if (el.dataset.tier) body.tier = el.dataset.tier;
          K.api.post("creature/feed/do/", body, el).then(function (r) {
            var done = r.done;
            d = r; delete panels[r.creature.id]; patchCreature(r.creature); draw(true);
            if (done.levels > 0) levelUp(done, [K.ic("food") + K.n(done.eaten) + " تا غذا"]);
            else { K.haptic("ok"); K.toast(done.eaten + " تا غذا خورد · +" + fmt(done.xp) + " XP", "ok"); }
            (done.missions || []).forEach(function (m) { K.toast("مأموریت «" + m + "» تکمیل شد!", "ok"); });
          }).catch(noop);
        });
      });
    }
  });

  // ═════════════════════════ devouring spare creatures ═════════════════════════
  K.screen("cr_devour", {
    title: "بلعیدن هیولا", tab: "creatures",
    render: function (root, params, ctx) {
      return K.api.get("creature/devour/?id=" + Number(params.id)).then(function (d) {
        var sel = {}, filt = "";
        function chosen() { return d.candidates.filter(function (x) { return sel[x.id]; }); }
        function sumXp(list) { var s = 0; list.forEach(function (x) { s += x.xp; }); return s; }
        function tile(x) { return K.creatureTile(x, { attrs: 'data-act="tick"', sel: !!sel[x.id], flag: "+" + fmt(x.xp) + " XP" }); }
        function foot() {
          var list = chosen(), xp = sumXp(list), enough = xp >= d.need;
          return '<div class="flex between sm"><span class="muted">انتخاب‌شده: <b class="num" style="color:var(--text)">' + list.length + '</b></span><span class="' + (enough ? "t-good" : "t-xp") + ' b">' + plus(xp) + " XP" + (enough ? " · کافیه" : "") + "</span></div>" +
            K.bar(d.need ? xp / d.need : 0, enough ? "good" : "") +
            '<div class="btns mt"><button class="btn" data-act="all">' + (list.length ? "لغو انتخاب‌ها" : "انتخاب خودکار") + '</button><button class="btn danger" data-act="eat"' + (list.length ? "" : " disabled") + ">" + K.ic("jaws") + 'بلعیدن <span class="num">(' + list.length + ")</span></button></div>";
        }
        function draw(flash) {
          var c = d.creature, counts = {}, list, html;
          d.candidates.forEach(function (x) { counts[x.rarity] = (counts[x.rarity] || 0) + 1; });
          if (filt && !counts[filt]) filt = "";
          list = d.candidates.filter(function (x) { return !filt || x.rarity === filt; });
          html = creatureHead(c, xpBlock(c) + (c.maxed ? "" : '<div class="xs muted" style="margin-top:6px">تا سقف سطح ' + K.n(d.need) + " XP دیگه لازمه.</div>"), flash);
          if (c.maxed || d.need <= 0) {
            root.innerHTML = html + K.state("lock", "به سقف سطحش رسیده", "این هیولا دیگه نمی‌تونه هیولا بخوره. برای ادامه‌ی رشد باید با ادغام ستاره‌ش رو بالا ببری تا سقف سطح بالاتر بره.");
            return;
          }
          if (!d.candidates.length) {
            root.innerHTML = html + K.state("claw", "هیولای آزادی نداری", "هیولای فعال و هیولاهایی که سر کار، توی غار یا مأموریت اعزامی‌ان رو نمی‌شه قربانی کرد.");
            return;
          }
          html += '<div class="callout warn mt">' + K.ic("warn") + "<span>هیولاهایی که تیک می‌زنی <b>برای همیشه حذف می‌شن</b> و XPشون به این هیولا می‌رسه. هرچی نایاب‌تر، XP بیشتر. بیشتر از سقف سطح هدر می‌ره، پس فقط تا همون‌جا می‌شه انتخاب کرد.</span></div>" +
            '<div class="h2">' + K.ic("jaws") + "قربانی‌ها</div>" + rarChips(counts, filt, "filt", d.candidates.length) +
            K.grid(list, tile, 60) + '<div class="cr-foot" id="cr-dfoot">' + foot() + "</div>";
          root.innerHTML = html;
        }
        function refoot() { var f = root.querySelector("#cr-dfoot"); if (f) f.innerHTML = foot(); }
        draw();
        K.on(root, "filt", function (el) { K.haptic(); filt = el.dataset.r; draw(); });
        K.on(root, "tick", function (el) {
          var id = +el.dataset.creature;
          if (sel[id]) delete sel[id];
          else {
            // the bot's rule: nothing more once the ticked ones already reach the level cap
            if (sumXp(chosen()) >= d.need) { K.haptic("err"); K.toast("همین‌ها برای رسیدن به سقف سطح کافیه — بیشتر از این هدر می‌ره.", "err"); return; }
            if (chosen().length >= d.max_batch) { K.haptic("err"); K.toast("هر بار حداکثر " + d.max_batch + " هیولا رو می‌شه بلعید.", "err"); return; }
            sel[id] = 1;
          }
          K.haptic(); el.classList.toggle("sel", !!sel[id]); refoot();
        });
        K.on(root, "all", function () {
          K.haptic();
          if (chosen().length) { sel = {}; draw(); return; }
          // like the bot's «انتخاب همه»: the least valuable first, and only up to the level cap
          var ro = K.meta.rarity_order, run = 0, n = 0;
          d.candidates.slice().sort(function (a, b) { return ro.indexOf(a.rarity) - ro.indexOf(b.rarity) || a.star - b.star || a.xp - b.xp; }).forEach(function (x) {
            if (run >= d.need || n >= d.max_batch) return;
            sel[x.id] = 1; run += x.xp; n++;
          });
          filt = ""; draw();
          K.toast("کم‌ارزش‌ترین‌ها تا سقف سطح انتخاب شدن؛ قبل از بلعیدن یه نگاه بنداز.", "ok");
        });
        K.on(root, "eat", function (el) {
          var list = chosen(); if (!list.length) return;
          var ro = K.meta.rarity_order;
          list.sort(function (a, b) { return ro.indexOf(b.rarity) - ro.indexOf(a.rarity) || b.star - a.star || b.xp - a.xp; });
          K.confirm({ title: "بلعیدن " + list.length + " هیولا — مطمئنی؟", danger: true, icon: "jaws", ok: "آره، بلعیده بشن", cancel: "نه، برگرد",
            html: "<p>این هیولاها <b>برای همیشه حذف می‌شن</b> و تجربه‌شون به «" + K.esc(d.creature.name) + "» می‌رسه:</p>" +
              '<div class="cr-eatlist">' + list.slice(0, 8).map(function (x) { return "<div><span class=\"c-" + x.rarity + '">' + K.ic("gem") + "</span><span class=\"grow cut\">" + K.esc(x.name) + '</span><span class="muted num">' + x.star + " ستاره · سطح " + x.level + "</span></div>"; }).join("") +
              (list.length > 8 ? '<div class="muted">… و ' + K.n(list.length - 8) + " هیولای دیگه</div>" : "") + "</div>" +
              '<div class="items"><span class="it t-xp">' + K.ic("up") + '<span style="color:var(--text)">' + plus(sumXp(list)) + " XP</span></span></div>" }).then(function (yes) {
            if (!yes) return;
            K.api.post("creature/devour/do/", { id: d.creature.id, sacrifices: list.map(function (x) { return x.id; }) }, el).then(function (r) {
              var done = r.done;
              d = r; sel = {}; touched();
              if (!ctx.alive()) return;
              draw(true);
              if (done.levels > 0) levelUp(done, [K.ic("jaws") + K.n(done.count) + " هیولا خورده شد"]);
              else { K.haptic("ok"); K.toast(done.count + " هیولا خورده شد · +" + fmt(done.xp) + " XP", "ok"); }
            }).catch(noop);
          });
        });
      });
    }
  });

  // ═════════════════════════ equipment on a creature ═════════════════════════
  K.screen("cr_gear", {
    title: "تجهیزات هیولا", tab: "creatures",
    render: function (root, params, ctx) {
      return K.api.get("creature/gear/?id=" + Number(params.id)).then(function (d) {
        function slotOf(name) { for (var i = 0; i < d.slots.length; i++) if (d.slots[i].slot === name) return d.slots[i]; return null; }
        function draw(flashSlot) {
          var filled = d.slots.filter(function (s) { return s.item; }).length;
          root.innerHTML = creatureHead(d.creature, '<div class="cr-slots-dots">' + d.slots.map(function (s) {
              return '<span class="' + (s.item ? "on c-" + s.item.rarity : "") + '">' + K.ic(K.SLOT_ICON[s.slot] || "chest") + "</span>"; }).join("") +
              '<span class="xs muted grow">' + K.n(filled) + " از " + K.n(d.slots.length) + " جایگاه پره</span></div>", flashSlot === "*") +
            d.slots.map(function (s) {
              var it = s.item, n = s.all, icon = K.SLOT_ICON[s.slot] || "chest";
              var main = it
                ? '<button class="cr-slot-main" data-act="item" data-id="' + it.id + '"><img class="th cr-th c-' + it.rarity + '" src="' + it.img + '" alt=""><span class="grow"><small class="muted">' + K.ic(icon) + " " + K.esc(K.slotLabel(s.slot)) + "</small>" +
                  '<span class="b cut" style="display:block">' + K.esc(it.name) + ' <span class="c-' + it.rarity + '">' + lv(it.level) + "</span></span><small class=\"muted cut\" style=\"display:block\">" + K.esc(it.bonus) + "</small></span>" +
                  '<span class="cr-gain">' + K.ic("power") + plus(d.power - it.power_without) + "</span></button>"
                : '<div class="cr-slot-main"><span class="ico-box lg cr-empty">' + K.ic(icon) + '</span><span class="grow"><small class="muted">' + K.esc(K.slotLabel(s.slot)) + '</small><span class="b" style="display:block;color:var(--muted)">خالی</span>' +
                  '<small class="muted">' + (n ? K.n(n) + " تجهیزات برای این جایگاه داری" : "تجهیزاتی برای این جایگاه نداری") + "</small></span></div>";
              var btns = (n ? '<button class="btn sm ' + (it ? "" : "primary") + '" data-act="slot" data-slot="' + s.slot + '">' + K.ic(it ? "swap" : "plus") + (it ? "عوض کن" : "بپوشون") + ' <span class="num">(' + fmt(n) + ")</span></button>"
                            : offBtn("sm", K.ic("plus") + "بپوشون", "تجهیزات دیگه‌ای برای این جایگاه نداری. از باکس‌ها تجهیزات به‌دست بیار.")) +
                (it ? '<button class="btn sm ghost" data-act="off" data-id="' + it.id + '">' + K.ic("close") + "دربیار</button>" : "");
              return '<div class="panel cr-slot' + (s.slot === flashSlot ? " cr-flash" : "") + '">' + main + '<div class="btns">' + btns + "</div></div>";
            }).join("") +
            '<p class="note">عدد کنار هر تجهیزات یعنی الان چقدر به قدرت این هیولا اضافه کرده.</p>';
        }
        function apply(r, slot, text) { var before = d.power; d = r; touched(); if (!ctx.alive()) return; draw(slot); K.haptic("ok"); K.toast(text + " · " + powerText(before, r.power), "ok"); }
        /* the candidates of one slot: the best for this creature first, more pages and rarity tabs from the server */
        function openSlot(s) {
          var st = { rarity: "", list: s.candidates.slice(), total: s.total, busy: false };
          var box = K.sheet('<div class="grab"></div><div class="pad cr-sheet"><div class="ttl" style="font-size:18px">' + K.esc(K.slotLabel(s.slot)) + " برای " + K.esc(d.creature.name) + "</div>" +
            '<p class="lead" style="margin:4px 0 12px">بهترین‌ها برای این هیولا بالاترن. عدد سمت چپ یعنی قدرتش چقدر عوض می‌شه.</p><div class="cr-slot-list"></div></div>');
          var host = box.querySelector(".cr-slot-list");
          function paint() {
            host.innerHTML = rarChips(s.counts, st.rarity, "srar", s.all) +
              (st.list.length ? '<div class="panel list">' + st.list.map(function (it) {
                var diff = it.power_after - d.power;
                return '<button data-pick="' + it.id + '">' + itemRow(it, itemSub(it), '<span class="v ' + (diff > 0 ? "t-good" : diff < 0 ? "t-bad" : "muted") + '">' + K.ic("power") + ' <span class="num">' + (diff > 0 ? "+" : "") + fmt(diff) + "</span></span>") + "</button>";
              }).join("") + "</div>" : '<div class="sk" style="height:120px"></div>') +
              (st.total > st.list.length ? '<button class="btn ghost block mt' + (st.busy ? " busy" : "") + '" data-more>نمایش بیشتر <span class="num">(' + fmt(st.total - st.list.length) + ")</span></button>" : "");
          }
          function load(reset) {
            if (st.busy) return;
            st.busy = true; if (reset) st.list = []; paint();
            K.api.get("creature/gear/slot/?id=" + d.creature.id + "&slot=" + s.slot + "&skip=" + st.list.length + (st.rarity ? "&rarity=" + st.rarity : "")).then(function (r) {
              st.busy = false; st.list = st.list.concat(r.candidates); st.total = r.total; paint();
            }).catch(function (err) { st.busy = false; paint(); K.toast(err.message, "err"); });
          }
          paint();
          box.querySelector(".cr-sheet").addEventListener("click", function (ev) {
            var chip = ev.target.closest('[data-act="srar"]'), more = ev.target.closest("[data-more]"), b = ev.target.closest("[data-pick]");
            if (chip) { K.haptic(); st.rarity = chip.dataset.r; if (st.rarity) load(true); else { st.list = s.candidates.slice(); st.total = s.total; paint(); } return; }
            if (more) { load(false); return; }
            if (!b) return;
            var id = +b.dataset.pick, name = "";
            st.list.forEach(function (it) { if (it.id === id) name = it.name; });
            K.closeSheet();
            K.api.post("creature/equip/", { id: d.creature.id, item: id }).then(function (r) { apply(r, s.slot, name + " رو پوشید"); }).catch(noop);
          });
        }
        draw();
        bindWhy(root);
        K.on(root, "item", function (el) { K.go("item", { id: +el.dataset.id }); });
        K.on(root, "slot", function (el) { var s = slotOf(el.dataset.slot); if (s) openSlot(s); });
        K.on(root, "off", function (el) {
          K.api.post("creature/unequip/", { item: +el.dataset.id }, el).then(function (r) {
            if (r.slots) apply(r, "", r.item.name + " برگشت توی کوله"); else { touched(); ctx.reload(); }
          }).catch(noop);
        });
      });
    }
  });

  // ═════════════════════════ blacksmith: the list ═════════════════════════
  function risk(chance) {
    return chance > 0 ? '<span class="tag" style="color:var(--warn)">' + K.ic("warn") + "شانس شکست " + K.pct(chance) + "</span>"
                      : '<span class="tag" style="color:var(--good)">' + K.ic("shieldcheck") + "بدون ریسک</span>";
  }
  function smithHead(s) {
    var max = s.max, cap = s.cap;
    return '<div class="panel pad cr-smith"><span class="ico-box lg" style="color:var(--gold)">' + K.ic("anvil") + '</span><div class="grow"><div class="b">آهنگری سطح ' + K.n(s.level) + "</div>" +
      '<div class="xs muted">سقف فعلی تجهیزات: <b class="num" style="color:var(--text)">+' + Number(cap) + "</b>" + (cap < max ? " · آخرین سقف +" + Number(max) : " · آخرین سقف") + "</div>" +
      K.bar(max ? cap / max : 0, "gold") + "</div></div>";
  }
  K.screen("cr_forge", {
    title: "آهنگری", tab: "base",
    render: function (root) {
      return K.api.get("creature/forge/").then(function (d) {
        var shown = LIST_PAGE;
        K.on(root, "base", function () { K.tab("base"); });
        K.on(root, "open", function (el) { K.go("cr_item", { id: +el.dataset.id }); });
        K.on(root, "fslot", function (el) { K.haptic(); forgeSlot = el.dataset.slot; shown = LIST_PAGE; draw(); });
        K.on(root, "more", function () { shown += LIST_PAGE; draw(); });
        if (!d.built) {
          root.innerHTML = K.state("hammer", "اول آهنگری رو بساز", "آهنگری رو از بخش ساختمون‌ها بساز تا بتونی تجهیزاتت رو ارتقا بدی.",
            '<button class="btn primary" data-act="base" style="margin-top:16px">' + K.ic("hall") + "رفتن به پایگاه</button>");
          return;
        }
        function draw() {
          var counts = d.counts || {}, list = d.items.filter(function (it) { return !forgeSlot || it.slot === forgeSlot; });
          var html = smithHead(d) +
            '<div class="callout mt">' + K.ic("info") + "<span>با <b>طلا</b> سطح تجهیزات رو بالا ببر. از یه سطحی به بعد ممکنه شکست بخوره: طلا خرج می‌شه ولی سطح بالا نمی‌ره. توی صفحه‌ی هر تجهیزات «نمونه‌ی مشابه» و «ترکیب هم‌نوع» هم هست.</span></div>" +
            '<div class="row mt"><button class="chip ' + (forgeSlot ? "" : "on") + '" data-act="fslot" data-slot="">همه <span class="num">(' + fmt(d.total) + ")</span></button>" +
            Object.keys(K.meta.slots).map(function (k) { return '<button class="chip ' + (forgeSlot === k ? "on" : "") + '" data-act="fslot" data-slot="' + k + '">' + K.ic(K.SLOT_ICON[k] || "chest") + K.esc(K.slotLabel(k)) + ' <span class="num">(' + fmt(counts[k] || 0) + ")</span></button>"; }).join("") + "</div>" +
            (d.total > d.items.length ? '<p class="note" style="margin:0 0 10px">از ' + K.n(d.total) + " تجهیزاتِ قابل ارتقا، بهترین‌های هر دسته نمایش داده می‌شن. بقیه رو از لیست تجهیزات باز کن.</p>" : "");
          if (!list.length) {
            html += d.items.length ? K.state("search", "چیزی توی این دسته نیست", "هیچ موردی برای ارتقا توی این دسته نداری.")
                  : d.owned ? K.state("anvil", "همه به سقف رسیدن", "همه‌ی تجهیزاتت به سقف فعلی (+" + d.cap + ") رسیدن. برای بالاتر، ساختمون آهنگری رو ارتقا بده.")
                  : K.state("chest", "تجهیزاتی نداری", "از باکس‌ها تجهیزات به‌دست بیار.");
          } else {
            html += '<div class="panel list">' + list.slice(0, shown).map(function (it) {
              var f = it.forge;
              return '<button data-act="open" data-id="' + it.id + '">' + itemRow(it, K.esc(K.slotLabel(it.slot)) + " · " + K.esc(K.rarLabel(it.rarity)) + (it.on ? " · روی " + K.esc(it.on) : "") + (f.fail_chance > 0 ? ' · <span class="t-warn">شکست ' + K.pct(f.fail_chance) + "</span>" : ""),
                '<span class="v cr-price ' + (f.afford ? "t-coin" : "t-bad") + '">' + K.ic("coin") + K.short(f.cost) + '</span><span class="chev">' + K.ic("chevron") + "</span>") + "</button>";
            }).join("") + "</div>" + (list.length > shown ? moreBtn(list.length - shown) : "");
          }
          root.innerHTML = html;
        }
        draw();
      });
    }
  });

  // ═════════════════════════ blacksmith: one piece ═════════════════════════
  K.screen("cr_item", {
    title: "آهنگری", tab: "creatures",
    render: function (root, params, ctx) {
      return K.api.get("creature/item/?id=" + Number(params.id)).then(function (d) {
        var sel = {}, last = null, fuseR = "", fuseShown = LIST_PAGE;
        function selected() { return Object.keys(sel).map(Number); }
        function forgeWhy() {
          var f = d.forge, s = d.smith;
          if (s.locked) return s.locked;
          if (!s.built) return "اول باید آهنگری رو از بخش ساختمون‌ها بسازی.";
          if (f.at_max) return s.cap >= s.max ? "این تجهیزات به آخرین سطح (+" + s.max + ") رسیده." : "به سقف فعلی (+" + s.cap + ") رسیده — برای بالاتر باید ساختمون آهنگری رو ارتقا بدی.";
          if (!f.afford) return "طلا کم داری — این آهنگری " + fmt(f.cost) + " طلا می‌خواد.";
          return "";
        }
        function stamp() {
          if (!last) return "";
          return last.success
            ? '<div class="cr-stamp ok">' + '<span class="cr-stamp-ic">' + K.ic("hammer") + "</span><div><b>ارتقای موفق! " + lv(last.level_after) + "</b><small>قدرت " + ba(K.n(last.power_before), K.n(last.power_after)) + "</small></div></div>"
            : '<div class="cr-stamp bad">' + '<span class="cr-stamp-ic">' + K.ic("close") + "</span><div><b>شکست خورد</b><small>" + K.n(last.cost) + " طلا سوخت و سطح بالا نرفت.</small></div></div>";
        }
        function draw() {
          var it = d.item, f = d.forge, s = d.smith, why = forgeWhy(), open = s.built && !f.at_max, html;
          ctx.setTitle(it.name);
          html = '<div class="panel cr-ihero ' + it.rarity + (last ? (last.success ? " cr-flash" : " cr-shake") : "") + '"><img src="' + it.img + '" alt=""><div class="grow"><div class="b cut">' + K.esc(it.name) + "</div>" +
            '<div class="cr-ilv"><b class="c-' + it.rarity + '">' + lv(it.level) + '</b><span class="muted num"> / +' + Number(s.cap || s.max) + "</span></div>" +
            K.bar(s.max ? it.level / s.max : 0, "gold") +
            '<div class="xs muted cut" style="margin-top:6px">' + K.esc(K.slotLabel(it.slot)) + " · " + K.esc(K.rarLabel(it.rarity)) + " · " + (it.on ? "روی " + K.esc(it.on) : "توی کوله") + '</div></div><span class="pow"><b>' + K.n(it.power) + "</b><small>قدرت</small></span></div>";

          // ── forge with gold ──
          html += '<div class="h2">' + K.ic("hammer") + "ارتقا با طلا</div>" + '<div class="panel pad cr-forge">' + stamp();
          if (open) {
            html += '<div class="cr-lv"><span class="c-' + it.rarity + '">' + lv(it.level) + "</span>" + K.ic("chevron") + '<b class="t-good">' + lv(f.target_level) + "</b></div>" +
              '<div class="panel kv"><div><span>قدرت تجهیزات</span><span>' + ba(K.n(it.power), K.n(f.power_after)) + "</span></div>" +
              '<div><span>اثر الان</span><span class="sm">' + K.esc(it.bonus) + "</span></div>" +
              '<div><span>اثر بعد از ارتقا</span><span class="sm t-good">' + K.esc(f.bonus_after) + "</span></div>" +
              "<div><span>ریسک</span><span>" + risk(f.fail_chance) + "</span></div>" +
              '<div><span>هزینه</span><span class="' + (f.afford ? "t-coin" : "t-bad") + '">' + K.ic("coin") + " " + K.n(f.cost) + "</span></div></div>";
          }
          if (why) html += '<div class="callout warn mt">' + K.ic("lock") + "<span>" + K.esc(why) + "</span></div>";
          if (open) {
            var label = K.ic("hammer") + "ارتقا با طلا" + cost(f.cost);
            html += '<div class="mt">' + (why ? offBtn("lg block", label, why) : '<button class="btn gold lg block" data-act="forge">' + label + "</button>") + "</div>" +
              '<p class="note" style="margin-top:10px">' + (f.fail_chance > 0 ? "اگه شکست بخوره، طلا خرج می‌شه ولی سطح بالا نمی‌ره. راه بی‌ریسک، ارتقا با نمونه‌ی مشابهه." : "این سطح هیچ‌وقت شکست نمی‌خوره.") + "</p>";
          }
          html += "</div>";

          // ── merge an identical piece ──
          if (d.merge && d.dupes.length) {
            var m = d.merge;
            html += '<div class="h2">' + K.ic("box") + "ارتقا با نمونه‌ی مشابه</div>" + '<div class="panel pad"><p class="lead" style="margin:0 0 10px">یه نمونه‌ی هم‌نوع (همین مدل و همین نایابی) مصرف می‌شه و این یکی <b>حتماً</b> یه سطح بالا می‌ره. بدون ریسک.</p>' +
              (m.cost != null ? '<div class="panel kv mb"><div><span>سطح</span><span>' + ba(lv(it.level), lv(f.target_level)) + "</span></div><div><span>هزینه</span><span class=\"" + (m.gold ? "t-bad" : "t-coin") + '">' + K.ic("coin") + " " + K.n(m.cost) + "</span></div></div>" : "") +
              (m.ok ? "" : '<div class="callout warn mb">' + K.ic("lock") + "<span>" + K.esc(m.why) + "</span></div>") +
              '<div class="panel list">' + d.dupes.map(function (x) {
                var b = m.ok ? '<span class="btn sm gold" style="pointer-events:none">مصرف کن</span>' : "";
                return "<" + (m.ok ? 'button data-act="merge" data-id="' + x.id + '"' : "div") + ">" + itemRow(x, x.on ? '<span class="t-warn">روی ' + K.esc(x.on) + "</span>" : "توی کوله", b) + "</" + (m.ok ? "button" : "div") + ">";
              }).join("") + "</div>" +
              (d.dupes_total > d.dupes.length ? '<p class="note" style="margin-top:10px">' + K.n(d.dupes_total) + " نمونه‌ی مشابه داری؛ کم‌ارزش‌ترین‌ها نمایش داده می‌شن.</p>" : "") + "</div>";
          }

          // ── same-slot fusion ──
          if (open && !s.locked && d.fuse.length) {
            var n = selected().length, list = d.fuse.filter(function (x) { return !fuseR || x.rarity === fuseR; });
            html += '<div class="h2">' + K.ic("link") + "ترکیب هم‌نوع</div>" + '<div class="panel pad"><p class="lead" style="margin:0 0 10px">تجهیزات قربانی رو تیک بزن — هر کدوم یه شانس جدا برای یه سطح بالاتره. قربانی هرچی نایاب‌تر باشه شانس موفقیت بیشتره. <b>در هر صورت قربانی مصرف می‌شه.</b></p>' +
              rarChips(d.fuse_counts, fuseR, "frar", d.fuse_total) +
              '<div class="panel list cr-fuse">' + list.slice(0, fuseShown).map(function (x) {
                return '<button class="' + (sel[x.id] ? "on" : "") + '" data-act="tick" data-id="' + x.id + '"><span class="cr-check">' + K.ic("check") + "</span>" +
                  itemRow(x, K.esc(K.rarLabel(x.rarity)) + (x.on ? ' · <span class="t-warn">روی ' + K.esc(x.on) + "</span>" : ""), '<span class="v sm">' + K.pct(x.fail_chance) + ' <small class="muted">خطا</small></span>') + "</button>";
              }).join("") + "</div>" + (list.length > fuseShown ? moreBtn(list.length - fuseShown) : "") +
              (d.fuse_total > d.fuse.length ? '<p class="note" style="margin-top:10px">از ' + K.n(d.fuse_total) + " تجهیزاتِ هم‌نوع، کم‌ارزش‌ترین‌های هر نایابی نمایش داده می‌شن؛ بعد از هر ترکیب لیست تازه می‌شه.</p>" : "") +
              '<div class="btns mt"><button class="btn sm" data-act="fuse-all">' + (n ? "لغو همه" : "انتخاب همه") + '</button><button class="btn sm primary" data-act="fuse"' + (n ? "" : " disabled") + ">" + K.ic("link") + 'ترکیب منتخب <span class="num">(' + n + ")</span></button></div></div>";
          }
          root.innerHTML = html;
        }
        function refresh(r) { d = r; touched(); }
        draw();
        bindWhy(root);
        K.on(root, "frar", function (el) { K.haptic(); fuseR = el.dataset.r; fuseShown = LIST_PAGE; draw(); });
        K.on(root, "more", function () { fuseShown += LIST_PAGE; draw(); });
        K.on(root, "forge", function (el) {
          K.api.post("creature/forge/do/", { item: d.item.id }, el).then(function (r) {
            refresh(r); last = r.done;
            if (!ctx.alive()) return;
            draw(); window.scrollTo(0, 0);
            if (last.success) { K.haptic("ok"); K.toast("ارتقای موفق! سطح +" + last.level_after, "ok"); }
            else { K.haptic("err"); K.toast("شکست خورد — " + fmt(last.cost) + " طلا سوخت.", "err"); }
          }).catch(noop);
        });
        K.on(root, "merge", function (el) {
          var id = +el.dataset.id, x = d.dupes.filter(function (q) { return q.id === id; })[0]; if (!x) return;
          K.confirm({ title: "این نمونه مصرف بشه؟", danger: true, ok: "مصرف کن", icon: "box",
            text: "«" + x.name + " +" + x.level + "»" + (x.on ? " (الان روی " + x.on + ")" : "") + " برای همیشه مصرف می‌شه و «" + d.item.name + "» می‌ره روی +" + d.forge.target_level + "." }).then(function (yes) {
            if (!yes) return;
            K.api.post("creature/merge/", { item: d.item.id, dupe: id }).then(function (r) {
              var done = r.done; refresh(r); last = null; sel = {};
              if (!ctx.alive()) return;
              draw();
              celebrate({ big: "+" + Number(done.level_after), bigLabel: "سطح تجهیزات", title: "ارتقا انجام شد!", chips: done.cost ? ['<span class="t-coin">' + K.ic("coin") + "</span>" + K.n(done.cost) + " طلا خرج شد"] : [],
                rows: [["سطح", lv(done.level_before), lv(done.level_after)], ["قدرت تجهیزات", K.n(done.power_before), K.n(done.power_after)]] });
            }).catch(noop);
          });
        });
        K.on(root, "tick", function (el) {
          var id = +el.dataset.id;
          if (!sel[id] && selected().length >= d.fuse_batch) { K.haptic("err"); K.toast("هر بار حداکثر " + d.fuse_batch + " قربانی.", "err"); return; }
          K.haptic(); if (sel[id]) delete sel[id]; else sel[id] = 1; draw();
        });
        K.on(root, "fuse-all", function () {
          K.haptic();
          if (selected().length) { sel = {}; draw(); return; }
          // like the bot: «انتخاب همه» never ticks gear a creature is wearing
          var pool = d.fuse.filter(function (x) { return !fuseR || x.rarity === fuseR; });
          var spare = pool.filter(function (x) { return !x.on_id; }).slice(0, d.fuse_batch);
          spare.forEach(function (x) { sel[x.id] = 1; });
          if (pool.some(function (x) { return x.on_id; })) K.toast("تجهیزاتِ روی هیولاها انتخاب نشد؛ اگه می‌خوای، دستی تیک بزن.", "err");
          fuseShown = Math.max(fuseShown, spare.length);
          draw();
        });
        K.on(root, "fuse", function (el) {
          var ids = selected(); if (!ids.length) return;
          var picked = d.fuse.filter(function (x) { return sel[x.id]; }), worn = picked.filter(function (x) { return x.on_id; }).length;
          var ro = K.meta.rarity_order, top = "";
          picked.forEach(function (x) { if (!top || ro.indexOf(x.rarity) > ro.indexOf(top)) top = x.rarity; });
          K.confirm({ title: "ترکیب انجام بشه؟", danger: true, ok: "ترکیب کن", icon: "link",
            text: ids.length + " تا تجهیزات قربانی می‌شن و چه موفق بشه چه نه، برنمی‌گردن." + (top ? " نایاب‌ترینشون: " + K.rarLabel(top) + "." : "") + (worn ? " " + worn + " تاشون الان روی هیولاهاته." : "") }).then(function (yes) {
            if (!yes) return;
            K.api.post("creature/fuse/", { item: d.item.id, sacrifices: ids }, el).then(function (r) {
              var done = r.done; refresh(r); last = null; sel = {};
              if (!ctx.alive()) return;
              draw();
              var up = done.level_after !== done.level_before;
              celebrate({ big: up ? "+" + Number(done.level_after) : null, bigLabel: "سطح تجهیزات", icon: "warn", bad: !done.successes, title: done.successes ? "ترکیب انجام شد" : "این بار نگرفت",
                text: done.capped ? "به سقف فعلی آهنگری رسید — برای بالاتر، ساختمون آهنگری رو ارتقا بده." : (done.successes ? "" : "هیچ‌کدوم از شانس‌ها نگرفت و سطح بالا نرفت."),
                chips: ['<span class="t-good">' + K.ic("check") + "</span>" + K.n(done.successes) + " موفق", '<span class="t-bad">' + K.ic("close") + "</span>" + K.n(done.fails) + " شکست", K.ic("box") + K.n(done.consumed) + " قربانی مصرف شد"],
                rows: up ? [["سطح", lv(done.level_before), lv(done.level_after)], ["قدرت تجهیزات", K.n(done.power_before), K.n(done.power_after)]] : [],
                button: "باشه" });
            }).catch(noop);
          });
        });
      });
    }
  });

  // ═════════════════════════ rename (sheet) ═════════════════════════
  function renameSheet(c, ctx) {
    loadPanel(c.id).then(function (d) {
      var rn = d.rename, free = !rn.cost;
      var box = K.sheet('<div class="grab"></div><div class="pad cr-sheet"><div class="ttl" style="font-size:18px">اسم جدید برای ' + K.esc(c.name) + "</div>" +
        '<p class="lead" style="margin:4px 0 12px">اسم فقط نمایشیه؛ نژاد هیولا (' + K.esc(rn.species) + ") عوض نمی‌شه. حداکثر " + K.n(rn.max_len) + " حرف.</p>" +
        '<input class="input" id="cr-name" maxlength="' + Number(rn.max_len) + '" placeholder="مثلاً: شاه‌نیش" autocomplete="off">' +
        '<div class="callout mt ' + (free ? "good" : "") + '">' + K.ic(free ? "gift" : "gem") + "<span>" +
        (free ? "اولین تغییر نام این هیولا <b>رایگانه</b>. دفعه‌های بعد هر بار " + K.n(rn.step) + " الماس گرون‌تر می‌شه." : "هزینه‌ی این تغییر نام: <b class=\"t-diamond\">" + K.n(rn.cost) + " الماس</b> (هر بار " + K.n(rn.step) + " تا گرون‌تر می‌شه).") + "</span></div>" +
        '<button class="btn primary block mt" data-go>' + K.ic("pencil") + "ثبت اسم" + (free ? "" : '<span class="cost">' + K.ic("gem") + K.n(rn.cost) + "</span>") + "</button></div>");
      var input = box.querySelector("#cr-name");
      function send(name, btn) {
        K.api.post("creature/rename/", { id: c.id, name: name }, btn).then(function (r) {
          K.closeSheet(); delete panels[c.id]; K.haptic("ok");
          // a nickname shows everywhere this creature does (collection, gear rows): refetch both lists
          K.invalidate("profile/creatures/", "profile/equipment/");
          K.toast("اسمش شد «" + r.name + "»" + (r.cost ? " · " + r.cost + " الماس کم شد" : ""), "ok");
          ctx.reload();
        }).catch(noop);
      }
      box.querySelector("[data-go]").onclick = function () {
        var name = input.value.trim(), btn = this;
        if (!name) { K.toast("یه اسم بنویس (خالی نباشه).", "err"); input.focus(); return; }
        if (free) { send(name, btn); return; }
        K.confirm({ title: "تغییر نام با الماس؟", icon: "gem", ok: "تأیید و ثبت", text: "اسم می‌شه «" + name + "» و " + rn.cost + " الماس کم می‌شه." }).then(function (yes) { if (yes) send(name); });
      };
    }).catch(function (err) { K.toast(err.message, "err"); });
  }

  // ═════════════════════════ the creature screen: upgrade paths ═════════════════════════
  /* One tappable tile per way of making THIS creature stronger. `d` (creature/panel/) arrives a moment
     after the screen: until then the tiles show what they are, afterwards what they would do now. */
  function path(act, icon, color, title, sub, lock) {
    return '<button class="cr-path' + (lock ? " dim" : "") + '" data-act="' + act + '" style="--pc:' + color + '"><span class="ico-box">' + K.ic(icon) + '</span><span class="grow"><b>' + title + "</b><small>" + sub + '</small></span><span class="chev">' + K.ic("chevron") + "</span></button>";
  }
  function pathsHtml(c, d) {
    var cr = d && d.creature, h = "", caps = 0, slots = Object.keys((K.meta && K.meta.slots) || {}).length || 4;
    if (d) d.capsules.forEach(function (x) { caps += x.count; });
    h += cr ? '<div class="panel pad cr-level"><div class="flex between"><span class="cr-level-n"><small>سطح</small><b class="num">' + Number(cr.level) + '</b><span class="num muted">/ ' + Number(cr.max_level) + "</span></span>" +
              (cr.maxed ? '<span class="tag" style="color:var(--gold)">' + K.ic("trophy") + "سقف سطح</span>" : '<span class="xs muted">' + K.n(cr.xp) + " / " + K.n(cr.xp_need) + " XP</span>") + "</div>" +
              K.bar(cr.maxed ? 1 : (cr.xp_need ? cr.xp / cr.xp_need : 0), cr.maxed ? "gold" : "", "thick") +
              (cr.poison ? '<div class="xs muted" style="margin-top:8px">' + K.ic("drop") + " زهر: <b class=\"num\" style=\"color:var(--text)\">" + fmt(cr.poison) + "</b>" + (cr.base_power !== cr.power ? " · قدرت بدون پژوهش: <b class=\"num\" style=\"color:var(--text)\">" + fmt(cr.base_power) + "</b>" : "") + "</div>"
                         : cr.base_power !== cr.power ? '<div class="xs muted" style="margin-top:8px">قدرت بدون پژوهش: <b class="num" style="color:var(--text)">' + fmt(cr.base_power) + "</b></div>" : "") + "</div>"
            : '<div class="sk" style="height:74px"></div>';
    h += c.active ? "" : c.busy ? offBtn("block", K.ic("check") + "فعال کن", "این هیولا الان مشغوله — اول آزادش کن تا بتونی فعالش کنی.")
                                : '<button class="btn primary block" data-act="cr-active">' + K.ic("check") + "انتخاب به عنوان هیولای فعال</button>";
    h += '<div class="cr-paths">' +
      path("cr-feed", "food", "var(--xp)", "تغذیه", !cr ? "با غذا سطحش رو بالا ببر" : cr.maxed ? "به سقف سطح رسیده" : caps ? K.n(caps) + " غذا آماده داری" : "غذایی نداری", cr && (cr.maxed || !caps)) +
      path("cr-parts", "up", "var(--gold)", "اندام‌ها", !d ? "بال، زره، نیش و زهر" : d.parts.map(function (p) { return '<span class="cr-pl" style="color:' + (PART_COLOR[p.part] || "inherit") + '">' + K.ic(PART_ICON[p.part] || "up") + '<i class="num">' + Number(p.level) + "</i></span>"; }).join(""), false) +
      path("cr-gear", "chest", "var(--accent)", "تجهیزات", K.n((c.gear || []).length) + " از " + K.n(slots) + " جایگاه پره", false) +
      path("cr-devour", "jaws", "var(--bad)", "بلعیدن", cr && cr.maxed ? "به سقف سطح رسیده" : "XP از هیولاهای اضافه", cr && cr.maxed) +
      "</div>";
    h += '<button class="btn ghost sm block" data-act="cr-rename">' + K.ic("pencil") + "تغییر نام" + (d ? (d.rename.cost ? '<span class="cost t-diamond">' + K.ic("gem") + K.n(d.rename.cost) + "</span>" : '<span class="cost t-good">رایگان</span>') : "") + "</button>";
    return h;
  }

  // ═════════════════════════ entry points ═════════════════════════
  K.creatureActions.push({
    order: 10,
    render: function (c) { return '<div class="wide cr-hub" data-cr-hub="' + c.id + '">' + pathsHtml(c, panelOf(c.id)) + "</div>"; },
    bind: function (root, c, ctx) {
      bindWhy(root);
      K.on(root, "cr-active", function (el) {
        K.api.post("creature/activate/", { id: c.id }, el).then(function (r) {
          delete panels[c.id]; patchCreature(r.creature); K.haptic("ok"); K.toast(c.name + " هیولای فعالت شد.", "ok"); ctx.reload();
        }).catch(noop);
      });
      K.on(root, "cr-feed", function () { K.go("cr_feed", { id: c.id }); });
      K.on(root, "cr-parts", function () { K.go("cr_parts", { id: c.id }); });
      K.on(root, "cr-gear", function () { K.go("cr_gear", { id: c.id }); });
      K.on(root, "cr-devour", function () { K.go("cr_devour", { id: c.id }); });
      K.on(root, "cr-rename", function () { renameSheet(c, ctx); });
      if (!panelOf(c.id)) loadPanel(c.id).then(function (d) {
        var host = root.querySelector('[data-cr-hub="' + c.id + '"]');
        if (host && ctx.alive()) host.innerHTML = pathsHtml(c, d);
      }).catch(noop);   // the tiles work without the numbers
    }
  });

  K.itemActions.push({
    order: 10,
    render: function (e) {
      return (e.on_id ? '<button class="btn" data-act="cr-unequip">' + K.ic("close") + 'دربیار</button><button class="btn" data-act="cr-equip">' + K.ic("swap") + "بده به یکی دیگه</button>"
                      : '<button class="btn primary wide" data-act="cr-equip">' + K.ic("plus") + "بپوشون به یه هیولا</button>") +
        '<button class="btn gold wide" data-act="cr-smith">' + K.ic("hammer") + "آهنگری و ارتقا</button>";
    },
    bind: function (root, e, ctx) {
      K.on(root, "cr-unequip", function (el) {
        K.api.post("creature/unequip/", { item: e.id, brief: true }, el).then(function () { touched(); K.haptic("ok"); K.toast(e.name + " برگشت توی کوله.", "ok"); ctx.reload(); }).catch(noop);
      });
      K.on(root, "cr-equip", function () {
        K.pickCreature({ title: "روی کدوم هیولا؟", sub: "اگه اون هیولا توی این جایگاه چیزی پوشیده باشه، درمیاد و این جاش می‌ره.", exclude: e.on_id ? [e.on_id] : [] }).then(function (c) {
          if (!c) return;
          K.api.post("creature/equip/", { id: c.id, item: e.id, brief: true }).then(function (r) {
            touched(); K.haptic("ok"); K.toast(c.name + " «" + e.name + "» رو پوشید · " + powerText(c.power, r.power), "ok"); ctx.reload();
          }).catch(noop);
        });
      });
      K.on(root, "cr-smith", function () { K.go("cr_item", { id: e.id }); });
    }
  });

  K.hub("base", { id: "forge", title: "آهنگری", sub: "ارتقای تجهیزات با طلا", icon: "hammer", color: "var(--gold)", go: "cr_forge", order: 45, hall: HALL_FORGE });
})(window.K);
