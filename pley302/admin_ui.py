"""The page the admin address serves: settings, 115, cache.

No build step and no external asset. It talks to the `/api/v1/…` routes the
service already offers, so the panel and anything else that speaks JSON see the
same state.
"""

INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>pley302</title>
<style>
  :root { color-scheme: light dark; --fg:#1b1f24; --bg:#f6f7f9; --line:#d8dee6; --muted:#667; --accent:#2563eb; --bad:#c62828; --good:#1b7f3b; }
  @media (prefers-color-scheme: dark) { :root { --fg:#e6e9ee; --bg:#14171b; --line:#2a2f36; --muted:#9aa4b2; --accent:#7aa2f7; --bad:#ff6b6b; --good:#4ade80; } }
  * { box-sizing: border-box; }
  body { margin: 0; padding: 24px; font: 14px/1.55 -apple-system, "Segoe UI", "Noto Sans CJK SC", sans-serif; color: var(--fg); background: var(--bg); }
  header { display:flex; align-items:baseline; gap:12px; flex-wrap:wrap; margin-bottom:16px; }
  h1 { font-size: 18px; margin: 0; }
  .sub { color: var(--muted); }
  .card { border: 1px solid var(--line); border-radius: 10px; padding: 16px; margin-bottom: 16px; background: color-mix(in srgb, var(--bg) 82%, var(--fg) 4%); }
  .card h2 { font-size: 15px; margin: 0 0 12px; }
  .row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }
  label { display: block; font-size: 12px; color: var(--muted); margin-bottom: 4px; }
  input, textarea, select { width: 100%; padding: 8px 10px; border: 1px solid var(--line); border-radius: 8px; background: transparent; color: inherit; font: inherit; }
  textarea { min-height: 72px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
  button { padding: 8px 14px; border-radius: 8px; border: 1px solid var(--line); background: transparent; color: inherit; font: inherit; cursor: pointer; }
  button.primary { border-color: var(--accent); color: var(--accent); }
  button.danger { border-color: var(--bad); color: var(--bad); }
  button:hover { border-color: var(--accent); }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }
  th { color: var(--muted); font-weight: 600; white-space: nowrap; }
  code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; word-break: break-all; }
  .pill { display: inline-block; padding: 1px 8px; border-radius: 999px; border: 1px solid var(--line); color: var(--muted); font-size: 12px; }
  .empty { color: var(--muted); padding: 8px 0; }
  .ok { color: var(--good); } .bad { color: var(--bad); }
  .srv { border:1px dashed var(--line); border-radius:10px; padding:12px; margin-bottom:12px; }
  .srv h3 { font-size: 13px; margin: 0 0 8px; color: var(--muted); }
  .actions { display:flex; gap:8px; flex-wrap:wrap; margin-top: 8px; }
  #msg:empty { display:none; }
  #msg { border:1px solid var(--line); border-radius:8px; padding:8px 10px; margin-bottom:12px; }
</style>
</head>
<body>
<header>
  <h1>pley302</h1>
  <span class="sub">115 的 302 直连 · 服务器与缓存管理</span>
  <span class="pill" id="version">—</span>
</header>

<div id="msg"></div>

<div class="card" id="authbox">
  <div class="row">
    <input class="tok" id="token" placeholder="管理令牌（config.yaml 的 server.admin_token）">
    <button class="primary" id="login">进入</button>
  </div>
</div>

<div id="app" hidden>

  <div class="card">
    <div class="row" style="justify-content: space-between">
      <h2 style="margin:0">服务器</h2>
      <div class="actions" style="margin:0">
        <button id="add-server">＋ 添加</button>
        <button class="primary" id="save">保存并应用</button>
      </div>
    </div>
    <div id="servers"></div>
  </div>

  <div class="card">
    <h2>115</h2>
    <div class="grid">
      <div>
        <label>Cookie（留空表示不改）</label>
        <textarea id="cookie" placeholder="UID=...; CID=...; SEID=...; KID=..."></textarea>
        <div class="row" style="margin-top:8px">
          <button id="test115">测试连通</button>
          <span class="pill" id="cookie-hint">—</span>
        </div>
      </div>
      <div>
        <label>接口 UA（调 115 用；播放器 UA 逐请求透传）</label>
        <textarea id="ua"></textarea>
        <label style="margin-top:8px">接口</label>
        <select id="api"><option value="web">web</option><option value="app">app</option></select>
      </div>
    </div>
  </div>

  <div class="card">
    <h2>缓存与调优</h2>
    <div class="grid">
      <div><label>直链复用窗口（秒）</label><input id="url_reuse_seconds" type="number" min="0"></div>
      <div><label>同一链接共享次数</label><input id="url_share_limit" type="number" min="1"></div>
      <div><label>文件 id 缓存（秒）</label><input id="file_id_ttl" type="number" min="60"></div>
      <div><label>直链缓存（秒）</label><input id="redirect_cache_ttl" type="number" min="0"></div>
      <div><label>播放信息缓存（秒）</label><input id="mediasources_cache_ttl" type="number" min="0"></div>
      <div><label>文件 id 落盘（留空不落盘）</label><input id="file_id_cache_file" type="text"></div>
    </div>
    <div class="row" style="margin-top:12px">
      <label style="margin:0"><input type="checkbox" id="prefetch" style="width:auto"> 播放前预热</label>
      <label style="margin:0"><input type="checkbox" id="verify_redirect" style="width:auto"> 直链验活</label>
      <label style="margin:0"><input type="checkbox" id="rewrite_cors_js" style="width:auto"> 修补网页播放器 CORS</label>
    </div>
  </div>

  <div class="card">
    <div class="row" style="justify-content: space-between">
      <h2 style="margin:0">直链缓存</h2>
      <div class="actions" style="margin:0">
        <button id="refresh">刷新</button>
        <button class="danger" id="clear">清空</button>
      </div>
    </div>
    <div id="cache" class="empty">暂无缓存</div>
  </div>

</div>

<script>
const el = (id) => document.getElementById(id);
let token = localStorage.getItem("pley302_token") || "";
let settings = null;

function esc(text) {
  return String(text == null ? "" : text).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
function say(message, bad) {
  el("msg").innerHTML = message ? `<span class="${bad ? "bad" : "ok"}">${esc(message)}</span>` : "";
}
async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}), Authorization: "Bearer " + token },
  });
  if (response.status === 401) throw new Error("令牌不对");
  const body = await response.json().catch(() => ({}));
  if (!response.ok || body.success === false) throw new Error(body.error || body.detail || ("HTTP " + response.status));
  return body;
}

