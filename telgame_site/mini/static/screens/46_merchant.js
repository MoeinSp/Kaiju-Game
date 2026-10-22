/* «تابلوی سفارش تاجر» (api/merchant.py → game/merchant.py).
   Three merchant cards (portrait, what they want with have/need, what they pay) with «تحویل»
   and «رد»; an empty slot shows when the next merchant arrives; today's special order on top. */
(function (K) {
  "use strict";
  function pn(n) { return Number(n || 0).toLocaleString("en-US"); }   // a plain number, for toasts (K.n returns HTML)
  var REQ_IC = { dna: "dna", gold: "coin", serum: "vial", creature: "claw", gear: "shield" };

  K.hub("base", { id: "merchant", title: "سفارش تاجر", sub: "اضافه‌هات رو بفروش، طلا و XP بگیر", icon: "cart", color: "var(--gold)", go: "mo_board", order: 48, hall: 2 });

  function reward(o) {
    var out = '<span class="t-coin b">' + K.ic("coin") + " " + K.n(o.coins) + "</span>" +
      '<span class="t-xp b">' + K.ic("star") + " " + K.n(o.xp) + " XP</span>";
    if (o.extra_label) out += '<span class="mo-extra">' + K.ic("gift") + K.esc(o.extra_label) + "</span>";
    if (o.diamonds) out += '<span class="t-diamond b">' + K.ic("gem") + " " + K.n(o.diamonds) + "</span>";
    return '<div class="mo-reward">' + out + "</div>";
  }

  function card(o) {
    var reqs = o.reqs.map(function (r) {
      return '<div class="mo-req' + (r.ok ? " ok" : "") + '">' + K.ic(r.ok ? "check" : REQ_IC[r.t] || "box") + '<span class="grow">' + K.esc(r.label) + "</span>" +
        (r.ok ? "" : '<span class="num sm muted">' + K.n(r.have) + " / " + K.n(r.n) + "</span>") + "</div>";
    }).join("");
    var slot = o.special ? "s" : String(o.slot);
    return '<div class="panel mo-card' + (o.special ? " special" : "") + (o.ready ? " ready" : "") + '">' +
      '<div class="mo-head">' + (o.img ? '<img src="' + K.asset(o.img) + '" alt="">' : '<span class="mo-face">' + K.ic("cart") + "</span>") + '<div class="grow"><div class="sm muted">' + (o.special ? "سفارش ویژه‌ی امروز" : "سفارش") + "</div><b>" + K.esc(o.name) + "</b></div>" +
      (o.ready ? K.tag("آماده", "var(--good)", "check") : "") + "</div>" +
      '<div class="mo-reqs">' + reqs + "</div>" + reward(o) +
      '<div class="mo-btns"><button class="btn ' + (o.ready ? "good" : "") + '" data-act="deliver" data-slot="' + slot + '" data-id="' + o.id + '"' + (o.ready ? "" : " disabled") + ">" + K.ic("check") + "تحویل</button>" +
      (o.special ? "" : '<button class="btn ghost" data-act="reject" data-slot="' + slot + '" data-id="' + o.id + '">' + K.ic("close") + "رد</button>") + "</div></div>";
  }

  K.screen("mo_board", {
    title: "سفارش تاجر", tab: "base",
    render: function (root, params, ctx) {
      return K.api.get("merchant/").then(function (st) {
        var busy = false;
        function draw() {
          if (!st.unlocked) {
            root.innerHTML = K.state("cart", "تابلوی سفارش هنوز بسته‌ست", "از سطح " + K.n(st.unlock_hall) + " تالار مِهر باز می‌شه.");
            return;
          }
          var html = '<div class="banner mo-ban"' + (st.banner ? ' style="background-image:url(\'' + K.asset(st.banner) + '\')"' : "") + '><div class="grow"><div class="ttl">تابلوی سفارش تاجر</div>' +
            '<div class="mo-meta"><span>' + K.ic("box") + "تحویل امروز " + K.n(st.done_today) + " از " + K.n(st.daily) + "</span><span>" + K.ic("gem") + "الماس امروز " +
            K.n(st.diamonds_today) + " از " + K.n(st.diamond_cap) + "</span></div></div></div>";
          html += st.special ? card(st.special)
            : '<div class="panel pad mo-wait special">' + K.ic("star") + '<span class="grow">سفارش ویژه‌ی بعدی</span><span class="timer" data-left="' + st.special_in + '"></span></div>';
          html += '<div class="h2">' + K.ic("users") + "تاجرها</div>";
          html += st.slots.map(function (s) {
            if (s.reqs) return card(s);
            return '<div class="panel pad mo-wait">' + K.ic("hourglass") + '<span class="grow">تاجر بعدی تو راهه</span><span class="timer" data-left="' + s.wait + '"></span></div>';
          }).join("");
          html += '<p class="note">هیولایی که می‌دی همیشه ضعیف‌ترین هیولای اضافه‌ته: هیولای فعال، تیم، تیم دفاع، سیاه‌چال، دوستاره‌ها و فرم سیاه هیچ‌وقت داده نمی‌شن. تجهیزات پوشیده هم دست نمی‌خوره.</p>';
          root.innerHTML = html;
          K.timers(root, function () { K.after(1200, ctx.reload); });
        }
        function done(r) {
          busy = false; st = r.state; draw();
          var bits = ["+" + pn(r.coins) + " طلا", "+" + pn(r.xp) + " XP"];
          if (r.extra) bits.push(r.extra);
          if (r.diamonds) bits.push("+" + pn(r.diamonds) + " الماس");
          K.toast(r.name + " راضی رفت · " + bits.join(" · "), "ok");
          if (K.sfx) K.sfx("coin");
          if (r.levelup) K.after(900, function () { K.toast("آزمایشگاهت رسید به سطح " + pn(r.levelup.to), "ok"); });
        }
        function deliver(el) {
          var body = { slot: el.dataset.slot, id: +el.dataset.id };
          K.api.post("merchant/deliver/", body, el).then(done, function () { busy = false; });
        }
        draw();
        K.on(root, "deliver", function (el) {
          if (busy) return; busy = true;
          K.api.get("merchant/preview/?slot=" + el.dataset.slot + "&id=" + el.dataset.id).then(function (p) {
            if (!p.creatures.length) return deliver(el);
            K.confirm({ title: "این هیولا به تاجر داده می‌شه", text: p.creatures.join("، ") + " · تجهیزاتش برمی‌گرده به انبار.", ok: "تحویل بده", cancel: "بی‌خیال" })
              .then(function (yes) { if (yes) deliver(el); else busy = false; });
          }, function () { busy = false; });
        });
        K.on(root, "reject", function (el) {
          if (busy) return;
          K.confirm({ title: "این سفارش رو رد کنی؟", text: "تاجرش می‌ره و تاجر بعدی یک ساعت دیگه میاد.", ok: "رد کن", cancel: "نگهش دار", danger: true }).then(function (yes) {
            if (!yes) return; busy = true;
            K.api.post("merchant/reject/", { slot: el.dataset.slot, id: +el.dataset.id }, el).then(function (r) { busy = false; st = r.state; draw(); }, function () { busy = false; });
          });
        });
      });
    }
  });
})(window.K);
