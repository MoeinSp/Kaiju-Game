/* «آزمایشگاه سرم» and «انبار» (api/serums.py → game/serums.py, game/arena_chests.py).
   Lab: one card per serum (picture, level, effect, how many on the shelf) with «ساخت» and
   «مصرف»; the brewing queue with its clock; a details sheet with every level and the upgrade.
   Backpack: stored arena chests (move into a slot), boss chests, speed-up cards, serums. */
(function (K) {
  "use strict";
  function pn(n) { return Number(n || 0).toLocaleString("en-US"); }   // a plain number, for toasts (K.n returns HTML)
  function dna(n) { return '<span class="t-dna b">' + K.ic("dna") + " " + K.n(n) + "</span>"; }
  function money(gold, d) { return '<span class="t-coin b">' + K.ic("coin") + " " + K.n(gold) + "</span> + " + dna(d); }

  K.hub("base", { id: "serums", title: "آزمایشگاه سرم", sub: "بساز، صبر کن، استفاده کن", icon: "vial", color: "var(--good)", go: "sr_lab", order: 46, hall: 2 });
  K.hub("base", { id: "backpack", title: "انبار", sub: "جعبه‌ها، کارت سرعت و سرم‌ها", icon: "box", color: "var(--gold)", go: "bp_home", order: 47 });

  K.screen("sr_lab", {
    title: "آزمایشگاه سرم", tab: "base",
    render: function (root, params, ctx) {
      return K.api.get("serums/").then(function (st) {
        var busy = false;
        function draw() {
          if (!st.level) {
            root.innerHTML = '<div class="banner sr-ban" style="background-image:url(\'' + K.asset("img/hub_serums.jpg") + '\')"><div><div class="ttl">آزمایشگاه سرم</div><div class="sm">هنوز ساخته نشده</div></div></div>' +
              K.state("vial", "اول آزمایشگاه سرم رو بساز", "از سطح ۲ تالار مِهر باز می‌شه. برو «پایگاه ← ساختمان‌ها».",
                '<button class="btn primary" data-act="build" style="margin-top:14px">' + K.ic("building") + "ساختمان‌ها</button>");
            return;
          }
          var shelf = st.stock_total, cap = st.capacity, queued = st.queue.length;
          var html = '<div class="banner sr-ban" style="background-image:url(\'' + K.asset("img/hub_serums.jpg") + '\')"><div class="grow">' +
            '<div class="ttl">آزمایشگاه سرم · سطح ' + K.n(st.level) + "</div>" +
            '<div class="sr-meta"><span>' + K.ic("vial") + "قفسه " + K.n(shelf) + "/" + K.n(cap) + "</span><span>" + K.ic("clock") + K.n(st.brew_minutes) + " دقیقه هر سرم</span>" +
            "<span>" + K.ic("check") + "امروز " + K.n(st.used) + " از " + K.n(st.daily) + "</span></div>" + K.bar(cap ? (shelf + queued) / cap : 0, "good") + "</div></div>";
          // armed serums waiting for their moment
          var armed = st.kinds.filter(function (k) { return k.armed; });
          if (armed.length) html += '<div class="sr-armed">' + armed.map(function (k) {
            return '<span class="sr-chip"><img src="' + K.asset(k.img) + '" alt="">' + K.esc(k.label) + "<small>" + K.esc(k.where) + "</small>" +
              (k.key === "shell" && st.shell_left ? ' <span class="timer" data-left="' + st.shell_left + '"></span>' : "") + "</span>";
          }).join("") + "</div>";
          // the brewing queue
          if (queued) {
            html += '<div class="h2">' + K.ic("hourglass") + 'در حال ساخت</div><div class="panel pad sr-queue">' + st.queue.map(function (key, i) {
              var k = st.kinds.filter(function (x) { return x.key === key; })[0];
              return '<span class="sr-q' + (i === 0 ? " now" : "") + '"><img src="' + K.asset(k.img) + '" alt="">' +
                (i === 0 && st.next_in != null ? '<span class="timer" data-left="' + st.next_in + '"></span>' : "") + "</span>";
            }).join("") + (queued > (st.next_in != null ? 1 : 0) ? '<button class="btn sm ghost" data-act="cancel">' + K.ic("close") + "لغو آخری</button>" : "") +
              (shelf >= cap ? '<p class="note">قفسه پره؛ ساخت تا مصرف یه سرم صبر می‌کنه.</p>' : "") + "</div>";
          }
          if (st.upgrading) html += '<div class="callout good mt">' + K.ic("up") + "<div>در حال ارتقای «" + K.esc(st.upgrading.label) + '» · <span class="timer" data-left="' + st.upgrading.left + '"></span></div></div>';
          html += '<div class="h2">' + K.ic("flask") + 'سرم‌ها</div><div class="sr-grid">' + st.kinds.map(card).join("") + "</div>" +
            '<p class="note">هزینه‌ی ساخت هر سرم: ' + dna(st.brew_cost) + " · سرم‌ها رو اینجا ارتقا می‌دی؛ سطح هر سرم از سطح ساختمون بالاتر نمی‌ره.</p>" +
            '<button class="panel sr-notify" data-act="notify">' + K.ic("bell") + '<span class="grow">خبرم کن وقتی سرم‌ها آماده شد<small>حداکثر روزی ۲ پیام توی ربات</small></span>' +
            K.tag(st.notify ? "روشن" : "خاموش", st.notify ? "var(--good)" : "var(--muted)") + "</button>";
          root.innerHTML = html;
          K.timers(root, function () { K.after(1200, ctx.reload); });
        }
        function card(k) {
          if (!k.unlocked) return '<div class="sr-card locked"><img src="' + K.asset(k.img) + '" alt=""><b>' + K.esc(k.label) + "</b><small>از سطح " + K.n(k.unlock) + " آزمایشگاه</small></div>";
          return '<div class="sr-card' + (k.armed ? " armed" : "") + '" data-key="' + k.key + '">' +
            '<button class="sr-art" data-act="info" data-key="' + k.key + '"><img src="' + K.asset(k.img) + '" alt=""><span class="sr-lv">سطح ' + K.n(k.level) + "</span>" +
            (k.stock ? '<span class="sr-cnt num">×' + K.n(k.stock) + "</span>" : "") + "</button>" +
            "<b>" + K.esc(k.label) + "</b><small>" + K.esc(k.effect) + "</small>" +
            '<div class="sr-btns"><button class="btn sm" data-act="brew" data-key="' + k.key + '">' + K.ic("plus") + "ساخت</button>" +
            '<button class="btn sm primary" data-act="use" data-key="' + k.key + '"' + (k.stock ? "" : " disabled") + ">مصرف</button></div></div>";
        }
        function info(key) {
          var k = st.kinds.filter(function (x) { return x.key === key; })[0]; if (!k) return;
          var up = k.upgrade, rows = "";
          for (var l = 1; l <= 5; l++) rows += '<div class="sr-lrow' + (l === k.level ? " on" : l < k.level ? " done" : "") + '"><span>سطح ' + K.n(l) + "</span><span>" +
            K.esc((k.all || [])[l - 1] || "") + "</span></div>";
          var box = K.sheet('<div class="grab"></div><div class="pad sr-info"><img src="' + K.asset(k.img) + '" alt=""><div class="ttl" style="font-size:22px">' + K.esc(k.label) + "</div>" +
            '<p class="lead">' + K.esc(k.effect) + " · " + K.esc(k.where) + "</p>" + '<div class="panel list sr-levels">' + rows + "</div>" +
            (up ? (up.capped ? '<div class="callout mt">' + K.ic("lock") + "<div>برای سطح " + K.n(k.level + 1) + " باید آزمایشگاه سرم سطح " + K.n(k.level + 1) + " باشه.</div></div>"
              : '<button class="btn gold block lg mt" data-up="' + k.key + '"' + (st.upgrading ? " disabled" : "") + ">" + K.ic("up") + "ارتقا به سطح " + K.n(k.level + 1) +
                '<span class="cost">' + K.n(up.hours) + " ساعت</span></button>" + '<p class="note center">هزینه: ' + money(up.gold, up.dna) + "</p>")
              : '<div class="callout good mt">' + K.ic("check") + "<div>به بالاترین سطح رسیده.</div></div>") + "</div>");
          box.addEventListener("click", function (ev) {
            var b = ev.target.closest("[data-up]"); if (!b || busy) return; busy = true;
            K.api.post("serums/upgrade/", { kind: b.dataset.up }, b).then(function (r) {
              busy = false; st = r.state; K.closeSheet(); draw(); K.toast("ارتقای «" + k.label + "» به سطح " + pn(r.to) + " شروع شد · " + pn(r.hours) + " ساعت", "ok");
            }, function () { busy = false; });
          });
        }
        draw();
        K.on(root, "build", function () { K.go("bs_buildings"); });
        K.on(root, "info", function (el) { info(el.dataset.key); });
        K.on(root, "brew", function (el) {
          if (busy) return; busy = true;
          K.api.post("serums/brew/", { kind: el.dataset.key }, el).then(function (r) {
            busy = false; st = r.state; draw(); K.toast("رفت توی صف ساخت · " + pn(r.cost) + " DNA", "ok");
          }, function () { busy = false; });
        });
        K.on(root, "use", function (el) {
          if (busy) return; busy = true;
          K.api.post("serums/use/", { kind: el.dataset.key }, el).then(function (r) {
            busy = false; st = r.state; draw(); K.toast("«" + r.label + "» · " + r.note, "ok"); if (K.sfx) K.sfx("levelup");
          }, function () { busy = false; });
        });
        K.on(root, "notify", function (el) {
          if (busy) return; busy = true;
          K.api.post("serums/notify/", { on: !st.notify }, el).then(function (r) {
            busy = false; st = r.state; draw(); K.toast(st.notify ? "وقتی سرم‌ها آماده شد خبرت می‌کنم" : "اعلان خاموش شد", "ok");
          }, function () { busy = false; });
        });
        K.on(root, "cancel", function (el) {
          if (busy) return; busy = true;
          K.api.post("serums/cancel/", {}, el).then(function (r) { busy = false; st = r.state; draw(); K.toast("لغو شد · " + pn(r.refund) + " DNA برگشت", "ok"); },
            function () { busy = false; });
        });
      });
    }
  });

  K.screen("bp_home", {
    title: "انبار", tab: "base",
    render: function (root, params, ctx) {
      return K.api.get("serums/backpack/").then(function (d) {
        function draw() {
          var html = '<div class="banner sr-ban" style="background-image:url(\'' + K.asset("img/hub_backpack.jpg") + '\')"><div><div class="ttl">انبار</div>' +
            '<div class="sm">هیچ جایزه‌ای گم نمی‌شه: جعبه‌ای که جایگاهش پر بود اینجا منتظرته.</div></div></div>';
          html += '<div class="h2">' + K.ic("chest") + "جعبه‌های آرنا در انبار</div>" + (d.chests.length
            ? '<div class="panel list">' + d.chests.map(function (c) {
              return '<div><img class="th" src="' + c.img + '" alt=""><span class="t">' + K.esc(c.name) + "<small>منتظر یه جایگاه خالی</small></span>" +
                '<button class="btn sm primary" data-act="move" data-id="' + c.id + '"' + (d.free_slot ? "" : " disabled") + ">به جایگاه</button></div>";
            }).join("") + "</div>" + (d.free_slot ? "" : '<p class="note">هر ۴ جایگاه پره؛ اول یکی رو باز کن.</p>')
            : '<p class="note">جعبه‌ای توی انبار نیست؛ وقتی جایگاه‌های جعبه پر باشه، جعبه‌ی تازه اینجا میاد.</p>');
          if (d.boss_chests) html += '<div class="h2">' + K.ic("skull") + 'جعبه‌های غول</div><button class="panel info" data-act="boss"><span class="ic t-mythic">' + K.ic("chest") +
            "</span><div class='grow'><b>" + K.n(d.boss_chests) + ' جعبه‌ی بازنشده</b><small class="muted">از نبرد با غول سرگردان</small></div>' + K.ic("chevron") + "</button>";
          if (d.cup_chests) html += '<div class="h2">' + K.ic("trophy") + 'جعبه‌های جام</div><button class="panel info" data-act="cup"><span class="ic t-coin">' + K.ic("chest") +
            "</span><div class='grow'><b>" + K.n(d.cup_chests) + ' جعبه‌ی بازنشده</b><small class="muted">جایزه‌ی جام آخر هفته</small></div>' + K.ic("chevron") + "</button>";
          html += '<div class="h2">' + K.ic("hourglass") + "کارت‌های سرعت</div>" + (d.speedups.length
            ? '<div class="bp-chips">' + d.speedups.map(function (s) { return '<span class="bp-chip">' + K.ic("hourglass") + K.n(s.minutes) + " دقیقه <b>×" + K.n(s.count) + "</b></span>"; }).join("") +
              '</div><p class="note">کارت سرعت رو موقع ارتقای ساختمون‌ها مصرف می‌کنی.</p>'
            : '<p class="note">کارت سرعتی نداری.</p>');
          html += '<div class="h2">' + K.ic("vial") + "سرم‌ها</div>" + (d.serums.length
            ? '<button class="panel pad bp-serums" data-act="lab">' + d.serums.map(function (s) { return '<span class="bp-s"><img src="' + K.asset(s.img) + '" alt=""><b class="num">×' + K.n(s.count) + "</b><small>" + K.esc(s.label) + "</small></span>"; }).join("") + "</button>"
            : '<p class="note">سرمی نداری. توی «آزمایشگاه سرم» بساز.</p>');
          root.innerHTML = html;
        }
        draw();
        K.on(root, "move", function (el) {
          K.api.post("serums/backpack/move/", { id: +el.dataset.id }, el).then(function (r) {
            d = r.backpack; draw(); K.invalidate("arena/chests/"); K.toast("جعبه رفت توی جایگاه " + pn(r.slot), "ok");
          });
        });
        K.on(root, "boss", function () { K.go("wb_boss"); });
        K.on(root, "cup", function () { K.go("tr_home"); });
        K.on(root, "lab", function () { K.go("sr_lab"); });
      });
    }
  });
})(window.K);
