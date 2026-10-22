/* Game feel: rewards that fly into the resource bar, numbers that count up, and the full-screen
   «پیروزی / شکست» result. Nothing here talks to the server; it wraps K.setRes and K.reward from
   core.js, so every screen that already shows a reward gets the effect for free.

     K.result({won, title, sub, img, coins, dna, diamonds, energy, cup, xp, extra: ["…html…"],
               button: "ادامه", auto: ms}) → Promise (resolves when closed; `auto` closes it by itself)
     K.fly(key, fromRect, amount)   key: coins | dna | diamonds | energy | cup */
(function (K) {
  "use strict";

  // reward key → [resource-bar class, icon]
  var KIND = { coins: ["coin", "coin"], dna: ["dna", "dna"], diamonds: ["gem", "gem"], energy: ["energy", "energy"], cup: ["cup", "cup"] };
  var hudIn = document.getElementById("hud-in");
  var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function fmt(key, v) {
    if (key === "energy") return Number(v) + "<small>/" + ((K.res && K.res.max_energy) || "") + "</small>";
    return key === "cup" ? K.n(v) : K.short(v);
  }
  function pill(key) { return hudIn && hudIn.querySelector(".res.t-" + KIND[key][0]); }

  /* ── count-up in the resource bar ──
     A number that went UP is first shown at its old value; it rolls to the new one when the
     flying icons land (or at once when nothing is flying). */
  var pend = {}, hold = 0, flying = 0, fallback = null;
  function showing() { return K.sheetOpen() || !!document.querySelector(".rs"); }
  /* Roll what is waiting once nothing holds it. A reward sheet that another sheet replaced never
     reports its close, so «held» also ends when no reward is on screen any more (or after ~10 s). */
  function scheduleRoll() {
    clearTimeout(fallback);
    var tries = 0;
    (function check() {
      fallback = setTimeout(function () {
        if (flying) return check();
        if (hold && showing() && ++tries < 20) return check();
        hold = 0; rollAll();
      }, tries ? 500 : 450);
    })();
  }
  function roll(key) {
    var p = pend[key]; if (!p) return; delete pend[key];
    var el = pill(key), b = el && el.querySelector("b"); if (!b) return;
    el.classList.remove("bump"); void el.offsetWidth; el.classList.add("bump");
    if (reduced) { b.innerHTML = fmt(key, p.to); return; }
    var t0 = performance.now(), dur = 650;
    (function step(now) {
      var k = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - k, 3);
      b.innerHTML = fmt(key, Math.round(p.from + (p.to - p.from) * e));
      if (k < 1) requestAnimationFrame(step);
    })(t0);
  }
  function rollAll() { Object.keys(pend).forEach(roll); }
  var baseSet = K.setRes;
  K.setRes = function (res) {
    var prev = K.res;
    baseSet(res);
    if (!prev || !res) return;
    var any = false;
    Object.keys(KIND).forEach(function (key) {
      if (res[key] > prev[key]) {
        pend[key] = { from: pend[key] ? pend[key].from : prev[key], to: res[key] }; any = true;
      } else if (pend[key] && res[key] !== pend[key].to) delete pend[key];   // spent meanwhile: just show it
    });
    // the bar was just rebuilt with the new numbers: everything still waiting goes back to its old value
    Object.keys(pend).forEach(function (key) { var b = pill(key) && pill(key).querySelector("b"); if (b) b.innerHTML = fmt(key, pend[key].from); });
    if (any) scheduleRoll();
  };

  /* ── flying icons ── */
  K.fly = function (key, from, amount) {
    var target = pill(key), ri = target && target.querySelector(".ri");
    if (!KIND[key] || !ri || !from || reduced || !Element.prototype.animate) { roll(key); return Promise.resolve(); }
    var to = ri.getBoundingClientRect(), n = Math.max(3, Math.min(8, 2 + Math.round(Math.log10(Math.max(1, Math.abs(amount || 1))) * 1.5)));
    var x0 = from.left + from.width / 2, y0 = from.top + from.height / 2, x1 = to.left + to.width / 2, y1 = to.top + to.height / 2;
    var landed = 0;
    flying++;
    return new Promise(function (resolve) {
      for (var i = 0; i < n; i++) (function (i) {
        var el = document.createElement("i");
        el.className = "fx-coin"; el.innerHTML = K.resArt(KIND[key][1]);
        el.style.left = (x0 - 13) + "px"; el.style.top = (y0 - 13) + "px";
        document.body.appendChild(el);
        var ang = Math.random() * Math.PI * 2, r = 26 + Math.random() * 30, bx = Math.cos(ang) * r, by = Math.sin(ang) * r;
        var a = el.animate([
          { transform: "translate(0,0) scale(.3)", opacity: 0 },
          { transform: "translate(" + bx + "px," + by + "px) scale(1.1)", opacity: 1, offset: 0.28 },
          { transform: "translate(" + (x1 - x0) + "px," + (y1 - y0) + "px) scale(.55)", opacity: 0.95 }
        ], { duration: 820 + i * 55, delay: i * 45, easing: "cubic-bezier(.55,0,.35,1)", fill: "forwards" });
        a.onfinish = function () {
          el.remove(); landed++;
          if (landed === 1) { roll(key); K.sfx("coin"); }
          if (landed === n) { flying = Math.max(0, flying - 1); resolve(); }
        };
      })(i);
    });
  };
  /* fly every reward pill found inside `box` (classes t-coin / t-dna / t-diamond / t-energy / t-cup) */
  var PILL_KEY = { "t-coin": "coins", "t-dna": "dna", "t-diamond": "diamonds", "t-gem": "diamonds", "t-energy": "energy", "t-cup": "cup" };
  function flyFrom(box, amounts) {
    if (!box) { rollAll(); return; }
    var flown = {};
    Array.prototype.forEach.call(box.querySelectorAll("[data-fly], .it"), function (el) {
      var key = el.dataset.fly; if (!key) for (var c in PILL_KEY) if (el.classList.contains(c)) key = PILL_KEY[c];
      if (!key || flown[key] || !(amounts[key] > 0)) return;
      flown[key] = true; K.fly(key, el.getBoundingClientRect(), amounts[key]);
    });
    setTimeout(rollAll, 1400);   // anything that did not fly still rolls
  }

  /* For a module with its own reward sheet (15_daily.js reveal): hold the count-up while it is
     open, then fly from its loot pills when it closes. */
  K.fxHold = function () { hold++; K.sfx("collect"); };
  K.fxRelease = function (box, amounts) { hold = Math.max(0, hold - 1); flyFrom(box, amounts || {}); };

  /* ── coach marks: a pointing hand + a bubble on an element, once per key on this device ── */
  var coachEl = null;
  function seen(key) { try { return localStorage.getItem("kcoach:" + key) === "1"; } catch (e) { return true; } }
  function markSeen(key) { try { localStorage.setItem("kcoach:" + key, "1"); } catch (e) {} }
  function uncoach() {
    if (coachEl) { coachEl.remove(); coachEl = null; }
    window.removeEventListener("pointerdown", uncoach, true); window.removeEventListener("scroll", uncoach, true);
  }
  K.coach = function (target, text, key) {
    if (!target || !target.isConnected || seen(key) || coachEl || K.sheetOpen()) return;
    var r = target.getBoundingClientRect();
    if (!r.width || r.bottom < 60 || r.top > window.innerHeight - 90) return;   // only for something on screen
    markSeen(key);
    var el = coachEl = document.createElement("div");
    var below = r.top < window.innerHeight * 0.55, x = r.left + r.width / 2, y = below ? r.bottom - 6 : r.top + 6;
    el.className = "coach " + (below ? "down" : "up");
    el.style.left = x + "px"; el.style.top = y + "px";
    el.innerHTML = '<i class="coach-ring"></i><i class="coach-hand"><svg viewBox="0 0 32 32" aria-hidden="true"><path d="M12 3.5a2.2 2.2 0 0 1 4.4 0V14l.6-.1a2.2 2.2 0 0 1 2.6 1.4l.2.5.5-.1a2.2 2.2 0 0 1 2.6 1.5l.1.4.4-.1a2.1 2.1 0 0 1 2.6 2v4.6a8 8 0 0 1-8 8h-1.4a8 8 0 0 1-6.4-3.2l-4-5.4a2.2 2.2 0 0 1 3.4-2.8L12 18z" fill="#fff" stroke="#1b2335" stroke-width="1.6" stroke-linejoin="round"/></svg></i>' +
      '<span class="coach-tip">' + K.esc(text) + "</span>";
    document.body.appendChild(el);
    var tip = el.querySelector(".coach-tip"), tw = tip.offsetWidth;   // keep the bubble on screen
    tip.style.marginLeft = (Math.max(10, Math.min(window.innerWidth - tw - 10, x - tw / 2)) - x) + "px";
    var mine = el, born = Date.now();
    // gone with its target: a screen change (even one that no tap started) takes the hint away too
    (function watch() {
      if (coachEl !== mine) return;
      if (!target.isConnected || Date.now() - born > 6500) return uncoach();
      setTimeout(watch, 250);
    })();
    window.addEventListener("pointerdown", uncoach, true); window.addEventListener("scroll", uncoach, true);
  };

  /* ── seasons: the scene's tint and falling particles follow the season (Tehran calendar), or the
     element of a running festival. K.season = {key, label}; K.seasonFx() → particle layer html;
     K.setFestival(element|null) is called by the lobby when the home summary arrives. ── */
  var SEASONS = { spring: "بهار", summer: "تابستان", autumn: "پاییز", winter: "زمستان" };
  function seasonKey(d) {
    var md = (d.getMonth() + 1) * 100 + d.getDate();   // the Persian seasons start on the 21st–23rd
    return md >= 321 && md < 622 ? "spring" : md >= 622 && md < 923 ? "summer" : md >= 923 && md < 1222 ? "autumn" : "winter";
  }
  K.season = { key: seasonKey(new Date()), label: "" };
  K.season.label = SEASONS[K.season.key];
  var tint = document.createElement("div");
  tint.id = "season-tint"; document.body.insertBefore(tint, document.body.firstChild);
  document.body.classList.add("season-" + K.season.key);
  K.setFestival = function (element) {
    if (element) { document.body.dataset.fest = element; document.body.style.setProperty("--fest", "var(--" + element + ")"); }
    else { delete document.body.dataset.fest; document.body.style.removeProperty("--fest"); }
  };
  var fxHtml = null;   // one layout per visit: a redraw must not make the leaves jump
  K.seasonFx = function () {
    if (reduced) return "";
    if (fxHtml) return fxHtml;
    var n = K.season.key === "winter" ? 22 : 14, out = "";
    for (var i = 0; i < n; i++) {
      out += '<i style="--x:' + (Math.random() * 100).toFixed(1) + "%;--s:" + (K.season.key === "winter" ? 3 + Math.random() * 4 : 7 + Math.random() * 6).toFixed(1) +
        "px;--d:" + (7 + Math.random() * 8).toFixed(1) + "s;--w:-" + (Math.random() * 12).toFixed(1) + "s;--dx:" + Math.round(Math.random() * 120 - 60) + "px;--r:" + Math.round(Math.random() * 360) + 'deg"></i>';
    }
    return (fxHtml = '<span class="season-fx" aria-hidden="true">' + out + "</span>");
  };

  /* ── sound effects (static/sfx/*.mp3, made for the game): K.sfx(name). Off with K.setSound(false)
     (settings screen); decoded once with WebAudio on the first tap, which phones require anyway. ── */
  // gains even out the files' loudness (measured RMS: hit −5 dB … tap −43 dB) so UI sounds stay soft and rewards stand out
  var SFX = { tap: 1, tab: .35, open: .55, coin: .5, collect: .6, chest: .45, card: .6, win: .6, lose: .3, levelup: .3, error: .4, hit: .12 };
  var actx = null, bufs = {}, lastAt = {};
  K.soundOn = function () { try { return localStorage.getItem("ksfx") !== "0"; } catch (e) { return true; } };
  K.setSound = function (on) { try { localStorage.setItem("ksfx", on ? "1" : "0"); } catch (e) {} if (on) unlock(); };
  function unlock() {
    if (!K.soundOn()) return;
    var AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return;
    if (!actx) {
      try { actx = new AC(); } catch (e) { return; }
      Object.keys(SFX).forEach(function (n) {
        fetch(K.asset("sfx/" + n + ".mp3")).then(function (r) { return r.arrayBuffer(); })
          .then(function (b) { return new Promise(function (ok, no) { actx.decodeAudioData(b, ok, no); }); })
          .then(function (buf) { bufs[n] = buf; }).catch(function () {});
      });
    }
    if (actx.state === "suspended") actx.resume().catch(function () {});
  }
  K.sfx = function (name, delay) {
    if (!K.soundOn() || !actx || !bufs[name]) return;
    var now = Date.now();
    if (!delay) {   // the same sound twice in a blink is one (planned ones, e.g. card flips, all play)
      if (lastAt[name] && now - lastAt[name] < 90) return;
      lastAt[name] = now;
    }
    try {
      var src = actx.createBufferSource(), g = actx.createGain();
      g.gain.value = SFX[name] || .5; src.buffer = bufs[name]; src.connect(g); g.connect(actx.destination);
      src.start(actx.currentTime + (delay || 0));
    } catch (e) {}
  };
  window.addEventListener("pointerdown", unlock, { capture: true, passive: true });
  // which sound a tap makes: the bottom tabs swish, an attack hits, everything else clicks softly
  document.addEventListener("click", function (ev) {
    var t = ev.target;
    if (!t || !t.closest) return;
    if (t.closest("nav.tabs button")) return K.sfx("tab");
    if (t.closest('[data-act="attack"]')) return K.sfx("hit");
    if (t.closest("button, [data-hub], .tile, a")) K.sfx("tap");
  }, true);
  var baseHaptic = K.haptic;
  K.haptic = function (kind) { if (kind === "err") K.sfx("error"); return baseHaptic.apply(this, arguments); };
  var baseSheet = K.sheet;
  K.sheet = function () { K.sfx("open"); return baseSheet.apply(this, arguments); };

  /* ── K.reward: hold the count-up while the sheet is open, then fly on «عالیه» ── */
  var baseReward = K.reward;
  K.reward = function (o) {
    hold++;
    var p = baseReward(o), box = document.querySelector(".sheet .loot");
    // the gift's sound, then one flip per card as they turn over (the delays of .loot .cards in 05_fx.css)
    var cards = ((o && o.creatures) || []).length + ((o && o.items) || []).length;
    K.sfx(cards ? "chest" : "collect");
    for (var ci = 0; ci < Math.min(cards, 6); ci++) K.sfx("card", .35 + ci * .2);
    return p.then(function () {
      hold = Math.max(0, hold - 1);
      flyFrom(box, o || {});
    });
  };

  /* ── K.result: the full-screen «پیروزی / شکست» ── */
  var LOOT = [["coins", "coin"], ["dna", "dna"], ["diamonds", "gem"], ["energy", "energy"], ["cup", "cup"]];
  K.result = function (o) {
    o = o || {}; hold++;
    var won = !!o.won, loot = "";
    LOOT.forEach(function (l) {
      var v = Number(o[l[0]]) || 0; if (!v) return;
      loot += '<span class="rs-it' + (v < 0 ? " neg" : "") + '" data-fly="' + l[0] + '"><i>' + K.resArt(l[1]) + '</i><b class="num" data-to="' + v + '">' + (v > 0 ? "+" : "") + "0</b></span>";
    });
    if (o.xp) loot += '<span class="rs-it xp"><i>' + K.ic("up") + '</i><b class="num" data-to="' + o.xp + '">+0</b><small>XP</small></span>';
    (o.extra || []).forEach(function (h) { loot += '<span class="rs-it ex">' + h + "</span>"; });
    var confetti = "";
    if (won && !reduced) for (var i = 0; i < 26; i++) {
      confetti += '<i style="left:' + (Math.random() * 100).toFixed(1) + "%;--d:" + (1.6 + Math.random() * 1.6).toFixed(2) + "s;--w:" + (Math.random() * .7).toFixed(2) + "s;--r:" + Math.round(Math.random() * 360) + "deg;--x:" + Math.round(Math.random() * 80 - 40) + "px;background:" +
        ["#ffc53d", "#3ad6ff", "#ff5470", "#35d48a", "#a77bff", "#fff"][i % 6] + '"></i>';
    }
    var el = document.createElement("div");
    el.className = "rs " + (won ? "win" : "lose");
    el.innerHTML = (won ? '<div class="rs-rays"></div><div class="rs-confetti">' + confetti + "</div>" : "") +
      '<div class="rs-box">' +
      (o.img ? '<div class="rs-pic"><img src="' + o.img + '" alt=""></div>' : "") +
      '<div class="rs-banner"><span>' + K.esc(o.title || (won ? "پیروزی!" : "شکست")) + "</span></div>" +
      (won ? '<div class="rs-stars"><i>' + K.ic("star", "f") + "</i><i>" + K.ic("star", "f") + "</i><i>" + K.ic("star", "f") + "</i></div>" : "") +
      (o.sub ? '<div class="rs-sub">' + K.esc(o.sub) + "</div>" : "") +
      (loot ? '<div class="rs-loot">' + loot + "</div>" : "") +
      '<button class="rs-btn">' + K.esc(o.button || "ادامه") + "</button></div>";
    document.body.appendChild(el);
    K.haptic(won ? "ok" : "err");
    K.sfx(won ? "win" : "lose");
    if (o.levelup) K.sfx("levelup", 1.1);
    requestAnimationFrame(function () { el.classList.add("on"); });
    // the loot numbers count up after the banner has landed
    setTimeout(function () {
      Array.prototype.forEach.call(el.querySelectorAll(".rs-it b[data-to]"), function (b, i) {
        var to = Number(b.dataset.to), sign = to > 0 ? "+" : "", t0 = 0;
        setTimeout(function () {
          (function step(now) {
            if (!t0) t0 = now;
            var k = reduced ? 1 : Math.min(1, (now - t0) / 700), e = 1 - Math.pow(1 - k, 3);
            b.textContent = sign + Math.round(to * e).toLocaleString("en-US");
            if (k < 1) requestAnimationFrame(step);
          })(performance.now());
        }, i * 120);
      });
    }, reduced ? 0 : 650);
    return new Promise(function (resolve) {
      var done = false, opened = Date.now();
      function close() {
        if (done) return; done = true;
        var amounts = {}; LOOT.forEach(function (l) { amounts[l[0]] = Number(o[l[0]]) || 0; });
        var box = el.querySelector(".rs-loot");
        hold = Math.max(0, hold - 1);
        flyFrom(box, amounts);
        el.classList.remove("on"); el.classList.add("off");
        setTimeout(function () { el.remove(); }, 260);
        resolve();
      }
      el.querySelector(".rs-btn").onclick = close;
      // o.auto (ms): a quick loop (hunting) must not stop for a tap — it closes by itself, and any tap closes it at once
      if (o.auto) { el.classList.add("auto"); setTimeout(close, o.auto); el.addEventListener("click", close); }
      else el.addEventListener("click", function (ev) { if (ev.target === el && Date.now() - opened > 700) close(); });
    });
  };
})(window.K);
