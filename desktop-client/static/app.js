(function () {
  "use strict";

  var state = { files: [], uploading: false, info: null, space: "", downloads: {} };
  var UPLOAD_CONCURRENCY = 2;
  var DOWNLOAD_RETRIES = 3;

  function t(key, vars) {
    return window.LT ? window.LT.t(key, vars) : key;
  }

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
    heroToggle: document.getElementById("heroToggle"),
    heroBody: document.getElementById("heroBody"),
    filesToggle: document.getElementById("filesToggle"),
    filesBody: document.getElementById("filesBody"),
    connectToggle: document.getElementById("connectToggle"),
    connectBody: document.getElementById("connectBody"),
    bgLayer: document.getElementById("bgLayer"),
    bgBtn: document.getElementById("bgBtn"),
    bgSheet: document.getElementById("bgSheet"),
    bgPick: document.getElementById("bgPick"),
    bgReset: document.getElementById("bgReset"),
    bgInput: document.getElementById("bgInput"),
    bgMode: document.getElementById("bgMode"),
    bgDim: document.getElementById("bgDim"),
    bgDimVal: document.getElementById("bgDimVal"),
    bgOpacity: document.getElementById("bgOpacity"),
    bgOpacityVal: document.getElementById("bgOpacityVal"),
    topOpacity: document.getElementById("topOpacity"),
    topOpacityVal: document.getElementById("topOpacityVal"),
    bgFileName: document.getElementById("bgFileName"),
    bgClose: document.getElementById("bgClose"),
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

  function localeTag() {
    return window.LT ? window.LT.htmlLang() : undefined;
  }

  function formatTime(ts) {
    var d = new Date(ts * 1000);
    var tag = localeTag();
    var time = d.toLocaleTimeString(tag, { hour: "2-digit", minute: "2-digit" });
    if (d.toDateString() === new Date().toDateString()) return time;
    return d.toLocaleDateString(tag, { month: "short", day: "numeric" }) + " " + time;
  }

  function toast(message, isError) {
    el.toast.textContent = message;
    el.toast.className = "toast show" + (isError ? " error" : "");
    clearTimeout(toast.timer);
    toast.timer = setTimeout(function () {
      el.toast.className = "toast";
    }, isError ? 3000 : 1900);
  }

  // --------------------------- 折叠 --------------------------- //
  var COLLAPSE_KEY = "lan-transfer-collapse";
  var collapseState = { hero: false, files: false, connect: false };

  function readCollapse() {
    try {
      var raw = localStorage.getItem(COLLAPSE_KEY);
      if (!raw) return;
      var parsed = JSON.parse(raw) || {};
      Object.keys(collapseState).forEach(function (key) {
        collapseState[key] = !!parsed[key];
      });
    } catch (err) { /* 忽略损坏的本地设置 */ }
  }

  function saveCollapse() {
    try {
      localStorage.setItem(COLLAPSE_KEY, JSON.stringify(collapseState));
    } catch (err) { /* 忽略 */ }
  }

  function collapseParts(key) {
    if (key === "hero") return [el.heroToggle, el.heroBody];
    if (key === "files") return [el.filesToggle, el.filesBody];
    return [el.connectToggle, el.connectBody];
  }

  function paintCollapseButton(key) {
    var btn = collapseParts(key)[0];
    if (!btn) return;
    var collapsed = collapseState[key];
    btn.innerHTML = collapsed ? "\u2304" : "\u2303";
    btn.setAttribute("aria-expanded", collapsed ? "false" : "true");
    btn.title = t(collapsed ? "expand" : "collapse");
    btn.setAttribute("aria-label", btn.title);
  }

  function applyCollapse(key, persist) {
    var parts = collapseParts(key);
    var body = parts[1];
    if (!body) return;
    body.hidden = collapseState[key];
    paintCollapseButton(key);
    if (persist) saveCollapse();
  }

  function toggleCollapse(key) {
    collapseState[key] = !collapseState[key];
    applyCollapse(key, true);
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
        all.textContent = t("space_all");
        el.spaceSelect.appendChild(all);
        spaces.forEach(function (space) {
          var option = document.createElement("option");
          option.value = space.name;
          option.textContent = space.name + (space.mode === "ro" ? t("space_ro") : "");
          el.spaceSelect.appendChild(option);
        });
        if (state.space && !spaces.some(function (s) { return s.name === state.space; })) {
          state.space = "";
        }
        el.spaceSelect.value = state.space;
        el.spaceSelect.disabled = spaces.length <= 1;
      })
      .catch(function () {
        el.spaceSelect.innerHTML = "";
        var fallback = document.createElement("option");
        fallback.value = "";
        fallback.textContent = t("space_all");
        el.spaceSelect.appendChild(fallback);
        el.spaceSelect.disabled = true;
      });
  }

  function applyInfo(info) {
    var portTip = info.port_changed
      ? t("port_hint", { requested: info.port_requested, port: info.port })
      : "";
    el.deviceInfo.textContent = info.host + " · " + info.ip + ":" + info.port + portTip;
    el.storageInfo.textContent = t("storage_prefix") + (info.storage || info.receive_dir || "—");
    el.shareUrl.textContent = info.share_url;
  }

  function loadInfo() {
    return fetch("/api/info", { cache: "no-store" })
      .then(function (r) {
        if (r.status === 403) throw new Error("unauthorized");
        return r.json();
      })
      .then(function (info) {
        state.info = info;
        applyInfo(info);
        if (info.share_url) {
          renderQr(info.share_url);
          el.connectCard.hidden = false;
          applyCollapse("connect", false);
        }
      })
      .catch(function (err) {
        el.deviceInfo.textContent = err && err.message === "unauthorized"
          ? t("token_invalid")
          : t("connect_failed");
      });
  }

  function renderQr(url) {
    try {
      var qr = qrcode(0, "M");
      qr.addData(url);
      qr.make();
      el.qr.innerHTML = qr.createSvgTag(4, 2, t("qr_alt"));
    } catch (err) {
      el.qr.textContent = t("qr_fail");
    }
  }

  function loadFiles(silent) {
    return fetch("/api/files" + (state.space ? "?space=" + encodeURIComponent(state.space) : ""),
                 { cache: "no-store" })
      .then(function (r) {
        if (r.status === 403) throw new Error(t("token_expired"));
        return r.json();
      })
      .then(function (data) {
        state.files = data.files || [];
        renderFiles();
      })
      .catch(function (err) {
        if (!silent) toast((err && err.message) || t("files_fail"), true);
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
        keyword ? t("empty_no_match") : t("empty_none");
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
    dl.title = t("download_title");
    dl.setAttribute("aria-label", t("download_aria", { name: f.name }));
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
    del.title = t("delete_title");
    del.setAttribute("aria-label", t("delete_aria", { name: f.name }));
    del.innerHTML = ICON_TRASH;
    del.addEventListener("click", function () {
      if (!window.confirm(t("confirm_delete", { name: f.name }))) return;
      fetch("/api/files?name=" + encoded + sq, { method: "DELETE" })
        .then(function (r) { return r.json(); })
        .then(function (res) {
          if (res.error) {
            toast(res.error, true);
            return;
          }
          toast(t("deleted"));
          loadFiles(true);
        })
        .catch(function () { toast(t("delete_fail"), true); });
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
    var DB_NAME = "lan-transfer", STORE = "downloads", MEDIA = "media", dbPromise = null;
    function openDb() {
      if (dbPromise) return dbPromise;
      dbPromise = new Promise(function (resolve, reject) {
        if (!window.indexedDB) { reject(new Error("no indexedDB")); return; }
        var req = indexedDB.open(DB_NAME, 2);
        req.onupgradeneeded = function () {
          var db = req.result;
          if (!db.objectStoreNames.contains(STORE)) {
            db.createObjectStore(STORE, { keyPath: "key" });
          }
          if (!db.objectStoreNames.contains(MEDIA)) {
            db.createObjectStore(MEDIA, { keyPath: "key" });
          }
        };
        req.onsuccess = function () { resolve(req.result); };
        req.onerror = function () { reject(req.error || new Error("idb open failed")); };
      });
      return dbPromise;
    }
    function runIn(name, mode, fn) {
      return openDb().then(function (db) {
        return new Promise(function (resolve, reject) {
          var tx = db.transaction(name, mode);
          var store = tx.objectStore(name);
          var request = fn(store);
          tx.oncomplete = function () { resolve(request ? request.result : undefined); };
          tx.onerror = function () { reject(tx.error); };
          tx.onabort = function () { reject(tx.error); };
        });
      }).catch(function () { return null; });
    }
    function run(mode, fn) { return runIn(STORE, mode, fn); }
    return {
      get: function (key) { return run("readonly", function (s) { return s.get(key); }); },
      put: function (rec) { return run("readwrite", function (s) { return s.put(rec); }); },
      del: function (key) { return run("readwrite", function (s) { return s.delete(key); }); },
      mediaGet: function (key) {
        return runIn(MEDIA, "readonly", function (s) { return s.get(key); });
      },
      mediaPut: function (rec) {
        return runIn(MEDIA, "readwrite", function (s) { return s.put(rec); });
      },
      mediaDel: function (key) {
        return runIn(MEDIA, "readwrite", function (s) { return s.delete(key); });
      },
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
      ? t("eta", { n: Math.ceil((dl.total - dl.received) / dl.speed) }) : "";
    meta.textContent = base + " · " +
      (note || t("dl_meta", { pct: pct, speed: speedText, eta: etaText }));
  }

  function downloadFile(f, meta, trigger) {
    var key = downloadStateKey(f);
    var pkey = downloadPersistKey(f);
    var dl = state.downloads[key];
    if (dl && dl.running) {
      toast(t("download_busy", { name: f.name }));
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
        toast(t("resume_from", { size: formatSize(fresh.received) }));
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
        updateDownloadMeta(meta, dl, t("resume_at", { size: formatSize(dl.received) }));
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
              (res.status === 401 || res.status === 403 ? t("http_token_bad") : "") +
              (res.status === 429 ? t("http_busy") : ""));
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
          if (dl.total && dl.received < dl.total) throw new Error(t("conn_ended"));
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
          meta.textContent = (meta.dataset.base || "") + " · " + t("download_done_meta");
          toast(t("download_done", { name: f.name }));
        })
        .catch(function (err) {
          if (err && err.message === "__restart__") {
            step();
            return;
          }
          attempt++;
          if (attempt < DOWNLOAD_RETRIES && dl.received > 0) {
            persistDownload(pkey, dl);
            updateDownloadMeta(meta, dl, t("retry_n", { n: attempt }));
            setTimeout(step, 600 + attempt * 800);
            return;
          }
          dl.running = false;
          if (trigger) trigger.classList.remove("busy");
          if (dl.received) persistDownload(pkey, dl);
          var reason = (err && err.message) || t("net_error");
          updateDownloadMeta(meta, dl, t("download_interrupted", { size: formatSize(dl.received) }));
          toast(t("download_interrupted_toast", { reason: reason }), true);
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
    meta.textContent = formatSize(file.size) + " · " + t("wait");

    item.appendChild(head);
    item.appendChild(bar);
    item.appendChild(meta);
    el.uploadList.appendChild(item);
    return item;
  }

  function markFailed(item, message, file) {
    item.classList.add("failed");
    item.querySelector(".up-pct").textContent = t("failed");
    item.querySelector(".up-meta").textContent = message;

    var head = item.querySelector(".up-head");
    if (head.querySelector(".retry")) return;
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "retry";
    btn.textContent = t("retry");
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
    meta.textContent = formatSize(file.size) + " · " + t("wait");
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
            etaText = t("eta", { n: Math.ceil((e.total - e.loaded) / speed) });
          }
        }
        meta.textContent = formatSize(e.loaded) + " / " + formatSize(e.total) +
          " · " + t("uploading") + speedText + etaText;
      };

      xhr.onload = function () {
        var data = null;
        try { data = JSON.parse(xhr.responseText); } catch (err) { /* ignore */ }
        if (xhr.status === 200 && data && data.ok) {
          item.classList.add("done");
          pct.textContent = t("done");
          fill.style.width = "100%";
          meta.textContent = formatSize(data.size) + " · " + t("saved");
          if (data.name !== file.name) {
            name.textContent = file.name + " → " + data.name;
          }
          loadFiles(true);
          resolve(true);
        } else {
          markFailed(item, (data && data.error) ? data.error : t("upload_retry"), file);
          resolve(false);
        }
      };

      xhr.onerror = function () {
        markFailed(item, t("upload_network"), file);
        resolve(false);
      };

      xhr.send(file);
    });
  }

  function uploadFiles(fileList) {
    var files = Array.prototype.slice.call(fileList || []);
    if (!files.length) return;
    if (state.uploading) {
      toast(t("busy_upload"), true);
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
      toast(t("queued", { count: files.length, n: UPLOAD_CONCURRENCY }));
    }

    queue.then(function () {
      state.uploading = false;
      el.fileInput.value = "";
      loadFiles(true);
      if (success === files.length) {
        toast(t("sent", { count: success }));
      } else {
        toast(t("sent_partial", { ok: success, total: files.length }), true);
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
      el.toggleQr.textContent = t(hidden ? "qr_toggle_collapse" : "qr_toggle_expand");
    });

    if (el.heroToggle) el.heroToggle.addEventListener("click", function () { toggleCollapse("hero"); });
    if (el.filesToggle) el.filesToggle.addEventListener("click", function () { toggleCollapse("files"); });
    if (el.connectToggle) el.connectToggle.addEventListener("click", function () { toggleCollapse("connect"); });

    el.copyBtn.addEventListener("click", function () {
      var url = (state.info && state.info.share_url) || el.shareUrl.textContent;
      var done = function () { toast(t("link_copied")); };
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
    try { document.execCommand("copy"); done(); } catch (err) { toast(t("copy_failed"), true); }
    document.body.removeChild(input);
  }

  // --------------------------- 外观：自定义背景 + 卡片不透明度 --------------------------- //
  var BG_STORE_KEY = "background";
  var BG_SETTINGS_KEY = "lan-transfer-bg";
  var BG_MODES = ["fill", "fit", "tile", "center"];
  var OPACITY_MIN = 40;
  var OPACITY_MAX = 100;
  var OPACITY_DEFAULT = 75;
  var bgState = { url: "", name: "", mode: "fill", dim: 45, opacity: OPACITY_DEFAULT };

  function clampOpacity(value) {
    var n = Number(value);
    if (!isFinite(n)) return OPACITY_DEFAULT;
    return Math.max(OPACITY_MIN, Math.min(OPACITY_MAX, Math.round(n)));
  }

  function readBgSettings() {
    var cfg = { mode: "fill", dim: 45, name: "", opacity: OPACITY_DEFAULT };
    try {
      var raw = localStorage.getItem(BG_SETTINGS_KEY);
      if (raw) {
        var parsed = JSON.parse(raw) || {};
        if (BG_MODES.indexOf(parsed.mode) >= 0) cfg.mode = parsed.mode;
        var dim = Number(parsed.dim);
        if (isFinite(dim)) cfg.dim = Math.max(0, Math.min(100, Math.round(dim)));
        cfg.name = String(parsed.name || "");
        if (parsed.opacity != null) cfg.opacity = clampOpacity(parsed.opacity);
      }
    } catch (err) { /* 忽略损坏的本地设置 */ }
    return cfg;
  }

  function saveBgSettings() {
    try {
      localStorage.setItem(BG_SETTINGS_KEY, JSON.stringify({
        mode: bgState.mode, dim: bgState.dim, name: bgState.name,
        opacity: bgState.opacity
      }));
    } catch (err) { /* 隐私模式等会失败，忽略 */ }
  }

  // 卡片底色 alpha 与桌面端共用同一个「卡片不透明度」参数
  function paintCards() {
    var alpha = (bgState.opacity / 100).toFixed(3);
    var alphaAlt = Math.min(1, bgState.opacity / 100 + 0.04).toFixed(3);
    var root = document.documentElement;
    root.style.setProperty("--panel-a", alpha);
    root.style.setProperty("--panel-2-a", alphaAlt);
  }

  function paintBg() {
    var root = document.documentElement;
    root.style.setProperty("--bg-dim", (bgState.dim / 100 * 0.92).toFixed(3));
    root.style.setProperty("--bg-url", bgState.url ? 'url("' + bgState.url + '")' : "none");
    if (el.bgLayer) el.bgLayer.setAttribute("data-mode", bgState.mode);
    paintCards();
  }

  function syncBgPanel() {
    if (!el.bgMode) return;
    el.bgMode.value = bgState.mode;
    el.bgDim.value = String(bgState.dim);
    if (el.bgDimVal) el.bgDimVal.textContent = bgState.dim + "%";
    if (el.bgOpacity) el.bgOpacity.value = String(bgState.opacity);
    if (el.bgOpacityVal) el.bgOpacityVal.textContent = bgState.opacity + "%";
    if (el.topOpacity) el.topOpacity.value = String(bgState.opacity);
    if (el.topOpacityVal) el.topOpacityVal.textContent = bgState.opacity + "%";
    if (el.bgFileName) {
      el.bgFileName.textContent = bgState.name
        ? t("sheet_current_file", { name: bgState.name })
        : t("sheet_current_default");
    }
  }

  function applyBackgroundFile(file) {
    if (!file) return;
    var okType = /^image\/(jpeg|png|webp|bmp)$/.test(file.type) ||
      /\.(jpg|jpeg|png|webp|bmp)$/i.test(file.name);
    if (!okType) { toast(t("bg_need_image"), true); return; }
    if (file.size > 8 * 1024 * 1024) {
      toast(t("bg_too_big"), true);
      return;
    }
    var reader = new FileReader();
    reader.onload = function () {
      bgState.url = String(reader.result || "");
      bgState.name = file.name;
      paintBg();
      syncBgPanel();
      saveBgSettings();
      idb.mediaPut({
        key: BG_STORE_KEY, name: file.name, dataUrl: bgState.url,
        updated: Date.now()
      }).then(function (rec) {
        if (rec === null) toast(t("bg_saved_but"), true);
        else toast(t("bg_updated"));
      });
    };
    reader.onerror = function () { toast(t("bg_read_fail"), true); };
    reader.readAsDataURL(file);
  }

  function resetBackground() {
    bgState.url = "";
    bgState.name = "";
    bgState.mode = "fill";
    bgState.dim = 45;
    bgState.opacity = OPACITY_DEFAULT;
    paintBg();
    syncBgPanel();
    saveBgSettings();
    idb.mediaDel(BG_STORE_KEY);
    toast(t("bg_reset_toast"));
  }

  function openBgSheet() {
    if (!el.bgSheet) return;
    syncBgPanel();
    el.bgSheet.hidden = false;
  }

  function closeBgSheet() {
    if (el.bgSheet) el.bgSheet.hidden = true;
  }

  function initBg() {
    var cfg = readBgSettings();
    bgState.mode = cfg.mode;
    bgState.dim = cfg.dim;
    bgState.name = cfg.name;
    bgState.opacity = cfg.opacity;
    paintBg();
    syncBgPanel();

    if (el.bgBtn) el.bgBtn.addEventListener("click", openBgSheet);
    if (el.bgClose) el.bgClose.addEventListener("click", closeBgSheet);
    if (el.bgSheet) {
      el.bgSheet.addEventListener("click", function (e) {
        if (e.target === el.bgSheet) closeBgSheet();
      });
    }
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && el.bgSheet && !el.bgSheet.hidden) closeBgSheet();
    });
    if (el.bgPick && el.bgInput) {
      el.bgPick.addEventListener("click", function () {
        el.bgInput.value = "";
        el.bgInput.click();
      });
      el.bgInput.addEventListener("change", function () {
        applyBackgroundFile(el.bgInput.files && el.bgInput.files[0]);
      });
    }
    if (el.bgReset) el.bgReset.addEventListener("click", resetBackground);
    if (el.bgMode) {
      el.bgMode.addEventListener("change", function () {
        bgState.mode = el.bgMode.value;
        paintBg();
        saveBgSettings();
      });
    }
    if (el.bgDim) {
      el.bgDim.addEventListener("input", function () {
        var dim = Number(el.bgDim.value);
        bgState.dim = isFinite(dim) ? Math.max(0, Math.min(100, dim)) : 0;
        if (el.bgDimVal) el.bgDimVal.textContent = bgState.dim + "%";
        paintBg();
      });
      el.bgDim.addEventListener("change", saveBgSettings);
    }
    if (el.bgOpacity) {
      el.bgOpacity.addEventListener("input", function () {
        bgState.opacity = clampOpacity(el.bgOpacity.value);
        if (el.bgOpacityVal) el.bgOpacityVal.textContent = bgState.opacity + "%";
        if (el.topOpacity) el.topOpacity.value = String(bgState.opacity);
        if (el.topOpacityVal) el.topOpacityVal.textContent = bgState.opacity + "%";
        paintCards();
      });
      el.bgOpacity.addEventListener("change", saveBgSettings);
    }
    if (el.topOpacity) {
      el.topOpacity.addEventListener("input", function () {
        bgState.opacity = clampOpacity(el.topOpacity.value);
        if (el.topOpacityVal) el.topOpacityVal.textContent = bgState.opacity + "%";
        if (el.bgOpacity) el.bgOpacity.value = String(bgState.opacity);
        if (el.bgOpacityVal) el.bgOpacityVal.textContent = bgState.opacity + "%";
        paintCards();
      });
      el.topOpacity.addEventListener("change", saveBgSettings);
    }

    // 图片可能较大：延迟异步恢复，不影响文件列表首屏
    if (cfg.name) {
      setTimeout(function () {
        idb.mediaGet(BG_STORE_KEY).then(function (rec) {
          if (!rec || !rec.dataUrl) return;
          bgState.url = rec.dataUrl;
          bgState.name = rec.name || cfg.name;
          paintBg();
          syncBgPanel();
        });
      }, 300);
    }
  }

  // --------------------------- 启动 --------------------------- //
  function init() {
    idb.prune(3 * 24 * 60 * 60 * 1000); // 清理 3 天前的下载断点记录
    readCollapse();
    applyCollapse("hero", false);
    applyCollapse("files", false);
    applyCollapse("connect", false);
    bindEvents();
    initBg();
    var isDesktop = window.matchMedia("(min-width: 760px)").matches;
    el.qrWrap.hidden = !isDesktop;
    el.toggleQr.textContent = t(isDesktop ? "qr_toggle_collapse" : "qr_toggle_expand");
    loadSpaces();
    loadInfo();
    loadFiles(true);
    setInterval(function () {
      if (!document.hidden && !state.uploading) loadFiles(true);
    }, 8000);

    if (window.LT) {
      window.LT.onChange(function () {
        renderFiles();
        syncBgPanel();
        if (state.info) applyInfo(state.info);
        loadSpaces();
        ["hero", "files", "connect"].forEach(paintCollapseButton);
        if (el.qrWrap) {
          el.toggleQr.textContent = t(el.qrWrap.hidden ? "qr_toggle_expand" : "qr_toggle_collapse");
        }
      });
    }
  }

  if (window.LT) {
    window.LT.init().then(init);
  } else {
    init();
  }
})();
