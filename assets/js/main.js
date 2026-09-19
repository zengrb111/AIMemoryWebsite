// AI回忆录官网 · 交互脚本
(function () {
  // 移动端导航
  var toggle = document.querySelector(".nav-toggle");
  var links = document.querySelector(".nav-links");
  if (toggle && links) {
    toggle.addEventListener("click", function () {
      links.classList.toggle("open");
    });
    links.querySelectorAll("a").forEach(function (a) {
      a.addEventListener("click", function () { links.classList.remove("open"); });
    });
  }

  // 返回顶部
  var toTop = document.querySelector(".to-top");
  if (toTop) {
    window.addEventListener("scroll", function () {
      if (window.scrollY > 500) toTop.classList.add("show");
      else toTop.classList.remove("show");
    });
    toTop.addEventListener("click", function () {
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
  }

  // 联系表单（前端校验 + 感谢提示，提交逻辑需后端对接）
  var form = document.getElementById("contactForm");
  if (form) {
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var ok = document.querySelector(".form-ok");
      var name = form.querySelector("#cName").value.trim();
      var email = form.querySelector("#cEmail").value.trim();
      if (!name || !email) {
        alert("请填写称呼与联系方式");
        return;
      }
      // TODO: 接入后端 / 邮件 / 工单系统后替换此提示
      ok.style.display = "block";
      form.reset();
      ok.scrollIntoView({ behavior: "smooth", block: "center" });
    });
  }

  // 文档页 TOC 高亮
  var toc = document.querySelector(".doc-toc");
  if (toc) {
    var heads = Array.prototype.slice.call(document.querySelectorAll(".doc-content h2[id]"));
    var tocLinks = Array.prototype.slice.call(toc.querySelectorAll("a"));
    if (heads.length && "IntersectionObserver" in window) {
      var obs = new IntersectionObserver(function (entries) {
        entries.forEach(function (en) {
          if (en.isIntersecting) {
            var id = en.target.id;
            tocLinks.forEach(function (l) {
              l.style.background = l.getAttribute("href") === "#" + id ? "var(--gold-soft)" : "";
              l.style.color = l.getAttribute("href") === "#" + id ? "var(--ink)" : "";
            });
          }
        });
      }, { rootMargin: "-80px 0px -70% 0px" });
      heads.forEach(function (h) { obs.observe(h); });
    }
  }
})();
