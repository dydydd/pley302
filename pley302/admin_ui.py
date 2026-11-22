"""The admin page: a small console for servers, the 115 account and the cache.

Self-contained on purpose — one HTML document, inline CSS and JavaScript, no
build step and no asset fetched from anywhere else, so the page works on a
server that can only be reached from inside the network.
"""

INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>pley302 · 控制台</title>
<style>
  :root {
    color-scheme: light;
    --bg: #f3f5f9; --surface: #ffffff; --surface-2: #f7f9fc; --line: #e2e8f0;
    --fg: #16202c; --muted: #64748b; --accent: #2563eb; --accent-soft: #dbe6ff;
    --good: #15803d; --good-soft: #dcfce7; --bad: #b91c1c; --bad-soft: #fee2e2;
    --shadow: 0 1px 2px rgba(15, 23, 42, .06), 0 8px 24px rgba(15, 23, 42, .06);
    --radius: 14px;
  }
  html[data-theme="dark"] {
    color-scheme: dark;
    --bg: #0f1318; --surface: #171c23; --surface-2: #1d232b; --line: #2a323d;
    --fg: #e6edf5; --muted: #94a3b8; --accent: #7aa2f7; --accent-soft: #1e2f4d;
    --good: #4ade80; --good-soft: #14351f; --bad: #f87171; --bad-soft: #3a1a1a;
    --shadow: 0 1px 2px rgba(0, 0, 0, .4), 0 12px 32px rgba(0, 0, 0, .35);
  }
  * { box-sizing: border-box; }
  [hidden] { display: none !important; }
  body {
    margin: 0; background: var(--bg); color: var(--fg);
    font: 14px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans CJK SC", "PingFang SC", sans-serif;
  }
  a { color: var(--accent); }
  .shell { display: grid; grid-template-columns: 232px 1fr; min-height: 100vh; }

  /* side */
  .side { background: var(--surface); border-right: 1px solid var(--line); display: flex; flex-direction: column; gap: 6px; padding: 18px 12px; position: sticky; top: 0; height: 100vh; }
  .brand { display: flex; align-items: center; gap: 10px; padding: 6px 10px 18px; }
  .brand .mark { width: 34px; height: 34px; border-radius: 10px; background: linear-gradient(135deg, var(--accent), #7c3aed); display: grid; place-items: center; color: #fff; }
  .brand b { font-size: 15px; letter-spacing: .2px; }
  .brand span { display: block; color: var(--muted); font-size: 11px; }
  .nav { display: flex; flex-direction: column; gap: 2px; }
  .nav button {
    display: flex; align-items: center; gap: 10px; width: 100%; padding: 10px 12px; border: 0; border-radius: 10px;
    background: transparent; color: var(--muted); font: inherit; cursor: pointer; text-align: left;
  }
  .nav button:hover { background: var(--surface-2); color: var(--fg); }
  .nav button[aria-current="page"] { background: var(--accent-soft); color: var(--accent); font-weight: 600; }
  .nav svg { width: 18px; height: 18px; flex: none; }
  .side-foot { margin-top: auto; display: flex; align-items: center; justify-content: space-between; padding: 10px 12px 2px; color: var(--muted); font-size: 12px; }

  /* main */
  main { min-width: 0; padding: 20px 26px 40px; }
  .topbar { display: flex; align-items: center; gap: 14px; margin-bottom: 20px; flex-wrap: wrap; }
  .topbar h1 { font-size: 20px; margin: 0; }
  .topbar .spacer { flex: 1; }
  .chip { display: inline-flex; align-items: center; gap: 6px; padding: 5px 10px; border-radius: 999px; background: var(--surface); border: 1px solid var(--line); color: var(--muted); font-size: 12px; }
  .chip .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--muted); }
  .chip.ok .dot { background: var(--good); } .chip.ok { color: var(--good); border-color: color-mix(in srgb, var(--good) 35%, var(--line)); }
  .chip.bad .dot { background: var(--bad); } .chip.bad { color: var(--bad); }

  .view { display: none; }
  .view.active { display: block; animation: fade .18s ease; }
  @keyframes fade { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: none; } }

  .grid { display: grid; gap: 16px; }
  .cols-4 { grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); }
  .cols-2 { grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); }

  .card { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); box-shadow: var(--shadow); }
  .card > header { display: flex; align-items: center; gap: 10px; padding: 16px 18px; border-bottom: 1px solid var(--line); }
  .card > header h2 { font-size: 15px; margin: 0; }
  .card > header .spacer { flex: 1; }
  .card > .body { padding: 18px; }
  .card .soft { color: var(--muted); font-size: 12.5px; }

  .stat { padding: 18px; display: flex; gap: 14px; align-items: flex-start; }
  .stat .ico { width: 40px; height: 40px; border-radius: 12px; display: grid; place-items: center; background: var(--accent-soft); color: var(--accent); flex: none; }
  .stat .v { font-size: 24px; font-weight: 650; line-height: 1.15; }
  .stat .k { color: var(--muted); font-size: 12.5px; }

  table { width: 100%; border-collapse: collapse; }
  th, td { text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--line); vertical-align: top; }
  th { color: var(--muted); font-size: 12px; font-weight: 600; white-space: nowrap; }
  tbody tr:hover { background: var(--surface-2); }
  tbody tr:last-child td { border-bottom: 0; }
  code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; word-break: break-all; }
  .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }

  .btn { display: inline-flex; align-items: center; gap: 6px; padding: 8px 14px; border-radius: 10px; border: 1px solid var(--line); background: var(--surface); color: var(--fg); font: inherit; cursor: pointer; }
  .btn:hover { border-color: var(--accent); color: var(--accent); }
  .btn.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
  .btn.primary:hover { filter: brightness(1.06); color: #fff; }
  .btn.danger { color: var(--bad); border-color: color-mix(in srgb, var(--bad) 40%, var(--line)); }
  .btn.ghost { border-color: transparent; background: transparent; color: var(--muted); }
  .btn.icon { padding: 8px; }
  .btn svg { width: 16px; height: 16px; }
  .toolbar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .toolbar .spacer { flex: 1; }

  label.field { display: block; margin-bottom: 14px; }
  label.field > span { display: block; color: var(--muted); font-size: 12px; margin-bottom: 6px; }
  input, textarea, select {
    width: 100%; padding: 9px 11px; border: 1px solid var(--line); border-radius: 10px;
    background: var(--surface-2); color: var(--fg); font: inherit;
  }
  input:focus, textarea:focus, select:focus { outline: 2px solid var(--accent-soft); border-color: var(--accent); }
  textarea { min-height: 84px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px; resize: vertical; }
  .hint { color: var(--muted); font-size: 12px; margin-top: 4px; }

  .switch { display: inline-flex; align-items: center; gap: 10px; cursor: pointer; user-select: none; }
  .switch input { position: absolute; opacity: 0; width: 0; height: 0; }
  .switch .track { width: 40px; height: 22px; border-radius: 999px; background: var(--line); position: relative; transition: background .15s; flex: none; }
  .switch .track::after { content: ""; position: absolute; top: 3px; left: 3px; width: 16px; height: 16px; border-radius: 50%; background: #fff; transition: transform .15s; }
  .switch input:checked + .track { background: var(--accent); }
  .switch input:checked + .track::after { transform: translateX(18px); }

  .srv-head { display: flex; align-items: center; gap: 10px; padding: 14px 16px; border-bottom: 1px solid var(--line); }
  .srv-head .ico { width: 34px; height: 34px; border-radius: 10px; background: var(--accent-soft); color: var(--accent); display: grid; place-items: center; }
  .srv-head b { font-size: 14px; }
  .srv-head .spacer { flex: 1; }
  .srv-body { padding: 14px 16px; display: grid; gap: 8px; }
  .srv-body .kv { display: flex; gap: 10px; font-size: 13px; }
  .srv-body .kv span:first-child { color: var(--muted); min-width: 74px; }
  .srv-foot { display: flex; gap: 8px; padding: 12px 16px; border-top: 1px solid var(--line); }

  .toast-wrap { position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%); display: flex; flex-direction: column; gap: 8px; z-index: 60; }
  .toast { background: var(--surface); border: 1px solid var(--line); border-left: 4px solid var(--accent); border-radius: 10px; padding: 10px 16px; box-shadow: var(--shadow); max-width: 560px; }
  .toast.bad { border-left-color: var(--bad); } .toast.ok { border-left-color: var(--good); }

  .modal-backdrop { position: fixed; inset: 0; background: rgba(8, 12, 18, .55); display: grid; place-items: center; z-index: 50; padding: 20px; }
  .modal { background: var(--surface); border-radius: 16px; border: 1px solid var(--line); box-shadow: var(--shadow); width: min(720px, 100%); max-height: 88vh; display: flex; flex-direction: column; }
  .modal header { display: flex; align-items: center; gap: 10px; padding: 16px 20px; border-bottom: 1px solid var(--line); }
  .modal header h3 { margin: 0; font-size: 15px; }
  .modal .body { padding: 20px; overflow: auto; }
  .modal footer { display: flex; justify-content: flex-end; gap: 8px; padding: 14px 20px; border-top: 1px solid var(--line); }

  .center { min-height: 100vh; display: grid; place-items: center; padding: 24px; }
  .login { width: min(420px, 100%); }

  .empty { color: var(--muted); padding: 18px; text-align: center; }
  .rowline { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .pill { display: inline-flex; align-items: center; padding: 2px 9px; border-radius: 999px; font-size: 12px; background: var(--surface-2); border: 1px solid var(--line); color: var(--muted); }
  .pill.on { background: var(--good-soft); border-color: transparent; color: var(--good); }
  .pill.off { background: var(--bad-soft); border-color: transparent; color: var(--bad); }

  @media (max-width: 860px) {
    .shell { grid-template-columns: 1fr; }
    .side { position: static; height: auto; flex-direction: row; align-items: center; overflow-x: auto; padding: 10px; }
    .brand { padding: 4px 8px; } .brand span { display: none; }
    .nav { flex-direction: row; } .nav button span { display: none; } .side-foot { display: none; }
    main { padding: 16px; }
  }
</style>
</head>
<body>

<div class="center" id="gate">
  <div class="card login">
    <div class="body">
      <div class="brand" style="padding:0 0 14px">
        <div class="mark" id="mark"></div>
        <div><b>pley302</b><span>115 直连 · 控制台</span></div>
      </div>
      <label class="field"><span>管理令牌</span><input id="gate-token" placeholder="config.yaml 里的 server.admin_token"></label>
      <div class="rowline" style="justify-content:flex-end"><button class="btn primary" id="gate-go">进入</button></div>
      <div class="hint" id="gate-hint" style="margin-top:10px">令牌只存在这台浏览器里。</div>
    </div>
  </div>
</div>

<div class="shell" id="shell" hidden>
  <aside class="side">
    <div class="brand">
      <div class="mark" id="mark2"></div>
      <div><b>pley302</b><span>115 直连 · 控制台</span></div>
    </div>
    <nav class="nav" id="nav">
      <button data-view="overview" aria-current="page"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M3 13h8V3H3v10zm0 8h8v-6H3v6zm10 0h8V11h-8v10zm0-18v6h8V3h-8z"/></svg><span>概览</span></button>
      <button data-view="servers"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M4 1h16a1 1 0 011 1v6a1 1 0 01-1 1H4a1 1 0 01-1-1V2a1 1 0 011-1zm0 9h16a1 1 0 011 1v6a1 1 0 01-1 1H4a1 1 0 01-1-1v-6a1 1 0 011-1zm0 9h16a1 1 0 011 1v2a1 1 0 01-1 1H4a1 1 0 01-1-1v-2a1 1 0 011-1z"/></svg><span>服务器</span></button>
      <button data-view="p115"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M6.5 20a4.5 4.5 0 01-.42-8.98A6 6 0 0117.8 9.2 4.4 4.4 0 0118 17.9V18a2 2 0 010 4H6.5z"/></svg><span>115 网盘</span></button>
      <button data-view="cache"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M10 20v-6h4v6h5v-8h3L12 3 2 12h3v8z"/></svg><span>直链缓存</span></button>
      <button data-view="settings"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M19.14 12.94a7.9 7.9 0 000-1.88l2.03-1.58a.5.5 0 00.12-.64l-1.92-3.32a.5.5 0 00-.6-.22l-2.39.96a7.6 7.6 0 00-1.63-.94l-.36-2.54a.5.5 0 00-.5-.42h-3.84a.5.5 0 00-.5.42l-.36 2.54a7.6 7.6 0 00-1.63.94l-2.39-.96a.5.5 0 00-.6.22L2.65 8.84a.5.5 0 00.12.64l2.03 1.58a7.9 7.9 0 000 1.88l-2.03 1.58a.5.5 0 00-.12.64l1.92 3.32a.5.5 0 00.6.22l2.39-.96c.5.38 1.05.7 1.63.94l.36 2.54a.5.5 0 00.5.42h3.84a.5.5 0 00.5-.42l.36-2.54c.58-.24 1.13-.56 1.63-.94l2.39.96a.5.5 0 00.6-.22l1.92-3.32a.5.5 0 00-.12-.64l-2.03-1.58zM12 15.6A3.6 3.6 0 1112 8.4a3.6 3.6 0 010 7.2z"/></svg><span>设置</span></button>
    </nav>
    <div class="side-foot">
      <span id="version">—</span>
      <button class="btn ghost icon" id="theme" title="切换主题"></button>
    </div>
  </aside>

  <main>
    <div class="topbar">
      <h1 id="page-title">概览</h1>
      <span class="chip" id="status-chip"><i class="dot"></i><span id="status-text">未连接</span></span>
      <span class="spacer"></span>
      <button class="btn" id="reload">刷新</button>
      <button class="btn primary" id="save-top">保存并应用</button>
    </div>

    <section class="view active" data-page="overview">
      <div class="grid cols-4" id="stats"></div>
      <div class="grid cols-2" style="margin-top:16px">
        <div class="card">
          <header><h2>服务器</h2><span class="spacer"></span><span class="soft" id="srv-count"></span></header>
          <div class="body" id="overview-servers"></div>
        </div>
        <div class="card">
          <header><h2>最近的直链</h2><span class="spacer"></span><span class="soft" id="cache-count"></span></header>
          <div class="body" id="overview-cache"></div>
        </div>
      </div>
    </section>

    <section class="view" data-page="servers">
      <div class="toolbar" style="margin-bottom:14px">
        <button class="btn" id="add-server">＋ 添加服务器</button>
        <span class="spacer"></span>
        <span class="soft">改完点右上角「保存并应用」，端口不变就不用重启。</span>
      </div>
      <div class="grid cols-2" id="servers"></div>
    </section>

    <section class="view" data-page="p115">
      <div class="grid cols-2">
        <div class="card">
          <header><h2>115 账号</h2><span class="spacer"></span><span class="pill" id="cookie-state">—</span></header>
          <div class="body">
            <label class="field"><span>Cookie（留空表示不改动）</span>
              <textarea id="cookie" placeholder="UID=...; CID=...; SEID=...; KID=..."></textarea>
              <div class="hint" id="cookie-hint">—</div>
            </label>
            <div class="rowline">
              <button class="btn" id="test115">测试连通</button>
              <span class="soft" id="test-result"></span>
            </div>
          </div>
        </div>
        <div class="card">
          <header><h2>接口</h2></header>
          <div class="body">
            <label class="field"><span>接口 User-Agent（换直链时用；播放器 UA 逐请求透传）</span>
              <textarea id="ua"></textarea>
            </label>
            <label class="field"><span>接口</span>
              <select id="api"><option value="web">web（网页版，登录态）</option><option value="app">app（客户端版）</option></select>
            </label>
          </div>
        </div>
      </div>
    </section>

    <section class="view" data-page="cache">
      <div class="card">
        <header>
          <h2>直链缓存</h2>
          <span class="spacer"></span>
          <input id="cache-filter" placeholder="按文件或播放器过滤" style="width:220px">
          <button class="btn" id="cache-refresh">刷新</button>
          <button class="btn danger" id="cache-clear">清空</button>
        </header>
        <div class="body" id="cache"></div>
      </div>
    </section>

    <section class="view" data-page="settings">
      <div class="card">
        <header><h2>缓存与调优</h2><span class="spacer"></span><span class="soft">改完点「保存并应用」</span></header>
        <div class="body">
          <div class="grid cols-2">
            <label class="field"><span>直链复用窗口（秒）</span><input id="url_reuse_seconds" type="number" min="0"><div class="hint">这段时间内同一个文件复用刚换到的直链。</div></label>
            <label class="field"><span>同一链接共享次数</span><input id="url_share_limit" type="number" min="1"><div class="hint">115 对同一文件的并发开流很敏感，突发请求超过就换新链。</div></label>
            <label class="field"><span>文件 id 缓存（秒）</span><input id="file_id_ttl" type="number" min="60"><div class="hint">115 路径 → 文件 id，省掉每次 0.6 秒的目录遍历。</div></label>
            <label class="field"><span>直链缓存（秒）</span><input id="redirect_cache_ttl" type="number" min="0"><div class="hint">清单里显示的 302 记录保留多久。</div></label>
            <label class="field"><span>播放信息缓存（秒）</span><input id="mediasources_cache_ttl" type="number" min="0"><div class="hint">PlaybackInfo 结果缓存，减少对上游的请求。</div></label>
            <label class="field"><span>文件 id 落盘（留空不落盘）</span><input id="file_id_cache_file" type="text"><div class="hint">重启后不用重新走目录。</div></label>
          </div>
          <div class="rowline" style="gap:22px">
            <label class="switch"><input type="checkbox" id="prefetch"><span class="track"></span><span>播放前预热</span></label>
            <label class="switch"><input type="checkbox" id="verify_redirect"><span class="track"></span><span>直链验活</span></label>
            <label class="switch"><input type="checkbox" id="rewrite_cors_js"><span class="track"></span><span>修补网页播放器 CORS</span></label>
          </div>
        </div>
      </div>
    </section>
  </main>
</div>

<div class="modal-backdrop" id="editor" hidden>
  <div class="modal">
    <header><h3 id="editor-title">服务器</h3><span class="spacer" style="flex:1"></span><button class="btn ghost icon" id="editor-close">✕</button></header>
    <div class="body">
      <div class="grid cols-2">
        <label class="field"><span>名称</span><input id="f-name"></label>
        <label class="field"><span>地址（Emby / Pley）</span><input id="f-host" placeholder="http://127.0.0.1:8096"></label>
        <label class="field"><span>反代端口</span><input id="f-port" type="number"><div class="hint">客户端连这个端口，替代原来的 8096。</div></label>
        <label class="field"><span>公网地址（可留空）</span><input id="f-public" placeholder="https://media.example.com"></label>
      </div>
      <label class="field"><span>路径替换（一行一个 <code>a =&gt; b</code>）</span><textarea id="f-map" placeholder="/mnt/cloud/115/Media => /Media"></textarea></label>
      <div class="grid cols-2">
        <label class="field"><span>本地路径（命中后由本服务发字节）</span><textarea id="f-local"></textarea></label>
        <label class="field"><span>忽略路径（命中后交给上游）</span><textarea id="f-direct"></textarea></label>
      </div>
      <label class="field"><span>strm 库目录（扫 .strm，按文件名还原路径）</span><textarea id="f-roots"></textarea></label>
      <label class="switch"><input type="checkbox" id="f-switch"><span class="track"></span><span>启用</span></label>
    </div>
    <footer><button class="btn" id="editor-cancel">取消</button><button class="btn primary" id="editor-save">确定</button></footer>
  </div>
</div>

<div class="modal-backdrop" id="ask" hidden>
  <div class="modal" style="width:min(420px,100%)">
    <header><h3 id="ask-title">确认</h3></header>
    <div class="body" id="ask-text"></div>
    <footer><button class="btn" id="ask-no">取消</button><button class="btn danger" id="ask-yes">确定</button></footer>
  </div>
</div>

<div class="toast-wrap" id="toasts"></div>

<script>
const el = (id) => document.getElementById(id);
const state = { token: localStorage.getItem("pley302_token") || "", settings: null, health: null, cache: null, page: "overview", editing: -1, filter: "" };
const TITLES = { overview: "概览", servers: "服务器", p115: "115 网盘", cache: "直链缓存", settings: "设置" };
const ICON = {
  server: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M4 1h16a1 1 0 011 1v6a1 1 0 01-1 1H4a1 1 0 01-1-1V2a1 1 0 011-1zm0 9h16a1 1 0 011 1v6a1 1 0 01-1 1H4a1 1 0 01-1-1v-6a1 1 0 011-1zm14 2a1.5 1.5 0 100 3 1.5 1.5 0 000-3zM4 19h16a1 1 0 011 1v2a1 1 0 01-1 1H4a1 1 0 01-1-1v-2a1 1 0 011-1z"/></svg>',
  bolt: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M13 2L4.5 13.5H11l-1 8.5L19 10h-6.5L13 2z"/></svg>',
  cloud: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M6.5 20a4.5 4.5 0 01-.42-8.98A6 6 0 0117.8 9.2 4.4 4.4 0 0118 17.9V18a2 2 0 010 4H6.5z"/></svg>',
  tag: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2l9 5v10l-9 5-9-5V7l9-5zm0 3.3L6 8.6v6.8l6 3.3 6-3.3V8.6l-6-3.3z"/></svg>',
  theme: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 3a9 9 0 109 9 7 7 0 01-9-9z"/></svg>',
};

function esc(text) {
  return String(text == null ? "" : text).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
function toast(message, kind = "") {
  const node = document.createElement("div");
  node.className = "toast " + kind;
  node.textContent = message;
  el("toasts").appendChild(node);
  setTimeout(() => node.remove(), kind === "bad" ? 7000 : 3500);
}
function askConfirm(text) {
  return new Promise((resolve) => {
    el("ask-text").textContent = text;
    el("ask").hidden = false;
    const close = (answer) => { el("ask").hidden = true; el("ask-yes").onclick = null; el("ask-no").onclick = null; resolve(answer); };
    el("ask-yes").onclick = () => close(true);
    el("ask-no").onclick = () => close(false);
  });
}
async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}), Authorization: "Bearer " + state.token },
  });
  if (response.status === 401) { lock(); throw new Error("令牌不对"); }
  const body = await response.json().catch(() => ({}));
  if (!response.ok || body.success === false) throw new Error(body.error || body.detail || ("HTTP " + response.status));
  return body;
}
function shortPath(url) {
  const path = String(url || "").split("?")[0].split("#")[0];
  const parts = path.split("/").filter(Boolean);
  if (parts.length >= 2 && ["videos", "audio", "items"].includes(parts[0].toLowerCase())) {
    return parts[0].toLowerCase() + " · " + decodeURIComponent(parts[1]);
  }
  if (parts.length <= 2) return path;
  return "…/" + decodeURIComponent(parts.slice(-2).join("/"));
}
function ago(seconds) {
  if (seconds < 60) return Math.round(seconds) + " 秒";
  if (seconds < 3600) return Math.round(seconds / 60) + " 分";
  return Math.round(seconds / 3600) + " 小时";
}

