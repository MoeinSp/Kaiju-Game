/* «سیاه‌چال» (game/dungeon.py): home, the run (doors / fight / rune / merchant), finish, shop, relics.
   Screens: dg_home, dg_run. API: dungeon/…  Every answer is the whole current state of the run. */
(function (K) {
  "use strict";

  K.hub("battle", { id: "dungeon", title: "سیاه‌چال", sub: "در، تله، گنج و باس", icon: "skull", color: "var(--mythic)", go: "dg_home", order: 8 });

  var DOOR = {
    fight: ["swords", "نبرد", "var(--bad)"], elite: ["skull", "نخبه", "var(--mythic)"], treasure: ["chest", "گنج", "var(--gold)"],
    spring: ["drop", "چشمه", "var(--good)"], trap: ["warn", "تله", "var(--warn)"], merchant: ["cart", "تاجر", "var(--accent)"],
  };

  function runes(n) { return '<span class="dg-rn b">' + K.ic("spark") + " " + K.n(n) + "</span>"; }
  function loot(l) { return K.amounts({ coins: l.coins, dna: l.dna }) + (l.runes ? ' <span class="faint">·</span> ' + runes(l.runes) : ""); }
  function hpBar(hp) { return K.bar(hp / 100, hp > 60 ? "good" : hp > 30 ? "gold" : "bad"); }

  // ───────────────────────── home ─────────────────────────
  function homeHtml(d) {
    if (!d.unlocked) {
      var done = Math.min(d.stage, d.unlock_stage);
      return '<div class="panel pad dg-locked center">' + '<div class="dg-sigil">' + K.ic("lock") + "</div>" +
        '<div class="ttl">سیاه‌چال</div><p class="lead">بعد از فتح هر ' + K.n(d.unlock_stage) + " مرحله‌ی دانجن باز می‌شه.</p>" +
        K.bar(done / d.unlock_stage, "gold", "thick") + '<div class="sm muted mt">' + K.n(done) + " از " + K.n(d.unlock_stage) + " مرحله</div>" +
        '<p class="note">یه سیاه‌چال پر از در و تله و گنج، با باس آخر، رون‌های سیاه و آثار باستانی.</p></div>';
    }
    var art = (K.meta && K.meta.art && K.meta.art.dungeon) || "";
    var html = '<div class="banner dg-banner dg-art"' + (art ? ' style="background-image:url(\'' + K.esc(art) + '\')"' : "") + '><div class="grow"><div class="ttl">سیاه‌چال</div><div class="sm" style="color:#c5cee2">' + K.n(d.rooms) + " اتاق و یه باس · جون هیولاها تا آخر می‌مونه</div></div></div>" +
      '<div class="tiles mt">' +
      '<div class="panel info" style="color:var(--gold)"><span class="ic">' + K.ic("lock") + "</span><span><small>کلید (روزی " + K.n(d.daily_keys) + ")</small><b>" + K.n(d.keys) + "</b></span></div>" +
      '<div class="panel info" style="color:var(--mythic)"><span class="ic">' + K.ic("spark") + "</span><span><small>رون سیاه</small><b>" + K.n(d.black_runes) + "</b></span></div></div>" +
      '<div class="tiles mt">' +
      '<div class="panel info" style="color:var(--good)"><span class="ic">' + K.ic("trophy") + "</span><span><small>ورود موفق</small><b>" + K.n(d.clears) + " / " + K.n(d.runs) + "</b></span></div>" +
      '<div class="panel info" style="color:var(--accent)"><span class="ic">' + K.ic("ring") + "</span><span><small>آثار فعال</small><b>" + K.n(d.relics) + "</b></span></div></div>" +
      (d.week_el ? '<div class="panel pad mt dg-week"><div class="flex between"><span class="b">' + K.ic("spark") + " عنصر هفته</span>" + K.elTag(d.week_el) + "</div>" +
        '<div class="sm muted" style="margin-top:6px">بیشتر دشمن‌ها این عنصرن. برکت هفته: هیولاهای ' +
        d.week_favoured.map(function (e) { return K.esc(K.elLabel(e)); }).join("، ") + " +" + K.n(d.week_boost) + "٪ قدرت.</div></div>" : "") +
      '<button class="panel dg-shadow-cta mt" data-act="shadow"><span class="dg-core">' + K.ic("atom") + '</span><span class="grow"><b>فرم سیاه</b>' +
      '<small class="muted">هسته‌ی سایه: ' + K.n(d.shadow_cores) + " · قدرتی فراتر از سقف</small></span>" + K.ic("chevron") + "</button>";
    if (d.run) {
      html += '<div class="callout warn mt">' + K.ic("warn") + "<div>یه ورود نیمه‌کاره داری · " + (d.run.boss ? "باس" : "اتاق " + K.n(d.run.room) + " از " + K.n(d.rooms)) + "</div></div>" +
        '<button class="btn danger lg block mt" data-act="go">' + K.ic("swords") + "ادامه‌ی ورود</button>";
    } else {
      var open = d.difficulties.filter(function (t) { return t.open; }), locked = d.difficulties.filter(function (t) { return !t.open; });
      if (d.endless_open) open.push({ key: "endless", label: "بی‌انتها" });
      html += '<div class="h2">' + K.ic("swords") + 'ورود <span class="muted sm">(هر ورود ' + K.ic("lock") + " ۱ کلید)</span></div>" +
        '<div class="dg-entries">' + open.map(function (t) {
          return '<button class="btn ' + (t.key === "normal" ? "danger" : t.key === "endless" ? "dg-endless" : "dg-hard") + ' lg" data-act="start" data-tier="' + t.key + '">' +
            K.ic(t.key === "nightmare" ? "skull" : t.key === "endless" ? "tower" : "swords") + K.esc(t.label) + "</button>";
        }).join("") + "</div>" +
        locked.map(function (t) { return '<div class="callout mt dg-tier-lock">' + K.ic("lock") + "<div>سختی «" + K.esc(t.label) + "» بعد از یه برد در سختی قبلی باز می‌شه.</div></div>"; }).join("") +
        (d.endless_open ? "" : '<div class="callout mt dg-tier-lock">' + K.ic("lock") + "<div>«بی‌انتها» بعد از یه برد در سختی «سخت» باز می‌شه.</div></div>");
    }
    html += '<div class="btns mt"><button class="btn" data-act="shop">' + K.ic("cart") + 'فروشگاه</button><button class="btn" data-act="relics">' + K.ic("ring") + "آثار باستانی</button></div>" +
      '<button class="btn block mt" data-act="board">' + K.ic("podium") + "جدول هفتگی بی‌انتها</button>" +
      '<p class="note">تیم حمله‌ات وارد می‌شه. سر هر اتاق یه در انتخاب کن؛ بعد هر برد یه رون موقت می‌گیری. با باخت یا ترک، نصف غنیمت بهت می‌رسه.</p>';
    return html;
  }

  K.screen("dg_home", {
    title: "سیاه‌چال", tab: "battle",
    render: function (root, params, ctx) {
      return K.api.get("dungeon/").then(function (d) {
        root.innerHTML = homeHtml(d);
        K.on(root, "go", function () { K.go("dg_run"); });
        K.on(root, "start", function (el) {
          K.api.post("dungeon/start/", { difficulty: el.dataset.tier || "normal" }, el).then(function () { K.haptic("ok"); K.go("dg_run"); }).catch(function () {});
        });
        K.on(root, "shadow", function (el) { shadowSheet(el, function () { if (ctx.alive()) K.reload(); }); });
        K.on(root, "board", function (el) { boardSheet(el); });
        K.on(root, "shop", function (el) { shopSheet(el, function () { if (ctx.alive()) K.reload(); }); });
        K.on(root, "relics", function (el) { relicsSheet(el); });
      });
    },
  });

  // ───────────────────────── the run ─────────────────────────
  function teamHtml(r) {
    return '<div class="panel list dg-team">' + r.members.map(function (m) {
      var down = m.hp <= 0, heal = r.heal_serums > 0 && m.hp > 0 && m.hp < 100;
      return '<div class="' + (down ? "dg-down" : "") + '">' + K.fighter({ name: m.name, img: m.img, rarity: m.rarity, star: m.star, shadow: m.shadow, power: down ? null : m.power },
        down ? '<div class="sm t-bad">از پا افتاده</div>' : '<div class="dg-hp">' + hpBar(m.hp) + '<span class="num sm">' + K.n(m.hp) + "%</span> " + K.elTag(m.element) + "</div>" +
        (heal ? '<button class="btn sm good dg-heal" data-act="heal" data-id="' + m.id + '">' + K.ic("heart") + "ترمیم</button>" : "")) + "</div>";
    }).join("") + "</div>";
  }

  function phaseHtml(r) {
    if (r.phase === "doors") {
      return '<div class="h2">' + K.ic("grid") + 'کدوم در رو باز می‌کنی؟</div><div class="dg-doors">' + r.doors.map(function (d, i) {
        var t = DOOR[d.type];
        return '<button class="panel dg-door" style="--dc:' + t[2] + '" data-act="door" data-i="' + i + '"><span class="dg-door-ic">' + K.ic(t[0]) + "</span><b>" + t[1] + "</b></button>";
      }).join("") + "</div>";
    }
    if (r.phase === "fight") {
      var e = r.enemy;
      var head = '<div class="panel dg-enemy ' + e.kind + '">' + (e.img ? '<img src="' + K.esc(e.img) + '" alt="">' : "") +
        '<div class="grow"><div class="sm muted">' + (e.kind === "boss" ? "باس سیاه‌چال" : e.kind === "elite" ? "دشمن نخبه" : "دشمن") + '</div><div class="b">' + K.esc(e.name) + "</div>" +
        '<div class="sm">' + K.elTag(e.element) + ' <span class="t-accent">' + K.ic("power") + " " + K.n(e.power) + "</span></div></div></div>";
      return head + (r.first_strike ? '<div class="callout good mt">' + K.ic("spark") + "<div>«ضربه‌ی اول» فعاله: +۳۰٪ قدرت توی این نبرد.</div></div>" : "") +
        '<div class="h2">' + K.ic("swords") + 'کی بجنگه؟</div><div class="dg-picks">' + r.members.filter(function (m) { return m.hp > 0; }).map(function (m) {
          return '<button class="panel dg-pick ' + (m.wins ? "win" : "lose") + '" data-act="fight" data-id="' + m.id + '">' +
            '<span class="grow b cut">' + K.esc(m.name) + '</span><span class="num">' + K.n(m.vs) + ' <span class="faint">در برابر</span> ' + K.n(m.enemy_vs) + "</span>" +
            K.tag(m.wins ? "می‌بره" : "می‌بازه", m.wins ? "var(--good)" : "var(--bad)", m.wins ? "check" : "close") + "</button>";
        }).join("") + "</div>";
    }
    if (r.phase === "rune") {
      return '<div class="h2">' + K.ic("spark") + 'یه رون انتخاب کن</div><p class="sm muted" style="margin:-4px 0 8px">تا آخر همین ورود می‌مونه.</p>' +
        r.rune_offer.map(function (o, i) {
          return '<button class="panel dg-rune" data-act="rune" data-i="' + i + '"><b>' + K.esc(o.label) + '</b><span class="sm muted">' + K.esc(o.desc) + "</span></button>";
        }).join("");
    }
    if (r.phase === "merchant") {
      return '<div class="h2">' + K.ic("cart") + 'تاجر سرگردان</div>' + r.merchant.map(function (o, i) {
        return '<button class="panel dg-rune" data-act="buy" data-i="' + i + '"><b>' + K.esc(o.label) + ' <span class="t-coin">' + K.ic("coin") + " " + K.n(o.price) + "</span></b>" +
          '<span class="sm muted">' + K.esc(o.desc) + "</span></button>";
      }).join("") + '<button class="btn block mt" data-act="skip">رد شدن</button>';
    }
    return "";
  }

  function runHtml(d, note) {
    var r = d.run;
    var got = loot(r.loot);
    return '<div class="banner dg-banner sm-banner ' + r.difficulty + '"><div class="grow"><div class="sm" style="color:#c5cee2">سیاه‌چال · ' + K.esc(r.difficulty_label) + '</div><div class="ttl">' +
      (r.endless ? (r.boss ? "باس طبقه‌ی " : "طبقه‌ی ") + K.n(r.room) : r.boss ? "باس" : "اتاق " + K.n(r.room) + " از " + K.n(d.rooms)) + "</div></div></div>" +
      '<div class="panel dg-loot mt"><span class="muted sm">' + K.ic("box") + " غنیمت</span><span>" + (got || '<span class="faint sm">هنوز چیزی نیست</span>') + "</span></div>" +
      (note ? '<div class="callout mt"><div class="dg-note">' + note + "</div></div>" : "") +
      teamHtml(r) +
      (r.runes.length ? '<div class="dg-runes mt">' + r.runes.map(function (x) { return K.tag(K.esc(x.label), "var(--mythic)", "spark"); }).join(" ") + "</div>" : "") +
      phaseHtml(r) +
      (r.log.length ? '<div class="panel pad dg-log mt">' + r.log.map(function (x) { return "<div>" + K.esc(x) + "</div>"; }).join("") + "</div>" : "") +
      '<button class="btn ghost block mt" data-act="quit">' + K.ic("back") + "ترک سیاه‌چال</button>";
  }

  function finishHtml(f) {
    var head = { won: ["trophy", "باس سیاه‌چال رو شکست دادی!", "var(--gold)"], lost: ["skull", "تیمت از پا افتاد…", "var(--bad)"],
      quit: ["back", "از سیاه‌چال بیرون اومدی.", "var(--accent)"] }[f.status];
    if (f.endless) head = f.status === "lost" ? ["tower", "سفرت توی بی‌انتها تموم شد", "var(--mythic)"] : ["back", "از بی‌انتها بیرون اومدی.", "var(--accent)"];
    return '<div class="panel pad center dg-finish"><div class="dg-sigil" style="color:' + head[2] + '">' + K.ic(head[0]) + "</div><h3>" + head[1] + "</h3>" +
      (f.share < 1 ? '<p class="sm muted">نصف غنیمت جمع‌شده بهت رسید.</p>' : "") +
      (f.endless ? '<div class="dg-floor"><b class="num">' + K.n(f.floor) + "</b><small>طبقه</small></div>" +
        (f.new_record ? '<div class="tag" style="color:var(--gold)">' + K.ic("trophy") + "رکورد جدید این هفته!</div>" : "") +
        '<p class="sm muted mt">رکورد هفته‌ات: طبقه‌ی ' + K.n(f.week_best) + " · رتبه‌ی " + (f.rank ? K.n(f.rank) : "—") + "</p>" : "") +
      '<div class="mt">' + loot(f.loot) + (f.loot.cores ? ' <span class="faint">·</span> <span class="dg-rn b">' + K.ic("atom") + " " + K.n(f.loot.cores) + " هسته‌ی سایه</span>" : "") + "</div>" +
      (f.unlocked ? '<div class="callout good mt">' + K.ic("lock") + "<div><b>سختی «" + K.esc(f.unlocked) + "» باز شد!</b></div></div>" : "") +
      (f.badge ? '<div class="callout mt dg-badge">' + K.ic("medal") + "<div><b>نشان جدید: " + K.esc(f.badge) + '</b><br><span class="sm muted">از این به بعد کنار اسم آزمایشگاهت دیده می‌شه.</span></div></div>' : "") +
      (f.relic ? '<div class="callout good mt">' + K.ic("ring") + "<div><b>اثر باستانی: " + K.esc(f.relic.label) + "</b><br><span class=\"sm\">" + K.esc(f.relic.desc) + "</span><br><span class=\"sm muted\">" +
        (f.relic.extended ? "۳ روز به زمانش اضافه شد" : "۳ روز فعاله") + "</span></div></div>" : "") + "</div>" +
      '<button class="btn danger lg block mt" data-act="again" data-tier="' + (f.difficulty || "normal") + '">' + K.ic("swords") + 'ورود دوباره<span class="cost">' + K.ic("lock") + " ۱</span></button>" +
      '<button class="btn block mt" data-act="home">' + K.ic("back") + "سیاه‌چال</button>";
  }

  K.screen("dg_run", {
    title: "سیاه‌چال", tab: "battle",
    render: function (root, params, ctx) {
      var busy = false;
      function show(d, note) {
        if (!ctx.alive()) return;
        if (d.finish) {
          root.innerHTML = finishHtml(d.finish); window.scrollTo(0, 0);
          var fl = d.finish.loot || {};
          if (d.finish.status === "won" || d.finish.status === "lost") K.result({
            won: d.finish.status === "won" && !d.finish.endless,
            title: d.finish.endless ? "طبقه‌ی " + K.n(d.finish.floor) : d.finish.status === "won" ? "باس شکست خورد!" : "تیمت از پا افتاد",
            sub: d.finish.endless ? (d.finish.new_record ? "رکورد جدید این هفته!" : "سفرت توی بی‌انتها تموم شد.") : "",
            coins: fl.coins, dna: fl.dna, diamonds: fl.diamonds });
          else K.haptic("");
          return;
        }
        if (!d.run) { K.back(); return; }
        root.innerHTML = runHtml(d, note); window.scrollTo(0, 0);
      }
      function act(path, body, el, noteOf) {
        if (busy) return; busy = true;
        K.api.post("dungeon/" + path, body, el).then(function (d) { busy = false; show(d, noteOf ? noteOf(d) : ""); }).catch(function () { busy = false; });
      }
      K.on(root, "door", function (el) {
        act("door/", { i: +el.dataset.i }, el, function (d) {
          var g = d.event && d.event.got;
          return g ? K.ic("chest") + " گنج: " + loot({ coins: g.coins, dna: g.dna, runes: g.runes }) : "";
        });
      });
      K.on(root, "fight", function (el) {
        act("fight/", { creature_id: +el.dataset.id }, el, function (d) {
          var f = d.fight; if (!f) return "";
          K.haptic(f.won ? "ok" : "err");
          return f.won ? K.ic("check") + " <b>" + K.esc(f.member) + "</b> برد (" + K.n(f.mine) + " در برابر " + K.n(f.theirs) + ") · " + K.n(f.hp_lost) + "٪ جون کم شد · " + loot(f.got || {})
            : K.ic("skull") + " <b>" + K.esc(f.member) + "</b> باخت و از پا افتاد (" + K.n(f.mine) + " در برابر " + K.n(f.theirs) + "). یکی دیگه رو بفرست!";
        });
      });
      K.on(root, "rune", function (el) { act("rune/", { i: +el.dataset.i }, el, function (d) { return d.picked ? K.ic("spark") + " «" + K.esc(d.picked.label) + "» گرفتی." : ""; }); });
      K.on(root, "buy", function (el) { act("merchant/", { i: +el.dataset.i }, el); });
      K.on(root, "heal", function (el) {   // «سرم ترمیم» from the serum lab, on the member tapped
        if (busy) return; busy = true;
        K.api.post("serums/use/", { kind: "heal", member: +el.dataset.id }, el).then(function (s) {
          K.haptic("ok"); K.sfx && K.sfx("collect");
          return K.api.get("dungeon/").then(function (d) { busy = false; show(d, K.ic("heart") + " سرم ترمیم · " + K.esc(s.note)); });
        }).catch(function () { busy = false; });
      });
      K.on(root, "skip", function (el) { act("merchant/", { i: null }, el); });
      K.on(root, "quit", function () {
        K.confirm({ title: "از سیاه‌چال بیرون بیای؟", text: "نصف غنیمتی که تا الان جمع کردی بهت می‌رسه و کلیدت برنمی‌گرده.", ok: "بیرون بیا", cancel: "ادامه می‌دم", danger: true })
          .then(function (yes) { if (yes) act("quit/", {}, null); });
      });
      K.on(root, "again", function (el) {
        K.api.post("dungeon/start/", { difficulty: el.dataset.tier || "normal" }, el).then(function (d) { K.haptic("ok"); show(d); }).catch(function () {});
      });
      K.on(root, "home", function () { K.back(); });
      return K.api.get("dungeon/").then(function (d) { show(d); });
    },
  });

  // ───────────────────────── shop & relics ─────────────────────────
  function shopSheet(btn, onDone) {
    btn.classList.add("busy");
    K.api.get("dungeon/shop/").then(function (d) {
      // the screen behind is refreshed only once the sheet (or the reward shown over it) is
      // closed — refreshing at once closed the sheet and swallowed the purchase message
      var box = null, dirty = false;
      function refresh() { if (dirty && onDone) { dirty = false; onDone(); } }
      function body(s, note) {
        return '<div class="grab"></div><div class="pad"><div class="ttl" style="font-size:18px">فروشگاه سیاه‌چال</div>' +
          '<p class="lead" style="margin:4px 0 12px">رون سیاه: ' + runes(s.black_runes) + " · کلید: " + K.n(s.keys) + "</p>" + (note || "") +
          s.items.map(function (it) {
            var can = s.black_runes >= it.price;
            return '<button class="panel dg-rune' + (can ? "" : " dim") + '" data-buy="' + it.key + '"><b>' + K.esc(it.label) + "</b>" + runes(it.price) + "</button>";
          }).join("") + "</div>";
      }
      function wire() {
        box.querySelectorAll("[data-buy]").forEach(function (b) {
          b.onclick = function () {
            K.api.post("dungeon/shop/buy/", { key: b.dataset.buy }, b).then(function (s) {
              var x = s.bought, note = '<div class="callout good mb">' + K.ic("check") + "<div>«" + K.esc(x.label) + "» خریدی." +
                (x.relic ? "<br>" + K.esc(x.relic.label) + ": " + K.esc(x.relic.desc) : "") + (x.item ? "<br>" + K.esc(x.item.name) : "") +
                (x.chest ? "<br>" + K.amounts(x.chest) : "") + "</div></div>";
              K.haptic("ok"); box.innerHTML = body(s, note); wire();
              dirty = true;
              // a chest / gear / relic: a reward card (it takes the sheet's place); a key or a core:
              // a toast, and the sheet stays open with the note on top
              if (x.chest) {
                K.reward({ title: "«" + x.label + "» باز شد!", icon: "chest", coins: x.chest.coins, dna: x.chest.dna, diamonds: x.chest.diamonds,
                           creatures: x.chest.creatures, text: x.chest.item || "", button: "عالیه" }).then(refresh);
              } else if (x.relic || x.item) {
                K.reward({ title: "«" + x.label + "» خریدی!", icon: "gift", button: "عالیه",
                           text: x.relic ? x.relic.label + ": " + x.relic.desc : x.item.name }).then(refresh);
              } else {
                K.toast("«" + x.label + "» خریدی · الان " + (b.dataset.buy === "key" ? Number(s.keys).toLocaleString("en-US") + " کلید" : Number(s.shadow_cores).toLocaleString("en-US") + " هسته‌ی سایه") + " داری", "ok");
              }
            }).catch(function () {});
          };
        });
      }
      box = K.sheet(body(d), { onClose: refresh }); wire();
    }).catch(function (e) { K.toast(e.message, "err"); }).finally(function () { btn.classList.remove("busy"); });
  }

  // «فرم سیاه» (game/shadow.py)
  function shadowSheet(btn, onDone) {
    btn.classList.add("busy");
    K.api.get("dungeon/shadow/").then(function (d) {
      var box = null;
      function pips(n, max) { var h = '<span class="dg-pips">'; for (var i = 1; i <= max; i++) h += '<i class="' + (i <= n ? "on" : "") + '"></i>'; return h + "</span>"; }
      function body(s, note) {
        return '<div class="grab"></div><div class="pad"><div class="ttl" style="font-size:18px">فرم سیاه</div>' +
          '<p class="lead" style="margin:4px 0 10px">هیولای اساطیری ۵ ستاره‌ای که قدرتش به سقف (' + K.n(s.cap) + ") رسیده، با هسته‌ی سایه تا " + K.n(s.max_level) +
          " سطح فرم سیاه می‌گیره: هر سطح +" + K.n(s.per_level) + " قدرت و سقفش هم همون‌قدر بالا می‌ره.</p>" +
          '<div class="tiles"><div class="panel info" style="color:var(--mythic)"><span class="ic">' + K.ic("atom") + "</span><span><small>هسته‌ی سایه</small><b>" + K.n(s.cores) + "</b></span></div>" +
          '<div class="panel info" style="color:var(--gold)"><span class="ic">' + K.ic("coin") + "</span><span><small>طلا</small><b>" + K.n(s.coins) + "</b></span></div></div>" +
          (note || "") +
          (s.creatures.length ? s.creatures.map(function (c) {
            var foot = c.max ? K.tag("آخرین سطح", "var(--gold)", "crown")
              : !c.ok ? '<span class="sm muted">' + K.esc(c.why) + "</span>"
              : '<button class="btn sm dg-hard" data-asc="' + c.id + '">' + K.ic("up") + "سطح " + K.n(c.shadow + 1) + ' · <span class="dg-rn">' + K.ic("atom") + K.n(c.next.cores) + "</span> · " + K.ic("coin") + K.n(c.next.coins) + "</button>";
            return '<div class="panel pad mt dg-sh-row">' + K.fighter(c, '<div style="margin-top:6px">' + pips(c.shadow, s.max_level) + "</div>") + '<div class="mt">' + foot + "</div></div>";
          }).join("") : '<div class="panel pad center muted mt">هنوز هیولای اساطیری ۵ ستاره نداری.</div>') + "</div>";
      }
      function wire() {
        box.querySelectorAll("[data-asc]").forEach(function (b) {
          b.onclick = function () {
            K.confirm({ title: "فرم سیاه؟", text: "هسته و طلا خرج می‌شه و هیولات یه سطح فرم سیاه می‌گیره.", ok: "انجام بده", cancel: "نه", icon: "atom" }).then(function (yes) {
              if (!yes) return;
              K.api.post("dungeon/shadow/ascend/", { creature_id: +b.dataset.asc }, null).then(function (s) {
                var x = s.done;
                K.haptic("ok");
                if (x.lord) K.toast("ارباب سایه شدی! نشانش کنار اسمت نشست.");
                // the screen behind refreshes when this sheet closes (at once it closed the sheet too)
                box = K.sheet(body(s, '<div class="callout good mt">' + K.ic("spark") + "<div><b>" + K.esc(x.name) + " به فرم سیاه " + K.n(x.level) + " رسید!</b></div></div>"),
                              { onClose: function () { if (onDone) onDone(); } }); wire();
                K.toast(x.name + " به فرم سیاه " + x.level + " رسید!", "ok");
              }).catch(function () {});
            });
          };
        });
      }
      box = K.sheet(body(d)); wire();
    }).catch(function (e) { K.toast(e.message, "err"); }).finally(function () { btn.classList.remove("busy"); });
  }

  // «جدول هفتگی بی‌انتها»
  function boardSheet(btn) {
    btn.classList.add("busy");
    K.api.get("dungeon/endless/").then(function (b) {
      var rows = b.rows.length ? '<div class="panel list">' + b.rows.map(function (r) {
        return '<div class="' + (r.me ? "me" : "") + '"><span class="ic num">' + K.n(r.rank) + '</span><span class="t">' + K.esc(r.name) + '</span><span class="v">طبقه‌ی ' + K.n(r.floor) + "</span></div>";
      }).join("") + "</div>" : '<div class="panel pad center muted">هنوز کسی این هفته وارد بی‌انتها نشده — اولین نفر باش!</div>';
      var rew = b.rewards.slice(0, 3).map(function (x) { return "رتبه " + K.n(x.rank) + ": " + K.n(x.runes) + " رون و " + K.n(x.cores) + " هسته"; }).join(" · ") +
        " · ۴ تا ۱۰: " + K.n(b.rewards[3].runes) + " رون و " + K.n(b.rewards[3].cores) + " هسته";
      var box = K.sheet('<div class="grab"></div><div class="pad"><div class="ttl" style="font-size:18px">جدول هفتگی بی‌انتها</div>' +
        '<p class="lead" style="margin:4px 0 12px"><span class="timer" data-left="' + b.left + '" data-fmt="long" data-done="تموم شد"></span> تا پایان هفته</p>' + rows +
        '<div class="panel pad mt">' + (b.mine ? "تو: طبقه‌ی " + K.n(b.mine.floor) + " · رتبه‌ی " + K.n(b.mine.rank) : "تو هنوز این هفته طبقه‌ای ثبت نکردی.") + "</div>" +
        '<div class="h2">' + K.ic("gift") + 'جایزه‌ی آخر هفته</div><p class="sm muted">' + rew + "</p>" +
        (b.last ? '<div class="h2">' + K.ic("podium") + "برنده‌های هفته‌ی قبل</div>" + '<div class="panel list">' + b.last.top.map(function (r) {
          return '<div><span class="ic num">' + K.n(r.rank) + '</span><span class="t">' + K.esc(r.name) + '</span><span class="v">طبقه‌ی ' + K.n(r.floor) + "</span></div>";
        }).join("") + "</div>" : "") + "</div>");
      K.timers(box);
    }).catch(function (e) { K.toast(e.message, "err"); }).finally(function () { btn.classList.remove("busy"); });
  }

  function left(sec) { var d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600); return d ? K.n(d) + " روز و " + K.n(h) + " ساعت" : K.n(h) + " ساعت"; }

  function relicsSheet(btn) {
    btn.classList.add("busy");
    K.api.get("dungeon/relics/").then(function (d) {
      K.sheet('<div class="grab"></div><div class="pad"><div class="ttl" style="font-size:18px">آثار باستانی</div>' +
        '<p class="lead" style="margin:4px 0 12px">از باس سیاه‌چال میان و ' + K.n(d.days) + " روز فعالن؛ اگه دوباره بگیریش " + K.n(d.days) + " روز اضافه می‌شه (تا " + K.n(d.max_days) + " روز).</p>" +
        '<div class="panel list">' + d.relics.map(function (r) {
          return '<div class="' + (r.active ? "" : "dim") + '"><span class="ic" style="color:' + (r.active ? "var(--good)" : "var(--muted)") + '">' + K.ic(r.times ? "ring" : "lock") + '</span><span class="t">' +
            (r.times ? K.esc(r.label) : "؟") + "<small>" + (r.times ? K.esc(r.desc) : "هنوز پیداش نکردی") + "</small></span>" +
            (r.active ? '<span class="v sm t-good">' + left(r.seconds_left) + "</span>" : "") + "</div>";
        }).join("") + "</div></div>");
    }).catch(function (e) { K.toast(e.message, "err"); }).finally(function () { btn.classList.remove("busy"); });
  }
})(window.K);
