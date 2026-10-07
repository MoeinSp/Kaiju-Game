/* Looking after one creature and its gear.
   Screens: cr_parts (body parts), cr_feed (XP capsules), cr_gear (the 4 equipment slots),
   cr_forge (the blacksmith list), cr_item (one piece: forge with gold / merge a twin / same-slot fusion).
   Entry points: buttons on the creature screen and on the item screen, and the «آهنگری» tile in "base".
   API: creature/…  — every number shown here (costs, before → after, chances) comes from the server. */
(function (K) {
  "use strict";

  K.addIcons({
    wing: '<path d="M3 6c6-2 12 0 18 6-3 0-5 1-6 3-2-1-4-1-5 1-1-2-3-3-5-3 1-2 0-5-2-7z"/>',
    fang: '<path d="M6 4h12l-2 6-2 9-2-7-2 7-2-9z"/>',
    pencil: '<path d="M4 20l1-4.500L16 4.500l3.500 3.500L8.500 19zM14 6.500l3.500 3.500"/>',
    anvil: '<path d="M5 8h14v3c-3 0-5 1.500-5 4h-4c0-2.500-2-4-5-4zM8 19h8M10 15v4M14 15v4"/>',
    link: '<path d="M10 14a4 4 0 0 0 5.700 0l3-3a4 4 0 0 0-5.700-5.700l-1 1M14 10a4 4 0 0 0-5.700 0l-3 3a4 4 0 0 0 5.700 5.700l1-1"/>'
  });

  var HALL_FORGE = 3;   // SECTION_HALL_REQ["blacksmith"] — the server enforces it
  var PART_ICON = { wings: "wing", armor: "shield", fangs: "fang", poison: "drop" };
  var PART_COLOR = { wings: "#62e6b4", armor: "#5aa9ff", fangs: "#ff9a3d", poison: "#b58cff" };
  var TIER_COLOR = { small: "var(--common)", medium: "var(--rare)", large: "var(--legendary)" };
  var step = 1;            // ×1 / ×5 / ×10, remembered while the app is open
  var forgeSlot = "";      // slot filter of the blacksmith list

  // ───────────────────────── small helpers ─────────────────────────
  function touched() { K.invalidate("profile/creatures/", "profile/equipment/"); }
  /* before → after (RTL: the chevron points at the new value) */
  function ba(before, after, cls) {
    return '<span class="cr-ba"><span>' + before + "</span>" + K.ic("chevron") + '<b class="' + (cls || "t-good") + '">' + after + "</b></span>";
  }
  function plus(x) { return '<span class="num">+' + Number(x || 0).toLocaleString("en-US") + "</span>"; }
  function powerText(a, b) { return a === b ? "قدرت عوض نشد" : "قدرت از " + Number(a).toLocaleString("en-US") + " شد " + Number(b).toLocaleString("en-US"); }
  function lv(x) { return '<span class="num">+' + Number(x) + "</span>"; }
  function cost(x) { return '<span class="cost">' + K.ic("coin") + K.n(x) + "</span>"; }
  function offBtn(cls, label, why) { return '<button class="btn ' + cls + ' cr-off" data-act="why" data-why="' + K.esc(why) + '">' + label + "</button>"; }
  function bindWhy(root) { K.on(root, "why", function (el) { K.haptic("err"); K.toast(el.dataset.why, "err"); }); }
  function creatureHead(c, sub) { return '<div class="panel pad cr-head">' + K.fighter(c, sub || "") + "</div>"; }
  function itemRow(it, sub, right) {
    return '<img class="th cr-th c-' + it.rarity + '" src="' + it.img + '" alt=""><span class="t"><span class="cut" style="display:block">' + K.esc(it.name) + ' <b class="c-' + it.rarity + '">' + lv(it.level) + "</b></span><small>" + sub + "</small></span>" + (right || "");
  }
  function itemSub(it) { return K.esc(K.rarLabel(it.rarity)) + (it.bonus ? " · " + K.esc(it.bonus) : "") + (it.on ? " · روی " + K.esc(it.on) : ""); }
  /* a small "something good happened" sheet: {icon, title, text, chips:[html], rows:[[label, before, after]], button} */
  function celebrate(o) {
    K.sheet('<div class="grab"></div><div class="loot"><div class="burst">' + K.ic(o.icon || "up") + "</div><h3>" + K.esc(o.title) + "</h3>" +
      (o.text ? "<p>" + K.esc(o.text) + "</p>" : "") +
      ((o.chips || []).length ? '<div class="items">' + o.chips.map(function (h) { return '<span class="it">' + h + "</span>"; }).join("") + "</div>" : "") + "</div>" +
      ((o.rows || []).length ? '<div class="pad" style="margin-top:12px"><div class="panel kv">' + o.rows.map(function (r) { return "<div><span>" + r[0] + "</span><span>" + ba(r[1], r[2]) + "</span></div>"; }).join("") + "</div></div>" : "") +
      '<div class="pad" style="margin-top:16px"><button class="btn primary block" data-close>' + K.esc(o.button || "عالیه") + "</button></div>");
    K.haptic("ok");
  }

  // ═════════════════════════ body parts ═════════════════════════
  K.screen("cr_parts", {
    title: "ارتقای اندام‌ها", tab: "creatures",
    render: function (root, params) {
      return K.api.get("creature/panel/?id=" + Number(params.id)).then(function (d) {
        if (d.steps.indexOf(step) < 0) step = d.steps[0];
        function card(p, flash) {
          var o = null, i, color = PART_COLOR[p.part] || "var(--accent)";
          for (i = 0; i < p.options.length; i++) if (p.options[i].step === step) o = p.options[i];
          var foot, stat;
          if (p.maxed || !o) {
            stat = '<b class="num">' + K.n(p.stat_now) + "</b>";
            foot = '<span class="tag" style="color:var(--warn)">' + K.ic("lock") + "به سقف رسیده</span>" +
              '<span class="xs muted grow">' + (d.next_star ? "با ادغام و رسیدن به " + K.n(d.next_star.star) + " ستاره، سقف می‌شه " + K.n(d.next_star.part_cap) : "این آخرین سقف این هیولاست") + "</span>";
          } else {
            stat = ba(K.n(p.stat_now), K.n(o.stat_after));
            var label = K.ic("up") + "ارتقا <span class=\"num\">×" + o.buy + "</span>" + cost(o.cost);
            foot = '<span class="tag" style="color:var(--accent)">' + K.ic("power") + plus(o.power_gain) + " قدرت</span>" +
              '<span class="xs grow ' + (o.afford ? "muted" : "t-bad") + '">' + (o.afford ? (o.buy < o.step ? "فقط " + K.n(o.buy) + " سطح تا سقف مونده" : "") : "طلا کم داری") + "</span>" +
              (o.afford ? '<button class="btn gold sm" data-act="up" data-part="' + p.part + '">' + label + "</button>"
                        : offBtn("sm", label, "طلا کم داری — این ارتقا " + Number(o.cost).toLocaleString("en-US") + " طلا می‌خواد."));
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
          root.innerHTML = creatureHead(c, '<div class="xs muted">سقف سطح: ' + K.n(c.max_level) + (c.maxed ? " · به سقف رسیده" : "") + "</div>") +
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
            d = r; touched(); draw(part); K.haptic("ok");
            d.parts.forEach(function (p) { if (p.part === part) label = p.label; });
            K.toast(label + " رسید به سطح " + done.new_level + " · قدرت +" + Number(done.power_after - done.power_before).toLocaleString("en-US"), "ok");
          }).catch(function () {});
        });
      });
    }
  });

  // ═════════════════════════ feeding (XP capsules) ═════════════════════════
  K.screen("cr_feed", {
    title: "تغذیه", tab: "creatures",
    render: function (root, params) {
      return K.api.get("creature/feed/?id=" + Number(params.id)).then(function (d) {
        function hint(p) { return !p ? "" : p.levels ? "می‌رسه به سطح " + K.n(p.level_after) : "+" + Number(p.xp).toLocaleString("en-US") + " XP"; }
        function draw(flash) {
          var c = d.creature, html;
          html = '<div class="panel pad cr-head' + (flash ? " cr-flash" : "") + '">' + K.fighter(c, '<div class="xs muted">سقف سطح: ' + K.n(c.max_level) + "</div>") +
            '<div class="cr-xp"><div class="flex between xs"><span class="t-xp b">' + K.ic("up") + " تجربه</span><span class=\"muted\">" +
            (c.maxed ? "به سقف سطح رسیده" : K.n(c.xp) + " / " + K.n(c.xp_need) + " XP") + "</span></div>" + K.bar(c.maxed ? 1 : (c.xp_need ? c.xp / c.xp_need : 0), c.maxed ? "gold" : "") +
            (c.maxed ? "" : '<div class="xs muted" style="margin-top:6px">تا سقف سطح ' + K.n(c.xp_to_max) + " XP دیگه لازمه.</div>") + "</div></div>";
          if (c.maxed) html += '<div class="callout warn mt">' + K.ic("lock") + "<span>این هیولا به سقف سطحش رسیده — تغذیه بی‌فایده‌ست. اول با ادغام ستاره‌ش رو بالا ببر.</span></div>";
          else if (!d.total) html += '<div class="callout mt">' + K.ic("info") + "<span>هیچ غذایی برای تغذیه نداری. از «فروشگاه روزانه» بخر یا از جایزه‌ها بگیر.</span></div>";
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
            d = r; touched(); draw(true);
            if (done.levels > 0) {
              celebrate({ icon: "up", title: "رسید به سطح " + done.level_after + "!", text: done.maxed ? "به سقف سطحش رسید." : "",
                chips: ['<span class="t-xp">' + K.ic("up") + "</span>" + plus(done.xp) + " XP", K.ic("food") + K.n(done.eaten) + " تا غذا"],
                rows: [["سطح", K.n(done.level_before), K.n(done.level_after)], ["قدرت", K.n(done.power_before), K.n(done.power_after)]] });
            } else {
              K.haptic("ok");
              K.toast(done.eaten + " تا غذا خورد · +" + Number(done.xp).toLocaleString("en-US") + " XP", "ok");
            }
            (done.missions || []).forEach(function (m) { K.toast("مأموریت «" + m + "» تکمیل شد!", "ok"); });
          }).catch(function () {});
        });
      });
    }
  });

  // ═════════════════════════ equipment on a creature ═════════════════════════
  K.screen("cr_gear", {
    title: "تجهیزات هیولا", tab: "creatures",
    render: function (root, params) {
      return K.api.get("creature/gear/?id=" + Number(params.id)).then(function (d) {
        function slotOf(name) { for (var i = 0; i < d.slots.length; i++) if (d.slots[i].slot === name) return d.slots[i]; return null; }
        function draw(flashSlot) {
          var filled = d.slots.filter(function (s) { return s.item; }).length;
          root.innerHTML = creatureHead(d.creature, '<div class="xs muted">' + K.n(filled) + " از " + K.n(d.slots.length) + " جایگاه پره</div>") +
            d.slots.map(function (s) {
              var it = s.item, n = s.candidates.length, icon = K.SLOT_ICON[s.slot] || "chest";
              var main = it
                ? '<button class="cr-slot-main" data-act="item" data-id="' + it.id + '"><img class="th cr-th c-' + it.rarity + '" src="' + it.img + '" alt=""><span class="grow"><small class="muted">' + K.ic(icon) + " " + K.esc(K.slotLabel(s.slot)) + "</small>" +
                  '<span class="b cut" style="display:block">' + K.esc(it.name) + ' <span class="c-' + it.rarity + '">' + lv(it.level) + "</span></span><small class=\"muted cut\" style=\"display:block\">" + K.esc(it.bonus) + "</small></span>" +
                  '<span class="cr-gain">' + K.ic("power") + plus(d.power - it.power_without) + "</span></button>"
                : '<div class="cr-slot-main"><span class="ico-box lg" style="color:var(--faint)">' + K.ic(icon) + '</span><span class="grow"><small class="muted">' + K.esc(K.slotLabel(s.slot)) + '</small><span class="b" style="display:block;color:var(--muted)">خالی</span>' +
                  '<small class="muted">' + (n ? K.n(n) + " تجهیزات برای این جایگاه داری" : "تجهیزاتی برای این جایگاه نداری") + "</small></span></div>";
              var btns = (n ? '<button class="btn sm ' + (it ? "" : "primary") + '" data-act="slot" data-slot="' + s.slot + '">' + K.ic(it ? "swap" : "plus") + (it ? "عوض کن" : "بپوشون") + ' <span class="num">(' + n + ")</span></button>"
                            : offBtn("sm", K.ic("plus") + "بپوشون", "تجهیزات دیگه‌ای برای این جایگاه نداری. از باکس‌ها تجهیزات به‌دست بیار.")) +
                (it ? '<button class="btn sm ghost" data-act="off" data-id="' + it.id + '">' + K.ic("close") + "دربیار</button>" : "");
              return '<div class="panel cr-slot' + (s.slot === flashSlot ? " cr-flash" : "") + '">' + main + '<div class="btns">' + btns + "</div></div>";
            }).join("") +
            '<p class="note">عدد کنار هر تجهیزات یعنی الان چقدر به قدرت این هیولا اضافه کرده.</p>';
        }
        function apply(r, slot, text) { var before = d.power; d = r; touched(); draw(slot); K.haptic("ok"); K.toast(text + " · " + powerText(before, r.power), "ok"); }
        function openSlot(s) {
          var list = s.candidates.slice().sort(function (a, b) { return b.power_after - a.power_after; });
          var box = K.sheet('<div class="grab"></div><div class="pad cr-sheet"><div class="ttl" style="font-size:18px">' + K.esc(K.slotLabel(s.slot)) + " برای " + K.esc(d.creature.name) + "</div>" +
            '<p class="lead" style="margin:4px 0 12px">یکی رو بزن تا بپوشه. عدد سمت چپ یعنی قدرت هیولا چقدر عوض می‌شه.</p><div class="panel list">' +
            list.map(function (it) {
              var diff = it.power_after - d.power;
              return '<button data-pick="' + it.id + '">' + itemRow(it, itemSub(it), '<span class="v ' + (diff > 0 ? "t-good" : diff < 0 ? "t-bad" : "muted") + '">' + K.ic("power") + ' <span class="num">' + (diff > 0 ? "+" : "") + Number(diff).toLocaleString("en-US") + "</span></span>") + "</button>";
            }).join("") + "</div></div>");
          box.querySelector(".cr-sheet").addEventListener("click", function (ev) {
            var b = ev.target.closest("[data-pick]"); if (!b) return;
            var id = +b.dataset.pick, name = "";
            list.forEach(function (it) { if (it.id === id) name = it.name; });
            K.closeSheet();
            K.api.post("creature/equip/", { id: d.creature.id, item: id }).then(function (r) { apply(r, s.slot, name + " رو پوشید"); }).catch(function () {});
          });
        }
        draw();
        bindWhy(root);
        K.on(root, "item", function (el) { K.go("item", { id: +el.dataset.id }); });
        K.on(root, "slot", function (el) { var s = slotOf(el.dataset.slot); if (s) openSlot(s); });
        K.on(root, "off", function (el) {
          K.api.post("creature/unequip/", { item: +el.dataset.id }, el).then(function (r) {
            if (r.slots) apply(r, "", r.item.name + " برگشت توی کوله"); else { touched(); K.reload(); }
          }).catch(function () {});
        });
      });
    }
  });

  // ═════════════════════════ blacksmith: the list ═════════════════════════
  function risk(chance) {
    return chance > 0 ? '<span class="tag" style="color:var(--warn)">' + K.ic("warn") + "شانس شکست " + K.pct(chance) + "</span>"
                      : '<span class="tag" style="color:var(--good)">' + K.ic("shieldcheck") + "بدون ریسک</span>";
  }
  K.screen("cr_forge", {
    title: "آهنگری", tab: "base",
    render: function (root) {
      return K.api.get("creature/forge/").then(function (d) {
        K.on(root, "base", function () { K.tab("base"); });
        K.on(root, "open", function (el) { K.go("cr_item", { id: +el.dataset.id }); });
        K.on(root, "fslot", function (el) { K.haptic(); forgeSlot = el.dataset.slot; draw(); });
        if (!d.built) {
          root.innerHTML = K.state("hammer", "اول آهنگری رو بساز", "آهنگری رو از بخش ساختمون‌ها بساز تا بتونی تجهیزاتت رو ارتقا بدی.",
            '<button class="btn primary" data-act="base" style="margin-top:16px">' + K.ic("hall") + "رفتن به پایگاه</button>");
          return;
        }
        function draw() {
          var counts = {}, list;
          d.items.forEach(function (it) { counts[it.slot] = (counts[it.slot] || 0) + 1; });
          list = d.items.filter(function (it) { return !forgeSlot || it.slot === forgeSlot; });
          var html = '<div class="panel pad cr-smith"><span class="ico-box lg" style="color:var(--gold)">' + K.ic("anvil") + '</span><div class="grow"><div class="b">آهنگری سطح ' + K.n(d.level) + "</div>" +
            '<div class="xs muted">سقف فعلی تجهیزات: <b class="num" style="color:var(--text)">+' + Number(d.cap) + "</b>" + (d.cap < d.max ? " · آخرین سقف +" + Number(d.max) : "") + "</div></div></div>" +
            '<div class="callout mt">' + K.ic("info") + "<span>با <b>طلا</b> سطح تجهیزات رو بالا ببر. از یه سطحی به بعد ممکنه شکست بخوره: طلا خرج می‌شه ولی سطح بالا نمی‌ره. توی صفحه‌ی هر تجهیزات «نمونه‌ی مشابه» و «ترکیب هم‌نوع» هم هست.</span></div>" +
            '<div class="row mt"><button class="chip ' + (forgeSlot ? "" : "on") + '" data-act="fslot" data-slot="">همه <span class="num">(' + d.items.length + ")</span></button>" +
            Object.keys(K.meta.slots).map(function (k) { return '<button class="chip ' + (forgeSlot === k ? "on" : "") + '" data-act="fslot" data-slot="' + k + '">' + K.ic(K.SLOT_ICON[k] || "chest") + K.esc(K.slotLabel(k)) + ' <span class="num">(' + (counts[k] || 0) + ")</span></button>"; }).join("") + "</div>";
          if (!list.length) {
            html += d.items.length ? K.state("search", "چیزی توی این دسته نیست", "هیچ موردی برای ارتقا توی این دسته نداری.")
                  : d.owned ? K.state("anvil", "همه به سقف رسیدن", "همه‌ی تجهیزاتت به سقف فعلی (+" + d.cap + ") رسیدن. برای بالاتر، ساختمون آهنگری رو ارتقا بده.")
                  : K.state("chest", "تجهیزاتی نداری", "از باکس‌ها تجهیزات به‌دست بیار.");
          } else {
            html += '<div class="panel list">' + list.map(function (it) {
              var f = it.forge;
              return '<button data-act="open" data-id="' + it.id + '">' + itemRow(it, K.esc(K.slotLabel(it.slot)) + " · " + K.esc(K.rarLabel(it.rarity)) + (it.on ? " · روی " + K.esc(it.on) : "") + (f.fail_chance > 0 ? ' · <span class="t-warn">شکست ' + K.pct(f.fail_chance) + "</span>" : ""),
                '<span class="v cr-price ' + (f.afford ? "t-coin" : "t-bad") + '">' + K.ic("coin") + K.short(f.cost) + '</span><span class="chev">' + K.ic("chevron") + "</span>") + "</button>";
            }).join("") + "</div>";
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
        var sel = {}, last = null, flash = "";
        function selected() { return Object.keys(sel).map(Number); }
        function forgeWhy() {
          var f = d.forge, s = d.smith;
          if (s.locked) return s.locked;
          if (!s.built) return "اول باید آهنگری رو از بخش ساختمون‌ها بسازی.";
          if (f.at_max) return s.cap >= s.max ? "این تجهیزات به آخرین سطح (+" + s.max + ") رسیده." : "به سقف فعلی (+" + s.cap + ") رسیده — برای بالاتر باید ساختمون آهنگری رو ارتقا بدی.";
          if (!f.afford) return "طلا کم داری — این آهنگری " + Number(f.cost).toLocaleString("en-US") + " طلا می‌خواد.";
          return "";
        }
        function draw() {
          var it = d.item, f = d.forge, s = d.smith, why = forgeWhy(), open = s.built && !f.at_max, html;
          ctx.setTitle(it.name);
          html = '<div class="panel pad cr-head"><div class="fighter ' + it.rarity + '"><img class="tile ' + it.rarity + '" style="box-shadow:0 0 0 1.5px var(--rc,var(--line))" src="' + it.img + '" alt=""><div class="grow"><div class="nm cut">' + K.esc(it.name) + "</div>" +
            '<div class="sm muted">' + K.esc(K.slotLabel(it.slot)) + " · " + K.esc(K.rarLabel(it.rarity)) + " · سطح <b class=\"c-" + it.rarity + '">' + lv(it.level) + '</b> از <span class="num">+' + Number(s.max) + "</span></div>" +
            '<div class="xs muted cut">' + (it.on ? "روی " + K.esc(it.on) : "توی کوله") + '</div></div><span class="pw">' + K.ic("power") + K.n(it.power) + "</span></div></div>";

          // ── forge with gold ──
          html += '<div class="h2">' + K.ic("hammer") + "ارتقا با طلا</div>" + '<div class="panel pad cr-forge' + (flash ? " " + flash : "") + '">';
          flash = "";
          if (last) html += '<div class="callout ' + (last.success ? "good" : "bad") + ' mb">' + K.ic(last.success ? "check" : "warn") + "<span>" +
            (last.success ? "<b>ارتقای موفق!</b> سطح جدید: " + lv(last.level_after) + " · قدرت " + ba(K.n(last.power_before), K.n(last.power_after))
                          : "<b>ارتقا شکست خورد!</b> " + K.n(last.cost) + " طلا سوخت و سطح بالا نرفت.") + "</span></div>";
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
              (m.cost != null ? '<div class="panel kv mb"><div><span>سطح</span><span>' + ba(lv(it.level), lv(it.level + 1)) + "</span></div><div><span>هزینه</span><span class=\"" + (m.gold ? "t-bad" : "t-coin") + '">' + K.ic("coin") + " " + K.n(m.cost) + "</span></div></div>" : "") +
              (m.ok ? "" : '<div class="callout warn mb">' + K.ic("lock") + "<span>" + K.esc(m.why) + "</span></div>") +
              '<div class="panel list">' + d.dupes.map(function (x) {
                var b = m.ok ? '<span class="btn sm gold" style="pointer-events:none">مصرف کن</span>' : "";
                return "<" + (m.ok ? 'button data-act="merge" data-id="' + x.id + '"' : "div") + ">" + itemRow(x, x.on ? "روی " + K.esc(x.on) : "توی کوله", b) + "</" + (m.ok ? "button" : "div") + ">";
              }).join("") + "</div></div>";
          }

          // ── same-slot fusion ──
          if (open && !s.locked && d.fuse.length) {
            var n = selected().length;
            html += '<div class="h2">' + K.ic("link") + "ترکیب هم‌نوع</div>" + '<div class="panel pad"><p class="lead" style="margin:0 0 10px">تجهیزات قربانی رو تیک بزن — هر کدوم یه شانس جدا برای یه سطح بالاتره. قربانی هرچی نایاب‌تر باشه شانس موفقیت بیشتره. <b>در هر صورت قربانی مصرف می‌شه.</b></p>' +
              '<div class="panel list cr-fuse">' + d.fuse.map(function (x) {
                return '<button class="' + (sel[x.id] ? "on" : "") + '" data-act="tick" data-id="' + x.id + '"><span class="cr-check">' + K.ic("check") + "</span>" +
                  itemRow(x, K.esc(K.rarLabel(x.rarity)) + (x.on ? ' · <span class="t-warn">روی ' + K.esc(x.on) + "</span>" : ""), '<span class="v sm">' + K.pct(x.fail_chance) + ' <small class="muted">خطا</small></span>') + "</button>";
              }).join("") + "</div>" +
              '<div class="btns mt"><button class="btn sm" data-act="fuse-all">' + (n ? "لغو همه" : "انتخاب همه") + '</button><button class="btn sm primary" data-act="fuse"' + (n ? "" : " disabled") + ">" + K.ic("link") + 'ترکیب منتخب <span class="num">(' + n + ")</span></button></div></div>";
          }
          root.innerHTML = html;
        }
        function refresh(r) { d = r; touched(); }
        draw();
        bindWhy(root);
        K.on(root, "forge", function (el) {
          K.api.post("creature/forge/do/", { item: d.item.id }, el).then(function (r) {
            refresh(r); last = r.done; flash = last.success ? "cr-flash" : "cr-shake"; draw();
            if (last.success) { K.haptic("ok"); K.toast("ارتقای موفق! سطح +" + last.level_after, "ok"); }
            else { K.haptic("err"); K.toast("شکست خورد — " + Number(last.cost).toLocaleString("en-US") + " طلا سوخت.", "err"); }
          }).catch(function () {});
        });
        K.on(root, "merge", function (el) {
          var id = +el.dataset.id, x = d.dupes.filter(function (q) { return q.id === id; })[0]; if (!x) return;
          K.confirm({ title: "این نمونه مصرف بشه؟", danger: true, ok: "مصرف کن", icon: "box",
            text: "«" + x.name + " +" + x.level + "»" + (x.on ? " (الان روی " + x.on + ")" : "") + " برای همیشه مصرف می‌شه و «" + d.item.name + "» می‌ره روی +" + (d.item.level + 1) + "." }).then(function (yes) {
            if (!yes) return;
            K.api.post("creature/merge/", { item: d.item.id, dupe: id }).then(function (r) {
              var done = r.done; refresh(r); last = null; sel = {}; draw();
              celebrate({ icon: "hammer", title: "ارتقا انجام شد!", chips: done.cost ? ['<span class="t-coin">' + K.ic("coin") + "</span>" + K.n(done.cost) + " طلا خرج شد"] : [],
                rows: [["سطح", lv(done.level_before), lv(done.level_after)], ["قدرت تجهیزات", K.n(done.power_before), K.n(done.power_after)]] });
            }).catch(function () {});
          });
        });
        K.on(root, "tick", function (el) { K.haptic(); var id = +el.dataset.id; if (sel[id]) delete sel[id]; else sel[id] = 1; draw(); });
        K.on(root, "fuse-all", function () {
          K.haptic();
          if (selected().length) { sel = {}; draw(); return; }
          // like the bot: «انتخاب همه» never ticks gear a creature is wearing
          var spare = d.fuse.filter(function (x) { return !x.on_id; }).slice(0, 50);
          spare.forEach(function (x) { sel[x.id] = 1; });
          if (spare.length < d.fuse.length) K.toast("تجهیزاتِ روی هیولاها انتخاب نشد؛ اگه می‌خوای، دستی تیک بزن.", "err");
          draw();
        });
        K.on(root, "fuse", function () {
          var ids = selected(); if (!ids.length) return;
          var worn = d.fuse.filter(function (x) { return sel[x.id] && x.on_id; }).length;
          K.confirm({ title: "ترکیب انجام بشه؟", danger: true, ok: "ترکیب کن", icon: "link",
            text: ids.length + " تا تجهیزات قربانی می‌شن و چه موفق بشه چه نه، برنمی‌گردن." + (worn ? " " + worn + " تاشون الان روی هیولاهاته." : "") }).then(function (yes) {
            if (!yes) return;
            K.api.post("creature/fuse/", { item: d.item.id, sacrifices: ids }).then(function (r) {
              var done = r.done; refresh(r); last = null; sel = {}; draw();
              celebrate({ icon: done.successes ? "link" : "warn", title: done.successes ? "ترکیب انجام شد" : "این بار نگرفت",
                text: done.capped ? "به سقف فعلی آهنگری رسید — برای بالاتر، ساختمون آهنگری رو ارتقا بده." : "",
                chips: ['<span class="t-good">' + K.ic("check") + "</span>" + K.n(done.successes) + " موفق", '<span class="t-bad">' + K.ic("close") + "</span>" + K.n(done.fails) + " شکست", K.ic("box") + K.n(done.consumed) + " قربانی مصرف شد"],
                rows: done.level_after !== done.level_before ? [["سطح", lv(done.level_before), lv(done.level_after)], ["قدرت تجهیزات", K.n(done.power_before), K.n(done.power_after)]] : [],
                button: "باشه" });
            }).catch(function () {});
          });
        });
      });
    }
  });

  // ═════════════════════════ rename (sheet) ═════════════════════════
  function renameSheet(c, ctx) {
    K.api.get("creature/panel/?id=" + c.id).then(function (d) {
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
          K.closeSheet(); touched(); K.haptic("ok");
          K.toast("اسمش شد «" + r.name + "»" + (r.cost ? " · " + r.cost + " الماس کم شد" : ""), "ok");
          ctx.reload();
        }).catch(function () {});
      }
      box.querySelector("[data-go]").onclick = function () {
        var name = input.value.trim(), btn = this;
        if (!name) { K.toast("یه اسم بنویس (خالی نباشه).", "err"); input.focus(); return; }
        if (free) { send(name, btn); return; }
        K.confirm({ title: "تغییر نام با الماس؟", icon: "gem", ok: "تأیید و ثبت", text: "اسم می‌شه «" + name + "» و " + rn.cost + " الماس کم می‌شه." }).then(function (yes) { if (yes) send(name); });
      };
    }).catch(function (err) { K.toast(err.message, "err"); });
  }

  // ═════════════════════════ entry points ═════════════════════════
  K.creatureActions.push({
    order: 10,
    render: function (c) {
      return (c.active ? "" : c.busy ? offBtn("wide", K.ic("check") + "فعال کن", "این هیولا الان مشغوله — اول آزادش کن تا بتونی فعالش کنی.")
                                     : '<button class="btn primary wide" data-act="cr-active">' + K.ic("check") + "فعال کن</button>") +
        '<button class="btn" data-act="cr-feed">' + K.ic("food") + "تغذیه</button>" +
        '<button class="btn" data-act="cr-parts">' + K.ic("up") + "ارتقای اندام‌ها</button>" +
        '<button class="btn" data-act="cr-gear">' + K.ic("chest") + "تجهیزات</button>" +
        '<button class="btn" data-act="cr-rename">' + K.ic("pencil") + "تغییر نام</button>";
    },
    bind: function (root, c, ctx) {
      bindWhy(root);
      K.on(root, "cr-active", function (el) {
        K.api.post("creature/activate/", { id: c.id }, el).then(function () { touched(); K.haptic("ok"); K.toast(c.name + " هیولای فعالت شد.", "ok"); ctx.reload(); }).catch(function () {});
      });
      K.on(root, "cr-feed", function () { K.go("cr_feed", { id: c.id }); });
      K.on(root, "cr-parts", function () { K.go("cr_parts", { id: c.id }); });
      K.on(root, "cr-gear", function () { K.go("cr_gear", { id: c.id }); });
      K.on(root, "cr-rename", function () { renameSheet(c, ctx); });
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
        K.api.post("creature/unequip/", { item: e.id }, el).then(function () { touched(); K.haptic("ok"); K.toast(e.name + " برگشت توی کوله.", "ok"); ctx.reload(); }).catch(function () {});
      });
      K.on(root, "cr-equip", function () {
        K.pickCreature({ title: "روی کدوم هیولا؟", sub: "اگه اون هیولا توی این جایگاه چیزی پوشیده باشه، درمیاد و این جاش می‌ره.", exclude: e.on_id ? [e.on_id] : [] }).then(function (c) {
          if (!c) return;
          K.api.post("creature/equip/", { id: c.id, item: e.id }).then(function (r) {
            touched(); K.haptic("ok"); K.toast(c.name + " «" + e.name + "» رو پوشید · " + powerText(c.power, r.power), "ok"); ctx.reload();
          }).catch(function () {});
        });
      });
      K.on(root, "cr-smith", function () { K.go("cr_item", { id: e.id }); });
    }
  });

  K.hub("base", { id: "forge", title: "آهنگری", sub: "ارتقای تجهیزات با طلا", icon: "hammer", color: "var(--gold)", go: "cr_forge", order: 45, hall: HALL_FORGE });
})(window.K);
