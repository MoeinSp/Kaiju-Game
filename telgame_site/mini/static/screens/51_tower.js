/* Mugen Tower («برج موگن»): the floor, its guardian, the prize, the fight and the top climbers.
   Screen: tw_tower. API: tower/…  (uses K.hu from 50_hunt.js for the shared versus pieces) */
(function (K) {
  "use strict";
  var HU = K.hu;
  var HALL = 5;   // SECTION_HALL_REQ["mugen_tower"] — the server enforces it (with the story exemption)

  function prize(rew) {
    var out = K.amounts(rew);
    if (rew.tickets) out += (out ? ' <span class="faint">·</span> ' : "") + '<span class="t-gold b">' + K.ic("ticket") + " " + K.n(rew.tickets) + " بلیط</span>";
    return out;
  }

  K.screen("tw_tower", {
    title: "برج موگن", tab: "battle",
    render: function (root, params, ctx) {
      var S, note = "", lock = false;

      function view() {
        var g = S.guardian, me = S.me, adv = K.advantage(me.element, g.element), m = S.milestone;
        var html = '<div class="banner tw-banner" style="background-image:url(\'' + K.esc(S.art || "") + '\')"><div class="grow"><div class="sm" style="color:#c5cee2">برج بی‌پایان · هر طبقه یه نگهبان</div>' +
          '<div class="ttl">طبقه‌ی ' + K.n(S.floor) + "</div></div>" +
          '<div class="pow"><b>' + K.n(S.cleared) + "</b><small>طبقه‌ی فتح‌شده</small></div></div>" + note +
          '<div class="panel tw-arena ' + g.rarity + (g.boss ? " boss" : "") + '">' +
          '<div class="tw-head">' + (g.boss ? '<span class="tag" style="color:var(--mythic)">' + K.ic("skull") + "غول طبقه</span>" : '<span class="tag plain">' + K.ic("tower") + "نگهبان طبقه</span>") + K.rarTag(g.rarity) + "</div>" +
          '<div class="versus">' + HU.side(me, { role: "هیولای تو · " + K.esc(K.elLabel(me.element)), act: "swap" }) + '<div class="vs">VS</div>' +
          HU.side(g, { role: "نگهبان · " + K.esc(K.elLabel(g.element)), cls: "foe", fallback: S.art }) + "</div>" +
          '<div class="tw-adv">' + adv.html + "</div></div>" +
          '<div class="panel tw-prize"><div class="muted sm">' + K.ic("gift") + " پاداش فتح این طبقه</div><div class=\"tw-prize-v\">" + prize(S.rewards) + "</div></div>" +
          '<button class="btn danger lg block" data-act="fight">' + K.ic("swords") + 'نبرد با نگهبان<span class="cost">' + K.ic("bolt", "f") + K.n(S.energy_cost) + "</span></button>" +
          '<button class="btn block mt" data-act="swap">' + K.ic("swap") + "انتخاب هیولا</button>";
        if ((K.res ? K.res.energy : S.energy) < S.energy_cost) {
          html += '<div class="callout warn mt">' + K.ic("bolt") + "<span>هر نبرد برج " + K.n(S.energy_cost) + " انرژی می‌خواد.</span>" +
            (K.hasScreen("sh_energy") ? '<button class="btn sm tw-cbtn" data-act="energy">شارژ</button>' : "") + "</div>";
        }
        if (m) {
          html += '<div class="h2">' + K.ic("skull") + "غول بعدی · " + K.n(m.away) + ' طبقه مونده</div><div class="panel pad tw-mile ' + m.guardian.rarity + '">' +
            '<img src="' + K.esc(m.guardian.img || S.art || "") + '" alt=""><div class="grow"><div class="b cut">' + K.esc(m.guardian.name) + "</div>" +
            '<div class="sm muted">طبقه‌ی ' + K.n(m.guardian.floor) + ' · <span class="e-' + m.guardian.element + '">' + K.esc(K.elLabel(m.guardian.element)) + '</span> · <span class="t-accent">' + K.ic("power") + " " + K.n(m.guardian.power) + "</span></div>" +
            '<div class="tw-mile-v">' + prize(m.rewards) + "</div></div></div>";
        }
        html += '<div class="h2">' + K.ic("podium") + "برترین فاتحان</div>";
        html += S.leaderboard.length ? '<div class="panel tw-lb">' + S.leaderboard.map(function (r) {
          return '<div class="' + (r.me ? "me" : "") + '"><span class="rk' + (r.rank <= 3 ? " top" + r.rank : "") + '">' + r.rank + '</span><span class="nm">' + K.esc(r.name) + (r.me ? ' <span class="muted xs">(تو)</span>' : "") + "</span>" +
            '<span class="fl">' + K.ic("tower") + "طبقه " + K.n(r.floor) + "</span></div>";
        }).join("") + "</div>" : '<div class="panel pad center muted">هنوز کسی طبقه‌های اول رو فتح نکرده. اولین نفر باش!</div>';
        return html;
      }
      function draw(keep) { root.innerHTML = view(); if (!keep) window.scrollTo(0, 0); }

      K.on(root, "fight", function (el) {
        if (lock) return; lock = true;
        K.api.post("tower/fight/", {}, el).then(function (d) {
          lock = false;
          var r = d.result; S = d; K.invalidate("profile/creatures/");
          if (K.me) K.me.tower_floor = d.cleared;
          if (r.won) {
            note = "";
            draw();
            K.reward({ title: "طبقه‌ی " + r.floor + " فتح شد!", text: r.guardian.name + " شکست خورد. طبقه‌ی " + r.next_floor + " باز شد." + (r.levels ? " هیولات رسید به سطح " + r.level + "!" : ""),
              icon: "tower", coins: r.rewards.coins, dna: r.rewards.dna, diamonds: r.rewards.diamonds, xp: r.rewards.xp,
              extra: r.rewards.tickets ? [K.ic("ticket") + '<span class="num">+' + r.rewards.tickets + "</span> بلیط"] : [], button: "ادامه‌ی صعود" });
          } else {
            note = '<div class="callout bad mt">' + K.ic("skull") + "<span><b>شکست در طبقه‌ی " + K.n(r.floor) + "</b><br>نگهبان برج خیلی قوی بود. هیولات رو قوی‌تر کن، یا با عنصری برو که بهش برتری داره، و دوباره بیا." +
              (r.levels ? "<br>هیولات رسید به سطح " + K.n(r.level) + "." : "") + "</span></div>" + HU.log(r.log);
            draw(); K.haptic("err");
          }
        }).catch(function () { lock = false; });
      });
      K.on(root, "swap", function () {
        HU.swapSheet({ enemy: S.guardian.element, sub: "نگهبان عوض نمی‌شه؛ هیولایی که انتخاب کنی هیولای فعالت می‌شه." }).then(function (id) {
          if (!id) return;
          return K.api.post("tower/swap/", { creature_id: id }).then(function (d) {
            S = d; note = ""; K.invalidate("profile/creatures/"); draw(true); K.haptic("ok"); K.toast("هیولای فعالت عوض شد.", "ok");
          });
        }).catch(function () {});
      });
      K.on(root, "energy", function () { K.go("sh_energy"); });

      return K.api.get("tower/").then(function (d) { S = d; draw(); });
    }
  });

  var tile = { id: "tower", title: "برج موگن", sub: "طبقه به طبقه، جایزه‌ی بزرگ‌تر", icon: "tower", color: "var(--epic)", go: "tw_tower", order: 6 };
  /* `hall: 5`, except for the player whose active story quest sends them to the tower (the
     bot's exemption) — the hub status says whether the server would let them in. */
  Object.defineProperty(tile, "hall", { enumerable: true, get: function () { return HU.hubStatus().tower_open ? 0 : HALL; } });
  K.hub("battle", tile);
})(window.K);
