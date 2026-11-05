(function () {
  "use strict";

  var state = { files: [], uploading: false, info: null };

  var el = {
    deviceInfo: document.getElementById("deviceInfo"),
    storageInfo: document.getElementById("storageInfo"),
    dropzone: document.getElementById("dropzone"),
    fileInput: document.getElementById("fileInput"),
    uploadPanel: document.getElementById("uploadPanel"),
    uploadList: document.getElementById("uploadList"),
    fileList: document.getElementById("fileList"),
    fileCount: document.getElementById("fileCount"),
    emptyState: document.getElementById("emptyState"),
    search: document.getElementById("search"),
    refreshBtn: document.getElementById("refreshBtn"),
    connectCard: document.getElementById("connectCard"),
    shareUrl: document.getElementById("shareUrl"),
    qrWrap: document.getElementById("qrWrap"),
    qr: document.getElementById("qr"),
    toggleQr: document.getElementById("toggleQr"),
    copyBtn: document.getElementById("copyBtn"),
    toast: document.getElementById("toast")
  };

  var ICON_DOWNLOAD =
    '<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" ' +
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M12 4v11"/><path d="m7.5 10.5 4.5 4.5 4.5-4.5"/><path d="M4 19h16"/></svg>';
  var ICON_TRASH =
    '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" ' +
    'stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M4 7h16"/><path d="M9 7V5h6v2"/><path d="M6 7l1 12h10l1-12"/></svg>';

  var KIND_LABEL = {
    image: "IMG", video: "VID", audio: "AUD", doc: "DOC",
    archive: "ZIP", app: "APP", other: "FILE"
  };

  function formatSize(bytes) {
    if (!bytes) return "0 B";
    var units = ["B", "KB", "MB", "GB", "TB"];
    var i = 0;
    var n = bytes;
    while (n >= 1024 && i < units.length - 1) {
      n /= 1024;
      i++;
    }
    return (i === 0 ? Math.round(n) : n.toFixed(1)) + " " + units[i];
  }

  function formatTime(ts) {
    var d = new Date(ts * 1000);
    var now = new Date();
    var pad = function (v) { return v < 10 ? "0" + v : "" + v; };
    var hm = pad(d.getHours()) + ":" + pad(d.getMinutes());
    if (d.toDateString() === now.toDateString()) return "今天 " + hm;
    return (d.getMonth() + 1) + "月" + d.getDate() + "日 " + hm;
  }

  function toast(message, isError) {
    el.toast.textContent = message;
    el.toast.className = "toast show" + (isError ? " error" : "");
    clearTimeout(toast.timer);
    toast.timer = setTimeout(function () {
      el.toast.className = "toast";
    }, isError ? 3000 : 1900);
  }

  // --------------------------- 数据 --------------------------- //
  function loadInfo() {
    return fetch("/api/info", { cache: "no-store" })
      .then(function (r) {
        if (r.status === 403) throw new Error("unauthorized");
        return r.json();
      })
      .then(function (info) {
        state.info = info;
        var portTip = info.port_changed
          ? " · 已从 " + info.port_requested + " 自动换到 " + info.port
          : "";
        el.deviceInfo.textContent = info.host + " · " + info.ip + ":" + info.port + portTip;
        el.storageInfo.textContent = "接收目录：" + (info.storage || info.receive_dir || "—");
        el.shareUrl.textContent = info.share_url;
        if (info.share_url) {
          renderQr(info.share_url);
          el.connectCard.hidden = false;
        }
      })
      .catch(function (err) {
        el.deviceInfo.textContent = err && err.message === "unauthorized"
          ? "访问口令无效，请重新扫描电脑端二维码"
          : "连接服务失败";
      });
  }

  function renderQr(url) {
    try {
      var qr = qrcode(0, "M");
      qr.addData(url);
      qr.make();
      el.qr.innerHTML = qr.createSvgTag(4, 2, "手机扫码访问");
    } catch (err) {
      el.qr.textContent = "二维码生成失败，请手动输入地址";
    }
  }

  function loadFiles(silent) {
    return fetch("/api/files", { cache: "no-store" })
      .then(function (r) {
        if (r.status === 403) throw new Error("口令已失效，请重新扫描电脑端二维码");
        return r.json();
      })
      .then(function (data) {
        state.files = data.files || [];
        renderFiles();
      })
      .catch(function (err) {
        if (!silent) toast((err && err.message) || "获取文件列表失败", true);
      });
  }

  // --------------------------- 渲染 --------------------------- //
  function renderFiles() {
    var keyword = el.search.value.trim().toLowerCase();
    var files = state.files.filter(function (f) {
      return !keyword || f.name.toLowerCase().indexOf(keyword) !== -1;
    });

    el.fileCount.textContent = state.files.length;
    el.fileList.innerHTML = "";

    if (!files.length) {
      el.emptyState.hidden = false;
      el.emptyState.querySelector("p").textContent =
        keyword ? "没有匹配的文件" : "还没有文件";
      return;
    }
    el.emptyState.hidden = true;

    var frag = document.createDocumentFragment();
    files.forEach(function (f, index) {
      frag.appendChild(buildFileRow(f, index));
    });
    el.fileList.appendChild(frag);
  }

  function buildFileRow(f, index) {
    var row = document.createElement("div");
    row.className = "file";
    row.style.animationDelay = Math.min(index * 20, 200) + "ms";

    var encoded = encodeURIComponent(f.name);

    var thumb = document.createElement("div");
    thumb.className = "thumb t-" + f.kind;
    if (f.kind === "image") {
      var img = document.createElement("img");
      img.loading = "lazy";
      img.alt = "";
      img.src = "/download?inline=1&name=" + encoded;
      img.addEventListener("error", function () {
        img.remove();
        thumb.textContent = "IMG";
      });
      thumb.appendChild(img);
    } else {
      thumb.textContent = KIND_LABEL[f.kind] || "FILE";
    }

    var main = document.createElement("div");
    main.className = "file-main";

    var nameLink = document.createElement("a");
    nameLink.className = "file-name";
    nameLink.href = "/download?inline=1&name=" + encoded;
    nameLink.target = "_blank";
    nameLink.rel = "noopener";
    nameLink.title = f.name;
    nameLink.textContent = f.name;

    var meta = document.createElement("div");
    meta.className = "file-meta";
    meta.textContent = formatSize(f.size) + " · " + formatTime(f.mtime);

    main.appendChild(nameLink);
    main.appendChild(meta);

    var actions = document.createElement("div");
    actions.className = "file-actions";

    var dl = document.createElement("a");
    dl.className = "dl";
    dl.href = "/download?name=" + encoded;
    dl.title = "下载";
    dl.setAttribute("aria-label", "下载 " + f.name);
    dl.innerHTML = ICON_DOWNLOAD;

    var del = document.createElement("button");
    del.className = "del";
    del.type = "button";
    del.title = "删除";
    del.setAttribute("aria-label", "删除 " + f.name);
    del.innerHTML = ICON_TRASH;
    del.addEventListener("click", function () {
      if (!window.confirm("确定删除《" + f.name + "》吗？")) return;
      fetch("/api/files?name=" + encoded, { method: "DELETE" })
        .then(function (r) { return r.json(); })
        .then(function (res) {
          if (res.error) {
            toast(res.error, true);
            return;
          }
          toast("已删除");
          loadFiles(true);
        })
        .catch(function () { toast("删除失败", true); });
    });

    actions.appendChild(dl);
    actions.appendChild(del);
    row.appendChild(thumb);
    row.appendChild(main);
    row.appendChild(actions);
    return row;
  }

  // --------------------------- 上传 --------------------------- //
  function createUploadItem(file) {
    var item = document.createElement("div");
    item.className = "up-item";

    var head = document.createElement("div");
    head.className = "up-head";
    var name = document.createElement("span");
    name.className = "up-name";
    name.textContent = file.name;
    var pct = document.createElement("span");
    pct.className = "up-pct";
    pct.textContent = "0%";
    head.appendChild(name);
    head.appendChild(pct);

    var bar = document.createElement("div");
    bar.className = "bar";
    var fill = document.createElement("i");
    bar.appendChild(fill);

    var meta = document.createElement("div");
    meta.className = "up-meta";
    meta.textContent = formatSize(file.size) + " · 等待中";

    item.appendChild(head);
    item.appendChild(bar);
    item.appendChild(meta);
    el.uploadList.appendChild(item);
    return item;
  }

  function markFailed(item, message, file) {
    item.classList.add("failed");
    item.querySelector(".up-pct").textContent = "失败";
    item.querySelector(".up-meta").textContent = message;

    var head = item.querySelector(".up-head");
    if (head.querySelector(".retry")) return;
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "retry";
    btn.textContent = "重试";
    btn.addEventListener("click", function () {
      uploadOne(file, item);
    });
    head.appendChild(btn);
  }

  function uploadOne(file, item) {
    item = item || createUploadItem(file);
    var head = item.querySelector(".up-head");
    var pct = item.querySelector(".up-pct");
    var fill = item.querySelector(".bar > i");
    var meta = item.querySelector(".up-meta");
    var name = item.querySelector(".up-name");

    var oldRetry = head.querySelector(".retry");
    if (oldRetry) oldRetry.remove();
    item.classList.remove("done", "failed");
    pct.textContent = "0%";
    fill.style.width = "0%";
    meta.textContent = formatSize(file.size) + " · 等待中";
    el.uploadPanel.hidden = false;

    return new Promise(function (resolve) {
      var xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/upload?name=" + encodeURIComponent(file.name));
      xhr.setRequestHeader("Content-Type", "application/octet-stream");

      xhr.upload.onprogress = function (e) {
        if (!e.lengthComputable) return;
        var percent = Math.min(100, Math.round((e.loaded / e.total) * 100));
        pct.textContent = percent + "%";
        fill.style.width = percent + "%";
        meta.textContent = formatSize(e.loaded) + " / " + formatSize(e.total) + " · 上传中";
      };

      xhr.onload = function () {
        var data = null;
        try { data = JSON.parse(xhr.responseText); } catch (err) { /* ignore */ }
        if (xhr.status === 200 && data && data.ok) {
          item.classList.add("done");
          pct.textContent = "完成";
          fill.style.width = "100%";
          meta.textContent = formatSize(data.size) + " · 已保存";
          if (data.name !== file.name) {
            name.textContent = file.name + " → " + data.name;
          }
          loadFiles(true);
          resolve(true);
        } else {
          markFailed(item, (data && data.error) ? data.error : "上传失败，点重试", file);
          resolve(false);
        }
      };

      xhr.onerror = function () {
        markFailed(item, "网络中断，请检查 Wi-Fi 后点重试", file);
        resolve(false);
      };

      xhr.send(file);
    });
  }

  function uploadFiles(fileList) {
    var files = Array.prototype.slice.call(fileList || []);
    if (!files.length) return;
    if (state.uploading) {
      toast("正在传输，请稍候再试", true);
      return;
    }

    state.uploading = true;
    el.uploadPanel.hidden = false;
    el.uploadList.innerHTML = "";

    var queue = Promise.resolve();
    var success = 0;
    files.forEach(function (file) {
      queue = queue.then(function () {
        return uploadOne(file).then(function (ok) {
          if (ok) success++;
        });
      });
    });

    queue.then(function () {
      state.uploading = false;
      el.fileInput.value = "";
      loadFiles(true);
      if (success === files.length) {
        toast("已发送 " + success + " 个文件");
      } else {
        toast("成功 " + success + " / " + files.length + " 个文件", true);
      }
      setTimeout(function () {
        if (!state.uploading && el.uploadList.querySelectorAll(".up-item").length) {
          el.uploadPanel.hidden = true;
          el.uploadList.innerHTML = "";
        }
      }, 4000);
    });
  }

  // --------------------------- 交互 --------------------------- //
  function bindEvents() {
    el.dropzone.addEventListener("click", function () {
      el.fileInput.click();
    });
    el.fileInput.addEventListener("click", function (e) { e.stopPropagation(); });
    el.fileInput.addEventListener("change", function () {
      uploadFiles(el.fileInput.files);
    });

    el.dropzone.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        el.fileInput.click();
      }
    });

    ["dragenter", "dragover"].forEach(function (type) {
      document.addEventListener(type, function (e) {
        e.preventDefault();
        el.dropzone.classList.add("dragging");
      });
    });
    ["dragleave", "drop"].forEach(function (type) {
      document.addEventListener(type, function (e) {
        e.preventDefault();
        if (type === "dragleave" && e.relatedTarget) return;
        el.dropzone.classList.remove("dragging");
      });
    });
    document.addEventListener("drop", function (e) {
      e.preventDefault();
      if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) {
        uploadFiles(e.dataTransfer.files);
      }
    });

    el.refreshBtn.addEventListener("click", function () {
      el.refreshBtn.classList.add("spinning");
      Promise.all([loadInfo(), loadFiles(false)]).then(function () {
        setTimeout(function () { el.refreshBtn.classList.remove("spinning"); }, 400);
      });
    });

    el.search.addEventListener("input", renderFiles);

    el.toggleQr.addEventListener("click", function () {
      var hidden = el.qrWrap.hidden;
      el.qrWrap.hidden = !hidden;
      el.toggleQr.textContent = hidden ? "收起" : "展开";
    });

    el.copyBtn.addEventListener("click", function () {
      var url = (state.info && state.info.share_url) || el.shareUrl.textContent;
      var done = function () { toast("链接已复制"); };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(url).then(done).catch(function () { fallbackCopy(url, done); });
      } else {
        fallbackCopy(url, done);
      }
    });

    document.addEventListener("visibilitychange", function () {
      if (!document.hidden && !state.uploading) loadFiles(true);
    });
  }

  function fallbackCopy(text, done) {
    var input = document.createElement("textarea");
    input.value = text;
    input.style.position = "fixed";
    input.style.opacity = "0";
    document.body.appendChild(input);
    input.select();
    try { document.execCommand("copy"); done(); } catch (err) { toast("复制失败，请手动输入", true); }
    document.body.removeChild(input);
  }

  function init() {
    bindEvents();
    var isDesktop = window.matchMedia("(min-width: 760px)").matches;
    if (isDesktop) {
      el.qrWrap.hidden = false;
      el.toggleQr.textContent = "收起";
    }
    loadInfo();
    loadFiles(true);
    setInterval(function () {
      if (!document.hidden && !state.uploading) loadFiles(true);
    }, 8000);
  }

  init();
})();
