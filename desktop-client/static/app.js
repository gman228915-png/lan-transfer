(function () {
  "use strict";

  var state = { files: [], uploading: false, info: null, space: "", downloads: {} };
  var UPLOAD_CONCURRENCY = 2;
  var DOWNLOAD_RETRIES = 3;

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
    spaceSelect: document.getElementById("spaceSelect"),
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
  function spaceQuery() {
    return state.space ? "&space=" + encodeURIComponent(state.space) : "";
  }

  function loadSpaces() {
    return fetch("/api/spaces", { cache: "no-store" })
      .then(function (r) {
        if (r.status === 403) throw new Error("unauthorized");
        return r.json();
      })
      .then(function (data) {
        var spaces = data.spaces || [];
        el.spaceSelect.innerHTML = "";
        var all = document.createElement("option");
        all.value = "";
        all.textContent = "全部空间";
        el.spaceSelect.appendChild(all);
        spaces.forEach(function (space) {
          var option = document.createElement("option");
          option.value = space.name;
          option.textContent = space.name + (space.mode === "ro" ? "（只读）" : "");
          el.spaceSelect.appendChild(option);
        });
        if (state.space && !spaces.some(function (s) { return s.name === state.space; })) {
          state.space = "";
        }
        el.spaceSelect.value = state.space;
        el.spaceSelect.disabled = spaces.length <= 1;
      })
      .catch(function () {
        el.spaceSelect.innerHTML = '<option value="">全部空间</option>';
        el.spaceSelect.disabled = true;
      });
  }

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
    return fetch("/api/files" + (state.space ? "?space=" + encodeURIComponent(state.space) : ""),
                 { cache: "no-store" })
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
    var sq = f.space ? "&space=" + encodeURIComponent(f.space) : spaceQuery();

    var thumb = document.createElement("div");
    thumb.className = "thumb t-" + f.kind;
    if (f.kind === "image") {
      var img = document.createElement("img");
      img.loading = "lazy";
      img.alt = "";
      img.src = "/download?inline=1&name=" + encoded + sq;
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
    nameLink.href = "/download?inline=1&name=" + encoded + sq;
    nameLink.target = "_blank";
    nameLink.rel = "noopener";
    nameLink.title = f.name;
    nameLink.textContent = f.name;

    var meta = document.createElement("div");
    meta.className = "file-meta";
    meta.dataset.base = formatSize(f.size) + " · " + formatTime(f.mtime) +
      (f.space && !state.space ? " · " + f.space : "");
    meta.textContent = meta.dataset.base;

    main.appendChild(nameLink);
    main.appendChild(meta);

    var actions = document.createElement("div");
    actions.className = "file-actions";

    var dl = document.createElement("a");
    dl.className = "dl";
    dl.href = "/download?name=" + encoded + sq;
    dl.title = "下载（支持断点续传）";
    dl.setAttribute("aria-label", "下载 " + f.name);
    dl.innerHTML = ICON_DOWNLOAD;
    dl.addEventListener("click", function (e) {
      // 用 fetch + Range 自己接管下载，失败后可从断点继续
      if (!window.fetch || !window.ReadableStream) return;
      e.preventDefault();
      downloadFile(f, meta, dl);
    });

    var del = document.createElement("button");
    del.className = "del";
    del.type = "button";
    del.title = "删除";
    del.setAttribute("aria-label", "删除 " + f.name);
    del.innerHTML = ICON_TRASH;
    del.addEventListener("click", function () {
      if (!window.confirm("确定删除《" + f.name + "》吗？")) return;
      fetch("/api/files?name=" + encoded + sq, { method: "DELETE" })
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

  // --------------------------- 下载（断点续传 + IndexedDB 持久化） --------------------------- //
  var idb = (function () {
    var DB_NAME = "lan-transfer", STORE = "downloads", dbPromise = null;
    function openDb() {
      if (dbPromise) return dbPromise;
      dbPromise = new Promise(function (resolve, reject) {
        if (!window.indexedDB) { reject(new Error("no indexedDB")); return; }
        var req = indexedDB.open(DB_NAME, 1);
        req.onupgradeneeded = function () {
          var db = req.result;
          if (!db.objectStoreNames.contains(STORE)) {
            db.createObjectStore(STORE, { keyPath: "key" });
          }
        };
        req.onsuccess = function () { resolve(req.result); };
        req.onerror = function () { reject(req.error || new Error("idb open failed")); };
      });
      return dbPromise;
    }
    function run(mode, fn) {
      return openDb().then(function (db) {
        return new Promise(function (resolve, reject) {
          var tx = db.transaction(STORE, mode);
          var store = tx.objectStore(STORE);
          var request = fn(store);
          tx.oncomplete = function () { resolve(request ? request.result : undefined); };
          tx.onerror = function () { reject(tx.error); };
          tx.onabort = function () { reject(tx.error); };
        });
      }).catch(function () { return null; });
    }
    return {
      get: function (key) { return run("readonly", function (s) { return s.get(key); }); },
      put: function (rec) { return run("readwrite", function (s) { return s.put(rec); }); },
      del: function (key) { return run("readwrite", function (s) { return s.delete(key); }); },
      prune: function (maxAgeMs) {
        return run("readwrite", function (s) {
          var cutoff = Date.now() - maxAgeMs;
          var req = s.openCursor();
          req.onsuccess = function () {
            var cursor = req.result;
            if (!cursor) return;
            var rec = cursor.value || {};
            if (!rec.updated || rec.updated < cutoff) cursor.delete();
            cursor.continue();
          };
          return null;
        });
      }
    };
  })();

  function downloadStateKey(f) {
    return (f.space || state.space || "") + "|" + f.name;
  }

  function downloadPersistKey(f) {
    return (f.space || state.space || "") + "|" + f.name + "|" + (f.size || 0);
  }

  function persistDownload(pkey, dl) {
    if (!dl || !dl.received) return;
    idb.put({
      key: pkey,
      space: dl.space,
      name: dl.name,
      size: dl.size,
      etag: dl.etag,
      total: dl.total,
      received: dl.received,
      chunks: dl.chunks.slice(),   // 快照，避免写入期间又 push 新分片
      updated: Date.now()
    });
  }

  function updateDownloadMeta(meta, dl, note) {
    var base = meta.dataset.base || "";
    var pct = dl.total ? Math.min(100, Math.round(dl.received * 100 / dl.total)) : 0;
    var speedText = dl.speed ? formatSize(dl.speed) + "/s" : "";
    var etaText = (dl.total && dl.speed > 0 && dl.received < dl.total)
      ? " · 剩余 " + Math.ceil((dl.total - dl.received) / dl.speed) + "s" : "";
    meta.textContent = base + " · " +
      (note || ("下载 " + pct + "% " + speedText + etaText));
  }

  function downloadFile(f, meta, trigger) {
    var key = downloadStateKey(f);
    var pkey = downloadPersistKey(f);
    var dl = state.downloads[key];
    if (dl && dl.running) {
      toast("正在下载《" + f.name + "》");
      return;
    }
    if (dl) {
      runDownload(f, meta, trigger, dl, pkey, key);
      return;
    }
    if (trigger) trigger.classList.add("busy");
    // 新开始的下载：先尝试从 IndexedDB 恢复上次（含刷新前的）断点
    idb.get(pkey).then(function (rec) {
      if (!rec || !rec.received || rec.size !== (f.size || 0)) return null;
      if (!rec.chunks || !rec.chunks.length) return null;
      // 校验分片总字节数，避免半截/损坏的记录拼出坏文件
      var sum = 0;
      for (var i = 0; i < rec.chunks.length; i++) {
        sum += rec.chunks[i].byteLength || rec.chunks[i].length || 0;
      }
      return sum === rec.received ? rec : null;
    }).then(function (rec) {
      var fresh = {
        chunks: [], received: 0, total: f.size || 0, etag: "", running: false,
        speed: 0, space: f.space || state.space || "", name: f.name,
        size: f.size || 0, saved: 0, savedAt: 0
      };
      if (rec) {
        fresh.chunks = rec.chunks || [];
        fresh.received = rec.received;
        fresh.etag = rec.etag || "";
        if (rec.total) fresh.total = rec.total;
        fresh.saved = fresh.received;
        toast("从上次断点继续：" + formatSize(fresh.received));
      }
      state.downloads[key] = fresh;
      runDownload(f, meta, trigger, fresh, pkey, key);
    });
  }

  function runDownload(f, meta, trigger, dl, pkey, key) {
    dl.running = true;
    if (trigger) trigger.classList.add("busy");
    var encoded = encodeURIComponent(f.name);
    var sq = "&space=" + encodeURIComponent(dl.space);
    var attempt = 0;
    var restarted = false;

    function step() {
      var headers = {};
      if (dl.received > 0) {
        headers.Range = "bytes=" + dl.received + "-";
        if (dl.etag) headers["If-Range"] = dl.etag;
        updateDownloadMeta(meta, dl, "从 " + formatSize(dl.received) + " 续传…");
      }
      fetch("/download?checksum=0&name=" + encoded + sq,
            { headers: headers, cache: "no-store" })
        .then(function (res) {
          if (res.status === 416 && dl.received > 0 && !restarted) {
            // 本地断点已超出服务器文件：丢弃后从头下载
            restarted = true;
            dl.chunks = [];
            dl.received = 0;
            dl.etag = "";
            idb.del(pkey);
            throw new Error("__restart__");
          }
          if (!res.ok) {
            throw new Error("HTTP " + res.status +
              (res.status === 401 || res.status === 403 ? "（口令无效）" : "") +
              (res.status === 429 ? "（服务端繁忙，请稍后）" : ""));
          }
          if (dl.received > 0 && res.status === 200) {
            // 服务器未接受 Range（ETag 已变化），丢弃旧分片从头接收
            dl.chunks = [];
            dl.received = 0;
            dl.etag = "";
            idb.del(pkey);
          }
          var etag = res.headers.get("ETag");
          if (etag) dl.etag = etag;
          var cr = res.headers.get("Content-Range");
          if (cr) {
            var total = parseInt(cr.split("/")[1], 10);
            if (total) dl.total = total;
          } else {
            var len = parseInt(res.headers.get("Content-Length") || "0", 10);
            if (len) dl.total = dl.received + len;
          }
          var reader = res.body.getReader();
          var started = performance.now();
          var startBytes = dl.received;
          var lastPaint = 0;
          dl.savedAt = started;
          function pump() {
            return reader.read().then(function (r) {
              if (r.done) return;
              dl.chunks.push(r.value);
              dl.received += r.value.length;
              var now = performance.now();
              var elapsed = (now - started) / 1000;
              if (elapsed > 0.2) {
                dl.speed = (dl.received - startBytes) / elapsed;
                if (now - lastPaint > 400) {
                  lastPaint = now;
                  updateDownloadMeta(meta, dl);
                }
              }
              // 定期把已收字节与 ETag 写入 IndexedDB，刷新/关页后可续传
              if (dl.received - dl.saved >= 2 * 1024 * 1024 ||
                  (dl.etag && now - dl.savedAt > 1500)) {
                dl.saved = dl.received;
                dl.savedAt = now;
                persistDownload(pkey, dl);
              }
              return pump();
            });
          }
          return pump();
        })
        .then(function () {
          if (dl.total && dl.received < dl.total) throw new Error("连接提前结束");
          var blob = new Blob(dl.chunks);
          var url = URL.createObjectURL(blob);
          var a = document.createElement("a");
          a.href = url;
          a.download = f.name;
          document.body.appendChild(a);
          a.click();
          a.remove();
          setTimeout(function () { URL.revokeObjectURL(url); }, 60000);
          idb.del(pkey);
          delete state.downloads[key];
          if (trigger) trigger.classList.remove("busy");
          meta.textContent = (meta.dataset.base || "") + " · 下载完成";
          toast("下载完成：" + f.name);
        })
        .catch(function (err) {
          if (err && err.message === "__restart__") {
            step();
            return;
          }
          attempt++;
          if (attempt < DOWNLOAD_RETRIES && dl.received > 0) {
            persistDownload(pkey, dl);
            updateDownloadMeta(meta, dl, "连接中断，第 " + attempt + " 次重试…");
            setTimeout(step, 600 + attempt * 800);
            return;
          }
          dl.running = false;
          if (trigger) trigger.classList.remove("busy");
          if (dl.received) persistDownload(pkey, dl);
          var reason = (err && err.message) || "网络错误";
          updateDownloadMeta(meta, dl, "下载中断，已保留 " +
            formatSize(dl.received) + "，刷新页面后点下载可继续");
          toast("下载中断：" + reason + "，点下载按钮可从断点继续", true);
        });
    }
    step();
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
      xhr.open("POST", "/api/upload?name=" + encodeURIComponent(file.name) + spaceQuery());
      xhr.setRequestHeader("Content-Type", "application/octet-stream");

      var started = 0;
      var lastTime = 0;
      var lastLoaded = 0;
      xhr.upload.onprogress = function (e) {
        if (!e.lengthComputable) return;
        var now = performance.now();
        if (!started) {
          started = now;
          lastTime = now;
          lastLoaded = e.loaded;
        }
        var percent = Math.min(100, Math.round((e.loaded / e.total) * 100));
        pct.textContent = percent + "%";
        fill.style.width = percent + "%";
        var speedText = "";
        var etaText = "";
        if (now - lastTime >= 500) {
          var speed = Math.max(0, (e.loaded - lastLoaded) /
            ((now - lastTime) / 1000));
          lastTime = now;
          lastLoaded = e.loaded;
          speedText = " · " + formatSize(speed) + "/s";
          if (speed > 0 && e.loaded < e.total) {
            etaText = " · 剩余 " + Math.ceil((e.total - e.loaded) / speed) + "s";
          }
        }
        meta.textContent = formatSize(e.loaded) + " / " + formatSize(e.total) +
          " · 上传中" + speedText + etaText;
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

    // 并发上限固定为 2，避免一次选几十个文件把手机和服务端拖死
    var success = 0;
    var next = 0;
    function worker() {
      if (next >= files.length) return Promise.resolve();
      var file = files[next++];
      return uploadOne(file).then(function (ok) {
        if (ok) success++;
      }).then(worker);
    }
    var workers = [];
    for (var i = 0; i < Math.min(UPLOAD_CONCURRENCY, files.length); i++) {
      workers.push(worker());
    }
    var queue = Promise.all(workers);
    if (files.length > UPLOAD_CONCURRENCY) {
      toast("已加入 " + files.length + " 个文件，最多同时上传 " +
        UPLOAD_CONCURRENCY + " 个");
    }

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
      Promise.all([loadSpaces(), loadInfo(), loadFiles(false)]).then(function () {
        setTimeout(function () { el.refreshBtn.classList.remove("spinning"); }, 400);
      });
    });

    el.search.addEventListener("input", renderFiles);

    el.spaceSelect.addEventListener("change", function () {
      state.space = el.spaceSelect.value;
      loadFiles(false);
    });

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
    idb.prune(3 * 24 * 60 * 60 * 1000); // 清理 3 天前的下载断点记录
    bindEvents();
    var isDesktop = window.matchMedia("(min-width: 760px)").matches;
    if (isDesktop) {
      el.qrWrap.hidden = false;
      el.toggleQr.textContent = "收起";
    }
    loadSpaces();
    loadInfo();
    loadFiles(true);
    setInterval(function () {
      if (!document.hidden && !state.uploading) loadFiles(true);
    }, 8000);
  }

  init();
})();