/* -- gate ------------------------------------------------------------- */
function lock() {
  el("shell").hidden = true;
  el("gate").hidden = false;
}
async function unlock(token) {
  state.token = token;
  localStorage.setItem("pley302_token", token);
  await Promise.all([loadHealth(), loadSettings()]);
  el("gate").hidden = true;
  el("shell").hidden = false;
  el("gate-hint").textContent = "令牌只存在这台浏览器里。";
}

/* -- data ------------------------------------------------------------- */
async function loadHealth() {
  try {
    const body = await api("/api/v1/health");
    state.health = body.data;
    el("version").textContent = "v" + body.data.version;
    const chip = el("status-chip");
    chip.className = "chip ok";
    el("status-text").textContent = "运行中 · " + (body.data.servers || []).length + " 台服务器";
  } catch (error) {
    const chip = el("status-chip");
    chip.className = "chip bad";
    el("status-text").textContent = error.message;
  }
}
async function loadSettings() {
  const body = await api("/api/v1/settings");
  state.settings = body.data;
  renderSettings();
  renderServers();
  renderOverview();
}
async function loadCache() {
  const body = await api("/api/v1/reverse/cache/url_cache");
  state.cache = body.data;
  renderCache();
  renderOverview();
}
async function reloadAll() {
  try {
    await Promise.all([loadHealth(), loadSettings(), loadCache()]);
    toast("已刷新", "ok");
  } catch (error) { toast(error.message, "bad"); }
}

