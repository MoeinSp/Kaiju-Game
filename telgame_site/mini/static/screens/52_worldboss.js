/* World boss («غول سرگردان»): one boss for the whole server, twice a day.
   Screen: wb_boss. API: worldboss/ (full panel), worldboss/live/ (the 5-second poll), worldboss/hit/.
   Uses K.hu from 50_hunt.js (swap sheet, counters, hub status, the `boss` icon). */
(function (K) {
  "use strict";
  var HU = K.hu;
  /* the boss is shared, so its HP moves without us — but 8 s is plenty for a 30-minute fight, and a
     new boss is only ever spawned by a 5-minute job, so the idle screen looks every 45 s. Nothing
     is asked while the app is in the background. */
  var POLL_ACTIVE = 8000, POLL_IDLE = 45000;

  function rules(r) {
    var win = r.windows.map(function (w) { return K.n(w.from) + " تا " + K.n(w.to); }).join(" و ");
    function li(icon, color, html) { return '<div><span class="ic" style="color:' + color + '">' + K.ic(icon) + '</span><span class="t">' + html + "</span></div>"; }
    return '<div class="h2">' + K.ic("info") + 'قانون و جایزه‌ها</div><div class="panel list wb-rules">' +
      li("clock", "var(--accent)", "روزی دو بار (بین ساعت " + win + ") یه غول برای کل سرور پیدا می‌شه و " + K.n(r.minutes) + " دقیقه می‌مونه.") +
      li("sword", "var(--fire)", "هر نفر " + K.n(r.hits) + " ضربه داره. هر ضربه " + K.n(r.energy_cost) + " انرژی می‌خواد و همون لحظه طلا و DNA می‌ده.") +
      li("up", "var(--good)", "عنصر هیولات به عنصر غول برتری داشته باشه: +" + K.n(r.advantage_pct) + "٪ آسیب.") +
      li("chest", "var(--gold)", "آخرِ هر غول، همه‌ی کسایی که ضربه زدن بر اساس جونی که کل سرور زده جعبه می‌گیرن: " +
        (r.milestones || []).map(function (m) { return K.n(m.pct) + "٪ ← «" + K.esc(m.chest) + "»"; }).join("، ") + ". هر پله جعبه‌ی خودش رو اضافه می‌کنه.") +
      li("boss", "var(--mythic)", "اگه از پا دربیاد، همه " + K.n(r.kill_diamonds_all) + " الماس هم می‌گیرن.") +
      li("gem", "var(--diamond)", "نفر اول تا سوم: " + (r.top3_diamonds_escaped || []).map(function (x) { return K.n(x); }).join(" / ") + " الماس؛ اگه غول کشته بشه " +
        r.top3_diamonds.map(function (x) { return K.n(x); }).join(" / ") + " · ضربه‌ی آخر: +" + K.n(r.killer_diamonds) + ".") +
      li("clock", "var(--accent)", "هر " + K.n(r.hits) + " ضربه‌ات رو بزنی یه کارت سرعت " + K.n(r.full_hits_speedup) + " دقیقه‌ای هم می‌گیری.") +
      "</div>";
  }
  function topList(top) {
    if (!top || !top.length) return '<div class="panel pad center muted">هنوز کسی ضربه نزده. اولین ضربه رو تو بزن!</div>';
    var max = top[0].damage || 1;
    return '<div class="panel wb-top">' + top.map(function (r) {
      return '<div class="' + (r.me ? "me" : "") + '"><span class="rk' + (r.rank <= 3 ? " top" + r.rank : "") + '">' + r.rank + '</span><span class="nm"><span class="cut">' + K.esc(r.name) + (r.me ? ' <span class="muted xs">(تو)</span>' : "") + "</span>" +
        '<span class="wb-share"><i style="width:' + Math.max(4, Math.round(100 * r.damage / max)) + '%"></i></span></span><span class="dm">' + K.short(r.damage) + "</span></div>";
    }).join("") + "</div>";
  }
  function pips(left, total) {
    var s = "";
    for (var i = 0; i < total; i++) s += '<span class="' + (i < left ? "on" : "") + '">' + K.ic("sword") + "</span>";
    return s;
  }

  K.screen("wb_boss", {
    title: "غول سرگردان", tab: "battle",
    render: function (root, params, ctx) {
      var S, end = 0, busy = false, last = "", expired = false;

      function absentView() {
        var n = S.next, l = S.last;
        var html = '<div class="banner wb-banner" style="background-image:url(\'' + K.esc(S.art || "") + '\')"><div><div class="ttl">غول سرگردان</div>' +
          '<div class="sm" style="color:#c5cee2">یه غول برای کل سرور؛ با هم بزنیدش، همه جایزه ببرید</div></div></div>' +
          '<div class="panel pad mt wb-next"><span class="ico-box lg" style="color:var(--warn)">' + K.ic("hourglass") + '</span><div class="grow"><div class="muted sm">غول بعدی</div>' +
          (n ? '<div class="b wb-when">' + (n.today ? "امروز" : "فردا") + " بین ساعت " + K.n(n.from) + " تا " + K.n(n.to) + '</div><div class="muted xs">زمان دقیقش غافل‌گیریه</div>'
             : '<div class="b wb-when">به‌زودی</div><div class="muted xs">زمانش هنوز مشخص نشده</div>') + "</div></div>";
        html += lastCard(l);
        html += '<div class="callout mt">' + K.ic("bell") + "<span>وقتی غول بیاد ربات بهت خبر می‌ده. این صفحه هم خودش به‌روز می‌شه.</span></div>";
        return html + rules(S.rules);
      }

      /* how the last boss ended. An escape is the NORMAL ending (the boss is sized so about half
         its HP goes), so it is drawn as a finished fight with the player's own numbers. */
      function lastCard(l) {
        if (!l) return "";
        var dead = l.outcome === "dead", dealt = Math.max(0, Math.min(100, 100 - (l.hp_left_pct || 0)));
        var h = '<div class="h2">' + K.ic("doc") + 'آخرین غول</div><div class="panel wb-last ' + (dead ? "dead" : "esc") + '">' +
          '<div class="wb-last-h"><span class="ico-box lg">' + K.ic(dead ? "trophy" : "wind") + '</span><div class="grow"><div class="b cut">' + K.esc(l.name) + "</div>" +
          '<div class="sm muted">' + K.n(l.fighters) + " مبارز" + (l.element ? ' · <span class="e-' + l.element + '">' + K.esc(K.elLabel(l.element)) + "</span>" : "") + "</div></div>" +
          '<span class="tag">' + (dead ? "از پا دراومد" : "فرار کرد") + "</span></div>";
        if (l.max_hp) h += '<div class="wb-last-bar">' + K.bar(dealt / 100, dead ? "good" : "gold", "thick") +
          '<div class="xs muted">' + (dead ? "همه‌ی جونش رو با هم گرفتید" : K.n(dealt) + "٪ جونش رو با هم گرفتید و بعد وقتش تموم شد") + "</div></div>";
        if (l.my_damage > 0) {
          h += '<div class="wb-stats"><div><small>آسیب تو</small><b class="num">' + Number(l.my_damage).toLocaleString("en-US") + '</b></div><div><small>رتبه‌ی تو</small><b class="num">' + Number(l.my_rank || 0) + "</b></div></div>" +
            '<div class="wb-last-note">' + K.ic(dead ? "gift" : "check") + "<span>" +
            (dead ? (l.killer ? "ضربه‌ی آخر مال تو بود. " : "") + (l.settled ? "جایزه‌ی پایانی به حسابت اومده." : "جایزه‌ی پایانی تا چند دقیقه‌ی دیگه می‌رسه.")
                  : (l.settled ? "جایزه‌ی پایانی (بر اساس جونی که سرور زد) به حسابت اومده." : "طلا و DNA هر ضربه‌ات همون لحظه اومد؛ جایزه‌ی پایانی تا چند دقیقه‌ی دیگه می‌رسه.")) + "</span></div>";
        } else {
          h += '<div class="wb-last-note muted">' + K.ic("info") + "<span>توی این نبرد ضربه‌ای نزدی. غول بعدی رو از دست نده.</span></div>";
        }
        return h + "</div>";
      }

      function activeView() {
        var b = S.boss, me = S.me, p = S.per_hit;
        var html = '<div class="panel wb-boss e-' + b.element + '"><div class="wb-art" id="wb-art" style="background-image:url(\'' + K.esc(S.art || b.img || "") + '\')">' +
          '<span class="wb-live">' + K.ic("boss") + 'توی میدونه</span><span class="wb-left" id="wb-left"></span><div id="wb-float"></div>' +
          '<div class="wb-name"><div class="ttl">' + K.esc(b.name) + "</div>" + K.elTag(b.element) + "</div></div>" +
          '<div class="wb-hp"><div class="wb-hpbar"><i id="wb-bar"></i><b class="num" id="wb-pct"></b></div>' +
          '<div class="wb-hpline"><span>' + K.ic("heart", "f") + ' <span id="wb-hpnum"></span></span><span id="wb-fighters"></span></div>' +
          '<div class="wb-hint">' + K.ic("info") + "<span>هر ضربه همون لحظه جایزه می‌ده؛ چه غول بمیره، چه آخرش فرار کنه.</span></div></div></div>";
        html += '<div class="panel pad mt wb-mine"><div class="wb-stats"><div><small>ضربه‌های تو</small><div class="wb-pips" id="wb-pips"></div></div>' +
          '<div><small>آسیب تو</small><b class="num" id="wb-mydmg"></b></div></div>';
        if (me) {
          html += '<div class="wb-me">' + K.fighter(me, '<div class="wb-tags">' + K.elTag(me.element) +
            (S.advantage ? '<span class="tag" style="color:var(--good)">' + K.ic("up") + "برتری با تو: +۲۰٪ آسیب</span>" : '<span class="tag plain">بدون برتری عنصری</span>') + "</div>") +
            '<button class="btn sm" data-act="swap">' + K.ic("swap") + "تعویض</button></div>";
          if (p) html += '<div class="wb-per">' + K.ic("gift") + "<span>هر ضربه حدود " + K.n(p.damage) + " آسیب می‌زنه (" + K.n(p.swing_pct) + "٪ بالا و پایین) و حدود " +
            K.amounts({ coins: p.coins, dna: p.dna }, " و ") + " می‌ده.</span></div>";
        } else {
          html += '<div class="callout warn mt">' + K.ic("warn") + "<span>اول یه هیولای فعال انتخاب کن.</span></div>";
        }
        html += "</div>" +
          '<button class="btn danger lg block mt" id="wb-hit" data-act="hit"></button><div id="wb-last"></div>' +
          '<div class="h2">' + K.ic("podium") + 'بیشترین آسیب</div><div id="wb-top"></div>';
        return html + rules(S.rules);
      }

      /* everything that moves while the boss is up — no full redraw, so the HP bar animates */
      function patch() {
        var b = S.boss; if (!b) return;
        var q = function (id) { return root.querySelector("#" + id); };
        if (!q("wb-bar")) return;
        var ratio = Math.max(0, Math.min(1, b.hp / Math.max(1, b.max_hp)));
        q("wb-bar").style.width = (ratio * 100).toFixed(2) + "%";
        q("wb-bar").className = ratio <= 0.25 ? "low" : ratio <= 0.5 ? "mid" : "";
        q("wb-pct").textContent = (ratio > 0 && ratio < 0.01 ? "<1" : Math.round(ratio * 100)) + "%";
        q("wb-hpnum").innerHTML = K.n(b.hp) + ' <span class="faint">/ ' + K.short(b.max_hp) + "</span>";
        q("wb-fighters").innerHTML = K.ic("users") + " " + K.n(S.fighters) + " مبارز";
        q("wb-pips").innerHTML = pips(S.hits_left, S.rules.hits);
        q("wb-mydmg").textContent = Number(S.my_damage || 0).toLocaleString("en-US");
        q("wb-top").innerHTML = topList(S.top);
        q("wb-last").innerHTML = last;
        var btn = q("wb-hit"), cost = S.rules.energy_cost;
        if (S.hits_left <= 0) { btn.disabled = true; btn.innerHTML = K.ic("check") + "هر " + K.n(S.rules.hits) + " ضربه‌ات رو زدی"; }
        else if (!S.me) { btn.disabled = true; btn.innerHTML = K.ic("lock") + "هیولای فعال نداری"; }
        else { btn.disabled = false; btn.innerHTML = K.ic("sword") + 'ضربه بزن<span class="cost">' + K.ic("bolt", "f") + K.n(cost) + "</span>"; }
      }
      function tick() {
        var el = root.querySelector("#wb-left"); if (!el || !S.active) return;
        var left = end - Date.now() / 1000;
        if (left <= 0) {
          el.innerHTML = K.ic("wind") + "فرار کرد";
          if (!expired) { expired = true; K.after(1500, function () { ctx.reload(); }); }
          return;
        }
        el.classList.toggle("soon", left < 120);
        el.innerHTML = K.ic("clock") + '<span class="num">' + K.clock(left) + "</span>";
      }
      function draw() {
        root.innerHTML = S.active ? activeView() : absentView();
        if (S.active) { end = Date.now() / 1000 + S.boss.seconds_left; patch(); tick(); }
        HU.setHub({ boss: !!S.active });
      }
      function floatDamage(n, adv) {
        var host = root.querySelector("#wb-float"), art = root.querySelector("#wb-art"); if (!host) return;
        var el = document.createElement("span");
        el.className = "wb-dmg" + (adv ? " adv" : "");
        el.style.insetInlineStart = (28 + Math.random() * 40) + "%";
        el.textContent = "-" + Number(n).toLocaleString("en-US");
        host.appendChild(el);
        art.classList.remove("hit"); void art.offsetWidth; art.classList.add("hit");
        setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 1300);
      }
      function poll() {
        if (busy || !ctx.alive() || document.hidden) return;
        var was = S.active, id = S.boss && S.boss.id;
        K.api.get("worldboss/live/").then(function (d) {
          if (busy || !ctx.alive()) return;
          if (d.active !== was || (d.boss && d.boss.id !== id)) { ctx.reload(); return; }
          if (!d.active) return;
          S.boss.hp = d.boss.hp; S.boss.max_hp = d.boss.max_hp; S.boss.seconds_left = d.boss.seconds_left;
          S.fighters = d.fighters; S.top = d.top; S.hits_left = d.hits_left; S.my_damage = d.my_damage;
          end = Date.now() / 1000 + d.boss.seconds_left;
          patch();
        }).catch(function () {});
      }

      K.on(root, "hit", function (el) {
        if (busy) return; busy = true;
        K.api.post("worldboss/hit/", {}, el).then(function (d) {
          busy = false;
          var r = d.result, art = S.art;
          K.haptic("hit");
          floatDamage(r.damage, r.advantage);
          (r.missions || []).forEach(function (m) { K.toast("مأموریت کامل شد: " + m.label, "ok"); });
          if (!d.active) {            // that was the killing blow
            S.boss.hp = 0; patch();
            K.after(700, function () {
              S = d; S.art = S.art || art; draw();
              K.reward({ title: "ضربه‌ی آخر مال تو بود!", text: r.boss_name + " از پا دراومد. جایزه‌ی نهایی تا چند دقیقه‌ی دیگه می‌رسه.", icon: "boss", coins: r.coins, dna: r.dna, button: "عالیه" });
            });
            return;
          }
          last = '<div class="wb-lasthit">' + K.ic("sword") + "<span><b>" + K.n(r.damage) + "</b> آسیب زدی" + (r.advantage ? " (با برتری عنصری)" : "") + "</span>" +
            '<span class="wb-gain">' + K.amounts({ coins: r.coins, dna: r.dna }) + "</span></div>";
          var keepArt = S.art; S = d; S.art = S.art || keepArt;
          end = Date.now() / 1000 + d.boss.seconds_left;
          patch();
          if (S.hits_left <= 0) K.haptic("ok");
        }).catch(function (err) {
          busy = false;
          if (err && err.status === 400 && err.code !== "energy") poll();
        });
      });
      K.on(root, "swap", function () {
        if (busy) return;
        HU.swapSheet({ enemy: S.boss ? S.boss.element : null, sub: "هیولایی که به عنصر غول برتری داره +۲۰٪ آسیب می‌زنه. انتخابت هیولای فعالت می‌شه." }).then(function (id) {
          if (!id) return;
          return K.api.post("worldboss/swap/", { creature_id: id }).then(function (d) {
            S = d; K.invalidate("profile/creatures/"); draw(); K.haptic("ok"); K.toast("هیولای فعالت عوض شد.", "ok");
          });
        }).catch(function () {});
      });

      return K.api.get("worldboss/").then(function (d) {
        S = d; draw();
        K.every(1000, tick);
        K.every(d.active ? POLL_ACTIVE : POLL_IDLE, poll);
      });
    }
  });

  K.hub("battle", { id: "boss", title: "غول سرگردان", sub: "غول مشترک سرور، روزی دو بار", icon: "boss", color: "var(--mythic)", go: "wb_boss", order: 7,
    badge: function () { return HU.hubStatus().boss ? "live" : 0; } });
})(window.K);
