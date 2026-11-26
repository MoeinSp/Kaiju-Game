/* «خرید الماس و طلا» — the real-money store (api/shop.py paid/ → game/purchase.py).
   Owner packs, ready-made packages (diamonds / gold / دی‌ان‌ای, priced by the owner's per-unit prices),
   VIP and a custom amount. Paying is the bot's card-to-card flow: an order shows the card here,
   the bot sends it to the chat too, and the receipt photo is sent in the bot chat for approval. */
(function (K) {
  "use strict";
  var RES = { diamonds: ["الماس", "diamond", "t-diamond"], coins: ["طلا", "coin", "t-coin"], dna: ["دی‌ان‌ای", "dna", "t-dna"] };
  function toman(n) { return K.n(n) + " <small>تومان</small>"; }
  function pn(n) { return Number(n || 0).toLocaleString("en-US"); }

  K.hub("more", { id: "buy", title: "خرید", sub: "پک ویژه، طلا، الماس، VIP", icon: "gem",
                  color: "var(--diamond)", go: "sh_buy", order: 1, art: "buy" });

  function openBot(bot) {
    if (!bot) { K.toast("یوزرنیم ربات پیدا نشد", "bad"); return; }
    var url = "https://t.me/" + String(bot).replace(/^@/, "");
    try { if (K.tg && K.tg.openTelegramLink) { K.tg.openTelegramLink(url); return; } } catch (e) {}
    window.open(url, "_blank");
  }

  /* the «pay & send the receipt» sheet for an open order */
  function paySheet(p, onCancel) {
    if (!p || !p.card) {
      K.toast((p && p.error) || "کارت بانکی آماده نیست — دوباره امتحان کن", "bad");
      return;
    }
    var box = K.sheet('<div class="grab"></div><div class="pad st-pay">' +
      '<div class="ttl" style="font-size:20px">پرداخت و ارسال رسید</div>' +
      '<div class="panel pad st-order"><span class="muted sm">سفارش تو</span><b>' + K.esc(p.summary) + '</b><span class="st-sum">' + toman(p.price) + "</span></div>" +
      '<div class="st-step"><span class="st-no">۱</span><div class="grow">مبلغ رو به این کارت واریز کن:' +
      '<button class="st-card" data-copy="' + K.esc(p.card) + '"><span class="num">' + K.esc(p.card) + '</span><span class="st-copy">' + K.ic("doc") + "کپی</span></button>" +
      (p.holder ? '<div class="sm muted">به نام: <b>' + K.esc(p.holder) + "</b></div>" : "") + "</div></div>" +
      '<div class="st-step"><span class="st-no">۲</span><div class="grow">عکس رسید رو توی <b>چت همین ربات</b> بفرست. کارت و راهنما همین الان اونجا هم برات فرستاده شد.' +
      '<div class="sm muted">رسید رو تا ' + K.n(p.hours) + " ساعت بفرست؛ بعدش سفارش رو دوباره ثبت کن.</div></div></div>" +
      '<div class="st-step"><span class="st-no">۳</span><div class="grow">بعد از تأیید پشتیبانی، موجودی خودش به حسابت اضافه می‌شه.</div></div>' +
      (p.bot ? '<button class="btn primary block lg mt" data-bot>' + K.ic("send") + "ارسال رسید در ربات</button>" : "") +
      '<button class="btn ghost block mt t-bad" data-cancel>' + K.ic("close") + "لغو سفارش</button></div>");
    box.addEventListener("click", function (ev) {
      var c = ev.target.closest("[data-copy]");
      if (c) {
        var done = function () { K.toast("شماره کارت کپی شد", "ok"); };
        try { navigator.clipboard.writeText(c.dataset.copy).then(done, done); } catch (e) { done(); }
        return;
      }
      if (ev.target.closest("[data-bot]")) { openBot(p.bot); return; }
      if (ev.target.closest("[data-cancel]")) {
        K.confirm({ title: "لغو سفارش؟", danger: true, ok: "آره، لغو کن", cancel: "نه",
          text: "سفارش حذف می‌شه و باید دوباره ثبتش کنی." }).then(function (yes) {
          if (!yes) return;
          K.api.post("shop/paid/cancel/", {}, box).then(function () {
            K.closeSheet();
            K.toast("سفارش لغو شد", "ok");
            if (onCancel) onCancel();
          });
        });
      }
    });
  }
  K.paySheet = paySheet;

  K.screen("sh_buy", {
    title: "خرید", tab: "more",
    render: function (root, params, ctx) {
      var busy = false, tab = "diamonds", custom = { coins: 0, dna: 0, diamonds: 0 }, d;

      function card(img, name, amount, price, act, extra) {
        return '<button class="st-item" data-act="' + act + '"' + extra + ">" +
          '<span class="st-art"><img src="' + K.asset(img) + '" alt="" loading="lazy"></span>' +
          '<span class="st-name">' + K.esc(name) + "</span>" + amount +
          '<span class="st-price">' + toman(price) + "</span></button>";
      }
      function dealCard(o, kick) {
        var sold = o.sold_out;
        return '<button class="st-offer' + (sold ? " out" : "") + '" data-act="deal" data-id="' + o.id + '"' + (sold ? " disabled" : "") + ">" +
          '<img src="' + K.asset(o.art) + '" alt="">' +
          (o.discount ? '<span class="st-ribbon">-' + K.n(o.discount) + "٪</span>" : "") +
          '<span class="st-obody"><small class="of-kick">' + K.ic("flame") + K.esc(kick) + "</small>" +
          "<b>" + K.esc(o.title) + '</b><span class="sm muted cut">' + K.esc(o.gets || "") + "</span>" +
          (o.ends_in != null ? '<span class="st-otime">' + K.ic("clock") + '<span class="num" data-oend="' + (Date.now() + o.ends_in * 1000) + '"></span></span>' : "") +
          (o.left != null ? '<span class="sm muted">' + (sold ? "سقف این دوره پر شد" : "باقی‌مونده: " + K.n(o.left)) + "</span>" : "") +
          '<span class="st-oprice">' + (o.discount ? '<s class="muted">' + K.n(o.original) + "</s> " : "") + toman(o.price) +
          "</span></span></button>";
      }
      function dealsHtml() {
        var deals = d.deals || {}, html = "";
        var weekly = deals.weekly || [], daily = deals.daily || [];
        if (weekly.length) {
          html += '<div class="h2">' + K.ic("calendar") + "افر هفتگی</div>" +
            '<div class="st-offers">' +
            weekly.map(function (o) { return dealCard(o, "افر هفتگی"); }).join("") + "</div>";
        }
        if (daily.length) {
          html += '<div class="h2">' + K.ic("flame") + "افر روزانه</div>" +
            '<div class="st-offers">' +
            daily.map(function (o) { return dealCard(o, "افر روزانه"); }).join("") + "</div>";
        }
        return html;
      }
      function offersHtml() {
        var list = d.offers || [];
        if (!list.length) return "";
        if (K.offers) K.offers.set(list);
        return '<div class="h2">' + K.ic("spark") + "افرای ویژه</div>" +
          '<div class="st-offers">' + list.map(function (o) {
            var left = o.max ? Math.max(0, o.max - (o.bought || 0)) : null;
            return '<button class="st-offer" data-act="offer" data-id="' + o.id + '">' +
              '<img src="' + K.asset(o.art) + '" alt="">' +
              (o.discount ? '<span class="st-ribbon">-' + K.n(o.discount) + "٪</span>" : "") +
              '<span class="st-obody"><small class="of-kick">' + K.ic("flame") + "پیشنهاد ویژه</small>" +
              "<b>" + K.esc(o.title) + '</b><span class="sm muted cut">' + K.esc(o.gets || "") + "</span>" +
              (o.ends_in != null ? '<span class="st-otime">' + K.ic("clock") + '<span class="num" data-oend="' + (Date.now() + o.ends_in * 1000) + '"></span></span>' : "") +
              (left != null ? '<span class="sm muted">باقی‌مونده: ' + K.n(left) + "</span>" : "") +
              '<span class="st-oprice">' + (K.offers ? K.offers.price(o, true) : toman(o.price_toman || 0)) + "</span></span></button>";
          }).join("") + "</div>";
      }
      function packsHtml() {
        if (!d.packs.length) return "";
        return '<div class="h2">' + K.ic("gift") + "پک‌های ویژه</div>" + '<div class="st-grid">' + d.packs.map(function (p) {
          var badge = p.discount ? '<span class="st-ribbon">' + K.n(p.discount) + "٪ تخفیف</span>" : "";
          var amt = '<span class="st-amt">' + K.amounts({ coins: p.coins, dna: p.dna, diamonds: p.diamonds }, "<br>") + "</span>";
          var price = (p.discount ? '<s class="muted">' + K.n(p.original) + "</s> " : "") + "";
          return card(p.img, p.title, badge + amt, p.price, "pack", ' data-id="' + p.id + '"').replace('<span class="st-price">', '<span class="st-price">' + price);
        }).join("") + "</div>";
      }
      function presetsHtml() {
        var kinds = Object.keys(RES).filter(function (r) { return d.presets.some(function (x) { return x.res === r; }); });
        if (!kinds.length) return "";
        if (kinds.indexOf(tab) < 0) tab = kinds[0];
        return '<div class="h2">' + K.ic("cart") + "بسته‌ها</div>" +
          '<div class="seg st-tabs">' + kinds.map(function (r) {
            return '<button class="' + (r === tab ? "on" : "") + '" data-act="tab" data-res="' + r + '">' + K.ic(RES[r][1]) + RES[r][0] + "</button>";
          }).join("") + "</div>" +
          '<div class="st-grid">' + d.presets.filter(function (x) { return x.res === tab; }).map(function (x) {
            var strike = (x.discount && x.list_price > x.price)
              ? '<s class="muted">' + K.n(x.list_price) + "</s> " : "";
            return card(x.img, x.name, '<span class="st-amt ' + RES[x.res][2] + '">' + K.ic(RES[x.res][1]) + " " + K.n(x.amount) + "</span>", x.price, "preset",
              ' data-res="' + x.res + '" data-amount="' + x.amount + '"').replace('<span class="st-price">', '<span class="st-price">' + strike);
          }).join("") + "</div>";
      }
      function vipHtml() {
        var v = d.vip;
        return '<div class="h2">' + K.ic("crown") + "اشتراک VIP</div>" +
          (v.active ? '<div class="callout good mb">' + K.ic("crown") + "<div>اشتراک فعال: <b>" + K.esc(v.tier_name) + "</b> · " + K.n(v.days_left) + " روز مونده. خرید دوباره، مدتش رو اضافه می‌کنه.</div></div>" : "") +
          '<div class="st-vips">' + v.tiers.map(function (t) {
            return '<div class="panel st-vip k-' + K.esc(t.key) + '"><img src="' + K.asset("img/vip_" + t.key + ".jpg") + '" alt="">' +
              '<div class="st-vip-b"><b>' + K.esc(t.name) + '</b><span class="sm muted">' + K.n(t.days) + " روزه</span>" +
              '<ul>' + t.perks.slice(0, 4).map(function (p) { return "<li>" + K.ic("check") + K.esc(p) + "</li>"; }).join("") + "</ul>" +
              (t.perks.length > 4 ? '<button class="linkbtn sm" data-act="perks" data-tier="' + t.key + '">همه‌ی ' + K.n(t.perks.length) + " امتیاز</button>" : "") +
              '<button class="btn gold block" data-act="vip" data-tier="' + t.key + '">' +
              (t.discount ? '<s class="muted">' + K.n(t.list_price) + "</s> " : "") + toman(t.price_toman) + "</button></div></div>";
          }).join("") + "</div>";
      }
      function customHtml() {
        if (!d.custom) return "";
        var sellable = Object.keys(RES).filter(function (r) { return d.prices[r] > 0; });
        var listTotal = Math.round(sellable.reduce(function (s, r) { return s + custom[r] * d.prices[r]; }, 0));
        var pct = (d.sale && d.sale.percent) ? d.sale.percent : 0;
        var total = pct ? Math.max(0, Math.round(listTotal * (100 - pct) / 100 / 1000) * 1000) : listTotal;
        return '<div class="h2">' + K.ic("sliders") + "مقدار دلخواه</div>" + '<div class="panel pad st-custom">' +
          sellable.map(function (r) {
            var step = d.steps[r];
            return '<div class="st-row"><span class="' + RES[r][2] + ' b">' + K.ic(RES[r][1]) + RES[r][0] + "</span>" +
              '<button class="btn sm" data-act="dec" data-res="' + r + '">' + K.ic("minus") + "</button>" +
              '<span class="num st-val">' + K.n(custom[r]) + "</span>" +
              '<button class="btn sm" data-act="inc" data-res="' + r + '">' + K.ic("plus") + "</button></div>" +
              '<div class="sm muted st-rate">هر ' + K.n(step) + " " + RES[r][0] + " = " + K.n(Math.round(step * d.prices[r])) + " تومان</div>";
          }).join("") +
          '<div class="st-total"><span class="muted">مبلغ</span><b>' +
          (pct && listTotal > total ? '<s class="muted">' + K.n(listTotal) + "</s> " : "") +
          toman(total) + "</b></div>" +
          (d.min && total > 0 && total < d.min ? '<p class="note">حداقل خرید ' + K.n(d.min) + " تومانه؛ کمی بیشتر انتخاب کن.</p>" : "") +
          '<button class="btn primary block" data-act="custom"' + (total > 0 && total >= (d.min || 0) ? "" : " disabled") + ">ثبت سفارش</button></div>";
      }
      function saleBanner() {
        if (!d.sale || !d.sale.percent) return "";
        var reason = d.sale.reason ? ("به مناسبت «" + K.esc(d.sale.reason) + "» · ") : "";
        return '<div class="callout gold mb">' + K.ic("flame") + "<div><b>" + reason +
          K.n(d.sale.percent) + "٪ تخفیف همگانی روی همهٔ خریدهای تومانی</b>" +
          (d.sale.ends_in != null ? '<div class="sm muted">تا ' + K.n(Math.max(1, Math.round(d.sale.ends_in / 3600))) + " ساعت دیگه</div>" : "") +
          "</div></div>";
      }
      function draw() {
        var html = '<div class="banner mb st-ban" style="background-image:url(\'' + K.asset(d.banner) + '\')"><div><div class="ttl">خرید طلا و الماس</div>' +
          '<div class="sm" style="color:#d6f6ff">افر ویژه · پک · VIP · پرداخت کارت‌به‌کارت</div></div></div>' + saleBanner();
        if (d.pending) html += '<div class="callout gold mb">' + K.ic("hourglass") + '<div class="grow"><b>سفارش در انتظار رسید</b><div class="sm">' +
          K.esc(d.pending.summary) + " · " + toman(d.pending.price) + '</div></div><div class="col gap">' +
          '<button class="btn sm gold" data-act="pay">ارسال رسید</button>' +
          '<button class="btn sm ghost t-bad" data-act="cancelpay">لغو</button></div></div>';
        html += dealsHtml() + offersHtml();
        if (!d.ready) {
          html += K.state("cart", "خرید درون‌برنامه‌ای فعلاً بسته‌ست", d.link ? "از درگاه رسمی خرید کن." : "به‌زودی راه می‌افته.",
            d.link ? '<button class="btn primary" data-act="link">' + K.esc(d.link.title || "ورود به درگاه خرید") + "</button>" : "");
          html += vipHtml();
        } else {
          html += packsHtml() + presetsHtml() + vipHtml() + customHtml();
        }
        html += '<div class="panel pad st-how"><b>' + K.ic("info") + "خرید چطوریه؟</b><ol><li>بسته رو انتخاب کن و «خرید» بزن.</li>" +
          "<li>مبلغ رو به کارتی که نشون داده می‌شه واریز کن.</li><li>عکس رسید رو توی چت ربات بفرست؛ بعد از تأیید، موجودی اضافه می‌شه.</li></ol></div>";
        root.innerHTML = html;
      }
      function order(body, el, label) {
        if (busy) return; busy = true;
        K.confirm({ title: "ثبت سفارش", text: label, ok: "ثبت و نمایش کارت", cancel: "بی‌خیال" }).then(function (yes) {
          if (!yes) { busy = false; return; }
          K.api.post("shop/paid/order/", body, el).then(function (p) {
            busy = false; K.haptic("ok");
            function refresh() { K.api.get("shop/paid/").then(function (n) { d = n; if (ctx.alive()) draw(); }).catch(function () {}); }
            paySheet(p, refresh); refresh();
          }, function () { busy = false; });
        });
      }
      function cancelPay(el) {
        K.confirm({ title: "لغو سفارش؟", danger: true, ok: "آره، لغو کن", cancel: "نه",
          text: "سفارش حذف می‌شه و باید دوباره ثبتش کنی." }).then(function (yes) {
          if (!yes) return;
          K.api.post("shop/paid/cancel/", {}, el).then(function () {
            K.toast("سفارش لغو شد", "ok");
            K.api.get("shop/paid/").then(function (n) { d = n; if (ctx.alive()) draw(); });
          });
        });
      }
      return K.api.get("shop/paid/").then(function (data) {
        d = data; draw();
        K.on(root, "tab", function (el) { tab = el.dataset.res; draw(); });
        K.on(root, "deal", function (el) {
          var all = [].concat((d.deals && d.deals.weekly) || [], (d.deals && d.deals.daily) || []);
          var o = all.filter(function (x) { return String(x.id) === el.dataset.id; })[0];
          if (!o || o.sold_out) return;
          order({ kind: "item", id: o.id }, el, o.title + " · " + pn(o.price) + " تومان");
        });
        K.on(root, "offer", function (el) {
          var o = (d.offers || []).filter(function (x) { return String(x.id) === el.dataset.id; })[0];
          if (!o) return;
          if (o.price_toman) {
            order({ kind: "item", id: o.id }, el, o.title + " · " + pn(o.price_toman) + " تومان");
            return;
          }
          if (K.offers) K.offers.open(o);
        });
        K.on(root, "pack", function (el) {
          var p = d.packs.filter(function (x) { return String(x.id) === el.dataset.id; })[0]; if (!p) return;
          order({ kind: "pack", id: p.id }, el, p.title + " · " + pn(p.price) + " تومان");
        });
        K.on(root, "preset", function (el) {
          var x = d.presets.filter(function (q) { return q.res === el.dataset.res && String(q.amount) === el.dataset.amount; })[0]; if (!x) return;
          order({ kind: "preset", res: x.res, amount: x.amount }, el, pn(x.amount) + " " + RES[x.res][0] + " · " + pn(x.price) + " تومان");
        });
        K.on(root, "vip", function (el) {
          var t = d.vip.tiers.filter(function (q) { return q.key === el.dataset.tier; })[0]; if (!t) return;
          order({ kind: "vip", tier: t.key }, el, t.name + " (" + pn(t.days) + " روزه) · " + pn(t.price_toman) + " تومان");
        });
        K.on(root, "perks", function (el) {
          var t = d.vip.tiers.filter(function (q) { return q.key === el.dataset.tier; })[0]; if (!t) return;
          K.sheet('<div class="grab"></div><div class="pad"><div class="ttl" style="font-size:20px">' + K.esc(t.name) + '</div><div class="panel list mt">' +
            t.perks.map(function (p) { return '<div><span class="ic">' + K.ic("check") + '</span><span class="t">' + K.esc(p) + "</span></div>"; }).join("") + "</div></div>");
        });
        K.on(root, "inc", function (el) { var r = el.dataset.res; custom[r] = Math.min(d.max[r], custom[r] + d.steps[r]); draw(); });
        K.on(root, "dec", function (el) { var r = el.dataset.res; custom[r] = Math.max(0, custom[r] - d.steps[r]); draw(); });
        K.on(root, "custom", function (el) {
          var parts = Object.keys(RES).filter(function (r) { return custom[r] > 0; }).map(function (r) { return pn(custom[r]) + " " + RES[r][0]; });
          order({ kind: "custom", coins: custom.coins, dna: custom.dna, diamonds: custom.diamonds }, el, parts.join(" + "));
        });
        K.on(root, "pay", function (el) {
          K.api.get("shop/paid/card/").then(function (p) {
            paySheet(p, function () {
              K.api.get("shop/paid/").then(function (n) { d = n; if (ctx.alive()) draw(); });
            });
          }).catch(function () {});
        });
        K.on(root, "cancelpay", function (el) { cancelPay(el); });
        K.on(root, "link", function () { if (d.link) { try { K.tg.openLink(d.link.url); } catch (e) { window.open(d.link.url, "_blank"); } } });
      });
    }
  });
})(window.K);