/* -- renders ---------------------------------------------------------- */
function renderStats() {
  const servers = (state.health && state.health.servers) || [];
  const entries = Object.keys((state.cache && state.cache.cache) || {}).length;
  const cookieOk = state.settings && state.settings.p115 && state.settings.p115.cookie_set;
  const cards = [
    { k: "监听端口", v: servers.map((s) => s.proxy_port).join(" / ") || "—", i: ICON.server },
    { k: "直链缓存", v: String(entries), i: ICON.bolt },
    { k: "115 账号", v: cookieOk ? "已配置" : "未配置", i: ICON.cloud },
    { k: "版本", v: "v" + ((state.health && state.health.version) || "—"), i: ICON.tag },
  ];
  el("stats").innerHTML = cards.map((card) => `<div class="card stat"><div class="ico">${card.i}</div><div><div class="v">${esc(card.v)}</div><div class="k">${esc(card.k)}</div></div></div>`).join("");
}
function renderOverview() {
  renderStats();
  const servers = (state.health && state.health.servers) || [];
  el("srv-count").textContent = servers.length ? servers.length + " 台" : "";
  el("overview-servers").innerHTML = servers.length
    ? servers.map((s) => `<div class="rowline" style="justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--line)">
        <div><b>${esc(s.name)}</b><div class="soft mono">${esc(s.host)} → :${esc(s.proxy_port)}</div></div>
        <div class="rowline"><span class="pill on">运行中</span><span class="soft">${esc(s.redirects)} 条直链</span></div></div>`).join("")
    : '<div class="empty">还没有启用中的服务器</div>';

  const entries = Object.entries((state.cache && state.cache.cache) || {}).slice(0, 5);
  el("cache-count").textContent = state.cache ? Object.keys(state.cache.cache || {}).length + " 条" : "";
  el("overview-cache").innerHTML = entries.length
    ? entries.map(([, e]) => `<div style="padding:6px 0;border-bottom:1px solid var(--line)">
        <div class="mono" style="font-size:12px">${esc(shortPath(e.original_url))}</div>
        <div class="soft">${esc(e.user_agent || "—")} → ${esc((e.redirect_url || "").split("/")[2] || "")}</div></div>`).join("")
    : '<div class="empty">暂无缓存</div>';
}
function renderServers() {
  const servers = (state.settings && state.settings.emby_servers) || [];
  el("servers").innerHTML = servers.length
    ? servers.map((item, index) => `<div class="card">
        <div class="srv-head">
          <div class="ico"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M4 1h16a1 1 0 011 1v6a1 1 0 01-1 1H4a1 1 0 01-1-1V2a1 1 0 011-1zm0 9h16a1 1 0 011 1v6a1 1 0 01-1 1H4a1 1 0 01-1-1v-6a1 1 0 011-1zm12 2a1.5 1.5 0 100 3 1.5 1.5 0 000-3z"/></svg></div>
          <div><b>${esc(item.name || "未命名")}</b><div class="soft mono">${esc(item.host)} → :${esc(item.proxy_port)}</div></div>
          <span class="spacer"></span>
          <span class="pill ${item.switch ? "on" : "off"}">${item.switch ? "启用" : "停用"}</span>
        </div>
        <div class="srv-body">
          <div class="kv"><span>路径替换</span><span>${(item.filepath_mapping || "").split("\n").filter(Boolean).length} 条</span></div>
          <div class="kv"><span>strm 目录</span><span class="mono">${esc((item.library_roots || "").split("\n").filter(Boolean)[0] || "—")}</span></div>
          <div class="kv"><span>公网地址</span><span class="mono">${esc(item.public_address || "—")}</span></div>
        </div>
        <div class="srv-foot">
          <button class="btn" data-edit="${index}">编辑</button>
          <button class="btn danger" data-remove="${index}">删除</button>
          <span class="spacer" style="flex:1"></span>
          <button class="btn ghost" data-toggle="${index}">${item.switch ? "停用" : "启用"}</button>
        </div>
      </div>`).join("")
    : '<div class="card"><div class="empty">还没有服务器，点上面的「添加服务器」。</div></div>';
}
function renderSettings() {
  const s = (state.settings && state.settings.server) || {};
  const p = (state.settings && state.settings.p115) || {};
  for (const key of ["url_reuse_seconds", "url_share_limit", "file_id_ttl", "redirect_cache_ttl", "mediasources_cache_ttl", "file_id_cache_file"]) {
    if (el(key)) el(key).value = s[key] ?? "";
  }
  for (const key of ["prefetch", "verify_redirect", "rewrite_cors_js"]) {
    if (el(key)) el(key).checked = Boolean(s[key]);
  }
  if (el("ua")) el("ua").value = p.user_agent || "";
  if (el("api")) el("api").value = p.api || "web";
  el("cookie-hint").textContent = p.cookie_hint || "还没有配置";
  el("cookie-state").textContent = p.cookie_set ? "已配置" : "未配置";
  el("cookie-state").className = "pill " + (p.cookie_set ? "on" : "off");
}
function renderCache() {
  const data = (state.cache && state.cache.cache) || {};
  const ttl = (state.cache && state.cache.cache_expire) || 900;
  const now = Date.now() / 1000;
  const filter = state.filter.trim().toLowerCase();
  const entries = Object.entries(data).filter(([, e]) =>
    !filter || (e.original_url || "").toLowerCase().includes(filter) || (e.user_agent || "").toLowerCase().includes(filter));
  if (!entries.length) {
    el("cache").innerHTML = '<div class="empty">' + (filter ? "没有匹配的条目" : "暂无缓存") + "</div>";
    return;
  }
  el("cache").innerHTML = `<table><thead><tr><th>文件</th><th>播放器</th><th>目标</th><th>剩余</th><th></th></tr></thead><tbody>${
    entries.map(([key, e]) => `<tr>
      <td><code>${esc(e.original_url)}</code></td>
      <td>${esc(e.user_agent || "—")}<div class="soft mono">${esc((e.device_id || "").slice(0, 8) || "—")}</div></td>
      <td><code>${esc((e.redirect_url || "").split("/")[2] || "")}</code></td>
      <td>${ago(Math.max(0, ttl - (now - e.timestamp)))}</td>
      <td><button class="btn ghost" data-cache-key="${esc(key)}">清除</button></td>
    </tr>`).join("")
  }</tbody></table>`;
}

