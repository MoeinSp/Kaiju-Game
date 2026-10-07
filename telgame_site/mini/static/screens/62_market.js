/* «بازار سیاه» — the nightly auctions (mk_market: tonight's lots + the coming nights,
   mk_lot: one lot with a custom bid). Back end: api/market.py. Bids are real: every bid goes
   through the server preview and a confirmation first. */
(function (K) {
  "use strict";

  K.addIcons({
    gavel: '<path d="m13.500 4.500 6 6M11 7l6 6M15.500 6.500l-9 9M4 20h9M12.500 3.500l-2.500 2.500M20.500 11.500 18 14"/>'
  });

  var TYPE_ICON = { diamonds: "gem", tickets: "ticket", biocrate_tickets: "ticket", creature: "claw", egg: "egg", dna: "dna", arena_chest: "chest",
                    equip_roll: "sword", equipment: "sword", speedup: "hourglass", speedup_card: "hourglass", subscription: "crown", vip: "crown", coins: "coin", gold: "coin", energy: "bolt" };
  var LEAD_KEY = "kaiju_mk_lead";

  K.hub("more", { id: "market", title: "بازار سیاه", sub: "مزایده‌ی هر شب تا 22:30", icon: "gavel", color: "var(--accent-2)", go: "mk_market", order: 12, hall: 4 });

  function money(cur, n) {
    var d = cur === "diamonds";
    return '<span class="b ' + (d ? "t-diamond" : "t-coin") + '">' + K.ic(d ? "gem" : "coin") + " " + K.n(n) + "</span>";
  }
  function curName(cur) { return cur === "diamonds" ? "الماس" : "طلا"; }
  function plain(n) { return Number(n || 0).toLocaleString("en-US"); }
  /* digits typed with a Persian/Arabic keyboard, separators, spaces → integer (0 if invalid) */
  function toInt(v) {
    var s = String(v == null ? "" : v).replace(/[۰-۹]/g, function (c) { return String(c.charCodeAt(0) - 1776); })
      .replace(/[٠-٩]/g, function (c) { return String(c.charCodeAt(0) - 1632); }).replace(/[,٬،_\s]/g, "");
    return /^\d{1,13}$/.test(s) ? parseInt(s, 10) : 0;
  }
  function kv(rows) {
    return '<div class="panel kv mk-kv">' + rows.filter(Boolean).map(function (r) { return "<div><span>" + r[0] + "</span><span>" + r[1] + "</span></div>"; }).join("") + "</div>";
  }

  /* «ازت جلو زدن»: the server keeps no bid history, so the lots this player was leading are
     remembered on the device (a convenience only — the bot's message is the real notice). */
  function leads() { try { return JSON.parse(localStorage.getItem(LEAD_KEY) || "{}") || {}; } catch (e) { return {}; } }
  function saveLeads(map) { try { localStorage.setItem(LEAD_KEY, JSON.stringify(map)); } catch (e) {} }
  function markStatus(lots) {
    var old = leads(), now = {}, me = K.me ? K.me.id : 0;
    lots.forEach(function (l) {
      var k = String(l.id), mineBefore = !!(old[k] && old[k].u === me);
      l.outbid = !l.mine && l.has_bid && mineBefore;
      if (l.mine) now[k] = { u: me, a: l.current_bid };
      else if (mineBefore) now[k] = old[k];
    });
    saveLeads(now);
  }
  function statusTag(l) {
    if (l.mine) return '<span class="tag" style="color:var(--good)">' + K.ic("check") + "تو جلویی</span>";
    if (l.outbid) return '<span class="tag" style="color:var(--bad)">' + K.ic("warn") + "ازت جلو زدن</span>";
    return "";
  }
  function vipTag(l) { return l.vip ? '<span class="tag" style="color:var(--legendary)">' + K.ic("crown") + "VIP</span>" : ""; }

  /* preview (server) → confirm → bid. `done` runs after a placed bid, `fail` after a refused one. */
  function bidFlow(lot, amount, el, done, fail) {
    if (!amount) { K.toast("یه مبلغ معتبر وارد کن.", "err"); return; }
    if (el) el.classList.add("busy");
    K.api.get("market/preview/?id=" + lot.id + "&amount=" + amount).then(function (p) {
      if (el) el.classList.remove("busy");
      return K.confirm({ title: "تأیید پیشنهاد", icon: "gavel", ok: "تأیید پیشنهاد", cancel: "انصراف",
        html: "<p>" + K.esc(p.title) + "</p>" + kv([
          ["پیشنهاد تو", money(p.currency, p.bid_amount)],
          ["بالاترین پیشنهاد قبلی", p.prev_name ? K.esc(p.prev_name) + " · " + money(p.currency, p.prev_amount) : "هنوز پیشنهادی نیست"],
          ["از حسابت کم می‌شه", money(p.currency, p.cost)],
          ["مهلت", K.esc(p.deadline)],
          ["زمان باقی‌مونده", K.esc(K.dur(p.left))]]) +
          '<p class="xs muted" style="margin:8px 0 0">' + (p.is_own_increase ? "فقط اختلاف با پیشنهاد قبلی خودت کم می‌شه. " : "") + "اگه کسی بالاتر بزنه، کل مبلغ همون لحظه بهت برمی‌گرده.</p>" })
        .then(function (yes) {
          if (!yes) return;
          return K.api.post("market/bid/", { id: lot.id, amount: p.bid_amount }, el).then(function (r) {
            K.haptic("ok"); K.toast("پیشنهاد " + plain(r.bid_amount) + " " + curName(r.currency) + " ثبت شد.", "ok");
            if (done) done(r);
          }, function () { if (fail) fail(); });
        });
    }).catch(function (err) {
      if (el) el.classList.remove("busy");
      K.haptic("err"); K.toast(err.message, "err");
      if (fail) fail();
    });
  }

  function lotCard(l) {
    return '<div class="panel mk-lot' + (l.mine ? " mine" : l.outbid ? " out" : "") + '"><button class="mk-head" data-act="lot" data-id="' + l.id + '"><span class="ico-box lg t-' + (l.currency === "diamonds" ? "diamond" : "coin") + '">' + K.ic(TYPE_ICON[l.type] || "gift") + "</span>" +
      '<span class="grow"><span class="b mk-ttl">' + K.esc(l.title) + '</span><span class="mk-tags">' + vipTag(l) + statusTag(l) + "</span></span>" +
      '<span class="faint">' + K.ic("chevron") + "</span></button>" +
      '<div class="mk-bid"><div><small>' + (l.has_bid ? "بالاترین پیشنهاد" : "قیمت شروع") + "</small>" + money(l.currency, l.current_bid) + "</div>" +
      '<div><small>پیشنهاددهنده</small><span class="cut">' + (l.has_bid ? K.ic("user") + " " + K.esc(l.bidder || "—") : '<span class="muted">بدون پیشنهاد</span>') + "</span></div></div>" +
      (l.vip_locked ? '<div class="mk-acts"><button class="btn block" data-act="vip">' + K.ic("lock") + "فقط برای اشتراک VIP</button></div>"
        : '<div class="mk-acts btns"><button class="btn primary" data-act="next" data-id="' + l.id + '">' + K.ic("gavel") + '<span class="num">' + plain(l.next_bid) + "</span></button>" +
          '<button class="btn" data-act="lot" data-id="' + l.id + '">پیشنهاد دلخواه</button></div>') + "</div>";
  }

  K.screen("mk_market", {
    title: "بازار سیاه", tab: "more",
    render: function (root, params, ctx) {
      ctx.actions('<button class="iconbtn" id="mk-refresh" aria-label="بروزرسانی">' + K.ic("refresh") + "</button>");
      var rb = document.getElementById("mk-refresh"); if (rb) rb.onclick = function () { K.haptic(); ctx.reload(); };
      return K.api.get("market/").then(function (d) {
        markStatus(d.lots);
        var lead = d.lots.filter(function (l) { return l.mine; })[0];
        var html = '<div class="banner mb"' + (d.img ? ' style="background-image:url(\'' + d.img + '\')"' : "") + '><div class="grow"><div class="ttl">' + (d.night ? "بازار سیاه — " + K.esc(d.night.title) : "بازار سیاه") + "</div>" +
          (d.ends_at ? '<div class="sm mk-end">تا پایان مزایده <span class="timer" data-until="' + d.ends_at + '" data-done="تموم شد"></span> <span>(' + K.esc(d.deadline) + ")</span></div>" : "") + "</div></div>";
        if (lead) html += '<div class="callout good mb">' + K.ic("check") + "<div>الان روی «" + K.esc(lead.title) + "» جلویی. تا وقتی جلویی، روی مزایده‌ی دیگه‌ای نمی‌تونی پیشنهاد بدی.</div></div>";
        else if (d.lots.some(function (l) { return l.outbid; })) html += '<div class="callout bad mb">' + K.ic("warn") + "<div>روی یکی از مزایده‌ها ازت جلو زدن و مبلغت برگشته. اگه می‌خوایش، دوباره پیشنهاد بده.</div></div>";
        html += d.lots.length ? d.lots.map(lotCard).join("") : K.state("gavel", "مزایده‌ی فعالی نیست", "الان چیزی برای مزایده توی بازار نیست. بعداً سر بزن.");
        html += '<div class="callout mt">' + K.ic("info") + "<div>بالاترین پیشنهاد تا پایان مهلت برنده‌ست و جایزه خودکار به حسابت می‌آد. هر بازیکن هم‌زمان فقط روی یک مزایده می‌تونه جلو باشه. مزایده‌های VIP (الماسی و هیولا) فقط برای دارنده‌های اشتراکه.</div></div>";
        if (d.upcoming.length) {
          html += '<div class="h2">' + K.ic("calendar") + 'شب‌های بعد</div><div class="panel mk-prog">' + d.upcoming.map(function (n) {
            return '<details><summary><span class="mk-day">' + K.esc(n.weekday) + '</span><span class="b grow">' + K.esc(n.title) + '</span><span class="faint">' + K.ic("chevron") + '</span></summary><div class="mk-plots">' + n.lots.map(function (l) {
              return '<div><span class="faint">' + K.ic(TYPE_ICON[l.type] || "gift") + '</span><span class="grow">' + K.esc(l.title) + (l.vip ? ' <span class="xs" style="color:var(--legendary)">VIP</span>' : "") + '</span><span class="sm">از ' + money(l.currency, l.min_bid) + "</span></div>";
            }).join("") + "</div></details>";
          }).join("") + "</div>";
        }
        root.innerHTML = html;
        K.timers(root, function () { K.after(1500, function () { ctx.reload(); }); });
        function find(el) { return d.lots.filter(function (l) { return l.id === +el.dataset.id; })[0]; }
        K.on(root, "lot", function (el) { var l = find(el); if (l) K.go("mk_lot", { id: l.id }); });
        K.on(root, "vip", function () { if (K.hasScreen("sh_vip")) K.go("sh_vip"); });
        K.on(root, "next", function (el) {
          var l = find(el); if (!l) return;
          bidFlow(l, l.next_bid, el, function () { ctx.reload(); }, function () { ctx.reload(); });
        });
      });
    }
  });

  K.screen("mk_lot", {
    title: "مزایده", tab: "more",
    render: function (root, params, ctx) {
      return K.api.get("market/").then(function (d) {
        markStatus(d.lots);
        var l = d.lots.filter(function (x) { return x.id === +params.id; })[0];
        if (!l) {
          root.innerHTML = K.state("gavel", "این مزایده تموم شده", "برنده جایزه‌ش رو گرفته. مزایده‌های امشب رو ببین.", '<button class="btn primary" data-act="back" style="margin-top:16px">بازگشت به بازار</button>');
          K.on(root, "back", function () { ctx.back(); });
          return;
        }
        var html = '<div class="panel pad mk-hero' + (l.mine ? " mine" : l.outbid ? " out" : "") + '"><div class="mk-big t-' + (l.currency === "diamonds" ? "diamond" : "coin") + '">' + K.ic(TYPE_ICON[l.type] || "gift") + "</div>" +
          '<div class="b mk-name">' + K.esc(l.title) + '</div><div class="mk-tags center">' + vipTag(l) + statusTag(l) +
          '<span class="tag plain"><span class="timer" data-until="' + l.ends_at + '" data-done="تموم شد"></span></span></div></div>' +
          kv([[l.has_bid ? "بالاترین پیشنهاد" : "قیمت شروع", money(l.currency, l.current_bid)],
              ["پیشنهاددهنده", l.has_bid ? K.esc(l.bidder || "—") + (l.mine ? " (تو)" : "") : "هنوز کسی پیشنهاد نداده"],
              ["حداقل افزایش", money(l.currency, l.step)], ["حداقل پیشنهاد بعدی", money(l.currency, l.next_bid)], ["مهلت", K.esc(l.deadline)]]);
        if (l.vip_locked) {
          html += '<div class="callout warn mt">' + K.ic("crown") + "<div>این مزایده VIP هست و فقط دارنده‌های «اشتراک نقره‌ای» یا «اشتراک طلایی» می‌تونن توش شرکت کنن.</div></div>" +
                  '<button class="btn block mt" data-act="vip">' + K.ic("crown") + "دیدن اشتراک‌ها</button>";
        } else {
          html += (l.mine ? '<div class="callout good mt">' + K.ic("check") + "<div>الان تو جلویی. اگه پیشنهادت رو بالاتر ببری فقط اختلافش ازت کم می‌شه.</div></div>"
                 : l.outbid ? '<div class="callout bad mt">' + K.ic("warn") + "<div>ازت جلو زدن و مبلغ قبلیت برگشته.</div></div>" : "") +
            '<div class="h2">' + K.ic("gavel") + 'پیشنهاد تو</div><div class="panel pad"><div class="sm muted">مبلغ به ' + curName(l.currency) + " (حداقل " + K.n(l.next_bid) + ")</div>" +
            '<div class="mk-amt"><button class="iconbtn" data-step="-1" aria-label="کمتر">' + K.ic("minus") + '</button><input class="input num" id="mk-in" inputmode="numeric" autocomplete="off" value="' + l.next_bid + '">' +
            '<button class="iconbtn" data-step="1" aria-label="بیشتر">' + K.ic("plus") + "</button></div>" +
            '<div class="btns"><button class="btn" data-act="min">حداقل</button><button class="btn primary" data-act="bid">' + K.ic("gavel") + "بررسی و ثبت</button></div>" +
            '<p class="xs muted" style="margin:10px 0 0">قبل از ثبت، مبلغ دقیقی که از حسابت کم می‌شه رو می‌بینی و باید تأییدش کنی.</p></div>';
        }
        root.innerHTML = html;
        K.timers(root, function () { K.after(1500, function () { ctx.reload(); }); });
        var input = root.querySelector("#mk-in");
        function val() { return input ? toInt(input.value) : 0; }
        function again() { ctx.reload(); }
        root.addEventListener("click", function (ev) {
          var s = ev.target.closest("[data-step]"); if (!s || !input) return;
          K.haptic(); input.value = Math.max(l.next_bid, (val() || l.next_bid) + l.step * +s.dataset.step);
        });
        K.on(root, "min", function () { K.haptic(); input.value = l.next_bid; });
        K.on(root, "vip", function () { if (K.hasScreen("sh_vip")) K.go("sh_vip"); });
        K.on(root, "bid", function (el) {
          var amount = val();
          if (amount < l.next_bid) { K.haptic("err"); K.toast("حداقل پیشنهاد بعدی " + plain(l.next_bid) + " " + curName(l.currency) + " هست.", "err"); return; }
          bidFlow(l, amount, el, again, again);
        });
      });
    }
  });
})(window.K);
