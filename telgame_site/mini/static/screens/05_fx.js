/* Game feel: rewards that fly into the resource bar, numbers that count up, and the full-screen
   «پیروزی / شکست» result. Nothing here talks to the server; it wraps K.setRes and K.reward from
   core.js, so every screen that already shows a reward gets the effect for free.

     K.result({won, title, sub, img, coins, dna, diamonds, energy, cup, xp, extra: ["…html…"],
               button: "ادامه"}) → Promise (resolves when closed)
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
  var pend = {}, hold = 0, fallback = null;
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
        var from = pend[key] ? pend[key].from : prev[key];
        pend[key] = { from: from, to: res[key] }; any = true;
        var b = pill(key) && pill(key).querySelector("b"); if (b) b.innerHTML = fmt(key, from);
      }
    });
    if (any) { clearTimeout(fallback); fallback = setTimeout(function () { if (!hold) rollAll(); }, 450); }
  };

  /* ── flying icons ── */
  K.fly = function (key, from, amount) {
    var target = pill(key), ri = target && target.querySelector(".ri");
    if (!KIND[key] || !ri || !from || reduced) { roll(key); return Promise.resolve(); }
    var to = ri.getBoundingClientRect(), n = Math.max(3, Math.min(8, 2 + Math.round(Math.log10(Math.max(1, Math.abs(amount || 1))) * 1.5)));
    var x0 = from.left + from.width / 2, y0 = from.top + from.height / 2, x1 = to.left + to.width / 2, y1 = to.top + to.height / 2;
    var landed = 0;
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
          if (landed === 1) roll(key);
          if (landed === n) resolve();
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
  K.fxHold = function () { hold++; };
  K.fxRelease = function (box, amounts) { hold = Math.max(0, hold - 1); flyFrom(box, amounts || {}); };

  /* ── coach marks: a pointing hand + a bubble on an element, once per key on this device ── */
  var coachEl = null;
  function seen(key) { try { return localStorage.getItem("kcoach:" + key) === "1"; } catch (e) { return true; } }
  function markSeen(key) { try { localStorage.setItem("kcoach:" + key, "1"); } catch (e) {} }
  function uncoach() { if (coachEl) { coachEl.remove(); coachEl = null; } }
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
    setTimeout(uncoach, 6500);
    ["pointerdown", "scroll"].forEach(function (ev) { window.addEventListener(ev, uncoach, { once: true, passive: true, capture: true }); });
  };

  /* ── K.reward: hold the count-up while the sheet is open, then fly on «عالیه» ── */
  var baseReward = K.reward;
  K.reward = function (o) {
    hold++;
    var p = baseReward(o), box = document.querySelector(".sheet .loot");
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
      el.addEventListener("click", function (ev) { if (ev.target === el && Date.now() - opened > 700) close(); });
    });
  };
})(window.K);