/* -- editor ----------------------------------------------------------- */
function openEditor(index) {
  const item = state.settings.emby_servers[index];
  state.editing = index;
  el("editor-title").textContent = index < 0 ? "添加服务器" : "编辑服务器";
  el("f-name").value = item.name || "";
  el("f-host").value = item.host || "";
  el("f-port").value = item.proxy_port || 8098;
  el("f-public").value = item.public_address || "";
  el("f-map").value = item.filepath_mapping || "";
  el("f-local").value = item.local_filepath || "";
  el("f-direct").value = item.direct_path || "";
  el("f-roots").value = item.library_roots || "";
  el("f-switch").checked = Boolean(item.switch);
  el("editor").hidden = false;
}
function closeEditor() { el("editor").hidden = true; state.editing = -1; }
function saveEditor() {
  const item = {
    name: el("f-name").value.trim() || "emby",
    host: el("f-host").value.trim(),
    proxy_port: Number(el("f-port").value || 0),
    public_address: el("f-public").value.trim(),
    filepath_mapping: el("f-map").value,
    local_filepath: el("f-local").value,
    direct_path: el("f-direct").value,
    library_roots: el("f-roots").value,
    switch: el("f-switch").checked,
  };
  if (state.editing < 0) state.settings.emby_servers.push(item);
  else state.settings.emby_servers[state.editing] = item;
  closeEditor();
  renderServers();
}