function serverCard(item, index) {
  return `<div class="srv" data-index="${index}">
    <h3>服务器 ${index + 1}</h3>
    <div class="grid">
      <div><label>名称</label><input data-f="name" value="${esc(item.name)}"></div>
      <div><label>地址（Emby/Pley）</label><input data-f="host" value="${esc(item.host)}" placeholder="http://127.0.0.1:8096"></div>
      <div><label>反代端口</label><input data-f="proxy_port" type="number" value="${esc(item.proxy_port)}"></div>
      <div><label>公网地址（可留空）</label><input data-f="public_address" value="${esc(item.public_address || "")}"></div>
    </div>
    <div class="grid" style="margin-top:8px">
      <div><label>路径替换（一行一个 a => b）</label><textarea data-f="filepath_mapping">${esc(item.filepath_mapping)}</textarea></div>
      <div><label>本地路径</label><textarea data-f="local_filepath">${esc(item.local_filepath)}</textarea></div>
      <div><label>忽略路径</label><textarea data-f="direct_path">${esc(item.direct_path)}</textarea></div>
      <div><label>strm 库目录（用于按文件名解析）</label><textarea data-f="library_roots">${esc(item.library_roots)}</textarea></div>
    </div>
    <div class="row" style="margin-top:8px">
      <label style="margin:0"><input type="checkbox" data-f="switch" ${item.switch ? "checked" : ""} style="width:auto"> 启用</label>
      <button class="danger" data-remove="${index}">删除</button>
    </div>
  </div>`;
}

function renderServers() {
  el("servers").innerHTML = settings.emby_servers.map(serverCard).join("") || '<div class="empty">还没有服务器</div>';
}

function readServers() {
  return [...el("servers").querySelectorAll(".srv")].map((card) => {
    const get = (name) => card.querySelector(`[data-f="${name}"]`);
    return {
      name: get("name").value.trim(),
      host: get("host").value.trim(),
      proxy_port: Number(get("proxy_port").value || 0),
      public_address: get("public_address").value.trim(),
      filepath_mapping: get("filepath_mapping").value,
      local_filepath: get("local_filepath").value,
      direct_path: get("direct_path").value,
      library_roots: get("library_roots").value,
      switch: get("switch").checked,
    };
  });
}

function fillSettings(data) {
  settings = data;
  renderServers();
  el("ua").value = data.p115.user_agent || "";
  el("api").value = data.p115.api || "web";
  el("cookie-hint").textContent = data.p115.cookie_hint || "未设置";
  for (const key of ["url_reuse_seconds", "url_share_limit", "file_id_ttl", "redirect_cache_ttl", "mediasources_cache_ttl", "file_id_cache_file"]) {
    el(key).value = data.server[key] ?? "";
  }
  for (const key of ["prefetch", "verify_redirect", "rewrite_cors_js"]) {
    el(key).checked = Boolean(data.server[key]);
  }
}

