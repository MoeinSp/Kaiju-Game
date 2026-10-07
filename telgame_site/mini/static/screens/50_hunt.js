/* Hunt («شکار»): scout a wild target → attack → result → again; fighter swap, random
   encounters and auto-hunt. Screens: hu_hunt, hu_auto. API: hunt/…
   The scouted card lives in the client as a signed one-shot token (see api/hunt.py); it is
   kept in sessionStorage too, so a paid scout survives a reload.
   Also exports K.hu — the bits the tower (51) and world boss (52) screens share. */
(function (K) {
  "use strict";

  K.addIcons({
    boss: '<path d="M5 3.500 8 8M19 3.500 16 8M6 11a6 6 0 0 1 12 0v3.500a6 6 0 0 1-12 0zM9.500 16.500h5M10.800 16.500v2M13.200 16.500v2"/><circle cx="9.600" cy="11.800" r="1.200"/><circle cx="14.400" cy="11.800" r="1.200"/>',
    autohunt: '<path d="M19.500 9A8 8 0 0 0 5 7.500L4 9M4 4.500V9h4.500M4.500 15A8 8 0 0 0 19 16.500l1-1.500M20 19.500V15h-4.500"/><path d="m12.800 8-2.800 4.200h4L11.200 16"/>',
    footprint: '<path d="M8 3.500c1.500 0 2.500 1.800 2.500 4.500S9.800 12.500 8.300 12.500 6 10.500 6 8s.5-4.500 2-4.500zM6.500 15.500h4c0 3-1 5-2.300 5s-1.700-2-1.700-5zM16 6.500c1.500 0 2 2 2 4.500s-.8 4.500-2.300 4.500S13.500 13.500 13.500 11 14.500 6.500 16 6.500zM13.500 18h4c0 2-.8 3-2 3s-2-1-2-3z"/>'
  });

  var HU = K.hu = {};
  var TIER_ICON = { weak: "footprint", normal: "target", strong: "skull" };

  // ───────────────────────── shared pieces (also used by 51 / 52) ─────────────────────────
  /* one side of a `versus` card. c: {name, img, element, rarity, power}; o: {role, cls, act, big} */
  HU.side = function (c, o) {
    o = o || {};
    var tag = o.act ? "button" : "div";
    return "<" + tag + ' class="side hu-side ' + (c.rarity || "common") + " " + (o.cls || "") + '"' + (o.act ? ' data-act="' + o.act + '"' : "") + ">" +
      '<span class="hu-pic"><img src="' + K.esc(c.img || o.fallback || "") + '" alt="">' +
      (c.element ? '<span class="el e-' + c.element + '">' + K.ic(K.EL_ICON[c.element] || "atom") + "</span>" : "") +
      (o.act ? '<span class="hu-swapdot">' + K.ic("swap") + "</span>" : "") + "</span>" +
      '<span class="hu-nm cut">' + K.esc(c.name) + "</span>" +
      '<span class="hu-pw">' + K.ic("power") + K.n(c.power) + "</span>" +
      '<span class="hu-role">' + (o.role || "") + "</span></" + tag + ">";
  };
  /* completed missions (the same parts the bot prints under a result) */
  HU.missions = function (list) {
    if (!list || !list.length) return "";
    return '<div class="h2">' + K.ic("calcheck") + "مأموریت کامل شد</div><div class=\"panel list\">" + list.map(function (m) {
      var parts = [K.amounts(m)];
      if (m.points) parts.push('<span class="t-gold b">+' + K.n(m.points) + " امتیاز هفتگی</span>");
      (m.extras || []).forEach(function (x) { parts.push('<span class="b">' + K.esc(x) + "</span>"); });
      return '<div><span class="ic t-good">' + K.ic("check") + '</span><span class="t">' + K.esc(m.label) + (m.weekly ? ' <span class="muted xs">هفتگی</span>' : "") +
        "<small>" + parts.filter(Boolean).join(' <span class="faint">·</span> ') + "</small></span></div>";
    }).join("") + "</div>";
  };
  /* the game's battle report, folded */
  HU.log = function (text) {
    if (!text) return "";
    return '<details class="panel hu-details"><summary>' + K.ic("doc") + "گزارش نبرد" + K.ic("chevron", "hu-chev") + '</summary><div class="hu-log">' + K.esc(text) + "</div></details>";
  };
  /* numbers that count up: <b data-count="1234"></b>, then HU.count(root) */
  HU.count = function (root) {
    Array.prototype.forEach.call(root.querySelectorAll("[data-count]"), function (el) {
      var to = Number(el.dataset.count) || 0, pre = el.dataset.pre || "", t0 = null, dur = Math.min(900, 250 + Math.abs(to) / 4);
      el.removeAttribute("data-count");
      function fmt(v) { return pre + Math.round(v).toLocaleString("en-US"); }
      if (!window.requestAnimationFrame || Math.abs(to) < 3) { el.textContent = fmt(to); return; }
      function step(ts) {
        if (t0 === null) t0 = ts;
        var p = Math.min(1, (ts - t0) / dur);
        el.textContent = fmt(to * (1 - Math.pow(1 - p, 3)));
        if (p < 1) window.requestAnimationFrame(step);
      }
      el.textContent = fmt(0); window.requestAnimationFrame(step);
    });
  };
  /* a loot chip whose number counts up */
  HU.chip = function (icon, cls, value, suffix, fill) {
    if (!value) return "";
    return '<span class="it ' + cls + '">' + K.ic(icon, fill ? "f" : "") + '<b class="num" data-count="' + Number(value) + '" data-pre="' + (value > 0 ? "+" : "") + '"></b>' + (suffix || "") + "</span>";
  };
  HU.loot = function (o) {
    return '<div class="hu-loot">' + HU.chip("coin", "t-coin", o.coins) + HU.chip("dna", "t-dna", o.dna) + HU.chip("gem", "t-diamond", o.diamonds) +
      HU.chip("bolt", "t-energy", o.energy, "", true) + HU.chip("ticket", "t-gold", o.tickets, " بلیط") + HU.chip("up", "t-xp", o.xp, " XP") + "</div>";
  };
  /* «تعویض هیولا»: the team (bot: team_choices) → Promise<id|null>. o: {enemy: element, sub} */
  HU.swapSheet = function (o) {
    o = o || {};
    return K.api.get("hunt/team/").then(function (d) {
      return new Promise(function (resolve) {
        var done = false;
        var html = '<div class="grab"></div><div class="pad"><div class="ttl" style="font-size:18px">کدوم هیولا بجنگه؟</div>' +
          '<p class="lead" style="margin:4px 0 12px">' + K.esc(o.sub || "حریف عوض نمی‌شه؛ فقط هیولای خودت. عنصر مناسب رو انتخاب کن.") + "</p>" +
          '<div class="panel list hu-team">' + d.team.map(function (c) {
            var adv = o.enemy ? K.advantage(c.element, o.enemy) : null;
            var note = c.busy ? '<span class="t-warn">' + K.esc(c.busy_why || "مشغول") + "</span>" : adv ? adv.html : "";
            return '<button data-pick="' + c.id + '"' + (c.busy ? ' data-busy="1"' : "") + (c.active ? ' data-on="1"' : "") + ' class="' + (c.busy ? "dim" : "") + '">' +
              '<img class="th" src="' + c.img + '" alt=""><span class="t"><span class="b">' + K.esc(c.name) + '</span> <span class="e-' + c.element + ' sm">' + K.ic(K.EL_ICON[c.element] || "atom") + K.esc(K.elLabel(c.element)) + "</span>" +
              (c.active ? ' <span class="tag hu-on">' + K.ic("check") + "فعال</span>" : "") + '<small class="hu-note">' + note + "</small></span>" +
              '<span class="v t-accent">' + K.ic("power") + " " + K.n(c.power) + "</span></button>";
          }).join("") + "</div></div>";
        var box = K.sheet(html, { onClose: function () { if (!done) resolve(null); } });
        box.querySelector(".hu-team").addEventListener("click", function (ev) {
          var b = ev.target.closest("[data-pick]"); if (!b) return;
          if (b.dataset.busy) { K.toast("این هیولا الان مشغوله؛ اول آزادش کن.", "err"); return; }
          done = true; K.closeSheet(); resolve(b.dataset.on ? null : Number(b.dataset.pick));
        });
      });
    });
  };
  /* tiny status for the battle hub tiles (boss live? tower open?) — refreshed in the background */
  var hubStatus = null, hubAt = 0;
  function loadHub() { hubAt = Date.now(); K.api.get("hunt/hub/").then(function (d) { hubStatus = d; }).catch(function () {}); }
  HU.hubStatus = function () { if (Date.now() - hubAt > 45000) loadHub(); return hubStatus || {}; };
  HU.setHub = function (patch) { hubStatus = hubStatus || {}; for (var k in patch) hubStatus[k] = patch[k]; };
  loadHub();

  // ───────────────────────── the scouted card (client-held token) ─────────────────────────
  var KEY = "hu_card", token = null;
  try { token = window.sessionStorage.getItem(KEY) || null; } catch (e) {}
  function setToken(t) {
    token = t || null;
    try { if (token) window.sessionStorage.setItem(KEY, token); else window.sessionStorage.removeItem(KEY); } catch (e) {}
  }
  function costChip(icon, value, cls, fill) { return '<span class="cost ' + (cls || "") + '">' + K.ic(icon, fill ? "f" : "") + K.n(value) + "</span>"; }
  function energyNote(S) {
    var e = K.res ? K.res.energy : S.energy;
    return e < S.energy_cost ? '<div class="callout warn mt">' + K.ic("bolt") + "<span>انرژیت تموم شده. صبر کن پر بشه یا شارژش کن.</span>" +
      (K.hasScreen("sh_energy") ? '<button class="btn sm hu-cbtn" data-act="energy">شارژ</button>' : "") + "</div>" : "";
  }

  // ═════════════════════════ hunt ═════════════════════════
  K.screen("hu_hunt", {
    title: "شکار", tab: "battle",
    render: function (root, params, ctx) {
      var S, result = null, enc = null, lock = false;

      function idleView() {
        var me = S.me;
        return '<div class="banner hu-banner" style="background-image:url(\'' + K.esc(S.art || "") + '\')"><div><div class="ttl">شکار</div>' +
          '<div class="sm" style="color:#c5cee2">یه حریف وحشی پیدا کن، بزنش و طلا و DNA ببر</div></div></div>' +
          '<div class="panel pad mt hu-mine">' + K.fighter(me, '<div class="hu-tags">' + K.elTag(me.element) + "</div>") +
          '<button class="btn sm" data-act="swap">' + K.ic("swap") + "تعویض</button></div>" +
          '<button class="btn primary lg block mt" data-act="scout">' + K.ic("search") + "پیدا کردن حریف" + costChip("coin", S.scout_cost) + "</button>" +
          '<button class="btn block mt" data-act="auto">' + K.ic("autohunt") + "شکار خودکار</button>" + energyNote(S) +
          '<div class="callout mt">' + K.ic("info") + "<span>قبل از حمله قدرت حریف، عنصرش و جایزه‌ی دقیق برد رو می‌بینی. پیدا کردن هر حریف یه کم طلا می‌خواد و هر حمله " +
          K.n(S.energy_cost) + " انرژی. اگه حریف به هیولات نمی‌خوره، «حریف بعدی» رو بزن یا هیولات رو عوض کن.</span></div>";
      }

      function targetView() {
        var t = S.target, me = S.me, adv = K.advantage(me.element, t.element);
        return '<div class="panel hu-arena hu-t-' + t.tier + '">' +
          '<div class="hu-head"><span class="tag hu-tier">' + K.ic(TIER_ICON[t.tier] || "target") + K.esc(t.tier_label) + '</span><span class="muted sm">حریف پیدا شد</span></div>' +
          '<div class="versus">' + HU.side(me, { role: "هیولای تو · " + K.esc(K.elLabel(me.element)), act: "swap" }) + '<div class="vs">VS</div>' +
          HU.side(t, { role: "حریف وحشی · " + K.esc(K.elLabel(t.element)), cls: "foe hu-in", fallback: S.art }) + "</div>" +
          '<div class="hu-adv">' + adv.html + "</div></div>" +
          '<div class="panel hu-prize"><span class="muted">' + K.ic("gift") + " جایزه‌ی برد</span><span class=\"hu-prize-v\">" + (K.amounts(t.reward) || "—") + "</span></div>" +
          '<button class="btn danger lg block" data-act="attack">' + K.ic("swords") + "حمله" + costChip("bolt", S.energy_cost, "", true) + "</button>" +
          '<div class="btns mt"><button class="btn" data-act="scout">' + K.ic("refresh") + "حریف بعدی" + costChip("coin", S.scout_cost, "t-coin") + "</button>" +
          '<button class="btn" data-act="swap">' + K.ic("swap") + "تعویض هیولا</button></div>" +
          '<button class="btn ghost block mt" data-act="auto">' + K.ic("autohunt") + "شکار خودکار</button>" + energyNote(S);
      }

      function encounterView() {
        var t = S.target, a = t.actions || [], main = a.filter(function (x) { return x.style === "main"; }), skip = a.filter(function (x) { return x.style !== "main"; });
        var next = '<button class="btn" data-act="scout">' + K.ic("refresh") + "حریف بعدی" + costChip("coin", S.scout_cost, "t-coin") + "</button>";
        var html = '<div class="panel hu-enc"><div class="hu-enc-art" style="background-image:url(\'' + K.esc(t.img || S.art || "") + '\')"></div><div class="hu-enc-body">' +
          '<span class="tag" style="color:var(--gold)">' + K.ic("spark") + "رویداد غیرمنتظره در شکار</span>" +
          '<div class="hu-enc-ttl">' + K.esc(t.title) + '</div><p class="muted" style="margin:4px 0 0">' + K.esc(t.desc) + "</p></div></div>";
        if (main.length > 1) {
          html += '<div class="btns mt">' + main.map(function (x, i) { return '<button class="btn ' + (i ? "gold" : "primary") + ' lg" data-act="enc" data-k="' + K.esc(x.key) + '">' + K.esc(x.label) + "</button>"; }).join("") + "</div>" +
            '<div class="btns mt">' + next + "</div>";
        } else {
          html += main.map(function (x) { return '<button class="btn primary lg block mt" data-act="enc" data-k="' + K.esc(x.key) + '">' + K.esc(x.label) + "</button>"; }).join("") +
            '<div class="btns mt">' + skip.map(function (x) { return '<button class="btn" data-act="enc" data-k="' + K.esc(x.key) + '">' + K.esc(x.label) + "</button>"; }).join("") + next + "</div>";
        }
        return html + '<p class="note">هر رویداد فقط یه بار جواب می‌ده؛ انتخابت برگشت نداره.</p>';
      }

      function againButtons() {
        return '<button class="btn primary lg block mt" data-act="scout">' + K.ic("target") + "شکار دوباره" + costChip("coin", S.scout_cost) + "</button>" +
          '<div class="btns mt"><button class="btn" data-act="auto">' + K.ic("autohunt") + 'شکار خودکار</button><button class="btn ghost" data-act="exit">بازگشت به نبرد</button></div>';
      }

      function resultView() {
        var r = result, e = r.enemy;
        var html = '<div class="panel hu-result ' + (r.won ? "win" : "lose") + '">' +
          '<div class="hu-verdict">' + K.ic(r.won ? "trophy" : "skull") + (r.won ? "بردی!" : "باختی…") + "</div>" +
          '<div class="muted sm">' + K.esc(e.name) + " · " + K.esc(e.tier_label) + "</div>" +
          '<div class="versus hu-mini">' + HU.side(r.me, { cls: r.won ? "won" : "lost" }) + '<div class="vs">' + K.ic("swords") + "</div>" + HU.side(e, { cls: "foe " + (r.won ? "lost" : "won"), fallback: S.art }) + "</div>" +
          (r.won ? '<div class="hu-cap">غنیمت</div>' + HU.loot({ coins: r.coins, dna: r.dna, xp: r.xp })
                 : '<div class="hu-cap">تجربه‌ی تسلی‌بخش</div>' + HU.loot({ xp: r.xp })) + "</div>";
        if (r.levels) html += '<div class="callout good mt">' + K.ic("up") + "<span><b>" + K.esc(r.me.name) + "</b> رسید به سطح <b>" + K.n(r.level) + "</b>!</span></div>";
        if (r.lab_up) html += '<div class="callout good mt">' + K.ic("flask") + "<span>آزمایشگاهت رفت سطح <b>" + K.n(r.lab_up.to) + "</b>!</span></div>";
        if (!r.won) html += '<div class="callout mt">' + K.ic("info") + "<span>هیولات رو قوی‌تر کن، یا با عنصری برو که به حریف برتری داره.</span></div>";
        return html + HU.missions(r.missions) + againButtons() + HU.log(r.log);
      }

      function encResultView() {
        var r = enc, bad = r.coins < 0;
        var html = '<div class="panel hu-enc"><div class="hu-enc-art" style="background-image:url(\'' + K.esc(r.img || S.art || "") + '\')"></div><div class="hu-enc-body">' +
          '<span class="tag" style="color:var(--' + (bad ? "bad" : "gold") + ')">' + K.ic(bad ? "warn" : "spark") + K.esc(r.title || "رویداد") + "</span>" +
          '<p style="margin:8px 0 0">' + K.esc(r.text) + "</p>" +
          (bad ? '<div class="hu-loot">' + HU.chip("coin", "t-bad", r.coins) + "</div>" : HU.loot(r)) + "</div></div>";
        if (r.levels) html += '<div class="callout good mt">' + K.ic("up") + "<span>هیولات رسید به سطح <b>" + K.n(r.level) + "</b>!</span></div>";
        return html + againButtons();
      }

      function draw(keep) {
        root.innerHTML = result ? resultView() : enc ? encResultView() : !S.target ? idleView() : S.target.kind === "encounter" ? encounterView() : targetView();
        HU.count(root);
        if (!keep) window.scrollTo(0, 0);
      }
      function take(d) {            // a fresh panel state from the server
        S = d; setToken(d.token);
        if (d.stale) K.toast("کارت قبلی دیگه معتبر نبود؛ یه حریف تازه پیدا کن.", "err");
      }
      function refresh() { return K.api.get("hunt/" + (token ? "?t=" + encodeURIComponent(token) : "")).then(function (d) { d.stale = false; take(d); draw(); }).catch(function () {}); }
      function guard(p) { lock = true; return p.then(function (v) { lock = false; return v; }, function (err) { lock = false; throw err; }); }
      function staleMaybe(err) { if (err && err.status === 400 && err.code === "rule") refresh(); }

      K.on(root, "scout", function (el) {
        if (lock) return;
        guard(K.api.post("hunt/scout/", {}, el)).then(function (d) {
          result = null; enc = null; take(d); draw();
          K.haptic(d.target && d.target.kind === "encounter" ? "ok" : "hit");
        }).catch(function () {});
      });
      K.on(root, "attack", function (el) {
        if (lock || !token) return;
        guard(K.api.post("hunt/attack/", { token: token }, el)).then(function (r) {
          setToken(null); S.token = null; S.target = null; S.me = r.me; S.scout_cost = r.scout_cost;
          result = r; K.invalidate("profile/creatures/");
          draw(); K.haptic(r.won ? "ok" : "err");
        }).catch(staleMaybe);
      });
      K.on(root, "enc", function (el) {
        if (lock || !token) return;
        var card = S.target;
        guard(K.api.post("hunt/encounter/", { token: token, action: el.dataset.k }, el)).then(function (r) {
          setToken(null); S.token = null; S.target = null; S.me = r.me; S.scout_cost = r.scout_cost;
          r.title = card && card.title; enc = r; K.invalidate("profile/creatures/");
          draw();
          if (r.coins > 0 || r.dna > 0 || r.diamonds > 0 || r.energy > 0) K.reward({ title: r.title, icon: "chest", coins: r.coins, dna: r.dna, diamonds: r.diamonds, energy: r.energy, xp: r.xp });
          else K.haptic(r.coins < 0 ? "err" : "");
        }).catch(staleMaybe);
      });
      K.on(root, "swap", function () {
        if (lock) return;
        var foe = S.target && S.target.kind === "target" ? S.target.element : null;
        HU.swapSheet({ enemy: foe, sub: foe ? "" : "هیولایی که انتخاب کنی هیولای فعالت می‌شه." }).then(function (id) {
          if (!id) return;
          return guard(K.api.post("hunt/swap/", { creature_id: id, token: token || "" })).then(function (d) {
            take(d); K.invalidate("profile/creatures/"); draw(true); K.haptic("ok"); K.toast("هیولات عوض شد.", "ok");
          });
        }).catch(function () {});
      });
      K.on(root, "auto", function () { K.go("hu_auto"); });
      K.on(root, "energy", function () { K.go("sh_energy"); });
      K.on(root, "exit", function () { ctx.back(); });

      return K.api.get("hunt/" + (token ? "?t=" + encodeURIComponent(token) : "")).then(function (d) { take(d); draw(); });
    }
  });

  // ═════════════════════════ auto-hunt ═════════════════════════
  K.screen("hu_auto", {
    title: "شکار خودکار", tab: "battle",
    render: function (root, params, ctx) {
      var D, amount = 0, sum = null;

      function clamp(v) { var per = D.energy_cost; v = Math.max(per, Math.min(D.energy, Math.round(v / per) * per)); return v; }
      function promptView() {
        if (!D.can) {
          return '<div class="panel pad">' + K.fighter(D.me) + "</div>" +
            K.state("bolt", "انرژی کافی نداری", "الان " + D.energy + " از " + D.max_energy + " انرژی داری. هر شکار " + D.energy_cost + " انرژی می‌خواد.",
              (K.hasScreen("sh_energy") ? '<button class="btn primary" data-act="energy" style="margin-top:16px">' + K.ic("bolt") + "شارژ فوری انرژی</button> " : "") +
              (!D.subscription && K.hasScreen("sh_vip") ? '<button class="btn gold" data-act="vip" style="margin-top:16px">' + K.ic("crown") + "اشتراک</button>" : ""));
        }
        var per = D.energy_cost;
        return '<div class="panel pad">' + K.fighter(D.me, '<div class="hu-tags">' + K.elTag(D.me.element) + "</div>") + "</div>" +
          '<div class="panel pad mt hu-auto"><div class="muted sm center">چقدر انرژی خرج کنی؟</div>' +
          '<div class="hu-amount"><b class="num" id="hu-amt"></b><span>' + K.ic("bolt", "f") + "انرژی</span></div>" +
          '<div class="center muted sm" id="hu-hunts"></div>' +
          '<input class="hu-range" id="hu-range" type="range" min="' + per + '" max="' + D.energy + '" step="' + per + '" value="' + amount + '"' + (D.energy <= per ? " disabled" : "") + ">" +
          '<div class="hu-quick"><button class="chip" data-act="amt" data-v="' + D.all + '">همه (' + D.all + ")</button>" +
          '<button class="chip" data-act="amt" data-v="' + D.half + '">نصف (' + D.half + ")</button>" +
          '<span class="stepper"><button data-act="step" data-d="-1" aria-label="کمتر">' + K.ic("minus") + '</button><b class="num" id="hu-amt2"></b><button data-act="step" data-d="1" aria-label="بیشتر">' + K.ic("plus") + "</button></span></div>" +
          '<div class="muted xs center" style="margin-top:8px">الان ' + K.n(D.energy) + " از " + K.n(D.max_energy) + " انرژی داری · هر شکار " + K.n(per) + " انرژی</div></div>" +
          '<div class="callout warn mt">' + K.ic("warn") + "<span>شکار خودکار حدود <b>" + K.n(D.loot_pct) + "٪</b> لوت شکار دستی رو می‌ده (طلا و DNA کمتر). XP کامل می‌مونه. دست‌کم <b>" + K.n(D.win_floor_pct) + "٪</b> نبردها برد حساب می‌شن.</span></div>" +
          '<button class="btn primary lg block mt" data-act="run">' + K.ic("autohunt") + 'شروع شکار خودکار<span class="cost">' + K.ic("bolt", "f") + '<span class="num" id="hu-amt3"></span></span></button>';
      }
      function sync() {
        var per = D.energy_cost, a = root.querySelector("#hu-amt"); if (!a) return;
        a.textContent = amount; root.querySelector("#hu-amt2").textContent = amount; root.querySelector("#hu-amt3").textContent = amount;
        root.querySelector("#hu-hunts").textContent = Math.floor(amount / per) + " نبرد خودکار";
        root.querySelector("#hu-range").value = amount;
        Array.prototype.forEach.call(root.querySelectorAll('[data-act="amt"]'), function (b) { b.classList.toggle("on", Number(b.dataset.v) === amount); });
      }
      function kv(label, html) { return "<div><span>" + label + "</span><span>" + html + "</span></div>"; }
      function sumView() {
        var r = sum, ratio = r.hunts ? r.wins / r.hunts : 0;
        var html = '<div class="panel hu-result win"><div class="hu-verdict">' + K.ic("autohunt") + "نتیجه‌ی شکار خودکار</div>" +
          '<div class="hu-wl"><div><b>' + K.n(r.hunts) + "</b><small>نبرد</small></div><div class=\"t-good\"><b>" + K.n(r.wins) + '</b><small>برد</small></div><div class="t-bad"><b>' + K.n(r.losses) + "</b><small>باخت</small></div></div>" +
          K.bar(ratio, "good", "thick") + '<div class="hu-cap">مجموع غارت</div>' + HU.loot({ coins: r.coins, dna: r.dna, xp: r.xp }) + "</div>";
        if (r.levels) html += '<div class="callout good mt">' + K.ic("up") + "<span>هیولات رسید به سطح <b>" + K.n(r.level) + "</b>!</span></div>";
        if (r.lab_up) html += '<div class="callout good mt">' + K.ic("flask") + "<span>سطح آزمایشگاهت بالا رفت!</span></div>";
        if (r.sub_bonus_pct) {
          html += '<div class="h2">' + K.ic("crown") + "با احتساب " + K.n(r.sub_bonus_pct) + "٪ سود اشتراک " + K.esc(r.sub_name || "ویژه") + '</div><div class="panel kv">' +
            kv("طلای پایه", K.n(r.base_coins)) + kv("DNA پایه", K.n(r.base_dna)) +
            kv("سود طلا", '<span class="t-coin">+' + K.n(r.bonus_coins) + "</span>") + kv("سود DNA", '<span class="t-dna">+' + K.n(r.bonus_dna) + "</span>") + "</div>";
        } else {
          html += '<div class="callout mt">' + K.ic("crown") + "<span>با اشتراک نقره‌ای ۲۵٪ و اشتراک طلایی ۵۰٪ لوت بیشتر از شکار خودکار می‌گیری.</span>" +
            (K.hasScreen("sh_vip") ? '<button class="btn sm hu-cbtn" data-act="vip">اشتراک</button>' : "") + "</div>";
        }
        html += '<div class="panel kv mt">' + kv("انرژی باقی‌مانده", '<span class="t-energy">' + K.ic("bolt", "f") + " " + K.n(r.energy_left) + "/" + K.n(r.max_energy) + "</span>") + "</div>";
        return html + HU.missions(r.missions) +
          '<button class="btn primary lg block mt" data-act="again">' + K.ic("autohunt") + "شکار خودکار مجدد</button>" +
          '<div class="btns mt"><button class="btn" data-act="manual">' + K.ic("target") + 'شکار دستی</button><button class="btn ghost" data-act="exit">بازگشت به نبرد</button></div>';
      }
      function draw() { root.innerHTML = sum ? sumView() : promptView(); if (sum) HU.count(root); else sync(); window.scrollTo(0, 0); }

      root.addEventListener("input", function (ev) { if (ev.target.id === "hu-range") { amount = clamp(Number(ev.target.value)); sync(); } });
      K.on(root, "amt", function (el) { K.haptic(); amount = clamp(Number(el.dataset.v)); sync(); });
      K.on(root, "step", function (el) { K.haptic(); amount = clamp(amount + Number(el.dataset.d) * D.energy_cost); sync(); });
      K.on(root, "energy", function () { K.go("sh_energy"); });
      K.on(root, "vip", function () { K.go("sh_vip"); });
      K.on(root, "again", function () { ctx.reload(); });
      K.on(root, "manual", function () { ctx.back(); });
      K.on(root, "exit", function () { K.tab("battle"); });
      K.on(root, "run", function (el) {
        var hunts = Math.floor(amount / D.energy_cost);
        K.confirm({ title: "تأیید شکار خودکار", icon: "autohunt", ok: "تأیید و شروع", cancel: "انصراف",
          html: "<p>" + K.n(amount) + " انرژی صرف " + K.n(hunts) + " نبرد خودکار بشه؟</p>" }).then(function (yes) {
          if (!yes) return;
          return K.api.post("hunt/auto/run/", { energy: amount }, el).then(function (r) {
            sum = r; setToken(null);              // an auto-hunt also spends any open card
            K.invalidate("profile/creatures/"); draw(); K.haptic("ok");
          });
        }).catch(function (err) { if (err && err.code === "energy") ctx.reload(); });
      });

      return K.api.get("hunt/auto/").then(function (d) { D = d; amount = d.all; draw(); });
    }
  });

  K.hub("battle", { id: "hunt", title: "شکار", sub: "حریف وحشی، طلا و DNA", icon: "target", color: "var(--fire)", go: "hu_hunt", order: 1, wide: true });
})(window.K);