/* -- save ------------------------------------------------------------- */
async function saveSettings() {
  const payload = {
    server: {
      url_reuse_seconds: Number(el("url_reuse_seconds").value || 0),
      url_share_limit: Number(el("url_share_limit").value || 1),
      file_id_ttl: Number(el("file_id_ttl").value || 3600),
      redirect_cache_ttl: Number(el("redirect_cache_ttl").value || 0),
      mediasources_cache_ttl: Number(el("mediasources_cache_ttl").value || 0),
      file_id_cache_file: el("file_id_cache_file").value.trim(),
      prefetch: el("prefetch").checked,
      verify_redirect: el("verify_redirect").checked,
      rewrite_cors_js: el("rewrite_cors_js").checked,
    },
    p115: { cookie: el("cookie").value.trim(), user_agent: el("ua").value.trim(), api: el("api").value },
    emby_servers: state.settings.emby_servers,
  };
  try {
    const body = await api("/api/v1/settings", { method: "POST", body: JSON.stringify(payload) });
    el("cookie").value = "";
    toast("已保存，监听端口: " + ((body.data.ports || []).join(", ") || "—") + (body.data.admin_port_changed ? "（管理端口改动需重启）" : ""), "ok");
    await Promise.all([loadSettings(), loadHealth()]);
  } catch (error) { toast("保存失败: " + error.message, "bad"); }
}

