/* Finding an alliance (tabs, filters, search, join), the leaders' «جذب و دعوت» page and the
   «صندوق نامه». API: recruit/… (telgame_site/mini/api/recruit.py, game/alliance_recruit.py).
   K.recruit.browse(root, ctx) draws the finder inside «اتحاد» for a player in no alliance. */
(function (K) {
  "use strict";

  var TABS = [["suggested", "پیشنهادی", "star"], ["new", "تازه‌ها", "spark"], ["top", "برترین‌ها", "podium"]];
  var JOIN = [["", "همه"], ["auto", "ورود فوری"], ["approval", "با تأیید"]];
  var SIZE = [["", "همه‌ی اندازه‌ها"], ["small", "کوچیک (زیر ۱۰)"], ["medium", "متوسط"], ["large", "بزرگ (۲۰+)"]];
  var st = { tab: "suggested", join: 0, size: 0, reach: true, rec: false };   // kept while the app is open

  function when(sec) {
    var d = new Date(sec * 1000), p = function (n) { return (n < 10 ? "0" : "") + n; };
    return '<span class="num">' + p(d.getMonth() + 1) + "/" + p(d.getDate()) + " " + p(d.getHours()) + ":" + p(d.getMinutes()) + "</span>";
  }
  function bonusNote(b) { return b ? " · پاداش رشد " + K.n(b) + " طلا" : ""; }

  function cardHtml(c) {
    var tags = (c.is_new ? K.tag("تازه", "var(--good)", "spark") + " " : "") + (c.recruiting ? K.tag("دنبال عضو", "var(--gold)", "users") : "");
    var mode = c.auto_accept ? "ورود فوری" : "با تأیید رهبر";
    return '<div class="panel pad rc-card">' +
      '<div class="rc-head"><b class="cut">' + K.esc(c.name) + "</b>" + (tags ? '<span class="rc-tags">' + tags + "</span>" : "") + "</div>" +
      '<div class="rc-stats sm">' +
        "<span>" + K.ic("users") + K.n(c.members) + '<span class="faint">/' + K.n(c.capacity) + "</span></span>" +
        '<span class="t-good">' + K.ic("bolt") + K.n(c.active) + " فعال</span>" +
        '<span class="t-accent">' + K.ic("power") + "میانگین " + K.n(c.avg_power) + "</span></div>" +
      '<div class="sm muted mt-xs">' + mode + (c.min_power ? " · حداقل قدرت " + K.n(c.min_power) : "") + "</div>" +
      (c.note ? '<div class="rc-note">«' + K.esc(c.note) + "»</div>" : "") +
      '<button class="btn ' + (c.reachable ? "primary" : "") + ' block mt" data-act="rjoin" data-id="' + c.id + '"' + (c.reachable ? "" : " disabled") + ">" +
        K.ic(c.auto_accept ? "plus" : "send") + (c.reachable ? (c.auto_accept ? "پیوستن" : "درخواست عضویت") : "قدرتت هنوز کمه") + "</button></div>";
  }

  function query() {
    return "tab=" + st.tab + "&join=" + JOIN[st.join][0] + "&size=" + SIZE[st.size][0] + "&reach=" + (st.reach ? 1 : 0) + "&rec=" + (st.rec ? 1 : 0);
  }

  K.recruit = {
    /* the finder, drawn into `root` (the «اتحاد» screen of a player in no alliance) */
    browse: function (root, ctx, head) {
      var page = 0, searching = "";
      root.innerHTML = (head || "") +
        '<div class="btns mt"><button class="btn" data-act="rinbox">' + K.ic("bell") + 'صندوق نامه <span class="num" id="rc-unread"></span></button>' +
        '<button class="btn" data-act="rcreate">' + K.ic("plus") + "ساخت اتحاد</button></div>" +
        '<div class="rc-search mt"><input class="input" id="rc-q" maxlength="40" placeholder="جستجوی اسم اتحاد" enterkeyhint="search">' +
        '<button class="btn" data-act="rsearch">' + K.ic("search") + "</button></div>" +
        '<div class="mt" id="rc-tabs"></div><div class="rc-filters" id="rc-filters"></div>' +
        '<p class="sm muted" id="rc-hint"></p><div id="rc-list"></div><div id="rc-more"></div>';
      var listEl = root.querySelector("#rc-list"), moreEl = root.querySelector("#rc-more");
      function controls() {
        root.querySelector("#rc-tabs").innerHTML = '<div class="seg">' + TABS.map(function (t) {
          return '<button class="' + (st.tab === t[0] && !searching ? "on" : "") + '" data-act="rtab" data-tab="' + t[0] + '">' + K.ic(t[2]) + t[1] + "</button>";
        }).join("") + "</div>";
        root.querySelector("#rc-filters").innerHTML = searching ? "" :
          '<button class="chip ' + (st.join ? "on" : "") + '" data-act="rjoinf">' + K.ic("sliders") + JOIN[st.join][1] + "</button>" +
          '<button class="chip ' + (st.size ? "on" : "") + '" data-act="rsize">' + K.ic("users") + SIZE[st.size][1] + "</button>" +
          '<button class="chip ' + (st.reach ? "on" : "") + '" data-act="rreach">' + K.ic("power") + "فقط قابل‌ورود</button>" +
          '<button class="chip ' + (st.rec ? "on" : "") + '" data-act="rrec">' + K.ic("flag") + "دنبال عضو</button>";
        root.querySelector("#rc-hint").textContent = searching ? "نتیجه‌ی جستجوی «" + searching + "»" :
          st.tab === "suggested" ? "اتحادهای زنده‌ای که جا دارن و به سطحت می‌خورن — ترتیبش هر روز عوض می‌شه." :
          st.tab === "new" ? "اتحادهای تازه و کوچیکِ فعال؛ با پیوستن به این‌ها «پاداش رشد» می‌گیری." : "قوی‌ترین اتحادهایی که هنوز جا دارن.";
      }
      function load(append) {
        var p = searching ? K.api.get("recruit/search/?q=" + encodeURIComponent(searching)) : K.api.get("recruit/browse/?" + query() + "&page=" + page);
        return p.then(function (d) {
          if (!ctx.alive()) return;
          var u = root.querySelector("#rc-unread"); if (u && d.unread != null) u.textContent = d.unread ? "(" + d.unread + ")" : "";
          var html = d.cards.map(cardHtml).join("");
          if (!append) listEl.innerHTML = html || K.state("search", "اتحادی پیدا نشد", searching ? "اسم دیگه‌ای رو امتحان کن." : "فیلترها رو عوض کن یا با اسم جستجو کن.");
          else listEl.insertAdjacentHTML("beforeend", html);
          moreEl.innerHTML = d.has_next ? '<button class="btn ghost block" data-act="rmore">' + K.ic("chevron") + "اتحادهای بیشتر</button>" : "";
        });
      }
      function refresh() { page = 0; controls(); return load(false); }
      K.on(root, "rtab", function (el) { st.tab = el.dataset.tab; searching = ""; root.querySelector("#rc-q").value = ""; refresh(); });
      K.on(root, "rjoinf", function () { st.join = (st.join + 1) % JOIN.length; refresh(); });
      K.on(root, "rsize", function () { st.size = (st.size + 1) % SIZE.length; refresh(); });
      K.on(root, "rreach", function () { st.reach = !st.reach; refresh(); });
      K.on(root, "rrec", function () { st.rec = !st.rec; refresh(); });
      K.on(root, "rmore", function () { page++; load(true); });
      function doSearch() { searching = root.querySelector("#rc-q").value.trim(); refresh(); }
      K.on(root, "rsearch", doSearch);
      root.querySelector("#rc-q").addEventListener("keydown", function (ev) { if (ev.key === "Enter") { ev.preventDefault(); doSearch(); } });
      K.on(root, "rinbox", function () { K.go("so_inbox"); });
      K.on(root, "rcreate", function () { K.toast("ساخت اتحاد از ربات انجام می‌شه: «شهر» ← «اتحاد» ← «ساخت اتحاد»."); });
      K.on(root, "rjoin", function (el) {
        K.api.post("recruit/join/", { id: +el.dataset.id }, el).then(function (r) {
          K.haptic("ok");
          if (r.joined) { K.toast("عضو اتحاد «" + r.name + "» شدی!" + bonusNote(r.bonus), "ok"); K.invalidate("social/alliance/"); ctx.reload(); }
          else K.toast(r.already ? "قبلاً درخواست دادی؛ منتظر جواب رهبر باش." : "درخواستت برای «" + r.name + "» فرستاده شد.", "ok");
        }).catch(function () {});
      });
      return refresh();
    }
  };

  // ───────────────────────── «صندوق نامه» ─────────────────────────
  K.hub("more", { id: "inbox", title: "نامه‌ها", sub: "دعوت‌ها و پیام‌ها", icon: "bell", color: "var(--accent)", go: "so_inbox", order: 41 });

  function mailHtml(m) {
    var acts = "";
    if (m.action === "invite") acts = '<div class="btns mt"><button class="btn good" data-act="mok" data-ref="' + m.ref + '">' + K.ic("check") + 'قبول دعوت</button><button class="btn" data-act="mno" data-ref="' + m.ref + '">' + K.ic("close") + "رد</button></div>";
    if (m.action === "request") acts = '<div class="btns mt"><button class="btn good" data-act="rqok" data-ref="' + m.ref + '">' + K.ic("check") + 'تأیید</button><button class="btn" data-act="rqno" data-ref="' + m.ref + '">' + K.ic("close") + "رد</button></div>";
    return '<div class="panel pad rc-mail' + (m.read ? "" : " unread") + '"><div class="rc-head"><b>' + K.esc(m.title) + '</b><span class="sm faint">' + when(m.at) + "</span></div>" +
      (m.body ? '<div class="sm muted rc-body">' + K.esc(m.body).replace(/\n/g, "<br>") + "</div>" : "") + acts + "</div>";
  }

  K.screen("so_inbox", {
    title: "صندوق نامه", tab: "more",
    render: function (root, params, ctx) {
      return K.api.get("recruit/inbox/").then(function (d) {
        root.innerHTML = '<button class="chip ' + (d.blocked ? "" : "on") + '" data-act="mblock">' + K.ic(d.blocked ? "lock" : "check") + (d.blocked ? "دعوت اتحاد: بسته" : "دعوت اتحاد: باز") + "</button>" +
          '<div class="mt">' + (d.mails.length ? d.mails.map(mailHtml).join("") : K.state("bell", "صندوقت خالیه", "دعوت‌های اتحاد، جواب درخواست‌ها و پاداش‌ها اینجا میان.")) + "</div>";
        if (d.unread) K.api.post("recruit/inbox/read/", {}).then(function () { K.badges.inbox = 0; }).catch(function () {});
        function after(msg) { return function () { K.haptic("ok"); if (msg) K.toast(msg, "ok"); K.invalidate("social/alliance/"); ctx.reload(); }; }
        K.on(root, "mok", function (el) {
          K.api.post("recruit/invite/accept/", { id: +el.dataset.ref }, el).then(function (r) { after("عضو اتحاد «" + r.name + "» شدی!" + bonusNote(r.bonus))(); }).catch(function () {});
        });
        K.on(root, "mno", function (el) { K.api.post("recruit/invite/decline/", { id: +el.dataset.ref }, el).then(after("دعوت رد شد.")).catch(function () {}); });
        K.on(root, "rqok", function (el) { K.api.post("recruit/manage/approve/", { id: +el.dataset.ref }, el).then(after("عضو جدید اضافه شد.")).catch(function () {}); });
        K.on(root, "rqno", function (el) { K.api.post("recruit/manage/reject/", { id: +el.dataset.ref }, el).then(after("درخواست رد شد.")).catch(function () {}); });
        K.on(root, "mblock", function (el) {
          K.api.post("recruit/inbox/block/", { on: !d.blocked }, el).then(function (r) { K.toast(r.blocked ? "دیگه دعوت اتحاد نمی‌گیری." : "دعوت‌های اتحاد دوباره باز شد.", "ok"); ctx.reload(); }).catch(function () {});
        });
      });
    }
  });

  // ───────────────────────── leaders: «جذب و دعوت» ─────────────────────────
  K.screen("so_recruit", {
    title: "جذب و دعوت", tab: "more",
    render: function (root, params, ctx) {
      function draw(d) {
        root.innerHTML =
          '<div class="panel pad"><div class="rc-head"><b>دنبال عضو</b><button class="chip ' + (d.recruiting ? "on" : "") + '" data-act="mrec">' + K.ic(d.recruiting ? "check" : "close") + (d.recruiting ? "روشن" : "خاموش") + "</button></div>" +
          '<p class="sm muted">روشن باشه، اتحادت توی «پیشنهادی» بالاتر میاد و برچسب «دنبال عضو» می‌گیره. اعضا: ' + K.n(d.members) + " از " + K.n(d.capacity) + "</p></div>" +
          '<div class="panel pad mt"><b>آگهی جذب</b><p class="sm muted">یه جمله که روی کارت اتحادت دیده می‌شه.</p>' +
          '<textarea class="input" id="rc-note" rows="2" maxlength="' + d.note_max + '" placeholder="مثلاً: جنگ هفتگی رو جدی می‌گیریم، هر روز رید">' + K.esc(d.note) + "</textarea>" +
          '<button class="btn primary block mt" data-act="mnote">' + K.ic("check") + "ذخیره‌ی آگهی</button></div>" +
          '<div class="panel pad mt"><b>دعوت بازیکن</b><p class="sm muted">یوزرنیم (با @)، اسم آزمایشگاه یا آیدی عددی. با قبول دعوت، بدون تأیید و بدون شرط حداقل قدرت عضو می‌شه. امروز ' +
          K.n(d.sent_today) + " از " + K.n(d.daily) + "</p>" +
          '<div class="rc-search"><input class="input" id="rc-inv" maxlength="64" placeholder="@username یا اسم آزمایشگاه"><button class="btn primary" data-act="minv">' + K.ic("send") + "</button></div>" +
          (d.invites.length ? '<div class="list mt">' + d.invites.map(function (i) {
            return '<div class="rc-row"><span class="grow">' + K.esc(i.name) + ' <span class="sm faint">تا ' + when(i.until) + '</span></span><button class="btn sm" data-act="mcancel" data-id="' + i.id + '">' + K.ic("close") + "لغو</button></div>";
          }).join("") + "</div>" : "") + "</div>" +
          (d.requests.length ? '<div class="panel pad mt"><b>درخواست‌های عضویت</b>' + d.requests.map(function (r) {
            return '<div class="rc-row"><span class="grow">' + K.esc(r.name) + ' <span class="sm t-accent">' + K.ic("power") + K.n(r.power) + '</span></span><button class="btn sm good" data-act="mapp" data-id="' + r.id + '">' + K.ic("check") + '</button><button class="btn sm" data-act="mrej" data-id="' + r.id + '">' + K.ic("close") + "</button></div>";
          }).join("") + "</div>" : "") +
          (d.link ? '<div class="panel pad mt"><b>لینک دعوت</b><p class="sm muted">هر کی بازش کنه بدون تأیید عضو می‌شه. لینک جدید، قبلی رو باطل می‌کنه.</p>' +
          '<div class="rc-link num">' + K.esc(d.link) + '</div><div class="btns mt"><button class="btn primary" data-act="mshare">' + K.ic("send") + 'فرستادن</button><button class="btn" data-act="mcopy">' + K.ic("doc") + 'کپی</button><button class="btn" data-act="mlink">' + K.ic("refresh") + "لینک جدید</button></div></div>" : "");
        function post(path, body, el, msg) {
          return K.api.post("recruit/manage/" + path, body, el).then(function (nd) { K.haptic("ok"); if (msg) K.toast(typeof msg === "function" ? msg(nd) : msg, "ok"); draw(nd); }).catch(function () {});
        }
        K.on(root, "mrec", function (el) { post("recruiting/", { on: !d.recruiting }, el); });
        K.on(root, "mnote", function (el) { post("note/", { note: root.querySelector("#rc-note").value }, el, "آگهی ذخیره شد."); });
        K.on(root, "minv", function (el) {
          var q = root.querySelector("#rc-inv").value.trim(); if (!q) return;
          post("invite/", { query: q }, el, function (nd) { return "دعوت برای «" + nd.invited + "» فرستاده شد."; });
        });
        K.on(root, "mcancel", function (el) { post("cancel/", { id: +el.dataset.id }, el, "دعوت لغو شد."); });
        K.on(root, "mapp", function (el) { post("approve/", { id: +el.dataset.id }, el, "عضو جدید اضافه شد."); });
        K.on(root, "mrej", function (el) { post("reject/", { id: +el.dataset.id }, el, "درخواست رد شد."); });
        K.on(root, "mlink", function (el) { post("link/", {}, el, "لینک جدید ساخته شد؛ قبلی دیگه کار نمی‌کنه."); });
        K.on(root, "mcopy", function () {
          function done() { K.toast("لینک کپی شد.", "ok"); }
          try { navigator.clipboard.writeText(d.link).then(done, done); } catch (e) { done(); }
        });
        K.on(root, "mshare", function () {
          var url = "https://t.me/share/url?url=" + encodeURIComponent(d.link) + "&text=" + encodeURIComponent("بیا عضو اتحاد «" + d.name + "» شو!");
          try { if (K.tg && K.tg.openTelegramLink) { K.tg.openTelegramLink(url); return; } } catch (e) {}
          window.open(url, "_blank");
        });
      }
      return K.api.get("recruit/manage/").then(draw);
    }
  });
})(window.K);
