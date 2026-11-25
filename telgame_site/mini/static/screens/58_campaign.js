/* «دانجن» — the 50-stage PvE ladder (game/campaign.py). Screen: cp_home.
   API: campaign/…  Attack team is the same Team the dungeon uses. */
(function (K) {
  "use strict";

  var HALL = 4;   // SECTION_HALL_REQ["campaign"] — the server enforces it

  function rewardHtml(r) {
    if (!r || !Object.keys(r).length) return '<span class="faint sm">بدون پاداش</span>';
    var parts = K.amounts(r);
    if (r.speedup) parts += (parts ? ' <span class="faint">·</span> ' : "") +
      '<span class="t-accent b">' + K.ic("clock") + " " + K.n(r.speedup) + " دقیقه کارت سرعت</span>";
    return parts;
  }

  function teamSheet(onDone) {
    K.api.get("campaign/team/").then(function (d) {
      var byId = {};
      d.creatures.forEach(function (c) { byId[c.id] = c; });
      function body() {
        var n = d.members.length, slots = "";
        for (var i = 0; i < d.max; i++) {
          var c = byId[d.members[i]];
          slots += c
            ? '<button class="dg-tp-slot" data-pick="' + c.id + '"><img src="' + c.img + '" alt=""><b class="cut">' + K.esc(c.name) + "</b>" +
              '<span class="num t-accent">' + K.ic("power") + K.n(c.power) + '</span><i class="dg-tp-x">' + K.ic("close") + "</i></button>"
            : '<div class="dg-tp-slot empty">' + K.ic("plus") + "<span>جای خالی</span></div>";
        }
        var rest = d.creatures.filter(function (c) { return !c.in_team; });
        return '<div class="grab"></div><div class="pad">' +
          '<div class="ttl dg-tp-ttl">تیم حمله <span class="num faint">' + K.n(n) + "/" + K.n(d.max) + "</span></div>" +
          '<p class="lead dg-tp-lead">تا سه هیولا بچین؛ قدرت تیم با قدرت دشمن مرحله مقایسه می‌شه.</p>' +
          '<div class="dg-tp-slots">' + slots + "</div>" +
          '<div class="sm muted dg-tp-h">' + K.ic("users") + " هیولاهات</div>" +
          '<div class="panel list hu-team dg-tp-list' + (n >= d.max ? " full" : "") + '">' + rest.map(function (c) {
            return '<button data-pick="' + c.id + '"><img class="th" src="' + c.img + '" alt="">' +
              '<span class="t"><span class="b">' + K.esc(c.name) + '</span> <span class="e-' + c.element + ' sm">' +
              K.ic(K.EL_ICON[c.element] || "atom") + K.esc(K.elLabel(c.element)) + "</span></span>" +
              '<span class="v t-accent">' + K.ic("power") + " " + K.n(c.power) + "</span></button>";
          }).join("") + "</div></div>" +
          '<div class="foot dg-tp-foot"><button class="btn primary block" data-done>' + K.ic("check") + "تمومه</button></div>";
      }
      var box = K.sheet(body()), busy = false;
      box.addEventListener("click", function (ev) {
        var pick = ev.target.closest("[data-pick]");
        if (pick && !busy) {
          busy = true;
          K.api.post("campaign/team/toggle/", { creature_id: +pick.dataset.pick }, pick).then(function (nd) {
            d = nd; d.creatures.forEach(function (c) { byId[c.id] = c; });
            box.innerHTML = body(); K.haptic("ok");
          }).catch(function () {}).finally(function () { busy = false; });
          return;
        }
        if (ev.target.closest("[data-done]")) { K.closeSheet(); if (onDone) onDone(); }
      });
    }).catch(function (e) { K.toast(e.message, "err"); });
  }

  K.screen("cp_home", {
    title: "دانجن", tab: "battle",
    render: function (root, params, ctx) {
      var lock = false;
      function paint(d, note) {
        if (!ctx.alive()) return;
        var art = d.art || "";
        if (!d.next_stage) {
          root.innerHTML =
            '<div class="banner cp-banner"' + (art ? ' style="background-image:url(\'' + K.esc(art) + '\')"' : "") +
            '><div class="grow"><div class="ttl">دانجن</div><div class="sm" style="color:#c5cee2">همه‌ی ' +
            K.n(d.max_stage) + " مرحله فتح شد</div></div></div>" +
            '<div class="callout good mt">' + K.ic("trophy") + "<div><b>کل دانجن رو فتح کردی!</b><br>«سیاه‌چال» برات باز شده — هر روز با کلید وارد شو.</div></div>" +
            '<button class="btn danger lg block mt" data-act="dungeon">' + K.ic("skull") + "سیاه‌چال</button>";
          K.on(root, "dungeon", function () { K.go("dg_home"); });
          return;
        }
        var boss = d.next_is_boss;
        var ratio = d.enemy_power ? Math.min(1.4, d.team_power / Math.max(1, d.enemy_power)) : 0;
        var canWin = d.has_team && d.team_power >= d.enemy_power;
        var html = '<div class="banner cp-banner' + (boss ? " boss" : "") + '"' +
          (art ? ' style="background-image:url(\'' + K.esc(art) + '\')"' : "") +
          '><div class="grow"><div class="sm" style="color:#c5cee2">دانجن · مرحله‌ی ' +
          K.n(d.next_stage) + " از " + K.n(d.max_stage) + (boss ? " · باس" : "") +
          '</div><div class="ttl">' + (boss ? "باس مرحله‌ی " : "مرحله‌ی ") + K.n(d.next_stage) +
          "</div></div>" +
          '<div class="pow"><b>' + K.n(d.cleared) + "</b><small>فتح‌شده</small></div></div>";
        if (note) html += '<div class="callout mt">' + note + "</div>";
        html += '<div class="tiles mt">' +
          '<div class="panel info t-accent"><span class="ic">' + K.ic("power") + "</span><div><small>قدرت تیم تو</small><b>" +
          K.n(d.team_power) + "</b></div></div>" +
          '<div class="panel info ' + (canWin ? "t-good" : "t-bad") + '"><span class="ic">' + K.ic("skull") +
          "</span><div><small>قدرت دشمن</small><b>" + K.n(d.enemy_power) + "</b></div></div></div>" +
          '<div class="mt">' + K.bar(Math.min(1, ratio / 1.2), canWin ? "good" : "bad", "thick") + "</div>" +
          '<p class="sm muted" style="margin:6px 2px 0">' +
          (d.has_team
            ? (canWin ? "قدرت تیمت کافیه — این مرحله رو می‌بری." : "قدرت تیمت کمتر از دشمنه؛ قوی‌ترش کن یا ترکیب رو عوض کن.")
            : "اول تیم حمله‌ت رو بچین.") + "</p>";
        if (d.enemies && d.enemies.length) {
          html += '<div class="h2">' + K.ic(boss ? "skull" : "swords") + (boss ? "تیم باس" : "دشمن‌های این مرحله") + "</div>" +
            '<div class="panel list cp-enemies">' + d.enemies.map(function (e) {
              return '<div><img class="th" src="' + K.esc(e.img || "") + '" alt=""><span class="t"><span class="b cut">' +
                K.esc(e.name) + "</span><small>" + K.elTag(e.element) + " · " + K.rarTag(e.rarity) +
                '</small></span><span class="v t-accent">' + K.ic("power") + " " + K.n(e.power) + "</span></div>";
            }).join("") + "</div>";
        }
        html += '<div class="panel pad mt"><div class="sm muted mb">' + K.ic("gift") + " پاداش فتح این مرحله</div>" +
          '<div class="b">' + rewardHtml(d.reward) + "</div></div>" +
          '<div class="btns mt">' +
          (d.has_team
            ? '<button class="btn danger lg" data-act="fight">' + K.ic("swords") + 'شروع نبرد<span class="cost">' +
              K.ic("bolt", "f") + K.n(d.energy_cost) + "</span></button>"
            : "") +
          '<button class="btn" data-act="team">' + K.ic("users") + (d.has_team ? "مدیریت تیم" : "چیدن تیم") + "</button></div>";
        if ((K.res ? K.res.energy : d.energy) < d.energy_cost) {
          html += '<div class="callout warn mt">' + K.ic("bolt") + "<div>هر نبرد دانجن " + K.n(d.energy_cost) +
            " انرژی می‌خواد.</div>" +
            (K.hasScreen("sh_energy") ? '<button class="btn sm" data-act="energy">شارژ</button>' : "") + "</div>";
        }
        html += '<p class="note">سیاه‌چال بعد از مرحله‌ی ' + K.n(d.unlock_dungeon_at) + " باز می‌شه.</p>";
        root.innerHTML = html;
        window.scrollTo(0, 0);
        K.on(root, "team", function () { teamSheet(function () { if (ctx.alive()) ctx.reload(); }); });
        K.on(root, "energy", function () { K.go("sh_energy"); });
        K.on(root, "fight", function (el) {
          if (lock) return; lock = true;
          K.api.post("campaign/fight/", {}, el).then(function (r) {
            lock = false;
            var res = r.result;
            if (res.won) {
              K.result({
                won: true,
                title: "مرحله‌ی " + K.n(res.stage) + " فتح شد!" + (res.is_boss ? " 👑" : ""),
                sub: res.cleared_all ? "کل دانجن رو فتح کردی — سیاه‌چال باز شد!" :
                  (res.survivors ? res.survivors + " هیولا زنده موند." : ""),
                coins: res.reward.coins, dna: res.reward.dna, diamonds: res.reward.diamonds,
                button: res.cleared_all ? "سیاه‌چال" : "مرحله‌ی بعد",
              }).then(function () {
                if (res.cleared_all) K.go("dg_home");
                else if (ctx.alive()) paint(r);
              });
            } else {
              var note = '<div class="callout bad">' + K.ic("skull") + "<div><b>شکست در مرحله‌ی " +
                K.n(res.stage) + "</b><br>قدرت تیمت " + K.n(r.team_power) + " بود و قدرت دشمن " +
                K.n(r.enemy_power) + ". تیم رو قوی‌تر کن و دوباره بیا.</div></div>";
              paint(r, note);
              K.result({ won: false, title: "شکست", sub: "قدرت دشمن بیشتر بود." });
            }
          }).catch(function () { lock = false; });
        });
      }
      return K.api.get("campaign/").then(function (d) { paint(d); });
    }
  });

  K.hub("battle", {
    id: "campaign", title: "دانجن", sub: "۵۰ مرحله · پاداش فتح",
    icon: "map", color: "var(--accent)", go: "cp_home", order: 5, hall: HALL,
  });
})(window.K);
