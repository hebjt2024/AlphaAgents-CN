# Frontend Vendor Assets

These files are vendored for offline/self-hosted use. Do not remove the license
headers from the minified files.

| File | Version | License | Source |
|---|---|---|---|
| `marked.min.js` | 18.2.0 | MIT | https://github.com/markedjs/marked |
| `purify.min.js` | 3.4.17 | Apache-2.0 OR MPL-2.0 | https://github.com/cure53/DOMPurify |
| `echarts.min.js` | 5.5.0 | Apache-2.0 | https://github.com/apache/echarts |

> 版本升级说明（2026-11）：`purify.min.js` 由 3.0.11 升级至 3.4.17 ——
> 3.0.x 存在已公开的 mXSS 公告（CVE-2024-45801 / CVE-2024-47875 /
> CVE-2025-26791）。`marked.min.js` 由 12.0.2 升级至 18.2.0。
> `marked` 的浏览器产物路径已变更为 `lib/marked.umd.js`（UMD，仍暴露
> 全局 `marked`）。

The WebUI serves these files from `/static/vendor/`.
