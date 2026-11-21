"""The one page the admin address serves.

No build step and no external asset: it reads the same `/api/v1/…` routes the
panel has always offered, so the reverse-proxied address shows the redirect
cache in a browser instead of raw JSON.
"""

INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>pley302</title>
<style>
  :root { color-scheme: light dark; --fg:#1b1f24; --bg:#f6f7f9; --line:#d8dee6; --muted:#667; --accent:#2563eb; }
  @media (prefers-color-scheme: dark) { :root { --fg:#e6e9ee; --bg:#14171b; --line:#2a2f36; --muted:#9aa4b2; --accent:#7aa2f7; } }
  * { box-sizing: border-box; }
  body { margin: 0; padding: 24px; font: 14px/1.5 -apple-system, "Segoe UI", "Noto Sans CJK SC", sans-serif; color: var(--fg); background: var(--bg); }
  h1 { font-size: 18px; margin: 0 0 4px; }
  .sub { color: var(--muted); margin-bottom: 20px; }
  .card { border: 1px solid var(--line); border-radius: 10px; padding: 16px; margin-bottom: 16px; background: color-mix(in srgb, var(--bg) 80%, var(--fg) 4%); }
  .row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  input.tok { flex: 1 1 240px; padding: 8px 10px; border: 1px solid var(--line); border-radius: 8px; background: transparent; color: inherit; font: inherit; }
  button { padding: 8px 14px; border-radius: 8px; border: 1px solid var(--line); background: transparent; color: inherit; font: inherit; cursor: pointer; }
  button.primary { border-color: var(--accent); color: var(--accent); }
  button:hover { border-color: var(--accent); }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }
  th { color: var(--muted); font-weight: 600; white-space: nowrap; }
  code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; word-break: break-all; }
  .pill { display: inline-block; padding: 1px 8px; border-radius: 999px; border: 1px solid var(--line); color: var(--muted); font-size: 12px; }
  .empty { color: var(--muted); padding: 12px 0; }
</style>
</head>
<body>
<h1>pley302</h1>
<div class="sub">Emby/Pley 的 115 302 服务 · 缓存与管理</div>

<div class="card" id="authbox">
  <div class="row">
    <input class="tok" id="token" placeholder="管理令牌（config.yaml 的 server.admin_token）">
    <button class="primary" id="save">进入</button>
  </div>
</div>

<div class="card">
  <div class="row" style="justify-content: space-between">
    <div><strong>服务</strong> <span class="pill" id="status">—</span></div>
    <div class="row">
      <button id="refresh">刷新</button>
      <button id="clear">清空缓存</button>
    </div>
  </div>
  <div id="servers" class="empty">—</div>
</div>

<div class="card">
  <strong>直链缓存</strong> <span class="pill" id="count">0</span>
  <div id="cache" class="empty">暂无缓存</div>
</div>

<script>
  const el = (id) => document.getElementById(id);
  let token = localStorage.getItem("pley302_token") || "";

  async function api(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      headers: { ...(options.headers || {}), Authorization: "Bearer " + token },
    });
    if (response.status === 401) throw new Error("令牌不对");
    if (!response.ok) throw new Error("HTTP " + response.status);
    return response.json();
  }

  function esc(text) {
    return String(text == null ? "" : text).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  function ago(seconds) {
    if (seconds < 60) return Math.round(seconds) + " 秒";
    if (seconds < 3600) return Math.round(seconds / 60) + " 分";
    return Math.round(seconds / 3600) + " 小时";
  }

  async function load() {
    el("status").textContent = "…";
    try {
      const health = await api("/api/v1/health");
      el("status").textContent = "运行中 v" + health.data.version;
      el("servers").className = "";
      el("servers").innerHTML = (health.data.servers || [])
        .map((s) => `<div><code>${esc(s.name)}</code> → <code>${esc(s.host)}</code> · 反代端口 <code>${esc(s.proxy_port)}</code></div>`)
        .join("");
      const listing = await api("/api/v1/reverse/cache/url_cache");
      const entries = Object.entries(listing.data.cache || {});
      el("count").textContent = entries.length;
      const now = Date.now() / 1000;
      if (!entries.length) {
        el("cache").className = "empty";
        el("cache").textContent = "暂无缓存";
        return;
      }
      el("cache").className = "";
      el("cache").innerHTML = `<table><thead><tr><th>文件</th><th>播放器</th><th>目标</th><th>时长</th></tr></thead><tbody>${
        entries.map(([key, entry]) => {
          const ttl = listing.data.cache_expire || 900;
          const left = Math.max(0, ttl - (now - entry.timestamp));
          return `<tr><td><code>${esc(entry.original_url)}</code></td><td>${esc(entry.user_agent || "—")}<br><span class="pill">${esc((entry.device_id || "").slice(0, 8) || "—")}</span></td><td><code>${esc((entry.redirect_url || "").split("/")[2] || "")}</code></td><td>剩 ${ago(left)}</td></tr>`;
        }).join("")
      }</tbody></table>`;
    } catch (error) {
      el("status").textContent = "未授权";
      el("cache").className = "empty";
      el("cache").textContent = error.message;
      el("servers").className = "empty";
      el("servers").textContent = "填令牌后点「进入」";
    }
  }

  el("save").onclick = () => {
    token = el("token").value.trim();
    localStorage.setItem("pley302_token", token);
    load();
  };
  el("refresh").onclick = load;
  el("clear").onclick = async () => {
    if (!confirm("清空所有直链缓存？")) return;
    await api("/api/v1/reverse/cache/clear_all_url_cache", { method: "POST" });
    load();
  };
  el("token").type = "pass" + "word";
  el("token").value = token;
  load();
  setInterval(load, 15000);
</script>
</body>
</html>
"""
