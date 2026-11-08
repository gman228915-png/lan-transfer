/* 轻量多语言：JSON 资源 + 缺词回退英文 + localStorage 持久化 */
(function () {
  "use strict";

  var FALLBACK = "en";
  var STORE_KEY = "lan-transfer-lang";

  // code 用于资源文件名，html 用于 <html lang>，name 是语言自身的写法
  var LANGS = [
    { code: "zh-Hans", html: "zh-CN", name: "简体中文" },
    { code: "zh-Hant", html: "zh-TW", name: "繁體中文" },
    { code: "en", html: "en", name: "English" },
    { code: "ja", html: "ja", name: "日本語" },
    { code: "ko", html: "ko", name: "한국어" },
    { code: "es", html: "es", name: "Español" },
    { code: "ru", html: "ru", name: "Русский" }
  ];

  var dicts = {};
  var current = FALLBACK;
  var listeners = [];

  function codes() {
    return LANGS.map(function (l) { return l.code; });
  }

  function normalize(raw) {
    var tag = String(raw || "").toLowerCase().replace("_", "-");
    if (!tag) return "";
    if (tag === "zh" || tag === "zh-cn" || tag === "zh-sg" || tag === "zh-hans" ||
        tag.indexOf("zh-hans") === 0) return "zh-Hans";
    if (tag === "zh-tw" || tag === "zh-hk" || tag === "zh-mo" || tag === "zh-hant" ||
        tag.indexOf("zh-hant") === 0) return "zh-Hant";
    var base = tag.split("-")[0];
    return codes().indexOf(base) >= 0 ? base : "";
  }

  function detect() {
    var list = navigator.languages && navigator.languages.length
      ? navigator.languages
      : [navigator.language || navigator.userLanguage || ""];
    for (var i = 0; i < list.length; i++) {
      var code = normalize(list[i]);
      if (code) return code;
    }
    return FALLBACK;
  }

  function htmlLang(code) {
    for (var i = 0; i < LANGS.length; i++) {
      if (LANGS[i].code === code) return LANGS[i].html;
    }
    return "en";
  }

  function stored() {
    try {
      var value = localStorage.getItem(STORE_KEY);
      return codes().indexOf(value) >= 0 ? value : "";
    } catch (err) {
      return "";
    }
  }

  function load(code) {
    if (dicts[code]) return Promise.resolve(dicts[code]);
    return fetch("/static/i18n/" + code + ".json", { cache: "no-store" })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (data) {
        dicts[code] = data || {};
        return dicts[code];
      })
      .catch(function () {
        dicts[code] = {};
        return dicts[code];
      });
  }

  function pick(key) {
    var own = dicts[current] || {};
    if (own[key] != null) return own[key];
    var fb = dicts[FALLBACK] || {};
    if (fb[key] != null) return fb[key];
    return key;
  }

  function format(text, vars) {
    if (!vars) return text;
    return String(text).replace(/\{(\w+)\}/g, function (all, name) {
      return vars[name] != null ? String(vars[name]) : all;
    });
  }

  // t(key[, vars])：缺词回退英文，英文也缺则原样返回 key
  function t(key, vars) {
    return format(pick(key), vars);
  }

  function applyNode(node) {
    var key = node.getAttribute("data-i18n");
    if (key) node.textContent = t(key);

    var attrs = node.getAttribute("data-i18n-attr");
    if (attrs) {
      attrs.split(",").forEach(function (pair) {
        var parts = pair.split(":");
        if (parts.length !== 2) return;
        node.setAttribute(parts[0].trim(), t(parts[1].trim()));
      });
    }
  }

  function apply(root) {
    var scope = root || document;
    var nodes = scope.querySelectorAll("[data-i18n],[data-i18n-attr]");
    for (var i = 0; i < nodes.length; i++) applyNode(nodes[i]);
    document.title = t("page_title");
    document.documentElement.setAttribute("lang", htmlLang(current));
  }

  function syncSelect() {
    var select = document.getElementById("langSelect");
    if (!select) return;
    select.innerHTML = "";
    LANGS.forEach(function (l) {
      var option = document.createElement("option");
      option.value = l.code;
      option.textContent = l.name;
      select.appendChild(option);
    });
    select.value = current;
  }

  function fire() {
    for (var i = 0; i < listeners.length; i++) {
      try { listeners[i](current); } catch (err) { /* 单个监听器出错不影响其他 */ }
    }
  }

  function set(code) {
    var target = codes().indexOf(code) >= 0 ? code : FALLBACK;
    current = target;
    try { localStorage.setItem(STORE_KEY, target); } catch (err) { /* 忽略 */ }
    return Promise.all([load(target), load(FALLBACK)]).then(function () {
      apply();
      syncSelect();
      fire();
      return current;
    });
  }

  function init() {
    var code = stored() || detect();
    current = code;
    return Promise.all([load(code), load(FALLBACK)]).then(function () {
      apply();
      syncSelect();
      var select = document.getElementById("langSelect");
      if (select && !select.dataset.bound) {
        select.dataset.bound = "1";
        select.addEventListener("change", function () { set(select.value); });
      }
      return current;
    });
  }

  function lang() { return current; }

  function onChange(fn) {
    if (typeof fn === "function") listeners.push(fn);
  }

  window.LT = {
    LANGS: LANGS,
    LANG_CODES: codes(),
    FALLBACK: FALLBACK,
    init: init,
    set: set,
    apply: apply,
    t: t,
    lang: lang,
    detect: detect,
    normalize: normalize,
    htmlLang: htmlLang,
    onChange: onChange
  };
})();
