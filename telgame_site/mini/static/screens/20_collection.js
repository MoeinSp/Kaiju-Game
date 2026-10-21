/* Collection (the «هیولاها» tab), the equipment list, the profile and the cup leaderboard.
   The creature DETAIL screen ("creature") is deliberately small here: other modules add
   their actions to it through K.creatureActions (see below). */
(function (K) {
  "use strict";
  /* Filters live for the whole session, and so does how far each grid was opened («نمایش بیشتر»),
     so coming back from a detail screen shows the list as it was left. */
  var filt = { creatures: { rarity: "", element: "", q: "", sort: "power" }, equipment: { slot: "", rarity: "" } };
  var memo = { creatures: {}, equipment: {} };

  function chip(value, current, attr, label, icon, cls) {
    return '<button class="chip ' + (cls || "") + (value === current ? " on" : "") + '" data-' + attr + '="' + K.esc(value) + '">' + (icon ? K.ic(icon) : "") + label + "</button>";
  }
  /* Light one chip of a row without re-drawing the row (which would throw away its sideways scroll). */
  function light(btn) {
    Array.prototype.forEach.call(btn.parentNode.children, function (b) { b.classList.toggle("on", b === btn); });
  }
  /* A cached list may be older than the thing we were asked to show (a creature that was just
     hatched, an item that was just bought): look once more on the server before giving up. */
  function findIn(path, key, id) {
    function look(again) {
      return K.api.cached(path).then(function (d) {
        var hit = d[key].filter(function (x) { return x.id === id; })[0];
        if (!hit && !again) { K.invalidate(path); return look(true); }
        return hit || null;
      });
    }
    return look(false);
  }

  // ── creatures grid ──
  K.screen("creatures", {
    tab: "creatures", skeleton: "grid",
    render: function (root) {
      return K.api.cached("profile/creatures/").then(function (data) {
        var f = filt.creatures, ro = K.meta.rarity_order, timer = null;
        function list() {
          var out = data.creatures.slice();
          if (f.rarity) out = out.filter(function (c) { return c.rarity === f.rarity; });
          if (f.element) out = out.filter(function (c) { return c.element === f.element; });
          var q = f.q.trim().toLowerCase();
          if (q) out = out.filter(function (c) { return String(c.name).toLowerCase().indexOf(q) >= 0 || String(c.species).toLowerCase().indexOf(q) >= 0; });
          out.sort(f.sort === "level" ? function (a, b) { return b.level - a.level || b.power - a.power; }
                 : f.sort === "rarity" ? function (a, b) { return ro.indexOf(b.rarity) - ro.indexOf(a.rarity) || b.star - a.star || b.power - a.power; }
                 : function (a, b) { return b.power - a.power; });
          return out;
        }
        /* only the result block is re-drawn: the search box keeps its focus and keyboard, the
           chip rows keep their scroll position */
        function drawList(keep) {
          if (!keep) memo.creatures.at = 0;
          var out = list(), filtered = f.rarity || f.element || f.q.trim();
          root.querySelector("#cl-list").innerHTML =
            '<div class="count"><span>' + K.n(out.length) + " هیولا</span>" + (filtered ? '<button class="clear" data-act="clear">' + K.ic("close") + "حذف فیلتر</button>" : "<span>از " + K.n(data.creatures.length) + "</span>") + "</div>" +
            (out.length ? K.grid(out, function (c) { return K.creatureTile(c); }, 60, memo.creatures)
                        : K.state("search", "چیزی پیدا نشد", "هیولایی با این فیلتر نداری.", '<button class="btn" data-act="clear" style="margin-top:14px">' + K.ic("close") + "حذف فیلتر</button>"));
        }
        var SORTS = [["power", "قوی‌ترین"], ["level", "بالاترین سطح"], ["rarity", "نایاب‌ترین"]];
        function sortLabel() { return SORTS.filter(function (s) { return s[0] === f.sort; })[0][1]; }
        /* one bar instead of five stacked rows: search, a filter button (rarity + element in a
           sheet, with a count of what is on) and a sort button that steps through the orders */
        function fbtn() { var n = (f.rarity ? 1 : 0) + (f.element ? 1 : 0); return K.ic("sliders") + (n ? '<i class="num">' + n + "</i>" : ""); }
        function drawHead() {
          root.querySelector("#cl-head").innerHTML =
            '<div class="seg"><button class="on">' + K.ic("claw") + 'هیولاها</button><button data-act="equip-list">' + K.ic("chest") + "تجهیزات</button></div>" +
            '<div class="cl-bar"><div class="search">' + K.ic("search") + '<input id="q" type="search" enterkeyhint="search" placeholder="جستجوی اسم هیولا" value="' + K.esc(f.q) + '" autocomplete="off" autocapitalize="off" spellcheck="false"></div>' +
            '<button class="cl-fbtn' + (f.rarity || f.element ? " on" : "") + '" data-act="filters" aria-label="فیلتر">' + fbtn() + "</button>" +
            '<button class="cl-sort" data-act="sort">' + K.ic("swap") + "<span>" + sortLabel() + "</span></button></div>";
          var q = root.querySelector("#q");
          q.oninput = function () { f.q = q.value; clearTimeout(timer); timer = setTimeout(function () { drawList(); }, 220); };
          q.onkeydown = function (ev) { if (ev.key === "Enter") q.blur(); };
        }
        function filterSheet() {
          function body() {
            return '<div class="grab"></div><div class="pad"><div class="ttl" style="font-size:18px;padding-inline-start:44px">فیلتر هیولاها</div>' +
              '<div class="h2">' + K.ic("gem") + "رده</div>" +
              '<div class="cl-chips">' + chip("", f.rarity, "rarity", "همه") + ro.slice().reverse().map(function (k) { return chip(k, f.rarity, "rarity", K.esc(K.rarLabel(k)), "gem", "c-" + k); }).join("") + "</div>" +
              '<div class="h2">' + K.ic("spark") + "عنصر</div>" +
              '<div class="cl-chips">' + chip("", f.element, "element", "همه‌ی عناصر") + Object.keys(K.meta.elements).map(function (k) { return chip(k, f.element, "element", K.esc(K.elLabel(k)), K.EL_ICON[k], "e-" + k); }).join("") + "</div>" +
              '<div class="btns" style="margin-top:16px"><button class="btn" data-act="f-clear">' + K.ic("close") + 'پاک کردن</button><button class="btn primary grow" data-close>نمایش ' + K.n(list().length) + " هیولا</button></div></div>";
          }
          var box = K.sheet(body());
          box.addEventListener("click", function (ev) {
            var b = ev.target.closest("button"); if (!b || b.hasAttribute("data-close")) return; var d = b.dataset;
            if ("rarity" in d) f.rarity = d.rarity; else if ("element" in d) f.element = d.element;
            else if (d.act === "f-clear") { f.rarity = ""; f.element = ""; } else return;
            K.haptic(); box.innerHTML = body(); drawList(); var fb = root.querySelector(".cl-fbtn");
            if (fb) { fb.innerHTML = fbtn(); fb.classList.toggle("on", !!(f.rarity || f.element)); }
          });
        }
        root.innerHTML = '<div id="cl-head"></div><div id="cl-list"></div>';
        drawHead(); drawList(true);
        if (K.coach) setTimeout(function () { K.coach(root.querySelector(".cl-fbtn"), "فیلتر رده و عنصر اینجاست", "cl_filter"); }, 900);
        root.addEventListener("click", function (ev) {
          var t = ev.target.closest("button"); if (!t) return; var d = t.dataset;
          if (d.creature) K.go("creature", { id: +d.creature });
          else if (d.act === "equip-list") K.go("equipment");
          else if (d.act === "clear") { K.haptic(); f.rarity = ""; f.element = ""; f.q = ""; drawHead(); drawList(); }
          else if (d.act === "filters") filterSheet();
          else if (d.act === "sort") {
            K.haptic(); var i = SORTS.map(function (s) { return s[0]; }).indexOf(f.sort);
            f.sort = SORTS[(i + 1) % SORTS.length][0]; t.querySelector("span").textContent = sortLabel(); drawList();
          }
        });
      });
    }
  });

  // ── creature detail ──
  /* Other modules add buttons to the creature screen:
       K.creatureActions.push({ order: 10, render: function (c, me) { return '<button class="btn" data-act="feed">…</button>' or ""; },
                                bind: function (root, c, ctx) { K.on(root, "feed", function (el) { … ctx.reload(); }); } });
     `c` is the creatureDict; after an action that changes creatures call K.invalidate("profile/creatures/") and ctx.reload(). */
  K.creatureActions = [];
  K.screen("creature", {
    tab: "creatures", skeleton: "detail",
    title: function () { return "هیولا"; },
    render: function (root, params, ctx) {
      // the profile is not waited for (K.meFast answers from memory); it is only handed to the action buttons
      return Promise.all([findIn("profile/creatures/", "creatures", params.id), K.meFast(), K.api.cached("profile/creatures/").catch(function () { return null; })]).then(function (r) {
        var c = r[0];
        // the stat bars measure each stat against the player's own best in it
        var best = null;
        if (r[2] && r[2].creatures) {
          best = { hp: 0, atk: 0, def: 0, spd: 0 };
          r[2].creatures.forEach(function (x) { for (var k in best) if (x[k] > best[k]) best[k] = x[k]; });
          if (c) for (var k in best) if (c[k] > best[k]) best[k] = c[k];
        }
        if (!c) { root.innerHTML = K.state("claw", "این هیولا پیدا نشد", "شاید ادغام یا منتقل شده.", '<button class="btn" data-act="gone" style="margin-top:14px">برگشت به کلکسیون</button>'); K.on(root, "gone", function () { ctx.back(); }); return; }
        ctx.setTitle(c.name);
        var acts = K.creatureActions.slice().sort(function (a, b) { return (a.order || 50) - (b.order || 50); });
        var buttons = acts.map(function (a) { try { return a.render(c, r[1]) || ""; } catch (e) { console.error(e); return ""; } }).join("");
        // one picture slot per equipment slot, like the gear screen of an RPG; empty ones show the slot's icon
        var slots = Object.keys((K.meta && K.meta.slots) || {});
        c.gear.forEach(function (g) { if (slots.indexOf(g.slot) < 0) slots.push(g.slot); });
        var gear = '<div class="cr-slots">' + slots.map(function (s) {
          var g = c.gear.filter(function (x) { return x.slot === s; })[0];
          return g ? '<button class="cr-slot on" style="--rc:var(--' + g.rarity + ')" data-act="gear" data-id="' + g.id + '">' +
              (g.img ? '<img src="' + g.img + '" alt="" loading="lazy" decoding="async">' : '<span class="ic">' + K.ic(K.SLOT_ICON[s] || "chest") + "</span>") +
              '<span class="lv num">+' + g.level + '</span><small class="cut">' + K.esc(g.name) + "</small></button>"
            : '<div class="cr-slot"><span class="ic">' + K.ic(K.SLOT_ICON[s] || "chest") + '</span><small>' + K.esc(K.slotLabel(s)) + "</small></div>";
        }).join("") + "</div>";
        root.innerHTML = '<div class="panel hero ' + c.rarity + '" data-el="' + c.element + '"><div class="art"><img src="' + c.img + '&s=l" alt="" decoding="async"><div class="over"><div><div class="ttl">' + K.esc(c.name) + "</div>" + K.stars(c.star) + "</div>" +
          '<div class="pow"><b>' + K.n(c.power) + "</b><small>قدرت</small></div></div></div>" +
          '<div class="meta">' + K.rarTag(c.rarity) + K.elTag(c.element) + K.tag("سطح " + K.n(c.level)) +
          (c.active ? K.tag("فعال", "var(--good)", "check") : c.busy ? K.tag("مشغول", "var(--warn)", "clock") : "") +
          (c.name !== c.species ? K.tag(K.esc(c.species)) : "") + "</div>" + K.statGrid(c, best) +
          (best ? '<div class="xs muted stat-note">نوار هر آمار: در مقایسه با بهترین هیولای تو</div>' : "") + "</div>" +
          (buttons ? '<div class="creature-actions">' + buttons + "</div>" : "") +
          '<div class="h2">' + K.ic("chest") + "تجهیزات</div>" + gear;
        K.on(root, "gear", function (el) { K.go("item", { id: +el.dataset.id }); });
        acts.forEach(function (a) { if (a.bind) try { a.bind(root, c, ctx); } catch (e) { console.error(e); } });
      });
    }
  });

  // ── equipment grid + item detail ──
  K.screen("equipment", {
    tab: "creatures", title: "تجهیزات", skeleton: "grid",
    render: function (root) {
      return K.api.cached("profile/equipment/").then(function (data) {
        var f = filt.equipment;
        function drawList(keep) {
          if (!keep) memo.equipment.at = 0;
          var list = data.equipment.slice();
          if (f.slot) list = list.filter(function (e) { return e.slot === f.slot; });
          if (f.rarity) list = list.filter(function (e) { return e.rarity === f.rarity; });
          root.querySelector("#eq-list").innerHTML =
            '<div class="count"><span>' + K.n(list.length) + " تجهیزات</span><span>از " + K.n(data.equipment.length) + "</span></div>" +
            (list.length ? K.grid(list, function (e) { return K.itemTile(e); }, 60, memo.equipment)
              : data.equipment.length ? K.state("chest", "چیزی پیدا نشد", "تجهیزاتی با این فیلتر نداری.")
              : K.state("chest", "هنوز تجهیزاتی نداری", "از شکار، جعبه‌ها و فروشگاه تجهیزات می‌گیری."));
        }
        root.innerHTML = '<div class="row">' + chip("", f.slot, "slot", "همه") + Object.keys(K.meta.slots).map(function (k) { return chip(k, f.slot, "slot", K.esc(K.slotLabel(k)), K.SLOT_ICON[k]); }).join("") + "</div>" +
          '<div class="row">' + chip("", f.rarity, "erarity", "همه‌ی رده‌ها") + K.meta.rarity_order.slice().reverse().map(function (k) { return chip(k, f.rarity, "erarity", K.esc(K.rarLabel(k)), "gem", "c-" + k); }).join("") + "</div>" +
          '<button class="btn ghost block mb" data-act="rcy-open">' + K.ic("coin") + "بازیافت تجهیزات اضافه به طلا</button>" +
          '<div id="eq-list"></div>';
        drawList(true);
        function recycleSheet() {
          K.api.get("profile/recycle_gold/").then(function (r) {
            var box = K.sheet('<div class="h2">' + K.ic("coin") + "بازیافت به طلا</div>" +
              '<p class="sm muted">فقط تجهیزاتِ <b>پوشیده‌نشده</b> بازیافت می‌شن؛ هر چی تن هیولاهاته دست نمی‌خوره. هر سطح بالاتر از +۱، ' + K.n(r.level_bonus_pct) + "٪ طلای بیشتر.</p>" +
              '<div class="sm mb">' + r.values.map(function (v) { return '<span class="c-' + v.rarity + '">' + K.esc(K.rarLabel(v.rarity)) + "</span> " + K.n(v.gold); }).join(" · ") + "</div>" +
              (r.groups.length ? '<div class="panel list">' + r.groups.map(function (g) {
                return '<button data-act="rcy-go" data-key="' + g.key + '" data-count="' + g.count + '" data-gold="' + g.gold + '"><span class="grow"><b>' + K.esc(g.label) + "</b> <span class=\"muted\">(" + K.n(g.count) + ")</span></span>" +
                  '<span class="t-coin b">' + K.ic("coin") + K.n(g.gold) + "</span></button>";
              }).join("") + "</div>" : K.state("chest", "چیزی برای بازیافت نیست", "تجهیزات پوشیده‌نشده‌ای نداری.")));
            K.on(box, "rcy-go", function (el) {
              K.confirm({ title: "بازیافت به طلا", danger: true, icon: "coin", ok: "بازیافت کن", cancel: "بی‌خیال",
                html: "<p>" + K.n(+el.dataset.count) + " تجهیزات پوشیده‌نشده‌ی «" + K.esc(r.groups.filter(function (g) { return g.key === el.dataset.key; })[0].label) +
                  "» پاک می‌شن و <b>" + K.n(+el.dataset.gold) + "</b> طلا می‌گیری. برگشت‌پذیر نیست.</p>" }).then(function (yes) {
                if (!yes) return;
                K.api.post("profile/recycle_gold/do/", { group: el.dataset.key }, el).then(function (res) {
                  K.haptic("ok"); K.toast(K.n(res.done.count) + " تجهیزات بازیافت شد · +" + K.n(res.done.gold) + " طلا");
                  K.invalidate("profile/equipment/"); K.closeSheet(); K.reload();
                });
              });
            });
          });
        }
        root.addEventListener("click", function (ev) {
          var t = ev.target.closest("button"); if (!t) return; var d = t.dataset;
          if (d.act === "rcy-open") { recycleSheet(); return; }
          if (d.equip) K.go("item", { id: +d.equip });
          else if ("slot" in d) { K.haptic(); f.slot = d.slot; light(t); drawList(); }
          else if ("erarity" in d) { K.haptic(); f.rarity = d.erarity; light(t); drawList(); }
        });
      });
    }
  });
  /* Same extension point as K.creatureActions, for one equipment piece (`e` = the row of profile/equipment/). */
  K.itemActions = [];
  K.screen("item", {
    tab: "creatures", title: "تجهیزات", skeleton: "detail",
    render: function (root, params, ctx) {
      return findIn("profile/equipment/", "equipment", params.id).then(function (e) {
        if (!e) { root.innerHTML = K.state("chest", "این تجهیزات پیدا نشد", "شاید فروخته یا بازیافت شده."); return; }
        ctx.setTitle(e.name);
        var acts = K.itemActions.slice().sort(function (a, b) { return (a.order || 50) - (b.order || 50); });
        var buttons = acts.map(function (a) { try { return a.render(e, K.me) || ""; } catch (x) { console.error(x); return ""; } }).join("");
        root.innerHTML = '<div class="panel hero"><div class="art"><img src="' + e.img + '&s=l" alt="" decoding="async"><div class="over"><div><div class="ttl">' + K.esc(e.name) + ' <span class="num c-' + e.rarity + '">+' + e.level + "</span></div></div>" +
          '<div class="pow"><b>' + K.n(e.power) + "</b><small>قدرت</small></div></div></div>" +
          '<div class="meta">' + K.rarTag(e.rarity) + K.tag(K.esc(K.slotLabel(e.slot)), "", K.SLOT_ICON[e.slot] || "chest") + "</div><div style=\"height:10px\"></div></div>" +
          '<div class="panel list mt">' +
          (e.bonus ? '<div style="color:var(--accent-2)"><span class="ic">' + K.ic("bolt") + '</span><span class="t">' + K.esc(e.bonus) + "<small>اثر روی هیولا</small></span></div>" : "") +
          '<div style="color:' + (e.on ? "var(--good)" : "var(--faint)") + '"><span class="ic">' + K.ic(e.on ? "check" : "chest") + '</span><span class="t">' + (e.on ? "روی " + K.esc(e.on) : "توی کوله") + "<small>وضعیت</small></span></div></div>" +
          (buttons ? '<div class="creature-actions">' + buttons + "</div>" : "");
        acts.forEach(function (a) { if (a.bind) try { a.bind(root, e, ctx); } catch (x) { console.error(x); } });
      });
    }
  });

  // ── profile ──
  function info(icon, color, label, value, cls) {
    return '<div class="panel info ' + (cls || "") + '"' + (color ? ' style="color:' + color + '"' : "") + '><span class="ic">' + K.ic(icon) + "</span><span><small>" + label + "</small><b>" + value + "</b></span></div>";
  }
  K.screen("profile", {
    tab: "more", title: "پروفایل",
    render: function (root, params, ctx) {
      function draw(me) {
        var tier = String(me.league.key || "bronze").split("_")[0];
        root.innerHTML = '<div class="panel glow profile-head"><span class="ico-box lg t-accent">' + K.ic("flask") + '</span><span class="grow"><div class="nm cut">' + K.esc(me.lab_name) + '</div>' +
          '<small class="muted">سطح آزمایشگاه ' + K.n(me.lab_level) + (me.subscription ? " · " + K.esc(me.subscription) : "") + "</small></span></div>" +
          '<div class="tiles">' +
          info("medal", "", "لیگ", K.esc(me.league.name), "lg-" + tier) +
          info("trophy", "var(--cup)", "رتبه‌ی کاپ", K.n(me.cup_rank)) +
          info("claw", "var(--accent)", "هیولاها", K.n(me.creatures)) +
          info("chest", "var(--accent-2)", "تجهیزات", K.n(me.equipment)) +
          info("hall", "#62e6b4", "تالار مِهر", "سطح " + K.n(me.hall_level)) +
          info("tower", "#ff8a5c", "برج موگن", "طبقه‌ی " + K.n(me.tower_floor)) +
          info("calcheck", "var(--electric)", "ورود پشت‌سرهم", K.n(me.streak) + " روز") +
          info("flask", "var(--accent)", "سطح آزمایشگاه", K.n(me.lab_level)) + "</div>";
      }
      return K.meFast(function (me) { if (ctx.alive()) draw(me); }).then(draw);
    }
  });

  // ── leaderboard ──
  K.screen("leaderboard", {
    tab: "more", title: "جدول کاپ", skeleton: "list",
    render: function (root) {
      return K.api.get("profile/leaderboard/").then(function (data) {
        var rows = data.rows, top = rows.slice(0, 3), rest = rows.slice(3);
        function pod(r, cls) { return r ? '<div class="panel pod ' + cls + (r.me ? " me" : "") + '"><div class="medal num">' + r.rank + '</div><div class="nm">' + K.esc(r.name) + '</div><div class="cp">' + K.ic("trophy") + K.n(r.cup) + "</div></div>" : "<div></div>"; }
        var html = '<div class="panel glow lb-mine"><span class="rk num">#' + Number(data.me.rank).toLocaleString("en-US") + '</span><span class="grow"><div class="b cut">' + K.esc(data.me.name) + '</div><small class="muted">رتبه‌ی تو در جدول کاپ</small></span>' +
          '<span class="b t-cup flex" style="gap:5px">' + K.ic("trophy") + '<span style="color:var(--text)">' + K.n(data.me.cup) + "</span></span></div>";
        if (top.length) html += '<div class="podium">' + pod(top[1], "s") + pod(top[0], "g") + pod(top[2], "z") + "</div>";
        if (rest.length) html += '<div class="panel lb">' + rest.map(function (r) {
          return '<div class="' + (r.me ? "me" : "") + '"><span class="rk num">' + r.rank + '</span><svg class="i lg lg-' + String(r.league).split("_")[0] + '" viewBox="0 0 24 24">' + K.icons.medal + '</svg><span class="nm">' + K.esc(r.name) + '</span><span class="cp">' + K.ic("trophy") + K.n(r.cup) + "</span></div>";
        }).join("") + "</div>";
        if (!rows.length) html += K.state("podium", "جدول هنوز خالیه", "اولین کاپ رو تو بگیر.");
        root.innerHTML = html;
      });
    }
  });

  K.hub("more", { id: "profile", title: "پروفایل", sub: "آمار و وضعیت تو", icon: "user", color: "var(--accent)", go: "profile", order: 90 });
  K.hub("more", { id: "leaderboard", title: "جدول کاپ", sub: "۵۰ نفر برتر", icon: "podium", color: "var(--gold)", go: "leaderboard", order: 60 });
})(window.K);
