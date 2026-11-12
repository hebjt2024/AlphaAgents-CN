/* =============================================================================
 * AlphaAgents-CN 前端核心工具
 *
 * WHY 独立成文件：此前 3450 行的 index.html 里内联了 2350 行 JS、约 50 个
 * 挂在 window 上的全局函数，既无法测试也无法复用。这里把最基础、最需要
 * 统一口径的部分抽出来，作为后续继续拆分模块的落脚点。
 *
 * 覆盖范围：
 *   - HTML 转义与 Markdown 渲染（含 XSS 防护）
 *   - 统一 API 客户端（检查 HTTP 状态码、注入 API Key）
 *   - 轻提示
 *   - 通用格式化
 * ========================================================================== */
(function (global) {
  'use strict';

  /* ---------------------------------------------------------------------
   * 基础：转义与格式化
   * ------------------------------------------------------------------- */

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, function (ch) {
      return {
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
      }[ch];
    });
  }

  function localDateStr(d) {
    const date = d || new Date();
    const p = function (n) { return String(n).padStart(2, '0'); };
    return date.getFullYear() + '-' + p(date.getMonth() + 1) + '-' + p(date.getDate());
  }

  function fmtPct(v) {
    return (Number(v) * 100).toFixed(2) + '%';
  }

  function fmtAmount(v) {
    const n = Number(v);
    if (!isFinite(n)) return '--';
    if (Math.abs(n) >= 1e8) return (n / 1e8).toFixed(2) + ' 亿';
    if (Math.abs(n) >= 1e4) return (n / 1e4).toFixed(2) + ' 万';
    return n.toFixed(2);
  }

  function looksLikeMarkdown(text) {
    if (!text) return false;
    return /^\s{0,3}#{1,6}\s/m.test(text) || /(\*\*|__)[^*]+(\*\*|__)/m.test(text)
      || /^\s{0,3}[-*+]\s/m.test(text) || /^\s{0,3}\d+\.\s/m.test(text)
      || /\|.*\|/m.test(text) || /```/.test(text) || /^\s{0,3}>/m.test(text);
  }

  /* ---------------------------------------------------------------------
   * Markdown 渲染（XSS 防护）
   *
   * 报告内容部分来自抓取的新闻与公告，属于"可被外部影响"的文本，因此
   * 渲染器是安全边界而不仅是展示逻辑。三条硬规则：
   *   1) 禁止 <img> 与 src —— 否则报告里的一张外链图片就会变成追踪信标，
   *      泄露"谁在什么时间看了哪份报告"；
   *   2) 链接若带 target="_blank"，强制 rel="noopener noreferrer"，
   *      避免反向标签劫持（DOMPurify 不会自动补 rel）；
   *   3) style / on* / data-* 一律不放行（由 ALLOWED_* 白名单天然覆盖）。
   * ------------------------------------------------------------------- */

  var MARKDOWN_ALLOWED_TAGS = [
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'br', 'hr',
    'strong', 'em', 'del', 'code', 'pre', 'blockquote',
    'ul', 'ol', 'li', 'table', 'thead', 'tbody', 'tr', 'th', 'td',
    'a', 'span', 'div', 'sub', 'sup'
  ];
  var MARKDOWN_ALLOWED_ATTR = [
    'href', 'alt', 'title', 'colspan', 'rowspan', 'target', 'rel'
  ];

  var hookInstalled = false;
  function installPurifyHook() {
    if (hookInstalled || typeof DOMPurify === 'undefined') return;
    hookInstalled = true;
    DOMPurify.addHook('afterSanitizeAttributes', function (node) {
      if (node.tagName === 'A' && node.getAttribute('target') === '_blank') {
        node.setAttribute('rel', 'noopener noreferrer');
      }
      // 统一去掉 src，彻底断掉外链图片信标
      if (node.hasAttribute && node.hasAttribute('src')) {
        node.removeAttribute('src');
      }
    });
  }

  function renderMarkdown(text) {
    if (!text) return '';
    try {
      if (typeof marked !== 'undefined' && typeof DOMPurify !== 'undefined') {
        installPurifyHook();
        const html = marked.parse(String(text));
        return DOMPurify.sanitize(html, {
          ALLOWED_TAGS: MARKDOWN_ALLOWED_TAGS,
          ALLOWED_ATTR: MARKDOWN_ALLOWED_ATTR,
          FORBID_ATTR: ['style', 'src', 'onerror', 'onload', 'onclick'],
          ALLOW_DATA_ATTR: false
        });
      }
    } catch (e) {
      if (global.console && console.warn) console.warn('markdown render failed', e);
    }
    return escapeHtml(text);
  }

  /* ---------------------------------------------------------------------
   * API Key
   *
   * 后端可通过 WEBUI_API_KEY 开启鉴权（不配置时保持开放，兼容本地使用）。
   * 浏览器无法自动获得该密钥，因此由用户在设置页填写并保存在 localStorage。
   * ------------------------------------------------------------------- */

  var API_KEY_STORAGE = 'alphaagents.apiKey';

  function getApiKey() {
    try {
      return global.localStorage ? (localStorage.getItem(API_KEY_STORAGE) || '') : '';
    } catch (e) {
      return '';
    }
  }

  function setApiKey(value) {
    try {
      if (global.localStorage) {
        if (value) localStorage.setItem(API_KEY_STORAGE, value);
        else localStorage.removeItem(API_KEY_STORAGE);
      }
    } catch (e) { /* 隐私模式下 localStorage 可能不可用 */ }
  }

  function authHeaders(extra) {
    const headers = extra || {};
    const key = getApiKey();
    if (key) headers['X-API-Key'] = key;
    return headers;
  }

  /* ---------------------------------------------------------------------
   * 统一 API 客户端
   *
   * WHY：此前每个 fetch 都直接 `await resp.json()`，从不检查 resp.ok。
   * 422 校验错误会返回 {"detail": [...]}，于是 data.success 是 undefined，
   * 界面最终弹出一个空白提示（undefined）。这里统一处理状态码。
   * ------------------------------------------------------------------- */

  async function apiFetch(path, options) {
    const opts = Object.assign({}, options || {});
    opts.headers = authHeaders(opts.headers);
    const resp = await fetch(path, opts);

    let payload = null;
    const text = await resp.text();
    if (text) {
      try { payload = JSON.parse(text); }
      catch (e) { payload = { success: false, message: text }; }
    }
    if (!payload) payload = {};

    if (!resp.ok) {
      const message = payload.message
        || (payload.detail && typeof payload.detail === 'string' ? payload.detail : '')
        || ('请求失败（HTTP ' + resp.status + '）');
      const err = new Error(message);
      err.status = resp.status;
      err.code = payload.code || 'http_error';
      err.payload = payload;
      throw err;
    }
    return payload;
  }

  /* ---------------------------------------------------------------------
   * 轻提示
   * ------------------------------------------------------------------- */

  function showToast(text, type) {
    const box = document.createElement('div');
    box.className = 'toast ' + (type || 'ok');
    box.setAttribute('role', type === 'err' ? 'alert' : 'status');
    box.textContent = text == null ? '' : String(text);
    const host = document.getElementById('toasts');
    if (!host) return;
    host.appendChild(box);
    setTimeout(function () {
      box.style.opacity = '0';
      box.style.transition = '.3s';
      setTimeout(function () { box.remove(); }, 320);
    }, 3600);
  }

  global.Core = {
    escapeHtml: escapeHtml,
    localDateStr: localDateStr,
    fmtPct: fmtPct,
    fmtAmount: fmtAmount,
    looksLikeMarkdown: looksLikeMarkdown,
    renderMarkdown: renderMarkdown,
    getApiKey: getApiKey,
    setApiKey: setApiKey,
    authHeaders: authHeaders,
    apiFetch: apiFetch,
    showToast: showToast
  };
})(window);
