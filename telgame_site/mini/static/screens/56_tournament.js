/* «جام آخر هفته» — the weekly knockout cup (screen tr_home).
   The app only shows the state and edits the player's own entry; the draw and the three
   rounds are played by the game on its own schedule. No outcome is ever predicted here. */
(function (K) {
  "use strict";

  K.addIcons({ bracket: '<path d="M4 5h5v5H4M4 14h5v5H4M9 7.500h4v9H9M13 12h7"/>' });

  K.hub("battle", { id: "tournament", title: "جام آخر هفته", sub: "جام حذفی جمعه‌شب‌ها", icon: "trophy", color: "var(--gold)", go: "tr_home", order: 9 });

  var PLACE_COLOR = { 1: "var(--gold)", 2: "#c8d3e6", 3: "#d58a4f", 5: "var(--muted)" };

  function pic(c) {
    var rc = c && c.rarity ? ' style="--rc:var(--' + c.rarity + ')"' : "";
    return c && c.img ? '<img src="' + c.img + '" alt=""' + rc + ">" : '<div class="tr-nopic"' + rc + ">" + K.ic("claw") + "</div>";
  }
  function side(c, who, lab) {
    return '<div class="side">' + pic(c) + '<div class="tr-who">' + who + "</div>" +
      (lab ? '<div class="xs muted cut">' + K.esc(lab) + "</div>" : "") +
      '<div class="b cut">' + K.esc(c.name) + '</div><div class="tr-pw">' + K.ic("power") + K.n(c.power) + "</div>" +
      (c.star ? "<div>" + K.stars(c.star) + "</div>" : "") + (c.element ? '<div class="tr-el">' + K.elTag(c.element) + "</div>" : "") + "</div>";
  }
  function myRow(d) {
    return d.my ? '<div class="panel pad mt"><div class="xs muted" style="margin-bottom:8px">هیولایی که می‌فرستی</div>' + K.fighter(d.my, '<div style="margin-top:4px">' + K.elTag(d.my.element) + "</div>") + "</div>" : "";
  }
  function changeBtn(cls) { return '<button class="btn ' + (cls || "") + ' block mt" data-act="pick">' + K.ic("swap") + "عوض‌کردن هیولا</button>"; }
  function countdown(left, done) { return '<span class="timer" data-left="' + left + '" data-fmt="long" data-done="' + (done || "وقتشه") + '"></span>'; }
  function intro(d) {
    var hours = d.schedule.rounds.map(function (r) { return r.hour; });
    return '<div class="callout mt">' + K.ic("info") + "<div>هر هفته یه جام حذفی: توی گروه‌های " + K.n(d.schedule.group_size) + " نفره‌ی هم‌سطح قرعه می‌خوری و جمعه‌شب ساعت " +
      hours.map(function (h) { return K.n(h); }).join("، ") + " سه دور بازی می‌شه. قبل از هر دور می‌تونی هیولات رو عوض کنی.</div></div>";
  }

  function body(d) {
    var h = "";
    if (d.status === "running" && d.entered) {
      h += '<div class="tiles mt"><div class="panel info" style="color:var(--accent)"><span class="ic">' + K.ic("users") + "</span><span><small>گروه تو</small><b>" + K.n(d.group) + "</b></span></div>" +
        '<div class="panel info" style="color:' + (d.alive ? "var(--good)" : "var(--bad)") + '"><span class="ic">' + K.ic(d.alive ? "shieldcheck" : "skull") + "</span><span><small>وضعیت</small><b>" + (d.alive ? "توی جامی" : "حذف شدی") + "</b></span></div></div>";
      if (d.alive && d.match) {
        var m = d.match;
        h += '<div class="h2">' + K.ic("swords") + K.esc(m.name) + '</div><div class="panel tr-match"><div class="tr-when">' + K.ic("clock") + " شروع دور: " + countdown(m["in"], "داره بازی می‌شه") + "</div>";
        if (!m.foe) h += '<div class="pad center muted" style="padding:6px 16px 16px">حریف نداری؛ این دور رو بدون بازی بالا می‌ری.</div></div>' + myRow(d);
        else {
          var adv = d.my ? K.advantage(d.my.element, m.foe.creature.element) : { html: "" };
          h += '<div class="versus">' + (d.my ? side(d.my, "تو") : '<div class="side muted">هیولایی نداری</div>') + '<div class="vs">VS</div>' + side(m.foe.creature, "حریف", m.foe.name) + "</div>" +
            '<div class="center" style="padding:4px 12px 12px">' + adv.html + "</div></div>" +
            '<div class="callout mt">' + K.ic("info") + "<div>این هیولای فعلیِ حریفه؛ اون هم می‌تونه تا شروع دور عوضش کنه. برتری عنصری یعنی +۲۰٪ قدرت.</div></div>";
        }
        h += changeBtn("primary");
      } else if (d.alive) {
        h += myRow(d) + '<div class="callout mt">' + K.ic("hourglass") + "<div>منتظر دور بعدی باش.</div></div>" + changeBtn();
      } else {
        h += placeCard(d, "از جام کنار رفتی. جایزه‌ات آخر شب می‌رسه.");
      }
    } else if (d.status === "finished" && d.entered) {
      h += placeCard(d, "جام این هفته تموم شد." + (d.group ? " گروه " + d.group : "")) +
        (d.champion ? '<div class="panel list mt"><div style="color:var(--gold)"><span class="ic">' + K.ic("crown") + '</span><span class="t">قهرمان گروه<small>' + K.esc(d.champion) + "</small></span></div></div>" : "") +
        '<div class="callout mt">' + K.ic("clock") + "<div>ثبت‌نام جام بعدی: " + countdown(d.next_reg_in, "باز شد") + " دیگه</div></div>";
    } else if (d.registration_open) {
      h += intro(d) + '<div class="tiles tr-tiles mt"><div class="panel info" style="color:var(--warn)"><span class="ic">' + K.ic("clock") + "</span><span><small>تا قرعه‌کشی</small><b>" + countdown(d.draw_in, "قرعه‌کشی") + "</b></span></div>" +
        '<div class="panel info" style="color:var(--accent)"><span class="ic">' + K.ic("users") + "</span><span><small>ثبت‌نام‌شده‌ها</small><b>" + K.n(d.players) + " نفر</b></span></div></div>";
      if (d.entered) h += '<div class="callout good mt">' + K.ic("check") + "<div>ثبت‌نام کردی. بعد از قرعه‌کشی گروه و حریفت همین‌جا میاد.</div></div>" + myRow(d) + changeBtn("primary") +
        '<button class="btn ghost block mt" data-act="leave" style="color:var(--bad)">' + K.ic("close") + "انصراف از جام</button>";
      else h += '<button class="btn gold lg block mt" data-act="join">' + K.ic("trophy") + "ثبت‌نام (رایگان)</button>" +
        '<p class="note" style="margin-top:8px">با هیولای فعالت ثبت‌نام می‌شی؛ بعدش می‌تونی عوضش کنی.</p>';
    } else {
      h += intro(d) + '<div class="panel pad mt center"><div class="muted sm">ثبت‌نام جام بعدی</div><div class="tr-big">' + countdown(d.next_reg_in, "باز شد") + '</div><div class="xs muted">پنجشنبه تا جمعه ساعت ' + K.n(19) + ":" + K.n(30) + "</div></div>";
    }
    if (d.history.length) h += '<div class="h2">' + K.ic("bracket") + 'بازی‌های تو</div><div class="panel list">' + d.history.map(function (x) {
      return '<div style="color:' + (x.won ? "var(--good)" : "var(--bad)") + '"><span class="ic">' + K.ic(x.won ? "check" : "close") + '</span><span class="t">' + K.esc(x.name) + "<small>" +
        (x.bye ? "بدون حریف بالا رفتی" : K.esc(x.foe) + " · تو " + K.n(x.my_power) + (x.my_element ? " " + K.esc(K.elLabel(x.my_element)) : "") + " / حریف " + K.n(x.foe_power) + (x.foe_element ? " " + K.esc(K.elLabel(x.foe_element)) : "")) +
        '</small></span><span class="v">' + (x.won ? "برد" : "باخت") + "</span></div>";
    }).join("") + "</div>";
    h += '<div class="h2">' + K.ic("calendar") + 'برنامه‌ی جمعه‌شب</div><div class="panel tr-sched">' + d.schedule.rounds.map(function (r) {
      var on = d.status === "running" && d.round === r.round;
      return '<div class="' + (on ? "on" : "") + '"><b class="num">' + r.hour + ':00</b><span>' + K.esc(r.name) + "</span></div>";
    }).join("") + "</div>" +
      '<div class="h2">' + K.ic("gift") + 'جایزه‌ها</div><div class="panel list">' + d.prizes.map(function (p) {
        var mine = d.entered && d.place_title && (d.place || 5) === p.place;
        return '<div class="' + (mine ? "tr-mine" : "") + '" style="color:' + (PLACE_COLOR[p.place] || "var(--muted)") + '">' + (p.img ? '<img class="th" src="' + p.img + '" alt="">' : '<span class="ic">' + K.ic("chest") + "</span>") +
          '<span class="t">' + K.esc(p.title) + "<small>" + K.esc(p.chest) + (mine ? " · جایگاه تو" : "") + '</small></span><span class="v">' + (p.diamonds ? K.amounts({ diamonds: p.diamonds }) : "") + "</span></div>";
      }).join("") + '</div><p class="note">محتوای جعبه‌ی جایزه بر اساس لیگت حساب می‌شه و مستقیم به حسابت میاد.</p>';
    return h;
  }
  function placeCard(d, text) {
    var place = d.place || 5;
    return '<div class="panel tr-place mt" style="color:' + (PLACE_COLOR[place] || "var(--muted)") + '"><div class="tr-medal">' + K.ic(place === 1 ? "trophy" : "medal") + '</div><div class="muted sm">' + K.esc(text) + "</div><h3>" + K.esc(d.place_title || "") + "</h3></div>";
  }

  K.screen("tr_home", {
    title: "جام آخر هفته", tab: "battle",
    render: function (root, params, ctx) {
      var data = null;
      function draw(d) {
        data = d;
        var state = d.status === "running" ? "در حال برگزاری" : d.registration_open ? "ثبت‌نام بازه" : d.status === "finished" ? "این هفته تموم شد" : "ثبت‌نام بسته‌ست";
        root.innerHTML = '<div class="banner"' + (d.img ? ' style="background-image:url(\'' + d.img + "')\"" : "") + '><div class="grow"><div class="ttl">جام آخر هفته</div><div class="sm" style="color:#c5cee2">' + state + "</div></div></div>" + body(d);
        // a countdown ran out: the game plays the draw / round within a few minutes, so look again shortly
        K.timers(root, function (el) { if (root.contains(el)) K.after(20000, function () { load().catch(function () {}); }); });
      }
      function load() { return K.api.get("tournament/").then(function (d) { if (ctx.alive()) draw(d); }); }
      function post(path, bodyData, btn, text) {
        return K.api.post(path, bodyData, btn).then(function (d) { K.haptic("ok"); if (text) K.toast(text); if (ctx.alive()) draw(d); });
      }
      ctx.actions('<button class="iconbtn" data-act="refresh" aria-label="بروزرسانی">' + K.ic("refresh") + "</button>");
      var rb = document.querySelector('#topact [data-act="refresh"]');
      if (rb) rb.onclick = function () { K.haptic(); ctx.reload(); };
      K.on(root, "join", function (el) { post("tournament/register/", {}, el, "توی جام این هفته ثبت‌نام شدی.").catch(function () {}); });
      K.on(root, "leave", function (el) {
        K.confirm({ title: "انصراف از جام؟", text: "تا قبل از قرعه‌کشی می‌تونی دوباره ثبت‌نام کنی.", ok: "انصراف", cancel: "می‌مونم", danger: true }).then(function (yes) {
          if (yes) post("tournament/unregister/", {}, el, "از جام این هفته انصراف دادی.").catch(function () {});
        });
      });
      K.on(root, "pick", function (el) {
        el.classList.add("busy");
        K.api.get("tournament/choices/").then(function (c) {
          var foeEl = data && data.match && data.match.foe ? data.match.foe.creature.element : null;
          var box = K.sheet('<div class="grab"></div><div class="pad"><div class="ttl" style="font-size:18px">کدوم هیولا برای دور بعد بازی کنه؟</div><p class="lead" style="margin:4px 0 12px">قوی‌ترین‌ها اول. برتری عنصری روی حریف یعنی +۲۰٪ قدرت.</p><div class="grid">' +
            c.creatures.map(function (x) {
              var adv = foeEl ? K.advantage(x.element, foeEl) : null;
              return K.creatureTile(x, { attrs: 'data-pick="' + x.id + '"', sel: x.id === c.current, flag: x.id === c.current ? "انتخاب‌شده" : adv && adv.mine ? "برتری با تو" : "" });
            }).join("") + "</div></div>");
          box.querySelectorAll("[data-pick]").forEach(function (b) {
            b.onclick = function () {
              if (+b.dataset.pick === c.current) { K.closeSheet(); return; }
              b.classList.add("dim");
              K.api.post("tournament/creature/", { creature_id: +b.dataset.pick }).then(function (d) {
                K.closeSheet(); K.haptic("ok"); K.toast("«" + (d.my ? d.my.name : "هیولا") + "» برای دور بعد انتخاب شد.");
                if (ctx.alive()) draw(d);
              }).catch(function () { b.classList.remove("dim"); });
            };
          });
        }).catch(function (e) { K.toast(e.message, "err"); }).finally(function () { el.classList.remove("busy"); });
      });
      return K.api.get("tournament/").then(draw);
    }
  });
})(window.K);
