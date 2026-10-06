/* Collection (the «هیولاها» tab), the equipment list, the profile and the cup leaderboard.
   The creature DETAIL screen ("creature") is deliberately small here: other modules add
   their actions to it through K.creatureActions (see below). */
(function (K) {
  "use strict";
  var filt = { creatures: { rarity: "", element: "", q: "", sort: "power" }, equipment: { slot: "", rarity: "" } };

  function chip(value, current, attr, label, icon, cls) {
    return '<button class="chip ' + (value === current ? "on" : (cls || "")) + '" data-' + attr + '="' + K.esc(value) + '">' + (icon ? K.ic(icon) : "") + label + "</button>";
  }

  // ── creatures grid ──
  K.screen("creatures", {
    tab: "creatures", skeleton: "grid",
    render: function (root, params, ctx) {
      return K.api.cached("profile/creatures/").then(function (data) {
        function draw() {
          var f = filt.creatures, list = data.creatures.slice(), ro = K.meta.rarity_order;
          if (f.rarity) list = list.filter(function (c) { return c.rarity === f.rarity; });
          if (f.element) list = list.filter(function (c) { return c.element === f.element; });
          if (f.q) { var q = f.q.trim(); list = list.filter(function (c) { return c.name.indexOf(q) >= 0 || c.species.indexOf(q) >= 0; }); }
          list.sort(f.sort === "level" ? function (a, b) { return b.level - a.level || b.power - a.power; }
                  : f.sort === "rarity" ? function (a, b) { return ro.indexOf(b.rarity) - ro.indexOf(a.rarity) || b.star - a.star || b.power - a.power; }
                  : function (a, b) { return b.power - a.power; });
          root.innerHTML = '<div class="seg"><button class="on">' + K.ic("claw") + 'هیولاها</button><button data-act="equip-list">' + K.ic("chest") + "تجهیزات</button></div>" +
            '<div class="search">' + K.ic("search") + '<input id="q" placeholder="جستجوی اسم هیولا" value="' + K.esc(f.q) + '" autocomplete="off"></div>' +
            '<div class="row">' + chip("", f.rarity, "rarity", "همه") + ro.slice().reverse().map(function (k) { return chip(k, f.rarity, "rarity", K.esc(K.rarLabel(k)), "gem", "c-" + k); }).join("") + "</div>" +
            '<div class="row">' + chip("", f.element, "element", "همه‌ی عناصر") + Object.keys(K.meta.elements).map(function (k) { return chip(k, f.element, "element", K.esc(K.elLabel(k)), K.EL_ICON[k], "e-" + k); }).join("") + "</div>" +
            '<div class="seg">' + [["power", "قوی‌ترین"], ["level", "بالاترین سطح"], ["rarity", "نایاب‌ترین"]].map(function (s) { return '<button class="' + (f.sort === s[0] ? "on" : "") + '" data-sort="' + s[0] + '">' + s[1] + "</button>"; }).join("") + "</div>" +
            '<div class="count"><span>' + K.n(list.length) + " هیولا</span><span>از " + K.n(data.creatures.length) + "</span></div>" +
            (list.length ? '<div class="grid">' + list.map(function (c) { return K.creatureTile(c); }).join("") + "</div>" : K.state("search", "چیزی پیدا نشد", "هیولایی با این فیلتر نداری."));
          var q = root.querySelector("#q");
          q.oninput = function () { f.q = q.value; clearTimeout(q._t); q._t = setTimeout(function () { draw(); var x = root.querySelector("#q"); x.focus(); x.setSelectionRange(x.value.length, x.value.length); }, 260); };
        }
        draw();
        root.addEventListener("click", function (ev) {
          var t = ev.target.closest("button"); if (!t) return; var d = t.dataset, f = filt.creatures;
          if (d.creature) K.go("creature", { id: +d.creature });
          else if (d.act === "equip-list") K.go("equipment");
          else if ("rarity" in d) { K.haptic(); f.rarity = d.rarity; draw(); }
          else if ("element" in d) { K.haptic(); f.element = d.element; draw(); }
          else if ("sort" in d) { K.haptic(); f.sort = d.sort; draw(); }
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
    tab: "creatures",
    title: function () { return "هیولا"; },
    render: function (root, params, ctx) {
      return Promise.all([K.api.cached("profile/creatures/"), K.refreshMe()]).then(function (r) {
        var c = r[0].creatures.filter(function (x) { return x.id === params.id; })[0];
        if (!c) { root.innerHTML = K.state("claw", "این هیولا پیدا نشد", "شاید ادغام یا منتقل شده."); return; }
        ctx.setTitle(c.name);
        var acts = K.creatureActions.slice().sort(function (a, b) { return (a.order || 50) - (b.order || 50); });
        var buttons = acts.map(function (a) { try { return a.render(c, r[1]) || ""; } catch (e) { return ""; } }).join("");
        var gear = c.gear.length ? c.gear.map(function (g) {
          return '<button class="c-' + g.rarity + '" data-act="gear" data-id="' + g.id + '"><span class="ic">' + K.ic(K.SLOT_ICON[g.slot] || "chest") + '</span><span class="t">' + K.esc(g.name) + ' <span class="num b">+' + g.level + "</span><small>" + K.esc(K.slotLabel(g.slot)) + " · " + K.esc(K.rarLabel(g.rarity)) + '</small></span><span class="chev">' + K.ic("chevron") + "</span></button>";
        }).join("") : '<div><span class="ic" style="color:var(--faint)">' + K.ic("chest") + '</span><span class="t muted">تجهیزاتی نپوشیده</span></div>';
        root.innerHTML = '<div class="panel hero"><div class="art"><img src="' + c.img + '&s=l" alt=""><div class="over"><div><div class="ttl">' + K.esc(c.name) + "</div>" + K.stars(c.star) + "</div>" +
          '<div class="pow"><b>' + K.n(c.power) + "</b><small>قدرت</small></div></div></div>" +
          '<div class="meta">' + K.rarTag(c.rarity) + K.elTag(c.element) + K.tag("سطح " + K.n(c.level)) +
          (c.active ? K.tag("فعال", "var(--good)", "check") : c.busy ? K.tag("مشغول", "var(--warn)", "clock") : "") +
          (c.name !== c.species ? K.tag(K.esc(c.species)) : "") + "</div>" + K.statGrid(c) + "</div>" +
          (buttons ? '<div class="creature-actions">' + buttons + "</div>" : "") +
          '<div class="h2">' + K.ic("chest") + "تجهیزات</div>" + '<div class="panel list">' + gear + "</div>";
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
        function draw() {
          var f = filt.equipment, list = data.equipment.slice();
          if (f.slot) list = list.filter(function (e) { return e.slot === f.slot; });
          if (f.rarity) list = list.filter(function (e) { return e.rarity === f.rarity; });
          root.innerHTML = '<div class="row">' + chip("", f.slot, "slot", "همه") + Object.keys(K.meta.slots).map(function (k) { return chip(k, f.slot, "slot", K.esc(K.slotLabel(k)), K.SLOT_ICON[k]); }).join("") + "</div>" +
            '<div class="row">' + chip("", f.rarity, "erarity", "همه‌ی رده‌ها") + K.meta.rarity_order.slice().reverse().map(function (k) { return chip(k, f.rarity, "erarity", K.esc(K.rarLabel(k)), "gem", "c-" + k); }).join("") + "</div>" +
            '<div class="count"><span>' + K.n(list.length) + " تجهیزات</span><span>از " + K.n(data.equipment.length) + "</span></div>" +
            (list.length ? '<div class="grid">' + list.map(function (e) { return K.itemTile(e); }).join("") + "</div>" : K.state("chest", "چیزی پیدا نشد", "تجهیزاتی با این فیلتر نداری."));
        }
        draw();
        root.addEventListener("click", function (ev) {
          var t = ev.target.closest("button"); if (!t) return; var d = t.dataset;
          if (d.equip) K.go("item", { id: +d.equip });
          else if ("slot" in d) { K.haptic(); filt.equipment.slot = d.slot; draw(); }
          else if ("erarity" in d) { K.haptic(); filt.equipment.rarity = d.erarity; draw(); }
        });
      });
    }
  });
  /* Same extension point as K.creatureActions, for one equipment piece (`e` = the row of profile/equipment/). */
  K.itemActions = [];
  K.screen("item", {
    tab: "creatures", title: "تجهیزات",
    render: function (root, params, ctx) {
      return K.api.cached("profile/equipment/").then(function (data) {
        var e = data.equipment.filter(function (x) { return x.id === params.id; })[0];
        if (!e) { root.innerHTML = K.state("chest", "این تجهیزات پیدا نشد", ""); return; }
        ctx.setTitle(e.name);
        var acts = K.itemActions.slice().sort(function (a, b) { return (a.order || 50) - (b.order || 50); });
        var buttons = acts.map(function (a) { try { return a.render(e, K.me) || ""; } catch (x) { return ""; } }).join("");
        root.innerHTML = '<div class="panel hero"><div class="art"><img src="' + e.img + '&s=l" alt=""><div class="over"><div><div class="ttl">' + K.esc(e.name) + ' <span class="num c-' + e.rarity + '">+' + e.level + "</span></div></div>" +
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
    render: function (root) {
      return K.refreshMe().then(function (me) {
        var tier = String(me.league.key || "bronze").split("_")[0];
        root.innerHTML = '<div class="tiles">' +
          info("medal", "", "لیگ", K.esc(me.league.name), "lg-" + tier) +
          info("trophy", "var(--cup)", "رتبه‌ی کاپ", K.n(me.cup_rank)) +
          info("claw", "var(--accent)", "هیولاها", K.n(me.creatures)) +
          info("chest", "var(--accent-2)", "تجهیزات", K.n(me.equipment)) +
          info("hall", "#62e6b4", "تالار مِهر", "سطح " + K.n(me.hall_level)) +
          info("tower", "#ff8a5c", "برج موگن", "طبقه‌ی " + K.n(me.tower_floor)) +
          info("calcheck", "var(--electric)", "ورود پشت‌سرهم", K.n(me.streak) + " روز") +
          info("flask", "var(--accent)", "سطح آزمایشگاه", K.n(me.lab_level)) + "</div>";
      });
    }
  });

  // ── leaderboard ──
  K.screen("leaderboard", {
    tab: "more", title: "جدول کاپ",
    render: function (root) {
      return K.api.get("profile/leaderboard/").then(function (data) {
        var rows = data.rows, top = rows.slice(0, 3), rest = rows.slice(3);
        function pod(r, cls) { return r ? '<div class="panel pod ' + cls + '"><div class="medal num">' + r.rank + '</div><div class="nm">' + K.esc(r.name) + '</div><div class="cp">' + K.ic("trophy") + K.n(r.cup) + "</div></div>" : "<div></div>"; }
        var html = '<div class="panel glow lb-mine"><span class="rk num">#' + Number(data.me.rank).toLocaleString("en-US") + '</span><span class="grow"><div class="b cut">' + K.esc(data.me.name) + '</div><small class="muted">رتبه‌ی تو در جدول کاپ</small></span>' +
          '<span class="b t-cup flex" style="gap:5px">' + K.ic("trophy") + '<span style="color:var(--text)">' + K.n(data.me.cup) + "</span></span></div>";
        if (top.length) html += '<div class="podium">' + pod(top[1], "s") + pod(top[0], "g") + pod(top[2], "z") + "</div>";
        if (rest.length) html += '<div class="panel lb">' + rest.map(function (r) {
          return '<div class="' + (r.me ? "me" : "") + '"><span class="rk num">' + r.rank + '</span><svg class="i lg lg-' + String(r.league).split("_")[0] + '" viewBox="0 0 24 24">' + K.icons.medal + '</svg><span class="nm">' + K.esc(r.name) + '</span><span class="cp">' + K.ic("trophy") + K.n(r.cup) + "</span></div>";
        }).join("") + "</div>";
        root.innerHTML = html;
      });
    }
  });

  K.hub("more", { id: "profile", title: "پروفایل", sub: "آمار و وضعیت تو", icon: "user", color: "var(--accent)", go: "profile", order: 90 });
  K.hub("more", { id: "leaderboard", title: "جدول کاپ", sub: "۵۰ نفر برتر", icon: "podium", color: "var(--gold)", go: "leaderboard", order: 60 });
})(window.K);