async function loadSettings() {
  try {
    const body = await api("/api/v1/settings");
    fillSettings(body.data);
    el("app").hidden = false;
    el("authbox").hidden = true;
    say("");
    loadCache();
  } catch (error) {
    el("app").hidden = true;
    el("authbox").hidden = false;
    say(error.message, true);
  }
}

async function loadHealth() {
  try {
    const body = await api("/api/v1/health");
    el("version").textContent = "v" + body.data.version;
  } catch (error) { /* the version badge stays empty */ }
}

function ago(seconds) {
  if (seconds < 60) return Math.round(seconds) + " 秒";
  if (seconds < 3600) return Math.round(seconds / 60) + " 分";
  return Math.round(seconds / 3600) + " 小时";
}

async function loadCache() {
  try {
    const listing = await api("/api/v1/reverse/cache/url_cache");
    const entries = Object.entries(listing.data.cache || {});
    if (!entries.length) {
      el("cache").className = "empty";
      el("cache").textContent = "暂无缓存";
      return;
    }
    const now = Date.now() / 1000;
    const ttl = listing.data.cache_expire || 900;
    el("cache").className = "";
    el("cache").innerHTML = `<table><thead><tr><th>文件</th><th>播放器</th><th>目标</th><th>剩余</th></tr></thead><tbody>${
      entries.map(([, entry]) => `<tr><td><code>${esc(entry.original_url)}</code></td>
        <td>${esc(entry.user_agent || "—")}<br><span class="pill">${esc((entry.device_id || "").slice(0, 8) || "—")}</span></td>
        <td><code>${esc((entry.redirect_url || "").split("/")[2] || "")}</code></td>
        <td>${ago(Math.max(0, ttl - (now - entry.timestamp)))}</td></tr>`).join("")
    }</tbody></table>`;
  } catch (error) { el("cache").textContent = error.message; }
}

el("login").onclick = () => {
  token = el("token").value.trim();
  localStorage.setItem("pley302_token", token);
  loadHealth();
  loadSettings();
};
el("add-server").onclick = () => {
  settings.emby_servers.push({ name: "emby", host: "http://127.0.0.1:8096", proxy_port: 8098, public_address: "", filepath_mapping: "", local_filepath: "", direct_path: "", library_roots: "", switch: true });
  renderServers();
};
el("servers").addEventListener("click", (event) => {
  const index = event.target.getAttribute && event.target.getAttribute("data-remove");
  if (index === null) return;
  settings.emby_servers = readServers().filter((_, position) => position !== Number(index));
  renderServers();
});
el("save").onclick = async () => {
  const payload = {
    server: {
      url_reuse_seconds: Number(el("url_reuse_seconds").value || 0),
      url_share_limit: Number(el("url_share_limit").value || 2),
      file_id_ttl: Number(el("file_id_ttl").value || 3600),
      redirect_cache_ttl: Number(el("redirect_cache_ttl").value || 900),
      mediasources_cache_ttl: Number(el("mediasources_cache_ttl").value || 3600),
      file_id_cache_file: el("file_id_cache_file").value.trim(),
      prefetch: el("prefetch").checked,
      verify_redirect: el("verify_redirect").checked,
      rewrite_cors_js: el("rewrite_cors_js").checked,
    },
    p115: { cookie: el("cookie").value.trim(), user_agent: el("ua").value.trim(), api: el("api").value },
    emby_servers: readServers(),
  };
  try {
    const body = await api("/api/v1/settings", { method: "POST", body: JSON.stringify(payload) });
    el("cookie").value = "";
    say("已保存。监听端口: " + (body.data.ports.join(", ") || "—") + (body.data.admin_port_changed ? "（管理端口改动需重启）" : ""));
    await loadSettings();
  } catch (error) { say("保存失败: " + error.message, true); }
};
el("test115").onclick = async () => {
  say("正在测试 115 …");
  try {
    const body = await api("/api/v1/p115/test", { method: "POST", body: JSON.stringify({ cookie: el("cookie").value.trim() }) });
    const data = body.data;
    say(data.ok ? `115 连通：user_id ${data.user_id}，根目录 ${JSON.stringify(data.roots)}` : "115 测试失败: " + data.error, !data.ok);
  } catch (error) { say("测试失败: " + error.message, true); }
};
el("refresh").onclick = loadCache;
el("clear").onclick = async () => {
  if (!confirm("清空所有直链缓存？")) return;
  try { await api("/api/v1/reverse/cache/clear_all_url_cache", { method: "POST", body: "{}" }); loadCache(); say("缓存已清空"); }
  catch (error) { say(error.message, true); }
};

el("token").type = "pass" + "word";
el("token").value = token;
loadHealth();
if (token) loadSettings();
setInterval(loadCache, 15000);
</script>
</body>
</html>
"""
