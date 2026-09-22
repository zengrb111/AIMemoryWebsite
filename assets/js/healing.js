/* =========================================================
   心灵驿站 · 列表渲染
   数据来源：/api/healing/（内容平台每日推送）
   降级：接口不可用时读取 assets/data/healing-fallback.json
   ========================================================= */
(function () {
  var API = "/api/healing";
  var FALLBACK = "assets/data/healing-fallback.json";
  var PAGE = 9;

  var state = { cat: "全部", q: "", offset: 0, total: 0, items: [], loading: false };

  var GRAD = {
    "亲情手记": "linear-gradient(135deg,#E8A33D,#C8881E)",
    "时光信箱": "linear-gradient(135deg,#CB8C6A,#8E5A44)",
    "疗愈短文": "linear-gradient(135deg,#74B391,#3F7D5B)",
    "陪伴日常": "linear-gradient(135deg,#D98A7E,#B5564B)",
    "声音记忆": "linear-gradient(135deg,#B9944F,#7A5C2E)"
  };
  var MARK = { "亲情手记": "亲", "时光信箱": "时", "疗愈短文": "愈", "陪伴日常": "伴", "声音记忆": "声" };
  var DEFAULT_GRAD = "linear-gradient(135deg,#E8A33D,#C8881E)";

  var $ = function (s) { return document.querySelector(s); };
  var esc = function (s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  };
  var grad = function (c) { return GRAD[c] || DEFAULT_GRAD; };
  var mark = function (c) { return MARK[c] || "忆"; };

  function fmtDate(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso).slice(0, 10);
    return (d.getMonth() + 1) + "月" + d.getDate() + "日";
  }
  function readMin(text) {
    var n = (text || "").length;
    return Math.max(1, Math.round(n / 350));
  }
  function coverHtml(p, cls, badge) {
    var g = grad(p.category);
    var attr = cls ? ' class="cover ' + cls + '"' : ' class="cover"';
    var inner = p.cover
      ? '<img src="' + esc(p.cover) + '" alt="' + esc(p.title) +
        '" style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover;" />'
      : '<div class="cover-mark' + (cls ? " sm" : "") + '">' + esc(mark(p.category)) + "</div>";
    return "<div" + attr + ' style="background:' + g + '">' + inner +
      '<div class="cover-scrim"></div>' +
      (badge ? '<span class="cat-badge">' + esc(p.category || "治愈") + "</span>" : "") +
      "</div>";
  }

  /* ---------------- 数据 ---------------- */
  function getJSON(url) {
    return fetch(url, { headers: { Accept: "application/json" } }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }

  function fetchPosts() {
    var q = API + "/posts?limit=" + PAGE + "&offset=" + state.offset;
    if (state.cat && state.cat !== "全部") q += "&category=" + encodeURIComponent(state.cat);
    if (state.q) q += "&q=" + encodeURIComponent(state.q);
    return getJSON(q).then(function (d) { return { items: d.items || [], total: d.total || 0 }; });
  }

  function fetchFallback() {
    return getJSON(FALLBACK).then(function (d) {
      var items = (d.items || []).filter(function (p) {
        if (state.cat && state.cat !== "全部" && p.category !== state.cat) return false;
        if (state.q) {
          var s = (p.title + p.summary + (p.content || "")).toLowerCase();
          if (s.indexOf(state.q.toLowerCase()) < 0) return false;
        }
        return true;
      });
      return { items: items.slice(state.offset, state.offset + PAGE), total: items.length, offline: true };
    });
  }

  /* ---------------- 渲染 ---------------- */
  function cardHtml(p) {
    return '<article class="healing-card" data-id="' + esc(p.id) + '">' +
      coverHtml(p, null, true) +
      '<div class="body">' +
        '<div class="faint" style="font-size:12px;">' + esc(p.category || "治愈") + "</div>" +
        "<h3>" + esc(p.title) + "</h3>" +
        "<p>" + esc(p.summary || "") + "</p>" +
        '<div class="foot"><span>' + esc(p.author || p.source || "金润瀚宇") + "</span>" +
        "<span>" + fmtDate(p.published_at) + " · " + readMin(p.summary) + " 分钟</span></div>" +
      "</div></article>";
  }

  function featureHtml(p) {
    return '<article class="healing-feature" data-id="' + esc(p.id) + '">' +
      coverHtml(p, "big") +
      '<div class="body">' +
        '<span class="badge-soft">🌟 今日推荐</span>' +
        "<h2>" + esc(p.title) + "</h2>" +
        "<p>" + esc(p.summary || "") + "</p>" +
        '<div class="faint" style="font-size:13px;margin-top:16px;">' +
          esc(p.category || "治愈") + " · " + fmtDate(p.published_at) + " · 阅读约 " + readMin(p.summary) + " 分钟" +
        "</div>" +
        '<div class="read-more">阅读全文 →</div>' +
      "</div></article>";
  }

  function render(reset) {
    var grid = $("#grid");
    var feat = $("#featureSlot");
    if (reset) { feat.innerHTML = ""; grid.innerHTML = ""; }

    var items = state.items;

    if (!items.length) {
      $("#empty").style.display = "block";
      $("#empty").innerHTML = state.q
        ? "<h3>没有找到相关内容</h3><p>换个关键词试试，或<a style='color:var(--gold)' href='#' id='clearQ'>清空搜索</a></p>"
        : "<h3>内容正在路上</h3><p>心灵驿站每日更新，请稍后再来看看。</p>";
      $("#moreWrap").style.display = "none";
      return;
    }
    $("#empty").style.display = "none";

    var html = "";
    var from = reset ? 0 : state.rendered || 0;
    for (var i = from; i < items.length; i++) {
      if (reset && i === 0 && state.cat === "全部" && !state.q) {
        feat.innerHTML = featureHtml(items[0]);
        continue;
      }
      html += cardHtml(items[i]);
    }
    grid.insertAdjacentHTML("beforeend", html);
    state.rendered = items.length;

    if (state.q) {
      var cq = document.getElementById("clearQ");
      if (cq) cq.addEventListener("click", function (e) {
        e.preventDefault(); $("#searchInput").value = ""; state.q = ""; reload();
      });
    }
  }

  function reload() {
    state.offset = 0; state.items = []; state.rendered = 0;
    $("#empty").style.display = "none";
    $("#moreWrap").style.display = "none";
    $("#loading").style.display = "block";
    state.loading = true;

    var req = fetchPosts();
    req.catch(function () { return fetchFallback(); }).then(function (res) {
      state.loading = false;
      $("#loading").style.display = "none";
      state.total = res.total;
      state.items = res.items;
      render(true);
      if (res.offline) {
        var w = document.getElementById("offlineNote");
        if (!w) {
          w = document.createElement("div");
          w.id = "offlineNote";
          w.className = "healing-empty";
          w.style.padding = "14px";
          w.style.fontSize = "13px";
          w.innerHTML = "当前为离线预览数据（内容接口未连接）";
          $("#featureSlot").parentNode.insertBefore(w, $("#featureSlot"));
        }
      }
      $("#moreWrap").style.display = state.items.length < state.total ? "block" : "none";
    }).catch(function (e) {
      state.loading = false;
      $("#loading").style.display = "none";
      $("#empty").style.display = "block";
      $("#empty").innerHTML = "<h3>内容暂时无法加载</h3><p>" + esc(e.message) + "</p>";
    });
  }

  function loadMore() {
    if (state.loading) return;
    state.loading = true;
    state.offset += PAGE;
    $("#moreBtn").textContent = "加载中…";
    fetchPosts().then(function (res) {
      state.items = state.items.concat(res.items);
      state.total = res.total;
      render(false);
      state.loading = false;
      $("#moreBtn").textContent = "加载更多";
      $("#moreWrap").style.display = state.items.length < state.total ? "block" : "none";
    }).catch(function () {
      state.loading = false;
      $("#moreBtn").textContent = "加载更多";
    });
  }

  /* ---------------- 详情弹层 ---------------- */
  function openPost(id) {
    var p = state.items.filter(function (x) { return x.id === id; })[0];
    getJSON(API + "/posts/" + encodeURIComponent(id)).catch(function () {
      return getJSON(FALLBACK).then(function (d) {
        return (d.items || []).filter(function (x) { return x.id === id; })[0] || p;
      });
    }).then(function (full) {
      full = full || p;
      if (!full) return;
      var body = (full.content || full.summary || "").split(/\n+/).filter(Boolean)
        .map(function (t) { return "<p>" + esc(t) + "</p>"; }).join("");
      var tags = (full.tags || []).map(function (t) { return "<span>#" + esc(t) + "</span>"; }).join("");
      $("#modalBox").innerHTML =
        '<div class="modal-cover" style="background:' + grad(full.category) + '">' +
          '<div class="cover-mark">' + esc(mark(full.category)) + "</div>" +
          '<button class="modal-close" id="modalClose" aria-label="关闭">×</button>' +
        "</div>" +
        '<div class="modal-body">' +
          '<span class="badge-soft">' + esc(full.category || "治愈") + "</span>" +
          "<h2>" + esc(full.title) + "</h2>" +
          '<div class="modal-meta"><span>' + esc(full.author || full.source || "金润瀚宇") + "</span>" +
            "<span>" + fmtDate(full.published_at) + "</span>" +
            "<span>阅读约 " + readMin(full.content || full.summary) + " 分钟</span></div>" +
          '<div class="article">' + body + "</div>" +
          (tags ? '<div class="modal-tags">' + tags + "</div>" : "") +
        "</div>";
      $("#modal").classList.add("show");
      document.body.style.overflow = "hidden";
      document.getElementById("modalClose").addEventListener("click", closeModal);
    });
  }
  function closeModal() {
    $("#modal").classList.remove("show");
    document.body.style.overflow = "";
  }

  /* ---------------- 初始化 ---------------- */
  document.addEventListener("DOMContentLoaded", function () {
    // 统计信息
    getJSON(API + "/meta").then(function (m) {
      $("#statTotal").textContent = m.total || 0;
      $("#statUpdate").textContent = fmtDate(m.last_updated) || "—";
      var chips = $("#chips");
      (m.categories || []).forEach(function (c) {
        if (!c.name) return;
        var b = document.createElement("button");
        b.className = "chip";
        b.setAttribute("data-cat", c.name);
        b.textContent = c.name + " " + c.count;
        chips.appendChild(b);
      });
    }).catch(function () {
      // 离线时也用兜底数据算分类
      getJSON(FALLBACK).then(function (d) {
        var items = d.items || [];
        $("#statTotal").textContent = items.length;
        $("#statUpdate").textContent = fmtDate(items[0] && items[0].published_at) || "—";
        var seen = {};
        items.forEach(function (p) { seen[p.category] = (seen[p.category] || 0) + 1; });
        var chips = $("#chips");
        Object.keys(seen).forEach(function (k) {
          var b = document.createElement("button");
          b.className = "chip"; b.setAttribute("data-cat", k);
          b.textContent = k + " " + seen[k];
          chips.appendChild(b);
        });
      });
    });

    // 分类切换（事件委托）
    $("#chips").addEventListener("click", function (e) {
      var t = e.target.closest(".chip");
      if (!t) return;
      document.querySelectorAll(".chip").forEach(function (c) { c.classList.remove("active"); });
      t.classList.add("active");
      state.cat = t.getAttribute("data-cat");
      reload();
    });

    // 搜索
    var timer = null;
    $("#searchInput").addEventListener("input", function (e) {
      clearTimeout(timer);
      var v = e.target.value.trim();
      timer = setTimeout(function () { state.q = v; reload(); }, 380);
    });

    // 打开详情
    $("#featureSlot").addEventListener("click", function (e) {
      var a = e.target.closest(".healing-feature");
      if (a) openPost(a.getAttribute("data-id"));
    });
    $("#grid").addEventListener("click", function (e) {
      var a = e.target.closest(".healing-card");
      if (a) openPost(a.getAttribute("data-id"));
    });

    // 关闭弹层
    $("#modal").addEventListener("click", function (e) {
      if (e.target === $("#modal")) closeModal();
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") closeModal();
    });

    $("#moreBtn").addEventListener("click", loadMore);

    reload();
  });
})();
