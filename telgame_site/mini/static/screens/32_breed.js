/* Fusion («تالار ادغام») and the Monster Cave («غار هیولا»).
   Screens: br_fusion (ready pairs), br_fuse (one creature's fusion card), br_cave (the cave),
   br_pair (choose two parents). API: breed/…
   The cave redraws from the answer of every action (no second request) and keeps ONE countdown
   interval however often it redraws. */
(function (K) {
  "use strict";

  K.addIcons({
    merge: '<circle cx="8.500" cy="12" r="5.500"/><circle cx="15.500" cy="12" r="5.500"/>',
    cave: '<path d="M3 20c0-9 3.500-15.500 9-15.500S21 11 21 20zM9 20c0-4 1-6.500 3-6.500s3 2.500 3 6.500"/>',
    hatch: '<path d="M12 3c3.500 0 6.500 5.500 6.500 10a6.500 6.500 0 0 1-13 0C5.500 8.500 8.500 3 12 3z"/><path d="m5.800 12 2.700 1.800 2-2.300 1.500 2.300 2-2.300 1.800 2.300 2.400-1.800"/>'
  });

  var HALL = 2;   // SECTION_HALL_REQ["fusion"] / ["breeding"] — the server enforces it

  function pct(x) { return '<span class="num">' + Number(x) + "%</span>"; }
  function rar(r) { return '<b class="c-' + r + '">' + K.esc(K.rarLabel(r)) + "</b>"; }
  function noop() {}
  function fmt(x) { return Number(x || 0).toLocaleString("en-US"); }
  /* Countdowns that survive in-place redraws: each call replaces the previous interval of its
     channel (K.timers starts a new one every time and only navigation clears them).
     <span class="timer" data-left="seconds" data-done="text">, <div class="progress" data-br-left data-br-total><i>. */
  var ticking = {};
  function live(root, channel, onDone) {
    if (ticking[channel]) clearInterval(ticking[channel]);
    ticking[channel] = null;
    var els = Array.prototype.slice.call(root.querySelectorAll(".timer[data-left]")), bars = Array.prototype.slice.call(root.querySelectorAll("[data-br-left]"));
    if (!els.length && !bars.length) return;
    var t0 = Date.now() / 1000, fired = false;
    function tick() {
      var gone = Date.now() / 1000 - t0;
      els.forEach(function (el) {
        if (el._done) return;
        var left = Number(el.dataset.left) - gone;
        if (left <= 0) { el._done = true; el.classList.add("done"); el.innerHTML = K.ic("check") + (el.dataset.done || "آماده"); if (!fired && onDone) { fired = true; onDone(el); } }
        else el.innerHTML = K.ic("clock") + '<span class="num">' + K.clock(left) + "</span>";
      });
      bars.forEach(function (el) {
        var total = Number(el.dataset.brTotal), left = Number(el.dataset.brLeft) - gone;
        if (total > 0 && el.firstChild) el.firstChild.style.width = (Math.max(0, Math.min(1, 1 - left / total)) * 100).toFixed(1) + "%";
      });
    }
    tick(); ticking[channel] = K.every(1000, tick);
  }
  function bar(left, total, kind) {
    var ratio = total > 0 ? 1 - left / total : 1;
    return '<div class="progress ' + (kind || "") + '" data-br-left="' + Number(left) + '" data-br-total="' + Number(total) + '"><i style="width:' + (Math.max(0, Math.min(1, ratio)) * 100).toFixed(1) + '%"></i></div>';
  }
  /* The newborn / the fused creature: its art in a rarity-coloured frame, then what came with it.
     o: {title, text, creature, chips:[html], items:[item], button} → Promise (resolves when closed) */
  function reveal(o) {
    return new Promise(function (resolve) {
      var c = o.creature;
      K.sheet('<div class="grab"></div><div class="br-reveal ' + c.rarity + '"><div class="br-rays"></div><div class="br-rart"><img src="' + c.img + '&s=l" alt="">' +
        '<span class="el e-' + c.element + '">' + K.ic(K.EL_ICON[c.element] || "atom") + "</span></div>" +
        '<div class="br-rstars">' + K.stars(c.star) + "</div><h3>" + K.esc(o.title) + '</h3><div class="br-rname">' + K.esc(c.name) + "</div>" +
        '<div class="meta">' + K.rarTag(c.rarity) + K.elTag(c.element) + K.tag("سطح " + K.n(c.level)) + (c.power != null ? K.tag(K.n(c.power), "var(--accent)", "power") : "") + "</div>" +
        (o.text ? "<p>" + K.esc(o.text) + "</p>" : "") +
        ((o.chips || []).length ? '<div class="loot"><div class="items">' + o.chips.map(function (h) { return '<span class="it">' + h + "</span>"; }).join("") + "</div></div>" : "") +
        ((o.items || []).length ? '<div class="loot"><div class="cards">' + o.items.map(function (e) { return K.itemTile(e, { tag: "div" }); }).join("") + "</div></div>" : "") + "</div>" +
        '<div class="pad" style="margin-top:16px"><button class="btn primary block" data-close>' + K.esc(o.button || "عالیه") + "</button></div>", { onClose: resolve });
      K.haptic("ok");
    });
  }

  /* one square slot of a pair: a creature's art, or an empty «choose» box */
  function slot(c, act, label) {
    var tag = act ? "button" : "div", attr = act ? ' data-act="' + act + '"' : "";
    if (!c) return "<" + tag + ' class="br-slot empty"' + attr + '><span class="br-plus">' + K.ic("plus") + "</span><span>" + K.esc(label || "") + "</span></" + tag + ">";
    return "<" + tag + ' class="br-slot ' + c.rarity + '"' + attr + '><img src="' + c.img + '" alt=""><span class="br-shade"></span>' +
      '<span class="el e-' + c.element + '">' + K.ic(K.EL_ICON[c.element] || "atom") + "</span>" +
      (act ? '<span class="br-swap">' + K.ic("swap") + "</span>" : "") +
      '<span class="br-cap"><b class="cut">' + K.esc(c.name) + "</b><span>" + K.stars(c.star) + '<span class="num">Lv ' + Number(c.level) + "</span></span></span></" + tag + ">";
  }
  function pair(a, b, icon, actA, actB, labelA, labelB) {
    return '<div class="br-pair">' + slot(a, actA, labelA) + '<span class="br-mid">' + K.ic(icon) + "</span>" + slot(b, actB, labelB) + "</div>";
  }
  function lockedState(d) {
    return K.state("lock", "اول " + d.lab_label + " رو بساز", "این بخش وقتی باز می‌شه که " + d.lab_label + " رو از بخش ساختمون‌ها ساخته باشی.",
      '<button class="btn primary" data-act="base" style="margin-top:16px">' + K.ic("hall") + "رفتن به پایگاه</button>");
  }

  // ═════════════════════════ fusion: the ready pairs ═════════════════════════
  var fFilter = "";
  function blockedWhy(b, d) {
    if (b.reason === "max") return "به سقف " + K.n(d.star_max) + " ستاره رسیده";
    if (b.reason === "lab") return K.esc(d.lab_label) + " هنوز ساخته نشده";
    if (b.reason === "cap") return K.esc(d.lab_label) + " سطح " + K.n(b.need_level) + " می‌خواد (الان " + K.n(d.lab_level) + ")";
    return K.n(b.count - b.free) + " تا از " + K.n(b.count) + " تا مشغولن؛ اول آزادشون کن";
  }
  K.screen("br_fusion", {
    title: "تالار ادغام", tab: "base",
    render: function (root, params, ctx) {
      return K.api.get("breed/fusion/").then(function (d) {
        K.on(root, "base", function () { K.tab("base"); });
        K.on(root, "open", function (el) { K.go("br_fuse", { id: +el.dataset.id, b: +el.dataset.b || 0 }); });
        K.on(root, "blocked", function (el) { K.go("br_fuse", { id: +el.dataset.id }); });
        K.on(root, "filter", function (el) { K.haptic(); fFilter = el.dataset.r; draw(); });
        if (!d.built) { root.innerHTML = lockedState(d); return; }
        function draw() {
          var present = K.meta.rarity_order.filter(function (r) { return d.pairs.some(function (p) { return p.rarity === r; }); });
          if (fFilter && present.indexOf(fFilter) < 0) fFilter = "";
          var shown = d.pairs.filter(function (p) { return !fFilter || p.rarity === fFilter; }).sort(function (x, y) { return x.star - y.star || (x.name < y.name ? -1 : 1); });
          var html = '<div class="panel pad br-head"><span class="ico-box lg" style="color:var(--accent-2)">' + K.ic("merge") + '</span><div class="grow"><div class="b">سقف ستاره‌ی فعلی تو</div>' +
            '<div class="br-cap-stars">' + K.stars(d.cap) + '<span class="muted sm">' + K.esc(d.lab_label) + " سطح " + K.n(d.lab_level) + "</span></div></div></div>" +
            '<div class="callout mt">' + K.ic("info") + "<span>دو هیولای <b>هم‌نام، هم‌نایابی و هم‌ستاره</b> یکی می‌شن و یه ستاره بالاتر می‌رن. اول دو تا 1 ستاره رو 2 ستاره کن، بعد دو تا 2 ستاره رو 3 ستاره… (5 ستاره = 16 تا 1 ستاره).</span></div>";
          if (!d.pairs.length) {
            html += K.state("merge", "الان جفت آماده‌ای نداری", "برای هر ادغام دو تای دقیقاً یکسان (نام، نایابی و ستاره) لازم داری. از باکس‌ها یا غار هیولای بیشتری بگیر.");
          } else {
            html += '<div class="h2">' + K.ic("check") + "آماده‌ی ادغام</div>";
            if (present.length > 1) html += '<div class="row"><button class="chip ' + (fFilter ? "" : "on") + '" data-act="filter" data-r="">همه</button>' + present.slice().reverse().map(function (r) {
              return '<button class="chip ' + (fFilter === r ? "on" : "c-" + r) + '" data-act="filter" data-r="' + r + '">' + K.ic("gem") + K.esc(K.rarLabel(r)) + ' <span class="num">(' + d.pairs.filter(function (p) { return p.rarity === r; }).length + ")</span></button>"; }).join("") + "</div>";
            var last = 0;
            shown.forEach(function (p) {
              if (p.star !== last) { last = p.star; html += '<div class="br-step">' + K.stars(p.star) + K.ic("chevron") + K.stars(p.star + 1) + "</div>"; }
              html += '<button class="panel br-fcard ' + p.rarity + '" data-act="open" data-id="' + p.a.id + '" data-b="' + p.b.id + '">' +
                '<span class="br-duo"><img loading="lazy" decoding="async" src="' + p.a.img + '" alt=""><img loading="lazy" decoding="async" src="' + p.b.img + '" alt=""></span>' +
                '<span class="grow"><span class="b cut" style="display:block">' + K.esc(p.name) + (p.count > 2 ? ' <span class="muted sm num">+' + (p.count - 2) + "</span>" : "") + "</span>" +
                '<span class="sm c-' + p.rarity + '">' + K.esc(K.rarLabel(p.rarity)) + '</span> <span class="sm muted">' + K.n(p.count) + " تا داری</span></span>" +
                '<span class="br-cost ' + (p.enough ? "t-coin" : "t-bad") + '">' + K.ic("coin") + K.short(p.cost) + '</span><span class="faint">' + K.ic("chevron") + "</span></button>";
            });
          }
          if (d.blocked.length) {
            html += '<div class="h2">' + K.ic("lock") + 'فعلاً نمی‌شه</div><div class="panel list">' + d.blocked.map(function (b) {
              return '<button data-act="blocked" data-id="' + b.sample.id + '"><img class="th" loading="lazy" decoding="async" src="' + b.sample.img + '" alt=""><span class="t">' + K.esc(b.name) + " " + K.stars(b.star) +
                "<small>" + blockedWhy(b, d) + '</small></span><span class="chev">' + K.ic("chevron") + "</span></button>";
            }).join("") + "</div>";
          }
          root.innerHTML = html;
        }
        draw();
      });
    }
  });

  // ═════════════════════════ fusion: one creature ═════════════════════════
  function checkRow(ok, text, sub) {
    return '<div style="color:' + (ok ? "var(--good)" : "var(--bad)") + '"><span class="ic">' + K.ic(ok ? "check" : "close") + '</span><span class="t">' + text + (sub ? "<small>" + sub + "</small>" : "") + "</span></div>";
  }
  K.screen("br_fuse", {
    title: "ادغام", tab: "base",
    render: function (root, params, ctx) {
      return K.api.get("breed/fusion/pair/?id=" + encodeURIComponent(params.id)).then(function (d) {
        var c = d.creature, free = d.partners.filter(function (p) { return !p.busy; });
        K.on(root, "base", function () { K.tab("base"); });
        if (!d.built) { root.innerHTML = lockedState(d); return; }
        if (d.at_max) {
          root.innerHTML = '<div class="br-pair one">' + slot(c, "", "") + "</div>" +
            K.state("trophy", "به سقف " + d.star_max + " ستاره رسیده", "این قوی‌ترین فرم ممکنه؛ دیگه نیازی به ادغام نداره. روی ارتقای اعضا و تجهیزاتش تمرکز کن.");
          return;
        }
        var chosen = free.filter(function (p) { return p.id === params.b; })[0] || free[0] || null;
        function nextStep() {
          if (d.ready && chosen) return ["good", "check", "همه‌چیز آماده‌ست! دکمه‌ی «ادغام کن» رو بزن."];
          if (d.at_cap) return ["warn", "warn", "قدم بعدی: " + K.esc(d.lab_label) + " رو به سطح " + K.n(d.next_star) + " ارتقا بده (الان سطح " + K.n(d.lab_level) + ")."];
          if (d.self_busy) return ["warn", "warn", "این هیولا الان مشغوله (" + K.esc(d.self_why) + "). اول آزادش کن."];
          if (!d.partner_count) return ["warn", "warn", "قدم بعدی: یه «" + K.esc(d.species) + "» دیگه با همین نایابی و همین ستاره پیدا کن (از باکس یا غار هیولا)." + (d.partners.length ? " جفت‌هایی که داری الان مشغولن." : "")];
          return ["warn", "warn", "قدم بعدی: " + K.n(d.missing_gold) + " طلای دیگه جمع کن."];
        }
        function draw() {
          var st = nextStep(), canPick = d.partners.length > 0;
          var mixed = chosen && chosen.element !== c.element;
          root.innerHTML = pair(c, chosen, "merge", "", canPick ? "pick" : "", "", canPick ? "انتخاب جفت" : "جفتی نداری") +
            '<div class="br-result"><span class="muted sm">نتیجه</span><b class="cut">' + K.esc(d.species) + "</b>" + K.stars(d.star) + K.ic("chevron") + K.stars(d.next_star) + "</div>" +
            '<div class="callout ' + st[0] + ' mt">' + K.ic(st[1]) + "<span>" + st[2] + "</span></div>" +
            '<div class="h2">' + K.ic("list") + 'شرایط این ارتقا</div><div class="panel list">' +
            checkRow(d.built, K.esc(d.lab_label) + " ساخته شده") +
            checkRow(!d.at_cap, K.esc(d.lab_label) + " سطح " + K.n(d.next_star) + " یا بالاتر", "الان سطح " + K.n(d.lab_level) + " · سقف ستاره " + K.n(d.cap)) +
            checkRow(d.partner_count > 0, "یه هیولای هم‌نوع، هم‌رده و هم‌ستاره‌ی آزاد", d.partner_count ? K.n(d.partner_count) + " تا آماده داری" : (d.partners.length ? "همه‌شون مشغولن" : "نداری")) +
            checkRow(d.gold_ok, "طلای کافی: " + K.n(d.cost), "داری: " + K.n(d.coins)) + "</div>" +
            '<div class="h2">' + K.ic("info") + 'چی می‌مونه، چی می‌ره</div><div class="panel list br-facts">' +
            '<div style="color:var(--bad)"><span class="ic">' + K.ic("flame") + '</span><span class="t">هر دو هیولا برای همیشه حذف می‌شن<small>و یه «' + K.esc(d.species) + "» " + K.n(d.next_star) + " ستاره ساخته می‌شه که هیولای فعالت می‌شه.</small></span></div>" +
            '<div style="color:var(--good)"><span class="ic">' + K.ic("up") + '</span><span class="t">سطحِ والدِ قوی‌تر و بهترین اعضای هر دو<small>هیولای جدید این‌ها رو می‌گیره و XP دو والد جمع می‌شه.</small></span></div>' +
            (mixed ? '<div style="color:var(--warn)"><span class="ic">' + K.ic("atom") + '</span><span class="t">عنصرش تصادفیه<small>' + K.esc(K.elLabel(c.element)) + " یا " + K.esc(K.elLabel(chosen.element)) + "؛ عنصرِ یکی از دو والد.</small></span></div>" : "") +
            '<div style="color:var(--accent)"><span class="ic">' + K.ic("chest") + '</span><span class="t">تجهیزاتشون به کوله برمی‌گرده<small>با شانس ' + pct(d.inherit_pct) + " یکی از تجهیزاتِ پوشیده‌ی والدین به هیولای جدید به ارث می‌رسه.</small></span></div>" +
            '<div style="color:var(--coin)"><span class="ic">' + K.ic("coin") + '</span><span class="t">هزینه</span><span class="v">' + K.n(d.cost) + " طلا</span></div></div>" +
            (d.at_cap ? '<button class="btn ghost block mt" data-act="base">' + K.ic("hall") + "رفتن به پایگاه</button>" : "") +
            '<div class="br-foot"><button class="btn primary lg block" data-act="fuse"' + (d.ready && chosen ? "" : " disabled") + ">" + K.ic("merge") + 'ادغام کن<span class="cost">' + K.ic("coin") + K.short(d.cost) + "</span></button></div>";
        }
        draw();
        K.on(root, "pick", function () {
          var ids = {}, why = {};
          d.partners.forEach(function (p) { ids[p.id] = 1; if (p.busy) why[p.id] = "مشغوله (" + p.why + ")؛ اول آزادش کن."; });
          K.pickCreature({ title: "جفتِ " + c.name, sub: "هم‌نوع و هم‌ستاره‌ان، پس ادغامشون حتماً جواب می‌ده.",
            filter: function (x) { return !!ids[x.id]; }, disabled: function (x) { return why[x.id] || ""; },
            empty: "هیولای هم‌نوع و هم‌ستاره‌ی دیگه‌ای نداری." }).then(function (x) {
            if (!x || !ctx.alive()) return;
            params.b = x.id; chosen = free.filter(function (p) { return p.id === x.id; })[0] || chosen; draw();
          });
        });
        K.on(root, "fuse", function (el) {
          if (!chosen) return;
          K.confirm({ title: "تأیید ادغام", danger: true, icon: "merge", ok: "ادغام کن", cancel: "بی‌خیال",
            html: "<p>«" + K.esc(c.name) + "» و «" + K.esc(chosen.name) + "» <b>برای همیشه حذف می‌شن</b> و یه «" + K.esc(d.species) + "» " + K.n(d.next_star) + " ستاره ساخته می‌شه.</p>" +
              '<div class="items"><span class="it t-coin">' + K.ic("coin") + '<span style="color:var(--text)">' + K.n(d.cost) + " طلا</span></span></div>" }).then(function (yes) {
            if (!yes) return;
            K.api.post("breed/fusion/do/", { a: c.id, b: chosen.id }, el).then(function (r) {
              K.invalidate("profile/creatures/", "profile/equipment/");
              var extra = [];
              r.missions.forEach(function (m) {
                extra.push(K.ic("flag") + "مأموریت" + (m.weekly ? " هفتگی" : "") + " «" + K.esc(m.label) + "»");
                (m.extras || []).forEach(function (x) { extra.push(K.ic("gift") + K.esc(x)); });
              });
              var sum = { coins: 0, dna: 0, diamonds: 0 };
              r.missions.forEach(function (m) { sum.coins += m.coins || 0; sum.dna += m.dna || 0; sum.diamonds += m.diamonds || 0; });
              var chips = extra.slice();
              if (sum.coins || sum.dna || sum.diamonds) chips.unshift(K.amounts(sum));
              reveal({ title: "ادغام موفق بود!", creature: r.child, chips: chips, items: r.inherited ? [r.inherited] : [],
                text: "والدین سوزانده شدن و یه هیولای " + r.child.star + " ستاره متولد شد" + (r.inherited ? "؛ یه تجهیزات هم از والدین به ارث رسید." : ".") }).then(function () {
                if (!ctx.alive()) return;
                K.back();
                if (params.from === "creature") K.replace("creature", { id: r.child.id });
              });
            }).catch(noop);
          });
        });
      });
    }
  });

  // ═════════════════════════ the cave ═════════════════════════
  /* The tile's badge is server-side (api `badges` → K.badges.cave, refreshed with the profile); after an
     action here the number is corrected at once so the hub is right the moment the player goes back. */
  function setBadge(n) { if (K.badges) K.badges.cave = Number(n) || 0; }
  function countReady(d) {
    return d.jobs.filter(function (j) { return j.ready; }).length + d.eggs.filter(function (e) { return e.ready; }).length;
  }

  function guideSheet(r) {
    function sec(icon, title, lines) {
      return '<div class="h2">' + K.ic(icon) + title + '</div><div class="panel pad br-guide">' + lines.map(function (l) { return "<p>" + l + "</p>"; }).join("") + "</div>";
    }
    var gaps = r.gap_pct.map(function (g) { return K.n(g.gap) + " رده فاصله " + pct(g.pct); }).join("، ");
    var half = r.half_elements.map(function (e) { return K.esc(K.elLabel(e)); }).join(" یا ");
    K.sheet('<div class="grab"></div><div class="pad"><div class="ttl" style="font-size:18px">راهنمای غار هیولا</div>' +
      sec("egg", "فرایند", ["دو هیولای آزاد رو می‌فرستی توی غار. اول جفت‌گیری می‌کنن، بعد یه تخم می‌ذارن و آزاد می‌شن؛ تخم جدا رشد می‌کنه تا سر باز کنه."]) +
      sec("gem", "رده‌ی تخم", ["رده‌ی نوزاد هیچ‌وقت از والدین بالاتر نمی‌ره.",
        "والدین هم‌رده: " + pct(r.same_top_pct) + " همون رده، " + pct(100 - r.same_top_pct) + " یه رده پایین‌تر.",
        "والدین متفاوت‌رده، شانس رده‌ی بالاتر: " + gaps + "؛ وگرنه رده‌ی پایین‌تر.",
        "نوزاد " + half + ": شانس رده‌ی بالاتر " + (r.half_factor === 0.5 ? "نصف" : "×" + r.half_factor) + " می‌شه.",
        "نژاد نوزاد: تصادفی، نژاد یکی از دو والد."]) +
      sec("clock", "زمان و ظرفیت", ["جفت‌گیری: " + K.n(r.mating_hours[0]) + " تا " + K.n(r.mating_hours[1]) + " ساعت (بسته به نایابی والدین).",
        "رشد تخم: " + K.dur(r.hatch_minutes[0] * 60) + " تا " + K.dur(r.hatch_minutes[1] * 60) + ".",
        "ظرفیت: 1 جفت همزمان (با اشتراک طلایی 2 جفت)."]) +
      '<div class="h2">' + K.ic("dna") + 'هزینه‌ی DNA</div><div class="panel kv">' + r.dna.map(function (x) {
        return "<div><span>" + rar(x.a) + " + " + rar(x.b) + '</span><span class="t-dna">' + K.n(x.dna) + "</span></div>"; }).join("") + "</div>" +
      sec("gem", "فوری‌کردن", ["حدود " + K.n(r.gems_per_hour) + " الماس به ازای هر ساعتِ باقی‌مونده؛ هرچی زمان کمتری مونده باشه ارزون‌تره."]) +
      sec("hatch", "نوزاد", ["سطح 1 و 1 ستاره به دنیا میاد و هیچ‌چیزی از تمرین والدین به ارث نمی‌بره.", "لغو جفت‌گیری DNA رو برنمی‌گردونه."]) +
      '<div style="height:8px"></div></div>');
  }

  K.screen("br_cave", {
    title: "غار هیولا", tab: "base",
    render: function (root, params, ctx) {
      return K.api.get("breed/cave/").then(function (d) {
        var refreshing = false;
        K.on(root, "base", function () { K.tab("base"); });
        if (!d.built) { setBadge(0); root.innerHTML = lockedState(d); return; }
        ctx.actions('<button class="iconbtn" id="br-guide" aria-label="راهنما">' + K.ic("info") + "</button>");
        var gb = document.getElementById("br-guide"); if (gb) gb.onclick = function () { guideSheet(d.rules); };

        function draw() {
          setBadge(countReady(d));
          var html = '<div class="banner"' + (d.img ? ' style="background-image:url(\'' + d.img + '&s=l\')"' : "") + '><div class="grow"><div class="ttl">غار هیولا</div>' +
            '<div class="sm" style="color:#cfd8ee">دو هیولا بفرست، یه تخمِ اسرارآمیز بگیر.</div></div>' +
            '<span class="tag" style="color:var(--dna)">' + K.ic("heart") + '<span class="num">' + d.jobs.length + " / " + d.max_jobs + "</span> جفت</span></div>";
          if (d.rules.luck_pct > 0) html += '<div class="callout good mt">' + K.ic("spark") + "<span><b>هفته‌ی غار خوش‌شانس:</b> تخم‌هایی که این هفته باز می‌شن " + pct(d.rules.luck_pct) + " شانسِ بیشتر برای رده‌ی بالا دارن.</span></div>";

          html += '<div class="h2">' + K.ic("heart") + "جفت‌گیری</div>";
          d.jobs.forEach(function (j, i) {
            var num = d.max_jobs > 1 || d.jobs.length > 1 ? " " + (i + 1) : "";
            html += '<div class="panel pad br-job' + (j.ready ? " ready" : "") + '"><div class="flex between"><b>جفت' + num + "</b>" +
              (j.ready ? '<span class="timer done">' + K.ic("check") + "تموم شد</span>" : '<span class="timer" data-left="' + (j.left + 1) + '" data-done="تموم شد"></span>') + "</div>" +
              pair(j.a, j.b, "heart", "", "", "", "") +
              (j.ready ? '<button class="btn good block" data-act="lay" data-id="' + j.id + '">' + K.ic("egg") + "تخم بذار و آزادشون کن</button>"
                : bar(j.left, j.total, "") + '<div class="btns mt"><button class="btn" data-act="rush" data-id="' + j.id + '" data-price="' + j.finish_price + '">' + K.ic("bolt") + 'فوری‌کن<span class="cost t-diamond">' + K.ic("gem") + K.n(j.finish_price) + "</span></button>" +
                  '<button class="btn ghost t-bad" data-act="cancel" data-id="' + j.id + '">' + K.ic("close") + "لغو</button></div>") + "</div>";
          });
          if (d.jobs.length < d.max_jobs) {
            html += d.free_count >= 2
              ? '<button class="panel br-new" data-act="new"><span class="br-plus">' + K.ic("plus") + "</span><b>" + (d.jobs.length ? "جفت بعدی رو بفرست غار" : "جفت بفرست غار") + '</b><small class="muted">غار ' + (d.jobs.length ? "یه جای خالی داره" : "خالیه") + " · " + K.n(d.free_count) + " هیولای آزاد داری</small></button>"
              : '<div class="callout warn">' + K.ic("warn") + "<span>برای جفت‌گیری حداقل <b>دو</b> هیولای آزاد لازم داری. هیولای فعال و هیولاهایی که سر کارن حساب نمی‌شن.</span></div>";
          } else if (d.max_jobs === 1) {
            html += '<p class="note" style="margin-top:10px">ظرفیت غار پره. با اشتراک طلایی می‌تونی 2 جفت همزمان بفرستی.</p>';
          }

          if (d.eggs.length) {
            html += '<div class="h2">' + K.ic("egg") + "تخم‌های در حال رشد (" + K.n(d.eggs.length) + ')</div><div class="panel br-eggs">' + d.eggs.map(function (e, i) {
              return '<div class="br-egg' + (e.ready ? " ready" : "") + '"><span class="br-eggic">' + K.ic(e.ready ? "hatch" : "egg") + '</span><span class="grow"><b>تخم <span class="num">#' + (i + 1) + "</span></b>" +
                (e.ready ? '<span class="sm t-good" style="display:block">آماده‌ی سر باز کردنه!</span>' : '<span class="sm" style="display:block"><span class="timer" data-left="' + (e.left + 1) + '"></span></span>' + bar(e.left, e.total, "gold")) + "</span>" +
                (e.ready ? '<button class="btn good sm" data-act="hatch" data-id="' + e.id + '">' + K.ic("hatch") + "سر باز کن</button>"
                  : '<button class="btn sm" data-act="egg-rush" data-id="' + e.id + '" data-price="' + e.finish_price + '">' + K.ic("bolt") + 'فوری<span class="cost t-diamond">' + K.ic("gem") + K.n(e.finish_price) + "</span></button>") + "</div>";
            }).join("") + '</div><p class="note">چی توی تخم‌هاست؟ تا سر باز نکنن هیچ‌کس نمی‌دونه.</p>';
          }
          html += '<button class="btn ghost block mt" data-act="guide">' + K.ic("doc") + "راهنمای کامل غار</button>";
          root.innerHTML = html;
          // a countdown reached zero: the buttons that speed it up are gone at once (nothing left to pay for)
          // and the server is asked what is ready now
          live(root, "cave", function (el) {
            var card = el.closest(".br-job, .br-egg");
            if (card) Array.prototype.forEach.call(card.querySelectorAll('[data-act="rush"],[data-act="egg-rush"],[data-act="cancel"]'), function (b) { b.disabled = true; });
            if (refreshing) return;
            refreshing = true;
            K.after(1200, function () {
              K.api.get("breed/cave/").then(function (f) { refreshing = false; if (ctx.alive()) apply(f); }, function () { refreshing = false; });
            });
          });
        }
        function apply(f) { d = f; draw(); }
        draw();

        K.on(root, "guide", function () { guideSheet(d.rules); });
        K.on(root, "new", function () { K.go("br_pair", { cave: d, at: Date.now() }); });
        K.on(root, "lay", function (el) {
          K.api.post("breed/cave/lay/", { job: +el.dataset.id }, el).then(function (r) {
            K.invalidate("profile/creatures/"); K.haptic("ok"); K.toast("تخم گذاشته شد! والدها آزاد شدن."); if (ctx.alive()) apply(r);
          }).catch(noop);
        });
        K.on(root, "hatch", function (el) {
          K.api.post("breed/cave/hatch/", { egg: +el.dataset.id }, el).then(function (r) {
            K.invalidate("profile/creatures/");
            if (ctx.alive() && r.cave) apply(r.cave);
            return reveal({ title: "تخم سر باز کرد!", creature: r.child, button: "عالیه",
              text: (r.top_reached ? "به بالاترین رده‌ی ممکنِ این جفت رسید!" : "این‌بار نایابیِ پایین‌تری دراومد.") + " والدین: " + r.parents.join(" و ") });
          }).catch(noop);
        });
        function rush(el, path, body, what, done) {
          K.confirm({ title: what, icon: "bolt", ok: "آره، فوری‌کن", cancel: "نه",
            html: "<p>با حداکثر <b class=\"t-diamond\">" + K.n(el.dataset.price) + " الماس</b> " + done + "</p><p class=\"sm\">هرچی زمانِ کمتری مونده باشه ارزون‌تر حساب می‌شه. موجودی الماس: " + K.n(K.res ? K.res.diamonds : d.diamonds) + "</p>" }).then(function (yes) {
            if (!yes) return;
            var before = K.res ? K.res.diamonds : null;
            K.api.post(path, body, el).then(function (r) {
              var spent = before != null && K.res ? before - K.res.diamonds : 0;
              K.invalidate("profile/creatures/"); K.haptic("ok"); K.toast(spent > 0 ? "فوری شد · " + fmt(spent) + " الماس" : "آماده بود؛ الماسی کم نشد."); if (ctx.alive()) apply(r);
            }).catch(noop);
          });
        }
        K.on(root, "rush", function (el) { rush(el, "breed/cave/finish/", { job: +el.dataset.id }, "فوری‌کردن جفت‌گیری", "همین الان تخم گذاشته می‌شه و والدها آزاد می‌شن."); });
        K.on(root, "egg-rush", function (el) { rush(el, "breed/cave/egg_finish/", { egg: +el.dataset.id }, "فوری‌کردن تخم", "همین الان آماده‌ی سر باز کردن می‌شه."); });
        K.on(root, "cancel", function (el) {
          K.confirm({ title: "لغو جفت‌گیری", danger: true, ok: "آره، لغو کن", cancel: "انصراف", text: "هر دو هیولا آزاد می‌شن ولی DNAای که خرج کردی برنمی‌گرده. مطمئنی؟" }).then(function (yes) {
            if (!yes) return;
            K.api.post("breed/cave/cancel/", { job: +el.dataset.id }, el).then(function (r) { K.invalidate("profile/creatures/"); K.toast("لغو شد. DNA برنمی‌گرده."); if (ctx.alive()) apply(r); }).catch(noop);
          });
        });
      });
    }
  });

  // ═════════════════════════ the cave: choose a pair ═════════════════════════
  K.screen("br_pair", {
    title: "جفت بفرست غار", tab: "base",
    render: function (root, params, ctx) {
      var fresh = params.cave && Date.now() - (params.at || 0) < 60000;
      return (fresh ? Promise.resolve(params.cave) : K.api.get("breed/cave/")).then(function (cave) {
        var freeIds = {}, info = null, loading = false, seq = 0;
        params.cave = null;   // only the first paint may trust it
        cave.free_ids.forEach(function (id) { freeIds[id] = 1; });
        if (params.a && !freeIds[params.a.id]) params.a = null;
        if (params.b && !freeIds[params.b.id]) params.b = null;

        function oddsHtml() {
          if (loading || !info) return '<div class="sk" style="height:150px;margin-top:12px"></div>';
          var h = '<div class="h2">' + K.ic("gem") + 'شانس رده‌ی تخم</div><div class="panel pad">';
          if (info.certain) h += '<div class="br-odds"><i class="c-' + info.top + '" style="width:100%"></i></div><div class="br-legend"><span>' + rar(info.top) + "</span><span>حتماً " + pct(100) + "</span></div>";
          else h += '<div class="br-odds"><i class="c-' + info.top + '" style="width:' + info.top_pct + '%"></i><i class="c-' + info.fallback + '" style="width:' + info.fallback_pct + '%"></i></div>' +
            '<div class="br-legend"><span>' + rar(info.top) + " " + pct(info.top_pct) + "</span><span>" + rar(info.fallback) + " " + pct(info.fallback_pct) + "</span></div>";
          var notes = [];
          if (!info.certain) info.children.forEach(function (ch) {
            if (ch.halved) notes.push("اگه نوزاد «" + K.esc(ch.name) + "» (" + K.esc(K.elLabel(ch.element)) + ") باشه، شانس " + K.esc(K.rarLabel(info.top)) + " می‌شه " + pct(ch.top_pct) + ".");
          });
          if (info.luck_pct > 0 && !info.certain) notes.push("هفته‌ی غار خوش‌شانس: اگه تخم همین هفته باز بشه " + pct(info.luck_pct) + " شانسِ بیشتر برای رده‌ی بالا داره.");
          notes.push("نژاد نوزاد تصادفیه: یکی از دو والد. رده هیچ‌وقت از والدین بالاتر نمی‌ره.");
          h += '<div class="br-notes">' + notes.map(function (n) { return "<p>" + n + "</p>"; }).join("") + "</div></div>";
          h += '<div class="panel kv mt"><div><span>' + K.ic("heart") + " زمان جفت‌گیری</span><span>" + K.dur(info.mating_seconds) + "</span></div>" +
            "<div><span>" + K.ic("egg") + " زمان رشد تخم</span><span>" + K.dur(info.hatch_seconds) + "</span></div>" +
            "<div><span>" + K.ic("dna") + ' هزینه</span><span class="' + (info.enough ? "t-dna" : "t-bad") + '">' + K.n(info.dna) + " DNA</span></div>" +
            "<div><span>موجودی تو</span><span>" + K.n(info.have_dna) + " DNA</span></div></div>";
          if (!info.enough) h += '<div class="callout bad mt">' + K.ic("warn") + "<span>DNA کافی نداری؛ این جفت‌گیری " + K.n(info.dna) + " DNA لازم داره.</span></div>";
          if (info.problem) h += '<div class="callout warn mt">' + K.ic("warn") + "<span>" + K.esc(info.problem) + "</span></div>";
          return h;
        }
        function draw() {
          var both = params.a && params.b;
          root.innerHTML = '<p class="lead">دو هیولای آزاد انتخاب کن. تا آخر جفت‌گیری هر دو مشغول می‌مونن؛ بعدش تخم می‌ذارن و آزاد می‌شن.</p>' +
            pair(params.a, params.b, "heart", "pick-a", "pick-b", "والد اول", "والد دوم") +
            (both ? oddsHtml() : '<div class="callout mt">' + K.ic("info") + "<span>والدین هم‌رده: " + pct(cave.rules.same_top_pct) + " همون رده. والدین متفاوت‌رده: بیشتر وقت‌ها رده‌ی پایین‌تر درمیاد. رده هیچ‌وقت از والدین بالاتر نمی‌ره.</span></div>" +
              '<button class="btn ghost block mt" data-act="guide">' + K.ic("doc") + "راهنمای کامل غار</button>") +
            '<div class="br-foot"><button class="btn primary lg block" data-act="go"' + (both && info && !loading && info.enough && !info.problem ? "" : " disabled") + ">" + K.ic("cave") + "بذارش توی غار" +
            (both && info && !loading ? '<span class="cost">' + K.ic("dna") + K.short(info.dna) + "</span>" : "") + "</button></div>";
        }
        function refresh() {
          info = null;
          if (!(params.a && params.b)) { loading = false; draw(); return; }
          loading = true; draw();
          var mine = ++seq;
          K.api.get("breed/cave/preview/?a=" + params.a.id + "&b=" + params.b.id).then(function (r) {
            if (mine !== seq || !ctx.alive()) return;
            info = r; loading = false; draw();
          }).catch(function (err) {
            if (mine !== seq || !ctx.alive()) return;
            loading = false; params.b = null; draw(); K.toast(err.message, "err");
          });
        }
        function pick(which) {
          var other = which === "a" ? params.b : params.a;
          K.pickCreature({ title: which === "a" ? "والد اول" : "والد دوم", sub: "فقط هیولاهای آزاد می‌تونن برن توی غار.", exclude: other ? [other.id] : [],
            disabled: function (c) { return freeIds[c.id] ? "" : c.active ? "هیولای فعالته؛ اول یکی دیگه رو فعال کن." : "الان مشغوله (معدن، غار یا مأموریت اعزامی)."; },
            note: null, empty: "هیولای آزادی نداری." }).then(function (c) {
            if (!c || !ctx.alive()) return;
            params[which] = c; refresh();
          });
        }
        K.on(root, "pick-a", function () { pick("a"); });
        K.on(root, "pick-b", function () { pick("b"); });
        K.on(root, "guide", function () { guideSheet(cave.rules); });
        K.on(root, "go", function (el) {
          if (!info) return;
          K.confirm({ title: "بفرستمشون توی غار؟", icon: "cave", ok: "بذارش توی غار", cancel: "بی‌خیال",
            html: "<p>«" + K.esc(params.a.name) + "» و «" + K.esc(params.b.name) + "» برای " + K.dur(info.mating_seconds) + " مشغول می‌شن. اگه لغو کنی DNA برنمی‌گرده.</p>" +
              '<div class="items"><span class="it t-dna">' + K.ic("dna") + '<span style="color:var(--text)">' + K.n(info.dna) + " DNA</span></span></div>" }).then(function (yes) {
            if (!yes) return;
            K.api.post("breed/cave/start/", { a: params.a.id, b: params.b.id }, el).then(function () {
              K.invalidate("profile/creatures/"); K.haptic("ok"); K.toast("رفتن توی غار!");
              if (ctx.alive()) K.back();
            }).catch(noop);
          });
        });
        if (!cave.built) { root.innerHTML = lockedState(cave); K.on(root, "base", function () { K.tab("base"); }); return; }
        refresh();
      });
    }
  });

  // ═════════════════════════ entry points ═════════════════════════
  K.hub("base", { id: "fusion", title: "ادغام", sub: "دو هیولای یکسان، یه ستاره بالاتر", icon: "merge", color: "var(--accent-2)", go: "br_fusion", order: 40, hall: HALL });
  K.hub("base", { id: "cave", title: "غار", sub: "جفت بفرست، تخم بگیر", icon: "egg", color: "var(--dna)", go: "br_cave", order: 42, hall: HALL });

  /* «ادغام» on the creature screen, like the bot's «ورود به فیوژن (N ستاره)»: always there below the last
     star — the fusion card explains what is missing. With the collection cached it also says whether a
     twin is waiting. Drawn as one of the upgrade-path tiles of 30_creature. */
  if (K.creatureActions) K.creatureActions.push({
    order: 40,
    render: function (c, me) {
      if (!me || me.hall_level < HALL || c.star >= 5) return "";
      var data = K.cache["profile/creatures/"], twins = 0;
      if (data) data.creatures.forEach(function (x) { if (x.id !== c.id && x.species === c.species && x.rarity === c.rarity && x.star === c.star) twins++; });
      return '<button class="cr-path wide' + (twins ? "" : " dim") + '" data-act="br-fuse" style="--pc:var(--accent-2)"><span class="ico-box">' + K.ic("merge") + '</span><span class="grow"><b>ادغام · رسیدن به ' + K.n(c.star + 1) + " ستاره</b><small>" +
        (twins ? K.n(twins) + " هیولای یکسان داری؛ سقف سطح و اندام‌ها بالاتر می‌ره" : "یه «" + K.esc(c.species) + "» دیگه با همین نایابی و ستاره لازمه") + '</small></span><span class="chev">' + K.ic("chevron") + "</span></button>";
    },
    bind: function (root, c) { K.on(root, "br-fuse", function () { K.go("br_fuse", { id: c.id, from: "creature" }); }); }
  });
})(window.K);
