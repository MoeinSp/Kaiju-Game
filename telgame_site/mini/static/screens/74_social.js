/* Alliance (read-mostly: info, deposit, leave), the alliance rankings, the guide and the
   settings. Data: /app/api/social/… (game.alliance / game.raid / game.guide). Creating,
   joining and managing an alliance, its buildings, heists and wars stay in the bot. */
(function (K) {
  "use strict";

  K.addIcons({
    sliders: '<path d="M4 7h8M16 7h4M4 12h2M10 12h10M4 17h10M18 17h2"/><circle cx="14" cy="7" r="2"/><circle cx="8" cy="12" r="2"/><circle cx="16" cy="17" r="2"/>',
    bank: '<path d="M3.500 9.500 12 4.500l8.500 5zM5.500 9.500V17M10 9.500V17M14 9.500V17M18.500 9.500V17M3.500 20h17M4.500 17h15"/>',
    logout: '<path d="M10 4.500H5.500v15H10M14.500 8l4 4-4 4M18.500 12H9.500"/>',
    edit: '<path d="M4 20h4L19 9l-4-4L4 16zM13 7l4 4"/>'
  });

  function banner(img, title, subHtml) {
    return '<div class="banner so-banner"' + (img ? ' style="background-image:url(\'' + img + '&s=l\')"' : "") + '><div class="grow"><div class="ttl">' + K.esc(title) + "</div>" + (subHtml ? '<div class="so-bsub">' + subHtml + "</div>" : "") + "</div></div>";
  }
  function stat(icon, cls, label, valueHtml) {
    return '<div class="panel info ' + cls + '"><span class="ic">' + K.ic(icon) + "</span><div><small>" + label + "</small><b>" + valueHtml + "</b></div></div>";
  }
  function seg(items, cur, attr) {
    return '<div class="seg">' + items.map(function (s) { return '<button class="' + (s[0] === cur ? "on" : "") + '" data-' + attr + '="' + s[0] + '">' + (s[2] ? K.ic(s[2]) : "") + s[1] + "</button>"; }).join("") + "</div>";
  }
  function openBot(bot) {
    var url = "https://t.me/" + bot;
    try { if (K.tg && K.tg.openTelegramLink) { K.tg.openTelegramLink(url); return; } } catch (e) {}
    window.open(url, "_blank");
  }
  var BOT_NOTE = "ساخت و پیوستن به اتحاد، مدیریت اعضا، ارتقای ساختمان‌ها، شبیخون، رید و جنگ از داخل ربات انجام می‌شه.";

  // ───────────────────────── alliance ─────────────────────────
  var ROLE = { leader: ["رهبر", "crown", "var(--gold)"], deputy: ["قائم‌مقام", "shieldcheck", "var(--accent)"], member: ["عضو", "user", "var(--muted)"] };
  var BLD_ICON = { hall: "hall", xp: "flask", pass: "ticket", fortress: "tower", barracks: "swords", vault: "bank" };
  var allyTab = "members";

  function membersHtml(a) {
    return '<div class="panel list">' + a.members.map(function (m) {
      var r = ROLE[m.role] || ROLE.member;
      return '<div class="' + (m.me ? "so-me" : "") + '"><span class="ic" style="color:' + r[2] + '">' + K.ic(r[1]) + '</span><span class="t"><span class="cut" style="display:block">' + K.esc(m.name) + (m.me ? ' <span class="faint sm">(تو)</span>' : "") + "</span>" +
        "<small>" + r[0] + '</small></span><span class="so-mstat"><span class="t-accent b">' + K.ic("power") + " " + K.n(m.power) + '</span><small class="t-cup">' + K.ic("trophy") + " " + K.n(m.cup) + "</small></span></div>";
    }).join("") + "</div>";
  }
  function buildingsHtml(a) {
    return '<div class="panel list">' + a.buildings.map(function (b) {
      return '<div><span class="ic t-accent">' + K.ic(BLD_ICON[b.key] || "building") + '</span><span class="t">' + K.esc(b.title) + ' <span class="faint sm">سطح ' + K.n(b.level) + " از " + K.n(b.max_level) + "</span>" +
        "<small>" + K.esc(b.desc) + "</small><small>" + (b.level ? "الان: " + K.esc(b.effect) : "هنوز ساخته نشده") + (b.maxed ? " · تکمیل شده" : " · سطح بعد: " + K.esc(b.next_effect)) + "</small></span>" +
        (b.maxed ? '<span class="tag" style="color:var(--good)">' + K.ic("check") + "کامل</span>" : '<span class="v sm t-coin">' + K.ic("coin") + " " + K.short(b.cost) + "</span>") + "</div>";
    }).join("") + "</div>" +
      '<p class="note">هزینه‌ی ارتقا از خزانه‌ی اتحاد کم می‌شه و فقط رهبر و قائم‌مقام می‌تونن از داخل ربات ارتقا بدن.' + (a.vault_income ? " درآمد روزانه‌ی خزانه: " + Number(a.vault_income).toLocaleString("en-US") + " طلا." : "") + "</p>";
  }
  function warHtml(a) {
    var w = a.war, h = '<div class="h2">' + K.ic("swords") + "جنگ یک‌روزه</div>";
    if (w) {
      var total = Math.max(1, w.my_score + w.foe_score);
      h += '<div class="panel pad"><div class="so-war"><div><div class="b cut">' + K.esc(w.my_name) + '</div><div class="so-score t-accent">' + K.n(w.my_score) + '</div><small class="muted">' + K.n(w.my_participants) + " نفر</small></div>" +
        '<span class="so-vs">VS</span><div><div class="b cut">' + K.esc(w.foe_name) + '</div><div class="so-score t-bad">' + K.n(w.foe_score) + '</div><small class="muted">' + K.n(w.foe_participants) + " نفر</small></div></div>" +
        '<div style="margin:12px 0 8px">' + K.bar(w.my_score / total, "", "thick") + "</div>" +
        '<div class="flex between sm"><span>' + (w.ended ? '<span class="muted">جنگ تموم شده؛ نتیجه به‌زودی اعلام می‌شه</span>' : '<span class="timer" data-fmt="long" data-left="' + Number(w.seconds_left) + '" data-done="تموم شد"></span>') + "</span>" +
        (w.rallied ? '<span class="t-good b">' + K.ic("check") + " سهم تو: " + K.n(w.my_contribution) + "</span>" : '<span class="muted">هنوز توی این جنگ شرکت نکردی</span>') + "</div></div>";
      if (w.contributors.length) {
        h += '<div class="panel list mt">' + w.contributors.map(function (c, i) {
          return '<div class="' + (c.me ? "so-me" : "") + '"><span class="so-rk num">' + (i + 1) + '</span><span class="t cut">' + K.esc(c.name) + '</span><span class="v t-accent">' + K.ic("power") + " " + K.n(c.power) + "</span></div>";
        }).join("") + "</div>";
      }
    } else h += '<div class="panel pad muted sm">الان جنگ فعالی نیست. رهبر یا قائم‌مقام می‌تونه از داخل ربات جنگ یک‌روزه رو شروع کنه.</div>';
    h += '<div class="h2">' + K.ic("flag") + 'جنگ هفتگی</div><div class="panel kv"><div><span>امتیاز جنگ این هفته</span><span>' + K.n(a.war_points) + "</span></div>" +
      "<div><span>جایزه‌ی اتحاد اول هفته</span><span>" + K.amounts({ coins: a.war_week_bonus }) + " به خزانه</span></div></div>";
    var rd = a.raid, b = rd.boss;
    h += '<div class="h2">' + K.ic("skull") + 'رید اتحاد</div><div class="panel pad"><div class="flex between"><span class="b">سطح رید اتحاد: ' + K.n(rd.level) + "</span>" + (b ? K.elTag(b.element) : "") + "</div>" +
      (b ? '<div class="sm" style="margin-top:8px">باس فعال: <b>' + K.esc(b.name) + "</b> · سطح " + K.n(b.level) + '</div><div style="margin:8px 0 5px">' + K.bar(b.hp / Math.max(1, b.max_hp), "bad", "thick") + '</div><div class="sm muted num" style="text-align:left">' + Number(b.hp).toLocaleString("en-US") + " / " + Number(b.max_hp).toLocaleString("en-US") + " HP</div>"
         : '<div class="sm muted" style="margin-top:6px">الان باس فعالی نیست — یکی از اعضا توی گروه «احضار» بزنه.</div>') + "</div>";
    h += rd.top.length ? '<div class="panel list mt">' + rd.top.map(function (m) {
      return '<div class="' + (m.me ? "so-me" : "") + '"><span class="so-rk num">' + m.rank + '</span><span class="t cut">' + K.esc(m.name) + '</span><span class="v t-bad">' + K.ic("sword") + " " + K.n(m.damage) + "</span></div>";
    }).join("") + "</div>" : '<p class="note">این هفته هنوز کسی اتک رید نزده.</p>';
    return h;
  }

  var depositing = false;
  function depositSheet(d, onDone) {
    var a = d.alliance, have = K.res ? K.res.coins : d.coins;
    var quick = [1000, 10000, 100000, 1000000].filter(function (v) { return v <= have; });
    var box = K.sheet('<div class="grab"></div><div class="pad"><div class="ttl" style="font-size:18px">واریز به خزانه</div>' +
      '<p class="lead" style="margin:4px 0 12px">طلای تو: ' + K.n(have) + " · خزانه‌ی «" + K.esc(a.name) + "»: " + K.n(a.treasury) + "</p>" +
      '<input class="input num" id="so-amount" type="text" inputmode="numeric" maxlength="12" placeholder="مقدار طلا" autocomplete="off">' +
      (quick.length ? '<div class="row" style="margin-top:10px">' + quick.map(function (v) { return '<button class="chip" data-q="' + v + '">' + K.ic("coin") + Number(v).toLocaleString("en-US") + "</button>"; }).join("") + "</div>" : "") +
      '<div class="callout warn" style="margin-top:6px">' + K.ic("warn") + "<div>طلایی که به خزانه واریز می‌کنی برنمی‌گرده.</div></div>" +
      '<button class="btn gold block" id="so-dep" style="margin-top:14px">' + K.ic("bank") + "واریز</button></div>");
    var input = box.querySelector("#so-amount");
    function value() { return parseInt(String(input.value).replace(/[^\d]/g, ""), 10) || 0; }
    input.oninput = function () { var v = value(); input.value = v ? v.toLocaleString("en-US") : ""; };
    box.addEventListener("click", function (ev) { var q = ev.target.closest("[data-q]"); if (q) { K.haptic(); input.value = Number(q.dataset.q).toLocaleString("en-US"); } });
    box.querySelector("#so-dep").onclick = function () {
      var amount = value();
      if (amount <= 0) { K.toast("مقدار باید بیشتر از صفر باشه.", "err"); return; }
      if (amount > have) { K.toast("طلا کافی نداری.", "err"); return; }
      K.confirm({ title: "واریز به خزانه", icon: "bank", ok: "واریز کن", cancel: "بی‌خیال",
        html: "<p><b>" + K.n(amount) + "</b> طلا به خزانه‌ی «" + K.esc(a.name) + "» واریز بشه؟<br>این طلا دیگه برنمی‌گرده.</p>" }).then(function (yes) {
        if (!yes) return;
        if (depositing) return; depositing = true;
        K.api.post("social/alliance/deposit/", { amount: amount }).then(function (r) {
          depositing = false;
          K.haptic("ok"); K.toast("واریز شد! خزانه‌ی فعلی: " + Number(r.treasury).toLocaleString("en-US") + " طلا", "ok");
          a.treasury = r.treasury; if (r.res) d.coins = r.res.coins;
          if (onDone) onDone();
        }, function () { depositing = false; });
      });
    };
  }
  function leaveFlow(d, ctx, el) {
    var a = d.alliance, lv = a.leave;
    var note = lv.last_member ? "تو آخرین عضوی: با خروجت <b>اتحاد و خزانه‌ش (" + K.n(a.treasury) + " طلا) برای همیشه حذف می‌شه</b>."
      : lv.is_leader ? "تو رهبری: با خروجت <b>رهبری به " + (lv.heir === "deputy" ? "قائم‌مقام" : "یکی از اعضا") + " می‌رسه</b> و طلایی که به خزانه دادی برنمی‌گرده."
      : "طلایی که به خزانه واریز کردی برنمی‌گرده.";
    K.confirm({ title: "خروج از اتحاد", danger: true, ok: "بله، خارج شو", cancel: "بی‌خیال", html: "<p>" + note + "<br>مطمئنی می‌خوای از «" + K.esc(a.name) + "» خارج بشی؟</p>" }).then(function (yes) {
      if (!yes) return;
      K.api.post("social/alliance/leave/", {}, el).then(function () { rankCache = {}; K.haptic("ok"); K.toast("از اتحاد خارج شدی.", "ok"); ctx.reload(); }, function () {});
    });
  }

  K.screen("so_alliance", {
    title: "اتحاد", tab: "more",
    render: function (root, params, ctx) {
      return K.api.get("social/alliance/").then(function (d) {
        var a = d.alliance;
        if (!a) {
          root.innerHTML = banner(d.banner, "اتحاد", '<span class="sm" style="color:#c5cee2">توی هیچ اتحادی نیستی</span>') +
            '<div class="panel pad mt"><div class="b" style="margin-bottom:8px">چطور عضو بشم؟</div><div class="so-steps">' +
            '<div><span class="num">1</span><p>توی ربات برو «شهر» و بعد «اتحاد».</p></div>' +
            '<div><span class="num">2</span><p>«پیوستن به اتحاد» رو بزن و از لیست یا با جستجوی اسم، اتحادت رو انتخاب کن. بعضی اتحادها درخواستت رو باید تأیید کنن.</p></div>' +
            '<div><span class="num">3</span><p>یا با «ساخت اتحاد» اتحاد خودت رو بساز — هزینه‌ش ' + Number(d.create_cost).toLocaleString("en-US") + " طلاست.</p></div></div></div>" +
            '<div class="callout mt">' + K.ic("info") + "<div>اعضای اتحاد با هم رید می‌زنن، جنگ می‌کنن و از ساختمان‌های اتحاد (XP و امتیاز پاس بیشتر) سود می‌برن. " + BOT_NOTE + "</div></div>" +
            '<div class="btns mt">' + (d.bot ? '<button class="btn primary" data-act="bot">' + K.ic("send") + "باز کردن ربات</button>" : "") + '<button class="btn" data-act="ranks">' + K.ic("podium") + "جدول اتحادها</button></div>";
          K.on(root, "bot", function () { openBot(d.bot); });
          K.on(root, "ranks", function () { K.go("so_ranks"); });
          return;
        }
        var role = ROLE[a.role] || ROLE.member;
        root.innerHTML = banner(d.banner, "اتحاد " + a.name, K.tag(role[0], role[2], role[1]) + (a.leader ? '<span class="sm" style="color:#c5cee2">رهبر: ' + K.esc(a.leader) + "</span>" : "")) +
          '<div class="tiles mt" id="so-astats"></div>' +
          (a.deputy ? '<p class="note" style="margin-top:8px">قائم‌مقام: ' + K.esc(a.deputy) + "</p>" : "") +
          (a.joined_today ? '<div class="callout warn mt">' + K.ic("clock") + "<div>تازه عضو شدی؛ تا نیمه‌شب امشب نمی‌تونی توی رید و جنگ اتحاد شرکت کنی.</div></div>" : "") +
          '<div class="btns mt mb"><button class="btn gold" data-act="deposit">' + K.ic("bank") + 'واریز به خزانه</button><button class="btn" data-act="ranks">' + K.ic("podium") + "جدول اتحادها</button></div>" +
          '<div id="so-aseg"></div><div id="so-abody"></div>' +
          '<div class="callout mt">' + K.ic("info") + "<div>" + BOT_NOTE + "</div></div>" +
          '<button class="btn ghost block mt so-leave" data-act="leave">' + K.ic("logout") + "خروج از اتحاد</button>";
        var segEl = root.querySelector("#so-aseg"), body = root.querySelector("#so-abody"), statsEl = root.querySelector("#so-astats");
        function stats() {
          statsEl.innerHTML = stat("users", "t-accent", "اعضا", K.n(a.member_count) + '<span class="faint sm"> / ' + Number(a.capacity) + "</span>") + stat("power", "t-accent", "قدرت کل", K.short(a.power)) +
            stat("bank", "t-coin", "خزانه", K.short(a.treasury)) + stat("flag", "t-cup", "امتیاز جنگ هفته", K.n(a.war_points));
        }
        stats();
        segEl.innerHTML = seg([["members", "اعضا", "users"], ["buildings", "ساختمان‌ها", "building"], ["war", "جنگ و رید", "swords"]], allyTab, "atab");
        function draw() {
          Array.prototype.forEach.call(segEl.querySelectorAll("button"), function (b) { b.classList.toggle("on", b.dataset.atab === allyTab); });
          body.innerHTML = allyTab === "buildings" ? buildingsHtml(a) : allyTab === "war" ? warHtml(a) : membersHtml(a);
        }
        // the war tab carries a live countdown; the tab is redrawn on every switch, so ONE interval
        // (registered once) refreshes whichever timer element is currently on screen
        var until = Date.now() / 1000 + (a.war ? a.war.seconds_left : 0), fired = false;
        function tick() {
          var el = a.war && !a.war.ended ? body.querySelector(".timer") : null; if (!el) return;
          var s = until - Date.now() / 1000;
          if (s > 0) { el.innerHTML = K.ic("clock") + '<span class="num">' + K.dur(s) + "</span>"; return; }
          el.classList.add("done"); el.innerHTML = K.ic("check") + "تموم شد";
          if (!fired) { fired = true; K.after(1500, ctx.reload); }
        }
        function show() { draw(); tick(); }
        if (a.war && !a.war.ended && a.war.seconds_left > 0) K.every(1000, tick);
        show();
        segEl.addEventListener("click", function (ev) { var b = ev.target.closest("[data-atab]"); if (!b) return; K.haptic(); allyTab = b.dataset.atab; show(); });
        K.on(root, "deposit", function () { depositSheet(d, function () { if (ctx.alive()) stats(); }); });
        K.on(root, "ranks", function () { K.go("so_ranks"); });
        K.on(root, "leave", function (el) { leaveFlow(d, ctx, el); });
      });
    }
  });

  // ───────────────────────── alliance rankings ─────────────────────────
  var BOARDS = { power: ["قدرت", "power", "قدرت کل", "t-accent"], treasury: ["خزانه", "bank", "خزانه", "t-coin"], raid: ["رید", "skull", "سطح رید", "t-bad"], war: ["جنگ هفتگی", "flag", "امتیاز جنگ", "t-cup"] };

  var rankCache = {}, RANK_TTL = 60000;
  function loadBoard(board) {
    var hit = rankCache[board];
    if (hit && Date.now() - hit.at < RANK_TTL) return Promise.resolve(hit.d);
    return K.api.get("social/ranks/?board=" + board).then(function (d) { rankCache[board] = { d: d, at: Date.now() }; return d; });
  }

  K.screen("so_ranks", {
    title: "جدول اتحادها", tab: "more",
    render: function (root, params) {
      var board = BOARDS[params.board] ? params.board : "power", wanted = board;
      root.addEventListener("click", function (ev) {
        var b = ev.target.closest("[data-board]"); if (!b || b.dataset.board === wanted) return;
        K.haptic(); wanted = b.dataset.board; params.board = wanted;
        Array.prototype.forEach.call(root.querySelectorAll("[data-board]"), function (x) { x.classList.toggle("on", x === b); });
        var w = wanted;
        loadBoard(w).then(function (d) { if (w === wanted) paint(d); }, function (err) { K.toast(err.message, "err"); });
      });
      return loadBoard(board).then(paint);
      function paint(d) {
        var meta = BOARDS[d.board];
        var html = banner(d.banner, "برترین اتحادها", "") + '<div class="mt"></div>' +
          seg(d.boards.filter(function (b) { return BOARDS[b]; }).map(function (b) { return [b, BOARDS[b][0], BOARDS[b][1]]; }), d.board, "board");
        if (d.locked) {
          html += '<div class="panel">' + K.state("lock", "هنوز باز نشده", "این جدول از سطح " + d.hall + " تالار مِهر باز می‌شه.") + "</div>";
        } else {
          if (d.board === "treasury" && d.my_rank) html += '<div class="panel pad glow mb flex between"><span>رتبه‌ی اتحاد تو</span><span class="b">' + K.n(d.my_rank) + '<span class="faint sm"> از ' + Number(d.total) + "</span></span></div>";
          html += d.rows.length ? '<div class="panel so-board">' + d.rows.map(function (r) {
            var rw = r.reward ? K.amounts(r.reward) : "";
            return '<div class="' + (r.me ? "so-me" : "") + '"><span class="so-rk r' + r.rank + ' num">' + r.rank + '</span><div class="grow"><div class="b cut">' + K.esc(r.name) + (r.me ? ' <span class="faint sm">(اتحاد تو)</span>' : "") + "</div>" +
              '<div class="sm muted">' + (r.members != null ? K.ic("users") + " " + K.n(r.members) + " عضو" : "") + "</div>" + (rw ? '<div class="sm so-rw">' + K.ic("gift") + " " + rw + "</div>" : "") + "</div>" +
              '<span class="so-val ' + meta[3] + '"><b>' + (d.board === "raid" || d.board === "war" ? K.n(r.value) : K.short(r.value)) + "</b><small>" + meta[2] + "</small></span></div>";
          }).join("") + "</div>" : '<div class="panel">' + K.state("podium", "هنوز خالیه", d.board === "war" ? "این هفته هنوز هیچ اتحادی امتیاز جنگ نگرفته." : "هنوز اتحادی توی این جدول نیست.") + "</div>";
          var note = d.board === "power" ? (d.league_open ? "جایزه‌ی «لیگ اتحادها» آخر هفته به هر عضوِ ۱۰ اتحاد اول داده می‌شه." : "جایزه‌های «لیگ اتحادها» از سطح " + d.league_hall + " تالار مِهر نمایش داده می‌شه.")
            : d.board === "treasury" ? "جدول بر اساس میانگین خزانه‌ی امروزه؛ هر روز به خزانه‌ی ۳ اتحاد اول طلا واریز می‌شه."
            : d.board === "raid" ? "جایزه‌ی هفتگی رید بین ریدرهای برترِ اتحادهای بالای جدول تقسیم می‌شه."
            : "به خزانه‌ی اتحاد اولِ هر هفته " + Number(d.bonus).toLocaleString("en-US") + " طلا واریز می‌شه.";
          html += '<p class="note">' + note + "</p>";
        }
        if (!d.in_alliance) html += '<div class="callout mt">' + K.ic("info") + "<div>هنوز عضو هیچ اتحادی نیستی. از «اتحاد» ببین چطور عضو بشی.</div></div>";
        root.innerHTML = html;
      }
    }
  });

  // ───────────────────────── guide ─────────────────────────
  var TOPIC_ICON = { beginner: "play", creatures: "claw", elements: "atom", energy: "bolt", missions: "calcheck", dispatch: "compass", worldboss: "skull", tournament: "trophy",
                     festival: "flag", league: "podium", events: "calendar", mugen: "tower", blackmarket: "cart", blacksmith: "hammer", expeditions: "map", alliance: "users",
                     trading: "swap", vip: "crown", fight: "swords", creature: "egg", build: "building", market: "chest", social: "globe" };
  var TOPIC_TINT = { book: "var(--accent)", creature: "var(--dna)", battle: "var(--fire)", energy: "var(--energy)", mission: "var(--good)", trophy: "var(--gold)", gift: "var(--plasma)",
                     shop: "var(--coin)", building: "var(--earth)", alliance: "var(--water)", diamond: "var(--diamond)" };
  var GROUPS = [["concepts", "مفاهیم بازی", "doc"], ["dm", "بخش‌های بازی", "grid"]];
  var guideQ = "";
  function topicIcon(t) { return TOPIC_ICON[t.key] || "doc"; }
  function topicTint(t) { return TOPIC_TINT[t.kind] || "var(--accent)"; }
  function norm(s) { return String(s || "").toLowerCase().replace(/[‌ً-ْ]/g, "").replace(/ي/g, "ی").replace(/ك/g, "ک"); }

  K.screen("so_guide", {
    title: "راهنما", tab: "more",
    render: function (root, params, ctx) {
      return K.api.cached("social/guide/").then(function (d) {
        root.innerHTML = banner(d.banner, "راهنمای بازی", '<span class="sm" style="color:#c5cee2">' + K.n(d.topics.length) + " موضوع</span>") +
          '<div class="search mt">' + K.ic("search") + '<input id="so-q" placeholder="جستجو توی راهنما" value="' + K.esc(guideQ) + '" autocomplete="off"></div><div id="so-glist"></div>';
        var list = root.querySelector("#so-glist"), q = root.querySelector("#so-q"), seq = 0, timer = null;
        function row(t, hit) {
          return '<button data-topic="' + K.esc(t.id) + '"><span class="ic" style="color:' + topicTint(t) + '">' + K.ic(topicIcon(t)) + '</span><span class="t">' + K.esc(t.title) + "<small>" + K.esc(hit || t.blurb) + '</small></span><span class="chev">' + K.ic("chevron") + "</span></button>";
        }
        /* titles, blurbs and section headings are searched here; `more` = topics the server found in the text */
        function results(needle, more) {
          var found = [], seen = {};
          d.topics.forEach(function (t) {
            var hit = null;
            if (norm(t.title).indexOf(needle) < 0 && norm(t.blurb).indexOf(needle) < 0) {
              hit = t.heads.filter(function (h) { return norm(h).indexOf(needle) >= 0; })[0];
              if (!hit) return;
            }
            seen[t.id] = 1; found.push(row(t, hit));
          });
          (more || []).forEach(function (m) {
            var t = d.topics.filter(function (x) { return x.id === m.id; })[0];
            if (t && !seen[t.id]) { seen[t.id] = 1; found.push(row(t, m.h)); }
          });
          return found;
        }
        function draw(more, pending) {
          var needle = norm(guideQ.trim()), html = "";
          if (!needle) {
            GROUPS.forEach(function (g) {
              var rows = d.topics.filter(function (t) { return t.group === g[0]; });
              if (rows.length) html += '<div class="h2">' + K.ic(g[2]) + g[1] + '</div><div class="panel list">' + rows.map(function (t) { return row(t); }).join("") + "</div>";
            });
          } else {
            var found = results(needle, more);
            html = '<div class="count"><span>' + (pending && !found.length ? "در حال جستجو…" : K.n(found.length) + " نتیجه") + "</span></div>" +
              (found.length ? '<div class="panel list">' + found.join("") + "</div>" : pending ? "" : K.state("search", "چیزی پیدا نشد", "یه کلمه‌ی دیگه رو امتحان کن."));
          }
          list.innerHTML = html;
        }
        function search() {
          var text = guideQ.trim(), mine = ++seq;
          clearTimeout(timer);
          if (text.length < 2) { draw(); return; }
          draw(null, true);   // what the index already knows, at once
          timer = setTimeout(function () {
            if (mine !== seq || !ctx.alive()) return;
            K.api.cached("social/guide/search/?q=" + encodeURIComponent(text)).then(function (r) { if (mine === seq && ctx.alive()) draw(r.hits); }, function () { if (mine === seq && ctx.alive()) draw(); });
          }, 350);
        }
        if (guideQ.trim().length >= 2) search(); else draw();
        q.oninput = function () { guideQ = q.value; search(); };
        list.addEventListener("click", function (ev) { var b = ev.target.closest("[data-topic]"); if (b) K.go("so_topic", { id: b.dataset.topic, q: guideQ.trim() }); });
      });
    }
  });

  /* a paragraph of guide text: one block per line, «a ← b» lines as a two-column row */
  function guideBody(text) {
    return String(text || "").split("\n").map(function (line) {
      line = line.replace(/^\s*[•·]\s*/, "");
      if (!line.trim()) return "";
      var parts = line.split(" ← ");
      if (parts.length === 2 && parts[0].length <= 34) return '<div class="so-pair"><span>' + K.esc(parts[0]) + "</span><b>" + K.esc(parts[1]) + "</b></div>";
      return "<p>" + K.esc(line) + "</p>";
    }).join("");
  }

  K.screen("so_topic", {
    title: "راهنما", tab: "more",
    render: function (root, params, ctx) {
      return Promise.all([K.api.cached("social/guide/"), K.api.cached("social/guide/topic/?id=" + encodeURIComponent(params.id || ""))]).then(function (res) {
        var d = res[0], t = res[1].topic, meta = d.topics.filter(function (x) { return x.id === t.id; })[0] || t;
        ctx.setTitle(t.title);
        var needle = norm(params.q);
        var same = d.topics.filter(function (x) { return x.group === t.group; }), i = same.indexOf(meta), next = i >= 0 ? same[i + 1] : null;
        root.innerHTML = '<div class="panel pad so-thead"><span class="ico-box lg" style="color:' + topicTint(t) + '">' + K.ic(topicIcon(t)) + '</span><div class="grow"><div class="so-ttl">' + K.esc(t.title) + '</div><div class="sm muted">' + K.esc(t.blurb) + "</div></div></div>" +
          (t.rows.length > 3 ? '<div class="row so-toc">' + t.rows.map(function (r, n) { return '<button class="chip" data-sec="' + n + '">' + K.esc(r.h) + "</button>"; }).join("") + "</div>" : "") +
          t.rows.map(function (r, n) {
            var hit = needle && (norm(r.h).indexOf(needle) >= 0 || norm(r.b).indexOf(needle) >= 0);
            return '<div class="panel pad so-sec' + (hit ? " hit" : "") + '" id="so-sec' + n + '"><div class="so-sech"><span class="num">' + (n + 1) + "</span>" + K.esc(r.h) + '</div><div class="so-secb">' + guideBody(r.b) + "</div></div>";
          }).join("") +
          (next ? '<button class="panel so-next" data-act="next"><span class="ic" style="color:' + topicTint(next) + '">' + K.ic(topicIcon(next)) + '</span><span class="grow"><small class="muted">موضوع بعدی</small><b>' + K.esc(next.title) + '</b></span><span class="faint">' + K.ic("chevron") + "</span></button>" : "");
        if (next) K.on(root, "next", function () { K.replace("so_topic", { id: next.id }); });
        root.addEventListener("click", function (ev) {
          var b = ev.target.closest("[data-sec]"), el = b && root.querySelector("#so-sec" + b.dataset.sec);
          if (el) { K.haptic(); window.scrollTo(0, Math.max(0, el.getBoundingClientRect().top + window.scrollY - 110)); }
        });
        // opened from a search: go to the first section that mentions it
        var first = needle && root.querySelector(".so-sec.hit");
        if (first) K.after(60, function () { window.scrollTo(0, Math.max(0, first.getBoundingClientRect().top + window.scrollY - 110)); });
      });
    }
  });

  // ───────────────────────── settings ─────────────────────────
  var CAT_ICON = { timers: "clock", attacks: "swords", events: "flag", reminders: "gift" };
  function sw(key, on, disabled) {
    return '<button class="so-switch' + (on ? " on" : "") + '" role="switch" aria-checked="' + (on ? "true" : "false") + '" data-act="notify" data-key="' + K.esc(key) + '"' + (disabled ? " disabled" : "") + "><i></i></button>";
  }

  K.screen("so_settings", {
    title: "تنظیمات", tab: "more",
    render: function (root, params, ctx) {
      return K.api.get("social/settings/").then(function (d) {
        root.innerHTML = '<div class="h2" style="margin-top:6px">' + K.ic("bell") + 'اعلان‌ها</div><div id="so-notif"></div>' +
          '<div class="h2">' + K.ic("flask") + 'اسم آزمایشگاه</div><div id="so-name"></div>' +
          '<p class="note">بقیه‌ی تنظیمات حساب از داخل ربات در «پروفایل» در دسترسه.</p>';
        var notif = root.querySelector("#so-notif"), nameEl = root.querySelector("#so-name"), saving = false;

        function drawNotif() {
          notif.innerHTML = '<div class="panel list"><div><span class="ic" style="color:' + (d.notifications_on ? "var(--good)" : "var(--bad)") + '">' + K.ic("bell") + '</span><span class="t">همه‌ی اعلان‌ها<small>' + (d.notifications_on ? "ربات برای موارد روشنِ پایین بهت پیام می‌ده" : "همه‌ی اعلان‌ها خاموشه؛ هیچ پیامی از ربات نمی‌گیری") + "</small></span>" + sw("all", d.notifications_on) + "</div></div>" +
            '<div class="panel list mt' + (d.notifications_on ? "" : " so-off") + '">' + d.categories.map(function (c) {
              return '<div><span class="ic t-accent">' + K.ic(CAT_ICON[c.key] || "bell") + '</span><span class="t">' + K.esc(c.title) + "<small>" + K.esc(c.desc) + "</small></span>" + sw(c.key, c.on, !d.notifications_on) + "</div>";
            }).join("") + "</div>" +
            '<p class="note">اعلان‌های هم‌زمان توی یک پیام می‌آن و رویدادها و یادآوری‌ها روزی حداکثر ۴ تا هستن.</p>';
        }
        function drawName() {
          nameEl.innerHTML = '<div class="panel pad"><div class="sm muted">اسم فعلی</div><div class="so-labname">' + K.esc(d.lab_name || "—") + "</div>" +
            '<input class="input" id="so-newname" maxlength="' + Number(d.name_max) + '" placeholder="اسم جدید (حداکثر ' + Number(d.name_max) + ' کاراکتر)" autocomplete="off">' +
            '<div class="flex between mt"><span class="sm">' + (d.rename_free ? '<span class="t-good b">اولین تغییر اسم رایگانه</span>' : "هزینه: " + K.amounts({ diamonds: d.rename_cost })) + "</span>" +
            '<button class="btn primary" data-act="rename">' + K.ic("edit") + "تغییر اسم</button></div></div>" +
            '<p class="note">اسم آزمایشگاه باید یکتا باشه. هزینه‌ی تغییر اسم هر بار بیشتر می‌شه.</p>';
        }
        drawNotif(); drawName();

        K.on(root, "notify", function (el) {
          if (saving) return; saving = true;   // one switch at a time: each answer redraws all of them
          var key = el.dataset.key, on = !el.classList.contains("on");
          el.classList.toggle("on", on); K.haptic();
          K.api.post("social/settings/notify/", { key: key, on: on }).then(function (r) {
            saving = false; d.notifications_on = r.notifications_on; d.categories = r.categories; drawNotif();
          }, function () { saving = false; drawNotif(); });
        });
        K.on(root, "rename", function (el) {
          var input = root.querySelector("#so-newname"), name = input.value;
          if (!name.trim()) { K.toast("اسم جدید رو بنویس.", "err"); input.focus(); return; }
          K.api.post("social/settings/rename/", { name: name, dry: true }, el).then(function (chk) {
            return K.confirm({ title: "تغییر اسم آزمایشگاه", icon: "edit", ok: chk.cost ? "تأیید و پرداخت" : "تأیید", cancel: "انصراف",
              html: "<p>اسم آزمایشگاهت می‌شه «<b>" + K.esc(chk.name) + "</b>».<br>" + (chk.cost ? "هزینه: <b>" + K.n(chk.cost) + "</b> الماس" : "این بار رایگانه.") + "</p>" }).then(function (yes) {
              if (!yes) return;
              return K.api.post("social/settings/rename/", { name: name, cost: chk.cost }, el).then(function (r) {
                K.haptic("ok"); K.toast("اسم آزمایشگاهت شد «" + r.name + "»", "ok");
                d = r; if (ctx.alive()) drawName();          // the new name and the next price came with the answer
                K.refreshMe().catch(function () {});       // the home screen shows the lab name
              });
            });
          }).catch(function () {});
        });
      });
    }
  });

  // ───────────────────────── entry points ─────────────────────────
  K.hub("more", { id: "alliance", title: "اتحاد", sub: "اعضا، خزانه، جنگ و جدول‌ها", icon: "users", color: "var(--water)", go: "so_alliance", order: 40 });
  K.hub("more", { id: "guide", title: "راهنما", sub: "همه‌چیز درباره‌ی بازی", icon: "doc", color: "var(--good)", go: "so_guide", order: 90 });
  K.hub("more", { id: "settings", title: "تنظیمات", sub: "اعلان‌ها و اسم آزمایشگاه", icon: "sliders", color: "var(--muted)", go: "so_settings", order: 95 });
})(window.K);