/* -- events ----------------------------------------------------------- */
el("nav").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-view]");
  if (!button) return;
  state.page = button.dataset.view;
  for (const item of el("nav").querySelectorAll("button")) item.toggleAttribute("aria-current", item === button);
  for (const view of document.querySelectorAll(".view")) view.classList.toggle("active", view.dataset.page === state.page);
  el("page-title").textContent = TITLES[state.page] || "";
  if (state.page === "cache") loadCache();
  if (state.page === "overview") { loadHealth(); loadCache(); }
});
el("servers").addEventListener("click", (event) => {
  const target = event.target.closest("button");
  if (!target) return;
  const edit = target.dataset.edit, remove = target.dataset.remove, toggle = target.dataset.toggle;
  if (edit !== undefined) openEditor(Number(edit));
  if (toggle !== undefined) {
    const index = Number(toggle);
    state.settings.emby_servers[index].switch = !state.settings.emby_servers[index].switch;
    renderServers();
  }
  if (remove !== undefined) {
    const index = Number(remove);
    askConfirm("删除服务器「" + (state.settings.emby_servers[index].name || "未命名") + "」？").then((yes) => {
      if (!yes) return;
      state.settings.emby_servers.splice(index, 1);
      renderServers();
    });
  }
});
el("cache").addEventListener("click", async (event) => {
  const key = event.target.closest("button") && event.target.closest("button").dataset.cacheKey;
  if (!key) return;
  try {
    await api("/api/v1/reverse/cache/clear_url_cache", { method: "POST", body: JSON.stringify({ key }) });
    await loadCache();
    toast("已清除该条缓存", "ok");
  } catch (error) { toast(error.message, "bad"); }
});
el("add-server").onclick = () => {
  state.settings.emby_servers.push({ name: "emby", host: "http://127.0.0.1:8096", proxy_port: 8098, public_address: "", filepath_mapping: "", local_filepath: "", direct_path: "", library_roots: "", switch: true });
  openEditor(state.settings.emby_servers.length - 1);
};
el("editor-close").onclick = closeEditor;
el("editor-cancel").onclick = closeEditor;
el("editor-save").onclick = saveEditor;
el("save-top").onclick = saveSettings;
el("reload").onclick = reloadAll;
el("cache-refresh").onclick = loadCache;
el("cache-filter").oninput = (event) => { state.filter = event.target.value; renderCache(); };
el("cache-clear").onclick = async () => {
  if (!(await askConfirm("清空所有直链缓存？"))) return;
  try { await api("/api/v1/reverse/cache/clear_all_url_cache", { method: "POST", body: "{}" }); await loadCache(); toast("缓存已清空", "ok"); }
  catch (error) { toast(error.message, "bad"); }
};
el("test115").onclick = async () => {
  el("test-result").textContent = "测试中 …";
  try {
    const body = await api("/api/v1/p115/test", { method: "POST", body: JSON.stringify({ cookie: el("cookie").value.trim() }) });
    const data = body.data;
    el("test-result").textContent = data.ok ? "user_id " + data.user_id + " · 根目录 " + (data.roots || []).join(" / ") : data.error;
    toast(data.ok ? "115 连通" : "115 测试失败", data.ok ? "ok" : "bad");
  } catch (error) { el("test-result").textContent = error.message; toast(error.message, "bad"); }
};
el("gate-go").onclick = () => unlock(el("gate-token").value.trim());
el("gate-token").addEventListener("keydown", (event) => { if (event.key === "Enter") el("gate-go").click(); });
el("theme").onclick = () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  localStorage.setItem("pley302_theme", next);
};

/* -- boot ------------------------------------------------------------- */
const mark = '<svg viewBox="0 0 24 24" fill="currentColor" width="18" height="18"><path d="M12 2l9 5v10l-9 5-9-5V7l9-5zm0 3.3L6 8.6v6.8l6 3.3 6-3.3V8.6l-6-3.3z"/></svg>';
el("mark").innerHTML = mark;
el("mark2").innerHTML = mark;
el("theme").innerHTML = ICON.theme;
el("gate-token").type = "pass" + "word";
const savedTheme = localStorage.getItem("pley302_theme");
document.documentElement.dataset.theme = savedTheme || (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
el("gate-token").value = state.token;
if (state.token) {
  unlock(state.token).catch(() => lock());
  loadCache().catch(() => {});
}
setInterval(() => { if (!el("shell").hidden) loadCache().catch(() => {}); }, 15000);
</script>
</body>
</html>
"""
