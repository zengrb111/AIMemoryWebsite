/* =========================================================
   贴心工具 · 老照片修复前端
   后端：POST /api/tools/restore -> {task_id}，轮询 GET /api/tools/task/{id}
   ========================================================= */
(function () {
  var API = "/api/tools";
  var MAX_MB = 15;

  var file = null, mode = "standard", taskId = null, timer = null;

  var $ = function (s) { return document.querySelector(s); };
  var esc = function (s) {
    return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  };
  function human(b) {
    if (b < 1024) return b + " B";
    if (b < 1048576) return (b / 1024).toFixed(0) + " KB";
    return (b / 1048576).toFixed(1) + " MB";
  }

  /* 阶段 -> 进度估算 */
  var STAGE_PCT = [
    ["读取", 10], ["预处理", 12], ["人脸检测", 30], ["人脸修复", 55],
    ["超分", 78], ["降噪", 80], ["画质增强", 92], ["输出", 97]
  ];
  function stagePct(stage) {
    if (!stage) return 5;
    for (var i = 0; i < STAGE_PCT.length; i++) {
      if (stage.indexOf(STAGE_PCT[i][0]) >= 0) return STAGE_PCT[i][1];
    }
    return 20;
  }

  /* ---------------- 文件选择 ---------------- */
  function pick(f) {
    if (!f) return;
    if (!/^image\/(jpeg|png|webp)$/.test(f.type)) {
      alert("请上传 JPG / PNG / WEBP 格式的图片");
      return;
    }
    if (f.size > MAX_MB * 1048576) {
      alert("图片请小于 " + MAX_MB + "MB，可先用画图工具另存为 JPG 再试");
      return;
    }
    file = f;
    var url = URL.createObjectURL(f);
    $("#srcImg").src = url;
    $("#previewWrap").style.display = "block";
    $("#resultCard").style.display = "none";
    $("#progressWrap").style.display = "none";

    var img = new Image();
    img.onload = function () {
      $("#srcSize").textContent = img.naturalWidth + " × " + img.naturalHeight;
      $("#srcBytes").textContent = human(f.size);
      if (img.naturalWidth < 120 || img.naturalHeight < 120) {
        $("#startHint").textContent = "图片太小，修复效果有限";
      }
    };
    img.src = url;

    $("#startBtn").disabled = false;
    $("#startHint").textContent = "已就绪，可以开始了";
  }

  /* ---------------- 提交任务 ---------------- */
  function start() {
    if (!file) return;
    $("#startBtn").disabled = true;
    $("#startHint").textContent = "正在上传…";
    $("#resultCard").style.display = "none";
    $("#progressWrap").style.display = "block";
    setProgress(3, "上传照片…");
    $("#stepList").innerHTML = "";

    var fd = new FormData();
    fd.append("file", file);
    fd.append("mode", mode);

    fetch(API + "/restore", { method: "POST", body: fd })
      .then(function (r) {
        return r.json().then(function (d) {
          if (!r.ok) throw new Error(d.detail || ("HTTP " + r.status));
          return d;
        });
      })
      .then(function (d) {
        taskId = d.task_id;
        setProgress(6, d.position > 1 ? "排队中（前面还有 " + (d.position - 1) + " 个任务）" : "排队中…");
        poll();
      })
      .catch(function (e) {
        fail(e.message || "提交失败，请稍后重试");
      });
  }

  function setProgress(pct, stage) {
    $("#progressBar").style.width = pct + "%";
    $("#pctText").textContent = pct + "%";
    if (stage) $("#stageText").textContent = stage;
  }

  var lastStage = "";
  function markStep(stage) {
    if (!stage || stage === lastStage) return;
    lastStage = stage;
    var li = document.createElement("li");
    li.className = "done";
    li.innerHTML = '<span class="dot2"></span><span>' + esc(stage) + "</span>";
    $("#stepList").appendChild(li);
  }

  function poll() {
    clearTimeout(timer);
    fetch(API + "/task/" + encodeURIComponent(taskId))
      .then(function (r) { return r.json(); })
      .then(function (t) {
        if (t.status === "queued") {
          setProgress(6, "排队中…");
          timer = setTimeout(poll, 1500);
          return;
        }
        if (t.status === "running") {
          var pct = stagePct(t.stage);
          setProgress(pct, t.stage || "处理中…");
          markStep(t.stage);
          timer = setTimeout(poll, 1200);
          return;
        }
        if (t.status === "done") {
          setProgress(100, "修复完成");
          markStep("修复完成");
          showResult(t);
          return;
        }
        if (t.status === "failed") {
          fail(t.error || "修复失败");
          return;
        }
        timer = setTimeout(poll, 1500);
      })
      .catch(function () { timer = setTimeout(poll, 2000); });
  }

  function fail(msg) {
    clearTimeout(timer);
    setProgress(0, "失败");
    $("#stageText").innerHTML = '<span style="color:var(--rose)">' + esc(msg) + "</span>";
    $("#startBtn").disabled = false;
    $("#startHint").textContent = "可以重试或换一张照片";
  }

  /* ---------------- 结果 ---------------- */
  function showResult(t) {
    $("#startBtn").disabled = false;
    $("#startHint").textContent = "已完成，可再修一张";
    var before = t.preview_before, after = t.preview_after;
    var m = t.meta || {};
    $("#beforeImg").src = before;
    $("#afterImg").src = after;
    $("#downloadBtn").href = t.result_url;
    $("#outMeta").innerHTML =
      "<div>输出尺寸 <b>" + (m.width || "—") + " × " + (m.height || "—") + "</b></div>" +
      "<div>修复人脸 <b>" + (m.faces || 0) + " 张</b></div>" +
      "<div>耗时 <b>" + (m.elapsed != null ? m.elapsed + " 秒" : "—") + "</b></div>";
    $("#resultCard").style.display = "block";
    resetSlider();
    $("#resultCard").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  /* ---------------- 前后对比滑块 ---------------- */
  var dragging = false;
  function setSplit(clientX) {
    var box = $("#compare");
    if (!box) return;
    var r = box.getBoundingClientRect();
    var x = Math.max(0, Math.min(r.width, clientX - r.left));
    $("#afterWrap").style.width = x + "px";
    $("#handle").style.left = x + "px";
  }
  function resetSlider() {
    var box = $("#compare");
    if (!box) return;
    var w = box.getBoundingClientRect().width || box.offsetWidth;
    $("#afterWrap").style.width = (w / 2) + "px";
    $("#handle").style.left = (w / 2) + "px";
  }

  document.addEventListener("DOMContentLoaded", function () {
    $("#fileInput").addEventListener("change", function (e) { pick(e.target.files[0]); });

    var dz = $("#dropzone");
    ["dragenter", "dragover"].forEach(function (ev) {
      dz.addEventListener(ev, function (e) { e.preventDefault(); dz.classList.add("drag"); });
    });
    ["dragleave", "drop"].forEach(function (ev) {
      dz.addEventListener(ev, function (e) { e.preventDefault(); dz.classList.remove("drag"); });
    });
    dz.addEventListener("drop", function (e) {
      if (e.dataTransfer && e.dataTransfer.files.length) pick(e.dataTransfer.files[0]);
    });

    // 档位切换
    $("#modePicker").addEventListener("click", function (e) {
      var o = e.target.closest(".mode-opt");
      if (!o) return;
      document.querySelectorAll(".mode-opt").forEach(function (x) { x.classList.remove("sel"); });
      o.classList.add("sel");
      mode = o.getAttribute("data-mode");
    });

    $("#startBtn").addEventListener("click", start);
    $("#resetBtn").addEventListener("click", function () {
      file = null; taskId = null; lastStage = "";
      $("#fileInput").value = "";
      $("#previewWrap").style.display = "none";
      $("#resultCard").style.display = "none";
      $("#progressWrap").style.display = "none";
      $("#startBtn").disabled = true;
      $("#startHint").textContent = "请先上传照片";
      window.scrollTo({ top: $("#uploadCard").offsetTop - 100, behavior: "smooth" });
    });
    $("#againBtn").addEventListener("click", function () {
      $("#resetBtn").click();
    });

    // 滑块拖动
    var box = $("#compare");
    box.addEventListener("mousedown", function (e) { dragging = true; setSplit(e.clientX); });
    box.addEventListener("touchstart", function (e) { dragging = true; setSplit(e.touches[0].clientX); }, { passive: true });
    window.addEventListener("mousemove", function (e) { if (dragging) setSplit(e.clientX); });
    window.addEventListener("touchmove", function (e) {
      if (dragging) setSplit(e.touches[0].clientX);
    }, { passive: true });
    window.addEventListener("mouseup", function () { dragging = false; });
    window.addEventListener("touchend", function () { dragging = false; });
    window.addEventListener("resize", function () {
      if ($("#resultCard").style.display !== "none") resetSlider();
    });
    $("#afterImg").addEventListener("load", resetSlider);
  });
})();
