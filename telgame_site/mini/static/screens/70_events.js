/* Events and progression: the monthly festival, the weekly event, the season pass and the
   achievements. Every number comes from /app/api/events/… (game.festival / game.events /
   game.battlepass / game.achievements) — nothing is computed here. */
(function (K) {
  "use strict";

  K.addIcons({
    tent: '<path d="M12 6 3.500 20h17zM12 2.500V6M12 3l3.200 1.200L12 5.400M12 20l-2.500-5.500L12 11l2.500 3.500z"/>',
    token: '<circle cx="12" cy="12" r="8.500"/><path d="m12 7.300 1.400 2.900 3.200.4-2.300 2.200.6 3.200L12 14.500 9.100 16l.6-3.200-2.300-2.200 3.200-.4z"/>',
    sliders: '<path d="M4 7h8M16 7h4M4 12h2M10 12h10M4 17h10M18 17h2"/><circle cx="14" cy="7" r="2"/><circle cx="8" cy="12" r="2"/><circle cx="16" cy="17" r="2"/>'
  });

  // ───────────────────────── shared bits ─────────────────────────
  function speedText(r) { return r.speedup_label ? "کارت سرعت " + K.esc(r.speedup_label) : "کارت سرعت (جمعاً " + K.n(r.speedup) + " دقیقه)"; }
  /* a game reward {coins, dna, diamonds, speedup, speedup_label} as inline chips */
  function rewardHtml(r, sep) {
    var parts = [], a = K.amounts(r || {}, sep);
    if (a) parts.push(a);
    if (r && r.speedup) parts.push('<span class="t-accent b">' + K.ic("hourglass") + " " + speedText(r) + "</span>");
    return parts.join(sep || ' <span class="faint">·</span> ') || '<span class="faint">—</span>';
  }
  /* the same reward for K.reward(...) */
  function reveal(r, o) {
    o = o || {}; o.coins = r.coins; o.dna = r.dna; o.diamonds = r.diamonds;
    if (r.speedup) o.extra = [K.ic("hourglass") + '<span style="color:var(--text)">' + speedText(r) + "</span>"];
    return K.reward(o);
  }
  function banner(img, title, subHtml) {
    return '<div class="banner ev-banner"' + (img ? ' style="background-image:url(\'' + img + '&s=l\')"' : "") + '><div class="grow"><div class="ttl">' + K.esc(title) + "</div>" + (subHtml ? '<div class="ev-bsub">' + subHtml + "</div>" : "") + "</div></div>";
  }
  function timerHtml(left, done) { return '<span class="timer" data-fmt="long" data-left="' + Number(left) + '" data-done="' + K.esc(done || "تموم شد") + '"></span>'; }
  /* live countdowns; when one of them ends the screen reloads once */
  function watch(root, ctx) { K.timers(root, function () { K.after(1500, ctx.reload); }); }
  function stat(icon, cls, label, valueHtml) {
    return '<div class="panel info ' + cls + '"><span class="ic">' + K.ic(icon) + "</span><div><small>" + label + "</small><b>" + valueHtml + "</b></div></div>";
  }
  function seg(items, cur, attr) {
    return '<div class="seg">' + items.map(function (s) { return '<button class="' + (s[0] === cur ? "on" : "") + '" data-' + attr + '="' + s[0] + '">' + (s[2] ? K.ic(s[2]) : "") + s[1] + "</button>"; }).join("") + "</div>";
  }

  // ───────────────────────── festival ─────────────────────────
  var EARN_ICON = { hunt: "target", arena_attack: "swords", dispatch: "compass", worldboss_hit: "skull", raid_attack: "shield", collect: "building", feed: "food", wheel_spin: "wheel" };
  var SHOP_ICON = { capsule: "food", speedup: "hourglass", gold: "coin", dna: "dna", diamonds: "gem", golden: "chest", magical: "box", grand: "crown" };
  var SHOP_TINT = { capsule: "var(--earth)", speedup: "var(--accent)", gold: "var(--coin)", dna: "var(--dna)", diamonds: "var(--diamond)", golden: "var(--gold)", magical: "var(--epic)", grand: "var(--legendary)" };
  var festTab = "shop";

  function prizeRange(prizes, i) {
    var from = i ? prizes[i - 1].max_rank + 1 : 1, to = prizes[i].max_rank;
    return from === to ? "رتبه‌ی " + K.n(to) : "رتبه‌ی " + K.n(from) + " تا " + K.n(to);
  }
  function prizesPanel(d) {
    return '<div class="h2">' + K.ic("trophy") + 'جایزه‌ی جدول</div><div class="panel kv">' + d.prizes.map(function (p, i) {
      return "<div><span>" + prizeRange(d.prizes, i) + "</span><span>" + K.amounts({ diamonds: p.diamonds }) + "</span></div>";
    }).join("") + '</div><p class="note">جدول بر اساس سکه‌ی «جمع‌شده» است و جایزه‌ش آخر جشنواره واریز می‌شه.</p>';
  }
  function earnPanel(d, live) {
    return '<div class="panel list">' + d.earn.map(function (e) {
      var full = live && e.done >= e.cap;
      return '<div><span class="ic" style="color:' + (full ? "var(--good)" : "var(--gold)") + '">' + K.ic(full ? "check" : (EARN_ICON[e.key] || "token")) + '</span><span class="t">' + K.esc(e.label) +
        "<small>" + K.n(e.per) + " سکه · تا " + K.n(e.day_max) + " سکه در روز</small>" +
        (live ? '<span class="ev-earnbar">' + K.bar(e.done / e.cap, full ? "good" : "gold") + "</span>" : "") + "</span>" +
        (live ? '<span class="v sm">' + K.n(e.done * e.per) + '<span class="faint"> / ' + Number(e.day_max) + "</span></span>" : "") + "</div>";
    }).join("") +
      '<div><span class="ic" style="color:var(--accent)">' + K.ic("calcheck") + '</span><span class="t">هر مأموریت<small>روزانه ' + K.n(d.mission_daily) + " سکه · هفتگی " + K.n(d.mission_weekly) + " سکه</small></span></div></div>" +
      '<p class="note">هر کار سقف روزانه داره؛ برای سکه‌ی بیشتر باید به همه‌ی بخش‌های بازی سر بزنی.</p>';
  }

  K.screen("ev_festival", {
    title: "جشنواره", tab: "more",
    render: function (root, params, ctx) {
      return K.api.get("events/festival/").then(function (d) {
        var th = d.theme, elTag = K.elTag(th.element);
        if (!d.active) {
          root.innerHTML = banner(d.banner, "جشنواره‌ی ماهانه", "روز " + K.n(d.start_day) + " تا " + K.n(d.end_day) + " هر ماه") +
            '<div class="panel pad glow ev-next mt"><div class="muted sm">جشنواره‌ی بعدی</div><div class="ev-nextname">«' + K.esc(th.title) + "» " + elTag + "</div>" +
            (d.seconds_until_next > 0 ? '<div class="ev-big">' + timerHtml(d.seconds_until_next, "شروع شد") + "</div>" : "") + "</div>" +
            '<div class="callout mt">' + K.ic("info") + "<div>هر ماه یه هفته جشنواره‌ست: از همه‌ی کارهات «سکه‌ی جشنواره» می‌افته و می‌تونی از فروشگاه مخصوصش خرید کنی. سکه‌ها آخر جشنواره باطل می‌شن. جایزه‌ی بزرگ یه هیولای افسانه‌ای با " + K.n(d.grand_cost) + " سکه‌ست.</div></div>" +
            '<div class="h2">' + K.ic("token") + "چطور سکه می‌گیری؟</div>" + earnPanel(d, false) + prizesPanel(d);
          watch(root, ctx);
          return;
        }
        root.innerHTML = banner(d.banner, "جشنواره‌ی «" + th.title + "»", elTag + (d.seconds_left > 0 ? " " + timerHtml(d.seconds_left, "تموم شد") : "")) +
          '<div class="tiles c3 mt ev-stats">' + stat("token", "t-gold", "سکه‌ی تو", K.n(d.coins)) + stat("up", "t-accent", "جمع‌شده", K.n(d.earned)) +
          stat("podium", "t-cup", "رتبه", d.rank ? K.n(d.rank) : '<span class="faint">—</span>') + "</div>" +
          '<div class="callout warn mt mb">' + K.ic("warn") + "<div>سکه‌ها آخر جشنواره باطل می‌شن؛ قبلش خرجشون کن. جایزه‌ی بزرگ: هیولای افسانه‌ایِ " + K.esc(th.element_label) + " با " + K.n(d.grand_cost) + " سکه.</div></div>" +
          '<div id="ev-fseg"></div><div id="ev-fbody"></div>';
        var segEl = root.querySelector("#ev-fseg"), body = root.querySelector("#ev-fbody");

        function draw() {
          segEl.innerHTML = seg([["shop", "فروشگاه", "cart"], ["earn", "کسب سکه", "token"], ["top", "جدول", "podium"]], festTab, "ftab");
          if (festTab === "earn") { body.innerHTML = earnPanel(d, true); return; }
          if (festTab === "top") {
            body.innerHTML = (d.top.length ? '<div class="panel ev-lb">' + d.top.map(function (r) {
              return '<div class="' + (r.me ? "me" : "") + '"><span class="rk num">' + r.rank + '</span><span class="nm">' + K.esc(r.name) + (r.me ? ' <span class="faint sm">(تو)</span>' : "") + "</span>" +
                '<span class="ev-topv"><span class="t-gold b">' + K.ic("token") + " " + K.n(r.earned) + "</span>" + (r.prize ? '<small class="t-diamond">' + K.ic("gem") + " " + K.n(r.prize) + "</small>" : "") + "</span></div>";
            }).join("") + "</div>" : '<div class="panel">' + K.state("podium", "هنوز کسی سکه جمع نکرده", "اولین نفر جدول تو باش.") + "</div>") +
              (d.rank && !d.top.some(function (r) { return r.me; }) ? '<div class="panel ev-mine mt"><span class="rk num">' + d.rank + '</span><span class="grow">رتبه‌ی تو</span><span class="t-gold b">' + K.ic("token") + " " + K.n(d.earned) + "</span></div>" : "") + prizesPanel(d);
            return;
          }
          body.innerHTML = '<div class="ev-shop">' + d.shop.map(function (it) {
            var out = it.left <= 0, poor = d.coins < it.cost;
            return '<div class="panel ev-item' + (out ? " out" : "") + (it.key === "grand" ? " grand" : "") + '"><span class="ico-box lg" style="color:' + (SHOP_TINT[it.key] || "var(--accent)") + '">' + K.ic(SHOP_ICON[it.key] || "gift") + "</span>" +
              '<div class="grow"><div class="b">' + K.esc(it.title) + '</div><div class="sm muted">' + (out ? "سقف خرید پر شد" : K.n(it.bought) + " از " + K.n(it.limit) + " خریده شده") + "</div></div>" +
              '<button class="btn sm ' + (out || poor ? "" : "gold") + '" data-act="fbuy" data-key="' + K.esc(it.key) + '"' + (out ? " disabled" : "") + ">" + (out ? K.ic("check") + "تموم شد" : K.ic("token") + K.n(it.cost)) + "</button></div>";
          }).join("") + "</div>";
        }
        draw();
        segEl.addEventListener("click", function (ev) { var b = ev.target.closest("[data-ftab]"); if (!b) return; K.haptic(); festTab = b.dataset.ftab; draw(); });
        K.on(root, "fbuy", function (el) {
          var it = d.shop.filter(function (x) { return x.key === el.dataset.key; })[0]; if (!it) return;
          if (d.coins < it.cost) { K.haptic("err"); K.toast("سکه‌ی جشنواره کافی نداری (" + d.coins + " از " + it.cost + ").", "err"); return; }
          K.confirm({ title: "خرید از جشنواره", icon: SHOP_ICON[it.key] || "cart", ok: "بخر", cancel: "بی‌خیال",
            html: "<p>«" + K.esc(it.title) + "» رو با <b>" + K.n(it.cost) + "</b> سکه‌ی جشنواره می‌خری؟<br>بعدش " + K.n(d.coins - it.cost) + " سکه برات می‌مونه.</p>" }).then(function (yes) {
            if (!yes) return;
            K.api.post("events/festival/buy/", { key: it.key }, el).then(function (r) {
              if (r.creatures.length) K.invalidate("profile/creatures/");
              if (r.items.length) K.invalidate("profile/equipment/");
              K.reward({ title: "خریدی!", text: r.got, icon: SHOP_ICON[it.key] || "gift", creatures: r.creatures, items: r.items }).then(ctx.reload);
            }, function () { ctx.reload(); });
          });
        });
        watch(root, ctx);
      });
    }
  });

  // ───────────────────────── weekly event ─────────────────────────
  var WEEK_ICON = { double_xp: "up", double_pass: "ticket", bounty: "gift", golden: "star", element_week: "atom", fast_dispatch: "compass", cave_luck: "egg", surprise_week: "spark" };
  function eventTags(e) {
    var t = "";
    if (e.xp_mult > 1) t += K.tag("XP آزمایشگاه ×" + K.n(e.xp_mult), "var(--xp)", "up");
    if (e.pass_mult > 1) t += K.tag("امتیاز پاس ×" + K.n(e.pass_mult), "var(--accent)", "ticket");
    if (e.reward_mult > 1) t += K.tag("جایزه‌ی روزانه ×" + K.n(e.reward_mult), "var(--gold)", "gift");
    if (e.element) t += K.elTag(e.element);
    return t;
  }

  K.screen("ev_week", {
    title: "رویداد هفته", tab: "more",
    render: function (root, params, ctx) {
      return K.api.get("events/week/").then(function (d) {
        var e = d.event, tags = eventTags(e);
        var html = banner(d.banner, e.title, d.seconds_left > 0 ? timerHtml(d.seconds_left, "تموم شد") : "") +
          '<div class="panel pad mt ev-rule"><span class="ico-box lg t-accent">' + K.ic(WEEK_ICON[e.key] || "calendar") + '</span><div class="grow"><div class="b">' + (e.rule ? "قانون این هفته" : "بونوس این هفته") + '</div><div class="sm" style="color:#cfd8ee">' + K.esc(e.desc) + "</div>" +
          (tags ? '<div class="ev-tags">' + tags + "</div>" : "") + "</div></div>" +
          '<div class="h2">' + K.ic("gift") + "جایزه‌ی روزانه‌ی رویداد</div>" +
          '<div class="panel pad ' + (d.can_claim ? "gold" : "") + '"><div class="flex between wrapx"><div><div class="sm muted">جایزه‌ی امروز · روز ' + K.n(d.day) + " از " + K.n(7) + '</div><div class="ev-today">' + rewardHtml(d.today_reward) + "</div></div>" +
          (d.can_claim ? '<button class="btn gold" data-act="wclaim">' + K.ic("gift") + "دریافت</button>" : '<span class="tag" style="color:var(--good)">' + K.ic("check") + "گرفتی</span>") + "</div>" +
          (d.can_claim ? "" : '<div class="sm muted" style="margin-top:8px">جایزه‌ی امروزو گرفتی. فردا دوباره بیا.</div>') + "</div>" +
          '<div class="ev-days">' + d.days.map(function (x) {
            var cls = x.day < d.day ? "past" : x.day === d.day ? (d.can_claim ? "now" : "now got") : "";
            return '<div class="ev-day ' + cls + '"><small>روز ' + K.n(x.day) + '</small><span class="ev-dayic">' + K.ic(x.day === d.day && !d.can_claim ? "check" : x.reward.diamonds ? "gem" : "gift") + "</span>" +
              '<span class="ev-dayrw">' + rewardHtml(x.reward, "<br>") + "</span></div>";
          }).join("") + "</div>" +
          '<p class="note">جایزه در روز ۴ و روز ۷ بزرگ‌تر می‌شه — روز آخر جک‌پات الماس! جایزه‌ی هر روز فقط همون روز قابل دریافته.</p>';
        if (d.upcoming.length) {
          html += '<div class="h2">' + K.ic("calendar") + 'هفته‌های بعد</div><div class="panel list">' + d.upcoming.map(function (u) {
            return '<div><span class="ic t-accent">' + K.ic(WEEK_ICON[u.key] || "calendar") + '</span><span class="t">' + K.esc(u.title) + (u.element_label ? " · " + K.esc(u.element_label) : "") + "<small>" + K.esc(u.desc) + "</small></span>" +
              '<span class="v sm muted">' + K.dur(u.starts_in).split(" و ")[0] + " دیگه</span></div>";
          }).join("") + "</div>";
        }
        root.innerHTML = html;
        K.on(root, "wclaim", function (el) {
          K.api.post("events/week/claim/", {}, el).then(function (r) { reveal(r.reward, { title: "جایزه‌ی رویداد دریافت شد!" }).then(ctx.reload); }, function () { ctx.reload(); });
        });
        watch(root, ctx);
      });
    }
  });

  // ───────────────────────── season pass ─────────────────────────
  var passAll = false;
  function passCell(reward, state, kind) {
    var ic = state === "got" ? "check" : state === "ready" ? "gift" : state === "nopass" ? "lock" : "";
    return '<div class="ev-cell ' + kind + " " + state + '">' + (ic ? '<span class="ev-cellic">' + K.ic(ic) + "</span>" : "") + '<span class="ev-cellrw">' + rewardHtml(reward, "<br>") + "</span></div>";
  }

  K.screen("ev_pass", {
    title: "پاس فصلی", tab: "more",
    render: function (root, params, ctx) {
      return K.api.get("events/pass/").then(function (d) {
        var maxed = d.tier >= d.max_tier;
        var ready = Math.max(0, d.tier - d.free_claimed) + (d.premium ? Math.max(0, d.tier - d.premium_claimed) : 0);
        root.innerHTML = banner(d.banner, "پاس ماهانه" + (d.month ? " · " + d.month + " " + d.year : ""), (d.seconds_left > 0 ? timerHtml(d.seconds_left, "فصل تموم شد") + ' <span class="sm" style="color:#c5cee2">تا ریست</span>' : "")) +
          '<div class="panel pad mt ' + (d.premium ? "gold" : "glow") + '"><div class="flex between"><div class="flex"><span class="ev-tier num">' + d.tier + '</span><div><div class="b">سطح ' + K.n(d.tier) + " از " + K.n(d.max_tier) + '</div><div class="sm muted">' + K.n(d.points) + " امتیاز</div></div></div>" +
          (d.premium ? K.tag("مسیر ویژه", "var(--gold)", "crown") : '<span class="tag plain">مسیر رایگان</span>') + "</div>" +
          '<div style="margin:12px 0 6px">' + K.bar(d.into / d.span, d.premium ? "gold" : "", "thick") + "</div>" +
          '<div class="flex between sm muted"><span>' + (maxed ? "به آخرین مرحله‌ی پاس رسیدی!" : "تا سطح " + K.n(d.tier + 1)) + '</span><span class="num">' + Number(d.into).toLocaleString("en-US") + " / " + Number(d.span).toLocaleString("en-US") + "</span></div></div>" +
          '<div class="btns mt">' + (d.has_claimable ? '<button class="btn gold" data-act="pclaim">' + K.ic("gift") + "دریافت جوایز (" + ready + ")</button>" : "") +
          (d.premium ? "" : '<button class="btn" data-act="pbuy">' + K.ic("crown") + 'پاس ویژه <span class="cost t-diamond">' + K.ic("gem") + K.n(d.premium_cost) + "</span></button>") + "</div>" +
          (!d.has_claimable && d.premium ? '<div class="callout good mt">' + K.ic("check") + "<div>همه‌ی جایزه‌های مرحله‌هایی که رسیدی رو گرفتی.</div></div>" : "") +
          '<div class="callout mt">' + K.ic("info") + "<div>کسب امتیاز از طریق: شکار، پیروزی در آرنا، ارتقای ساختمان و ورود روزانه. " + (d.premium ? "" : "پاس ویژه جایزه‌ی ویژه‌ی همه‌ی مرحله‌هایی که رسیدی رو هم باز می‌کنه.") + "</div></div>" +
          '<div class="h2">' + K.ic("ticket") + 'مسیر جوایز<button class="more" data-act="pall"></button></div><div id="ev-track"></div>';
        var track = root.querySelector("#ev-track"), toggle = root.querySelector('[data-act="pall"]');

        function draw() {
          var done = Math.min(d.free_claimed, d.premium ? d.premium_claimed : d.free_claimed);   // tiers with nothing left to take
          var rows = d.tiers.filter(function (t) { return passAll || t.tier > done; });
          toggle.textContent = passAll ? "فقط مانده‌ها" : "همه‌ی مرحله‌ها"; toggle.hidden = done < 1;
          track.innerHTML = '<div class="ev-trackhead"><span></span><span>رایگان</span><span>' + K.ic("crown") + "ویژه</span></div>" +
            (rows.length ? rows.map(function (t) {
              var reached = t.tier <= d.tier;
              var fs = t.tier <= d.free_claimed ? "got" : reached ? "ready" : "far";
              var ps = !d.premium ? "nopass" : t.tier <= d.premium_claimed ? "got" : reached ? "ready" : "far";
              return '<div class="ev-trow' + (reached ? " reached" : "") + (t.tier === d.tier + 1 ? " next" : "") + '"><span class="ev-tnum num">' + t.tier + "</span>" + passCell(t.free, fs, "free") + passCell(t.premium, ps, "prem") + "</div>";
            }).join("") : '<div class="panel">' + K.state("trophy", "همه رو گرفتی", "جایزه‌ی همه‌ی مرحله‌ها دریافت شده.") + "</div>");
        }
        draw();
        K.on(root, "pall", function () { K.haptic(); passAll = !passAll; draw(); });
        K.on(root, "pclaim", function (el) {
          K.api.post("events/pass/claim/", {}, el).then(function (r) {
            reveal(r.reward, { title: "جوایز پاس دریافت شد!", text: "جایزه‌ی " + r.tiers + " مرحله", icon: "ticket" }).then(ctx.reload);
          }, function () { ctx.reload(); });
        });
        K.on(root, "pbuy", function (el) {
          var have = K.res ? K.res.diamonds : 0;
          K.confirm({ title: "خرید پاس ویژه", icon: "crown", ok: "تأیید و خرید", cancel: "انصراف",
            html: "<p>هزینه: <b>" + K.n(d.premium_cost) + "</b> الماس · موجودی تو: " + K.n(have) + " الماس<br>جایزه‌ی ویژه‌ی همه‌ی مرحله‌هایی که تا حالا رسیدی هم قابل دریافت می‌شه. پاس ویژه فقط برای همین ماهه.</p>" }).then(function (yes) {
            if (!yes) return;
            K.api.post("events/pass/premium/", {}, el).then(function () { K.haptic("ok"); K.toast("پاس ویژه فعال شد!", "ok"); ctx.reload(); });
          });
        });
        watch(root, ctx);
      });
    }
  });

  // ───────────────────────── achievements ─────────────────────────
  var ACH_ICON = { first_raid: "sword", collector_5: "claw", collector_15: "grid", epic_owner: "gem", legendary_owner: "crown", star_3: "star", creature_lv20: "up",
                   lab_10: "flask", lab_25: "vial", hall_max: "hall", all_buildings_max: "building", arena_wins_10: "swords", arena_wins_50: "shield", cup_300: "trophy", streak_7: "flame" };
  var ACH_CAT = { first_raid: "arena", arena_wins_10: "arena", arena_wins_50: "arena", cup_300: "arena",
                  collector_5: "creatures", collector_15: "creatures", epic_owner: "creatures", legendary_owner: "creatures", star_3: "creatures", creature_lv20: "creatures",
                  lab_10: "lab", lab_25: "lab", streak_7: "lab", hall_max: "base", all_buildings_max: "base" };
  var ACH_CATS = [["creatures", "هیولاها", "claw"], ["arena", "آرنا", "swords"], ["lab", "آزمایشگاه", "flask"], ["base", "پایگاه", "hall"], ["other", "سایر", "medal"]];
  var achCat = "";

  K.screen("ev_achievements", {
    title: "دستاوردها", tab: "more",
    render: function (root, params, ctx) {
      return K.api.get("events/achievements/").then(function (d) {
        d.items.forEach(function (i) { i.cat = ACH_CAT[i.key] || "other"; });
        var cats = ACH_CATS.filter(function (c) { return d.items.some(function (i) { return i.cat === c[0]; }); });
        root.innerHTML = banner(d.banner, "دستاوردها", '<span class="sm" style="color:#c5cee2">هدف‌های بلندمدت؛ با تکمیل هر مرحله، پاداشش رو دریافت کن.</span>') +
          '<div class="panel pad mt"><div class="flex between"><span class="b">' + K.ic("medal") + " " + K.n(d.done) + " از " + K.n(d.total) + ' دریافت‌شده</span><span class="sm muted">' + (d.claimable ? K.n(d.claimable) + " آماده" : "") + "</span></div>" +
          '<div style="margin-top:10px">' + K.bar(d.done / Math.max(1, d.total), "gold", "thick") + "</div>" +
          (d.claimable ? '<button class="btn gold block mt" data-act="aclaim">' + K.ic("gift") + "دریافت همه (" + d.claimable + ")</button>" : "") + "</div>" +
          '<div class="row mt" id="ev-acats"></div><div id="ev-alist"></div>';
        var catsEl = root.querySelector("#ev-acats"), list = root.querySelector("#ev-alist");

        function draw() {
          catsEl.innerHTML = '<button class="chip ' + (achCat === "" ? "on" : "") + '" data-cat="">همه</button>' + cats.map(function (c) {
            var n = d.items.filter(function (i) { return i.cat === c[0] && i.earned && !i.claimed; }).length;
            return '<button class="chip ' + (achCat === c[0] ? "on" : "") + '" data-cat="' + c[0] + '">' + K.ic(c[2]) + c[1] + (n ? ' <span class="ev-dot num">' + n + "</span>" : "") + "</button>";
          }).join("");
          var rows = d.items.filter(function (i) { return !achCat || i.cat === achCat; });
          rows.sort(function (a, b) { return rank(a) - rank(b); });
          list.innerHTML = rows.map(function (i) {
            var st = i.claimed ? "got" : i.earned ? "ready" : "";
            return '<div class="panel ev-ach ' + st + '"><span class="ico-box lg">' + K.ic(i.claimed ? "check" : (ACH_ICON[i.key] || "medal")) + "</span>" +
              '<div class="grow"><div class="flex between"><span class="b">' + K.esc(i.title) + "</span>" +
              (i.claimed ? '<span class="tag plain">دریافت‌شده</span>' : i.earned ? '<button class="tag ev-ready" data-act="aclaim">' + K.ic("gift") + "آماده‌ی دریافت</button>" : '<span class="sm muted num">' + Number(i.current).toLocaleString("en-US") + " / " + Number(i.target).toLocaleString("en-US") + "</span>") + "</div>" +
              '<div class="sm muted">' + K.esc(i.desc) + "</div>" + (i.claimed || i.earned ? "" : '<div class="ev-abar">' + K.bar(i.current / Math.max(1, i.target)) + "</div>") +
              '<div class="sm ev-arw">' + K.ic("gift") + " " + rewardHtml(i.reward) + "</div></div></div>";
          }).join("") || K.state("medal", "چیزی اینجا نیست", "");
        }
        function rank(i) { return i.claimed ? 2 : i.earned ? 0 : 1; }
        draw();
        catsEl.addEventListener("click", function (ev) { var b = ev.target.closest("[data-cat]"); if (!b) return; K.haptic(); achCat = b.dataset.cat; draw(); });
        K.on(root, "aclaim", function (el) {
          K.api.post("events/achievements/claim/", {}, el.classList.contains("btn") ? el : null).then(function (r) {
            reveal(r.reward, { title: r.claimed.length + " دستاورد دریافت شد!", icon: "medal", text: r.claimed.map(function (a) { return a.title; }).join("، ") }).then(ctx.reload);
          }, function () { ctx.reload(); });
        });
      });
    }
  });

  // ───────────────────────── entry points ─────────────────────────
  K.hub("more", { id: "festival", title: "جشنواره", sub: "سکه‌ی جشنواره و فروشگاه ماهانه", icon: "tent", color: "var(--plasma)", go: "ev_festival", order: 30 });
  K.hub("more", { id: "week", title: "رویداد هفته", sub: "قانون هفته و جایزه‌ی روزانه", icon: "calendar", color: "var(--accent)", go: "ev_week", order: 32, hall: 3 });
  K.hub("more", { id: "pass", title: "پاس فصلی", sub: "مسیر جوایز ماهانه", icon: "ticket", color: "var(--gold)", go: "ev_pass", order: 34, hall: 2 });
  K.hub("more", { id: "achievements", title: "دستاوردها", sub: "هدف‌های بلندمدت و پاداش", icon: "medal", color: "var(--epic)", go: "ev_achievements", order: 36 });
})(window.K);
