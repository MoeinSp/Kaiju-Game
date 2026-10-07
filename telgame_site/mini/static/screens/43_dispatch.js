/* «مأموریت اعزامی» — dp_home (running missions + today's board + HQ perks) and dp_offer
   (one offer: every idle creature that may go, with the exact reward it would bring).
   API: dispatch/…  Shares the reward helpers of 15_daily.js (K.dy). */
(function (K) {
  "use strict";

  var D = K.dy || {};
  var DOT = ' <span class="faint">·</span> ';
  var HALL = 2;   // SECTION_HALL_REQ["dispatch"] — the server enforces it
  var FOCUS = { gold: ["طلا", "coin", "var(--coin)"], dna: ["DNA", "dna", "var(--dna)"], mixed: ["طلا و DNA", "swap", "var(--accent)"] };
  var TPL_ICON = { ruins: "hall", mine: "hammer", caravan: "flag", wreck: "drop", swamp: "flask", nest: "egg", fossil: "skull", patrol: "shield", crater: "spark", forest: "claw" };

  function num(x) { return Number(x || 0).toLocaleString("en-US"); }
  function extras(o) { return D.extras ? D.extras(o) : []; }
  function reveal(o) { return D.reveal ? D.reveal(o) : K.reward(o); }
  function tplIcon(key) { return TPL_ICON[key] || "compass"; }
  /* «6 ساعت» — and the real time when this week's rule shortens it */
  function durText(o) {
    return K.n(o.hours) + " ساعت" + (o.seconds && o.seconds !== o.hours * 3600 ? ' <span class="t-good">(این هفته ' + K.esc(K.dur(o.seconds)) + ")</span>" : "");
  }
  function offerTags(o, meta) {
    var f = FOCUS[o.focus] || FOCUS.mixed;
    return '<span class="tag plain">' + K.ic("clock") + durText(o) + "</span>" +
      '<span class="tag" style="color:' + f[2] + '">' + K.ic(f[1]) + f[0] + "</span>" +
      '<span class="tag e-' + o.element + '">' + K.ic(K.EL_ICON[o.element] || "atom") + K.esc(K.elLabel(o.element)) + ' <span class="num">+' + meta.element_bonus + "%</span></span>" +
      (o.min_rarity !== "common" ? '<span class="tag c-' + o.min_rarity + '">' + K.ic("gem") + "حداقل " + K.esc(K.rarLabel(o.min_rarity)) + "</span>" : "") +
      '<span class="tag" style="color:var(--good)">' + K.ic("gift") + 'شگفتی <span class="num">' + o.chance + "%</span></span>";
  }
  function specialTag(meta) { return '<span class="tag dp-special">' + K.ic("star") + 'ویژه <span class="num">×' + Number(meta.special_mult) + "</span></span>"; }

  // ═════════════════════════ collect: the reveal ═════════════════════════
  function bonusHtml(b) {
    var parts = [];
    if (b.diamonds) parts.push('<span class="t-diamond b">' + K.ic("gem") + " " + K.n(b.diamonds) + " الماس</span>");
    extras(b).forEach(function (h) { parts.push('<span class="b dy-x">' + h + "</span>"); });
    return parts.join(DOT);
  }
  function resultBlock(r, many) {
    return '<div class="dp-res">' +
      (many ? '<div class="dp-res-h"><span class="ico-box" style="color:var(--accent)">' + K.ic(tplIcon(r.key)) + '</span><div class="grow"><b class="dy-block cut">' + K.esc(r.title) + '</b><span class="xs muted">' + K.esc(r.name) + "</span></div></div>" +
        '<div class="sm dp-res-l">' + K.amounts({ coins: r.coins, dna: r.dna, xp: r.xp }) + "</div>" : "") +
      (r.bonus ? '<div class="dp-bonus"><div class="b">' + K.ic("spark") + " جایزه‌ی شگفتی پیدا شد!</div><div class=\"sm\">" + bonusHtml(r.bonus) + "</div></div>"
               : '<div class="xs muted dp-res-l">این بار شگفتی‌ای همراهش نبود.</div>') +
      (r.levels ? '<div class="sm t-good b dp-res-l">' + K.ic("up") + " " + K.esc(r.name) + " به سطح " + K.n(r.level) + " رسید!</div>" : "") + "</div>";
  }
  function showResults(results) {
    var many = results.length > 1, coins = 0, dna = 0, xp = 0;
    results.forEach(function (r) { coins += r.coins; dna += r.dna; xp += r.xp; });
    var one = results[0];
    return reveal({
      icon: "compass", title: many ? num(results.length) + " مأموریت تموم شد!" : "مأموریت تموم شد!",
      text: many ? "هیولاهات برگشتن؛ این هم جایزه‌هاشون." : one.title + " — " + one.name + (one.xp ? " (XP به خودش رسید)" : ""),
      coins: coins, dna: dna, xp: many ? 0 : xp,
      html: '<div class="pad dp-results">' + results.map(function (r) { return resultBlock(r, many); }).join("") + "</div>"
    });
  }

  // ═════════════════════════ the panel ═════════════════════════
  function runCard(m) {
    var c = m.creature || { name: "هیولا", img: "", rarity: "" };
    var done = m.total ? Math.max(0, Math.min(1, (m.total - m.left) / m.total)) : 1;
    return '<div class="panel pad dp-run' + (m.ready ? " ready" : "") + '">' +
      '<div class="dp-run-h"><span class="ico-box" style="color:var(--accent)">' + K.ic(tplIcon(m.key)) + "</span>" +
      '<div class="grow"><b class="dy-block cut">' + K.esc(m.title) + '</b><span class="xs muted">مأموریت ' + K.n(m.hours) + " ساعته</span></div>" +
      (m.ready ? '<span class="tag" style="color:var(--good)">' + K.ic("check") + "برگشته</span>" : '<span class="timer" data-left="' + m.left + '" data-done="برگشت"></span>') + "</div>" +
      K.fighter(c, '<div class="sm dp-run-r"><span class="muted">جایزه‌ی قطعی:</span> ' + K.amounts(m.reward) + "</div>") +
      (m.ready ? '<button class="btn good block mt" data-act="dp-collect" data-id="' + m.id + '">' + K.ic("gift") + "دریافت جایزه</button>"
               : '<div class="mt">' + K.bar(done) + '</div><button class="btn ghost sm block dp-cancel" data-act="dp-cancel" data-id="' + m.id + '" data-name="' + K.esc(c.name) + '">' + K.ic("close") + "برگردوندن (بدون جایزه)</button>") +
      "</div>";
  }
  function offerCard(o, d, full) {
    return '<div class="panel pad dp-offer' + (o.taken ? " taken" : "") + (o.special ? " special" : "") + '">' +
      '<div class="dp-run-h"><span class="ico-box lg" style="color:' + (o.special ? "var(--gold)" : "var(--accent)") + '">' + K.ic(tplIcon(o.key)) + "</span>" +
      '<div class="grow"><b class="dy-block">' + K.esc(o.title) + '</b><span class="xs muted dy-block">' + K.esc(o.flavor) + "</span></div>" + (o.special ? specialTag(d) : "") + "</div>" +
      '<div class="dp-tags">' + offerTags(o, d) + "</div>" +
      (o.taken ? '<div class="sm muted dp-sent">' + K.ic("check") + " امروز فرستادیش</div>"
               : '<button class="btn primary block"' + (full ? " disabled" : "") + ' data-act="dp-offer" data-idx="' + o.idx + '">' + K.ic("send") + "انتخاب هیولا</button>") +
      "</div>";
  }

  K.screen("dp_home", {
    title: "مأموریت اعزامی", tab: "battle",
    render: function (root, params, ctx) {
      return K.api.get("dispatch/").then(function (d) {
        var art = (K.meta && K.meta.art && K.meta.art.dispatch) || "";
        var ready = d.missions.filter(function (m) { return m.ready; });
        var open = d.offers.filter(function (o) { return !o.taken; });
        var full = d.missions.length >= d.slots;
        if (D.touch) D.touch("dispatch_ready", ready.length);   // keeps the hub badge fresh

        var html = '<div class="banner dp-banner"' + (art ? ' style="background-image:url(\'' + art + '\')"' : "") + '><div><div class="ttl">مأموریت‌های اعزامی</div>' +
          '<div class="sm" style="color:#c5cee2">هیولاهای بیکارت رو بفرست مأموریت و چند ساعت بعد جایزه بگیر.</div></div></div>' +
          '<div class="tiles mt"><div class="panel info"><span class="ic" style="color:var(--accent)">' + K.ic("users") + "</span><div><small>جایگاه اعزام</small><b>" + K.n(d.missions.length) + " / " + K.n(d.slots) + "</b></div></div>" +
          '<div class="panel info"><span class="ic" style="color:var(--gold)">' + K.ic("building") + "</span><div><small>پایگاه اعزام</small><b>" + (d.hq ? "سطح " + K.n(d.hq) : "ساخته نشده") + "</b></div></div></div>" +
          '<div class="panel dp-perks mt">' + d.perks.map(function (line, i) {
            var cut = line.indexOf(":"), head = cut > 0 ? line.slice(0, cut) : "", body = cut > 0 ? line.slice(cut + 1) : line;
            return '<div class="' + (i ? "next" : "now") + '">' + K.ic(i ? "up" : "check") + "<span>" + (head ? "<b>" + K.esc(head) + ":</b> " : "") + K.esc(body.replace(/^\s+/, "")) + "</span></div>";
          }).join("") +
          (d.slots > d.slots_base ? "<div class=\"now\">" + K.ic("crown") + "<span>" + K.n(d.slots - d.slots_base) + " جایگاه اضافه به‌خاطر اشتراکت</span></div>" : "") +
          (d.hq < d.hq_max ? '<div class="hint">ارتقای «پایگاه اعزام» جایگاه، مأموریت و جایزه رو بیشتر می‌کنه.</div>' : "") + "</div>" +
          (d.rule ? '<div class="callout good mt">' + K.ic("calendar") + "<span><b>" + K.esc(d.rule.title) + "</b>" + (d.rule.desc ? " — " + K.esc(d.rule.desc) : "") + "</span></div>" : "");

        if (d.missions.length) {
          html += '<div class="h2">' + K.ic("compass") + "در مأموریت</div>" +
            (ready.length > 1 ? '<button class="btn good block mb" data-act="dp-collect-all">' + K.ic("gift") + 'دریافت همه<span class="cost num">' + ready.length + "</span></button>" : "") +
            '<div class="dp-list">' + d.missions.map(runCard).join("") + "</div>";
        }
        html += '<div class="h2">' + K.ic("list") + 'مأموریت‌های امروز<span class="more" style="color:var(--muted)">' + K.n(open.length) + " از " + K.n(d.offers.length) + " مونده</span></div>";
        if (!open.length) html += '<div class="callout mb">' + K.ic("check") + "<span>همه‌ی مأموریت‌های امروز رو فرستادی. فردا فهرست تازه می‌آد.</span></div>";
        else if (full) html += '<div class="callout warn mb">' + K.ic("hourglass") + "<span>جایگاه‌هات پره؛ یکی که برگشت می‌تونی مأموریت بعدی رو بفرستی.</span></div>";
        html += '<div class="dp-list">' + open.concat(d.offers.filter(function (o) { return o.taken; })).map(function (o) { return offerCard(o, d, full); }).join("") + "</div>" +
          '<p class="note">عنصرِ کنار هر مأموریت ' + K.n(d.element_bonus) + "% جایزه‌ی بیشتر می‌ده. فهرست هر روز نیمه‌شب تازه می‌شه.</p>";
        root.innerHTML = html;

        K.timers(root, function () { K.after(1200, ctx.reload); });
        K.on(root, "dp-offer", function (el) { K.go("dp_offer", { idx: +el.dataset.idx }); });
        K.on(root, "dp-collect", function (el) {
          K.api.post("dispatch/collect/", { id: +el.dataset.id }, el).then(function (r) {
            K.invalidate("profile/creatures/");
            showResults(r.results).then(function () { ctx.reload(); });
          }, function () {});
        });
        K.on(root, "dp-collect-all", function (el) {
          K.api.post("dispatch/collect_all/", {}, el).then(function (r) {
            K.invalidate("profile/creatures/");
            showResults(r.results).then(function () { ctx.reload(); });
          }, function () {});
        });
        K.on(root, "dp-cancel", function (el) {
          K.confirm({ title: "هیولا رو برگردونم؟", danger: true, ok: "بله، برگرده", cancel: "نه",
                      text: "«" + el.dataset.name + "» برمی‌گرده؛ مأموریت لغو می‌شه و هیچ جایزه‌ای نمی‌گیری. همین مأموریت رو امروز می‌تونی دوباره بفرستی." }).then(function (yes) {
            if (!yes) return;
            K.api.post("dispatch/cancel/", { id: +el.dataset.id }, el).then(function () {
              K.invalidate("profile/creatures/");
              K.toast("هیولات برگشت؛ مأموریت لغو شد.");
              ctx.reload();
            }, function () {});
          });
        });
      });
    }
  });

  // ═════════════════════════ one offer: who goes? ═════════════════════════
  var sortBy = "best";

  function candRow(x) {
    var c = x.creature;
    return '<button class="dp-cand" data-act="dp-pick" data-id="' + c.id + '">' +
      K.fighter(c, '<div class="sm dp-cand-r">' + K.amounts({ coins: x.coins, dna: x.dna, xp: x.xp }) + "</div>" +
        (x.match ? '<span class="tag e-' + c.element + ' dp-match">' + K.ic(K.EL_ICON[c.element] || "atom") + "عنصرش جوره</span>" : "")) + "</button>";
  }

  K.screen("dp_offer", {
    title: "انتخاب هیولا", tab: "battle",
    render: function (root, params, ctx) {
      return K.api.get("dispatch/offer/?idx=" + encodeURIComponent(params.idx)).then(function (d) {
        var o = d.offer, f = FOCUS[o.focus] || FOCUS.mixed, byId = {};
        d.candidates.forEach(function (x) { byId[x.creature.id] = x; });
        ctx.setTitle(o.title);

        var kv = "<div><span>مدت</span><span>" + durText(o) + "</span></div>" +
          "<div><span>جایزه</span><span>" + f[0] + ' <small class="muted" style="font-weight:400">(بر اساس قدرت هیولا)</small></span></div>' +
          '<div><span>عنصر پیشنهادی</span><span class="e-' + o.element + '">' + K.ic(K.EL_ICON[o.element] || "atom") + " " + K.esc(K.elLabel(o.element)) + ' <span class="num">+' + d.element_bonus + "%</span></span></div>" +
          (o.min_rarity !== "common" ? '<div><span>حداقل نایابی</span><span class="c-' + o.min_rarity + '">' + K.esc(K.rarLabel(o.min_rarity)) + "</span></div>" : "") +
          '<div><span>شانس جایزه‌ی شگفتی</span><span class="t-good num">' + o.chance + "%</span></div>" +
          (o.special ? '<div><span>مأموریت ویژه‌ی امروز</span><span class="t-gold">طلا و DNA <span class="num">×' + Number(d.special_mult) + "</span></span></div>" : "") +
          (d.hq_bonus ? "<div><span>پایگاه اعزام سطح " + K.n(d.hq) + '</span><span class="t-good"><span class="num">+' + d.hq_bonus + "%</span> جایزه</span></div>" : "");
        var head = '<div class="panel pad dp-offer' + (o.special ? " special" : "") + '"><div class="dp-run-h"><span class="ico-box lg" style="color:' + (o.special ? "var(--gold)" : "var(--accent)") + '">' + K.ic(tplIcon(o.key)) + "</span>" +
          '<div class="grow"><b class="dy-block">' + K.esc(o.title) + '</b><span class="xs muted dy-block">' + K.esc(o.flavor) + "</span></div>" + (o.special ? specialTag(d) : "") + "</div></div>" +
          '<div class="panel kv mt">' + kv + "</div>";

        if (!d.candidates.length) {
          root.innerHTML = head + K.state("claw", "هیولای بیکارِ مناسبی نداری", "هیولای فعال و هیولاهای مشغول نمی‌تونن برن؛ یه هیولای دیگه آزاد کن یا از باکس و غار بگیر.");
          return;
        }
        root.innerHTML = head + '<div class="h2">' + K.ic("claw") + 'کدوم هیولا بره؟<span class="more" style="color:var(--muted)">' + K.n(d.candidates.length) + " هیولای بیکار</span></div>" +
          '<div class="seg"><button data-act="dp-sort" data-s="best">بیشترین جایزه</button><button data-act="dp-sort" data-s="rare">کمیاب‌ترین</button></div>' +
          '<div class="panel dp-cands" id="dp-cands"></div>' +
          '<button class="btn block mt" data-act="dp-grid">' + K.ic("grid") + "انتخاب از بین همه‌ی هیولاها</button>" +
          '<p class="note">جایزه‌ی هر هیولا همین الان با قدرتش حساب شده' + (d.hq_bonus || o.special ? " (پاداش پایگاه و مأموریت ویژه هم داخلشه)" : "") + ". عنصرِ جور " + K.n(d.element_bonus) + "% بیشتر می‌آره.</p>";

        function draw() {
          var list = d.candidates.slice();   // the server's order = rarest, then strongest (the bot's picker order)
          if (sortBy === "best") list.sort(function (a, b) { return b.coins - a.coins || b.dna - a.dna; });
          root.querySelector("#dp-cands").innerHTML = list.map(candRow).join("");
          Array.prototype.forEach.call(root.querySelectorAll(".seg button"), function (b) { b.classList.toggle("on", b.dataset.s === sortBy); });
        }
        draw();

        function confirmSend(x) {
          var c = x.creature;
          var body = '<div class="dp-confirm">' + K.fighter(c, '<div class="xs muted">' + K.esc(K.rarLabel(c.rarity)) + "</div>") +
            '<div class="dp-cf-line ' + (x.match ? "t-good" : "muted") + '">' + K.ic(x.match ? "check" : "minus") + (x.match ? " عنصرش جوره: +" + d.element_bonus + "% حساب شده" : " عنصرش با مأموریت جور نیست (بدون " + d.element_bonus + "% اضافه)") + "</div>" +
            '<div class="dp-cf-box"><div class="xs muted">جایزه‌ی قطعی بعد از ' + durText(o) + '</div><div class="dp-cf-amt">' + K.amounts({ coins: x.coins, dna: x.dna, xp: x.xp }) + "</div>" +
            '<div class="sm">' + K.ic("gift") + ' شانس جایزه‌ی شگفتی: <b class="t-good num">' + o.chance + "%</b>" + (o.special ? DOT + '<span class="t-gold">مأموریت ویژه (×' + Number(d.special_mult) + " حساب شده)</span>" : "") + "</div></div>" +
            '<p class="xs muted" style="margin:10px 0 0">تا وقتی برنگشته، این هیولا رو نمی‌تونی فعال، ترکیب، منتقل یا راهی معدن و غار کنی.</p></div>';
          K.confirm({ title: o.title, icon: "send", html: body, ok: "بفرستش", cancel: "یه هیولای دیگه" }).then(function (yes) {
            if (!yes) return;
            K.api.post("dispatch/send/", { idx: o.idx, creature_id: c.id }).then(function (r) {
              K.invalidate("profile/creatures/");
              K.haptic("ok");
              var msg = r.name + " راهی مأموریت شد و " + K.dur(r.left) + " دیگه برمی‌گرده.";
              if (r.missions && r.missions.length) reveal({ icon: "send", title: "راهی شد!", text: msg, missions: r.missions }).then(function () { K.back(); });
              else { K.toast(msg); K.back(); }
            }, function () { ctx.reload(); });
          });
        }
        K.on(root, "dp-sort", function (el) { K.haptic(); sortBy = el.dataset.s; draw(); });
        K.on(root, "dp-pick", function (el) { var x = byId[+el.dataset.id]; if (x) confirmSend(x); });
        K.on(root, "dp-grid", function () {
          var order = (K.meta && K.meta.rarity_order) || [];
          K.pickCreature({
            title: "کدوم هیولا بره؟", sub: "برچسب هر هیولا طلای قطعی‌شه. هیولاهای کم‌رنگ الان نمی‌تونن این مأموریت رو برن.",
            note: function (c) { var x = byId[c.id]; return x ? "+" + num(x.coins) + " طلا" : ""; },
            disabled: function (c) {
              if (byId[c.id]) return "";
              if (c.active) return "هیولای فعالت نمی‌تونه بره مأموریت.";
              if (c.busy) return "این هیولا الان مشغوله.";
              if (order.indexOf(c.rarity) < order.indexOf(o.min_rarity)) return "این مأموریت حداقل یه هیولای " + K.rarLabel(o.min_rarity) + " می‌خواد.";
              return "این هیولا الان نمی‌تونه بره.";
            }
          }).then(function (c) { if (c && byId[c.id]) confirmSend(byId[c.id]); });
        });
      });
    }
  });

  K.hub("battle", { id: "dispatch", title: "اعزام", sub: "هیولاهای بیکار رو بفرست مأموریت", icon: "compass", color: "var(--accent)", go: "dp_home", order: 45, hall: HALL,
                    badge: function () { var h = D.last && D.last(); return h ? h.dispatch_ready : 0; } });
})(window.K);
