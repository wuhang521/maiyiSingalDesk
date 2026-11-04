"use strict";
const el = id => document.getElementById(id);
let session = null, mediaUrl = "", busy = false, epoch = 0;
const status = text => { el("status").textContent = text; };
const permissionPattern = value => { const url = new URL(value); return `${url.protocol}//${url.hostname}:${url.port || (url.protocol === "https:" ? "443" : "80")}/*`; };
async function permitAddress(value) {
  try { return await chrome.permissions.request({ origins: [permissionPattern(value)] }); }
  catch { throw new Error("浏览器不支持精确主机 / 端口授权，请升级浏览器或改用客户端上传；不会放宽到其他端口"); }
}
function updateButtons() { el("save").disabled = busy || !session || !el("consent").checked || !el("source").value; el("media").disabled = busy || !session || !mediaUrl || !el("media-consent").checked; }
function displaySession() {
  el("capture").hidden = !session; el("login").hidden = !!session;
  el("identity").textContent = session ? `本人账号：${session.user.username}；接收后台：${session.backendUrl}` : "";
  mediaUrl = ""; el("content").value = ""; el("source").value = ""; el("title").value = ""; el("publisher").value = "";
  el("consent").checked = false; el("media-consent").checked = false; updateButtons();
}
function backendUrl(value) {
  const url = new URL(value.trim());
  if (!["https:", "http:"].includes(url.protocol) || url.username || url.password || url.search || url.hash || url.pathname !== "/") throw new Error("请输入后台根地址，不带密码、查询参数或路径");
  if (url.protocol === "http:" && !["localhost", "127.0.0.1", "[::1]"].includes(url.hostname) && !el("insecure").checked) throw new Error("远程后台请使用 HTTPS，或先明确接受 HTTP 风险");
  return url.origin;
}
async function api(path, options = {}) {
  if (!session || Date.parse(session.expires_at) <= Date.now()) throw new Error("登录已过期，请退出后重新登录");
  const response = await fetch(session.backendUrl + "/api/v1" + path, { ...options, redirect: "error", credentials: "omit", signal: AbortSignal.timeout(120000), headers: { Authorization: `Bearer ${session.access_token}`, ...options.headers } });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : `后台返回 ${response.status}；请检查版本和账号`);
  return data;
}
async function action(fn) {
  if (busy) return;
  busy = true; updateButtons(); const current = epoch;
  try { await fn(current); } catch (e) { if (epoch === current) status(e instanceof Error ? e.message : "结果未确认，请查看客户端；不自动重试"); }
  finally { busy = false; updateButtons(); }
}
el("login").addEventListener("submit", async event => {
  event.preventDefault(); if (busy) return;
  try {
    const backend = backendUrl(el("backend").value);
    // Request only this selected origin, during a user gesture; no all-sites grant.
    if (!await permitAddress(backend)) throw new Error("未授权访问所选后台，尚未发送密码");
    await action(async () => {
      const password = el("password").value; el("password").value = "";
      const response = await fetch(backend + "/api/v1/auth/login", { method: "POST", credentials: "omit", redirect: "error", signal: AbortSignal.timeout(20000), headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: el("username").value.trim(), password }) });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || typeof data.access_token !== "string" || !data.user || typeof data.expires_at !== "string") throw new Error("登录失败，请核对本人账号和后台地址");
      session = { ...data, backendUrl: backend }; ++epoch;
      await chrome.storage.session.set({ hermesSession: session }); await chrome.storage.local.set({ hermesBackend: backend });
      displaySession(); status("已登录。请预览当前微信页面，再决定保存。");
    });
  } catch (e) { status(e.message || "登录未完成"); }
});
el("logout").addEventListener("click", () => { if (busy) return; void action(async () => { await api("/auth/logout", { method: "POST" }).catch(() => undefined); session = null; ++epoch; await chrome.storage.session.remove("hermesSession"); displaySession(); status("已退出；保存过的资料不删除。"); }); });
el("preview").addEventListener("click", () => void action(async current => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const url = new URL(tab?.url || "");
  if (url.protocol !== "https:" || !["mp.weixin.qq.com", "weixin.qq.com"].includes(url.hostname) || !(url.pathname === "/s" || url.pathname.startsWith("/s/") || url.pathname.startsWith("/sph/"))) throw new Error("请打开 HTTPS 公众号文章或视频号分享页面，再点击预览");
  const results = await chrome.scripting.executeScript({ target: { tabId: tab.id }, world: "ISOLATED", func: () => {
    const article = document.querySelector("#js_content"); const videos = Array.from(document.querySelectorAll("video")).filter(v => v.getClientRects().length && v.readyState >= 1);
    const playing = videos.filter(v => !v.paused);
    const video = playing.length === 1 ? playing[0] : videos.length === 1 ? videos[0] : null;
    return { source: location.href, title: document.querySelector("#activity-name, #activity_name")?.innerText || document.title, publisher: document.querySelector("#js_name")?.innerText || "", content: article?.getClientRects().length ? article.innerText : "", video: video?.currentSrc || "", encrypted: !!video?.mediaKeys };
  } });
  const result = results[0]?.result;
  if (!result || current !== epoch || result.source !== tab.url) throw new Error("页面发生变化，请重新预览");
  if (result.source.length > 4000 || result.content.length > 1000000) throw new Error("页面内容超出限制，请拆分，不自动截断正文");
  el("source").value = result.source; el("title").value = result.title.trim().slice(0, 300); el("publisher").value = result.publisher.trim().slice(0, 200); el("content").value = result.content;
  mediaUrl = "";
  if (!result.encrypted && typeof result.video === "string") {
    try { const v = new URL(result.video); if (v.protocol === "https:" && !v.username && !v.password && ["qq.com", "qpic.cn"].some(d => v.hostname === d || v.hostname.endsWith("." + d)) && !/\.m3u8(?:\?|$)|\.mpd(?:\?|$)/i.test(v.href)) mediaUrl = v.href; } catch { /* No direct media exposed. */ }
  }
  el("media-status").textContent = mediaUrl ? "发现页面提供的 HTTPS 视频地址；只有授权保存后才获取。" : "未发现可获取的原视频（可能为加密 / blob / 验证页面），请上传合法取得的原件。";
  el("consent").checked = false; el("media-consent").checked = false;
  status(result.content ? "正文已预览，请核对。已存在的同链接资料不会被覆盖。" : "仅取得来源链接，没有正文。请补充素材；保存链接不代表内容已入库。");
}));
el("save").addEventListener("click", () => { if (!el("consent").checked || !el("source").value) return; void action(async current => {
  el("consent").checked = false;
  const data = await api("/knowledge/items", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: el("title").value, source_url: el("source").value, publisher: el("publisher").value, content: el("content").value, tags: [] }) });
  if (current === epoch) status(`资料编号：${data.id}。${data.status_note || "请到客户端查看"}；同链接去重时不会覆盖旧正文。`);
}); });
for (const id of ["consent", "media-consent"]) el(id).addEventListener("change", updateButtons);
const hex = bytes => Array.from(new Uint8Array(bytes), b => b.toString(16).padStart(2, "0")).join("");
const utf8Base64 = text => { const bytes = new TextEncoder().encode(text); return btoa(Array.from(bytes, b => String.fromCharCode(b)).join("")); };
el("media").addEventListener("click", async () => {
  if (busy || !mediaUrl || !el("media-consent").checked || !session) return;
  const url = mediaUrl; const origin = new URL(url).origin; const source = el("source").value; const current = epoch;
  try {
    if (!await permitAddress(origin)) throw new Error("未授权访问该视频地址，原件尚未获取");
    if (epoch !== current || !el("media-consent").checked || url !== mediaUrl) return;
    await action(async () => {
      el("media-consent").checked = false; status("正在获取你授权保存的直接视频文件…");
      const response = await fetch(url, { redirect: "error", credentials: "omit", signal: AbortSignal.timeout(60000) });
      if (!response.ok || !response.body || !/^(video\/(mp4|webm)|application\/octet-stream)(;|$)/i.test(response.headers.get("content-type") || "")) throw new Error("不是可访问的视频原件，请在微信合法保存后上传，不绕过验证");
      if (Number(response.headers.get("content-length")) > 25 * 1024 * 1024) { await response.body.cancel(); throw new Error("视频超过采集上限 25 MB，请保存后在客户端分批上传"); }
      const reader = response.body.getReader(); const chunks = []; let length = 0;
      try {
        while (true) { const { done, value } = await reader.read(); if (done) break; length += value.length; if (length > 25 * 1024 * 1024) throw new Error("视频超过 25 MB，获取已停止；未上传"); chunks.push(value); }
      } finally { await reader.cancel().catch(() => undefined); }
      if (!length || epoch !== current) throw new Error("原件为空或账号发生变化，未上传");
      const bytes = new Uint8Array(length); let position = 0; for (const chunk of chunks) { bytes.set(chunk, position); position += chunk.length; }
      const mp4 = length >= 12 && String.fromCharCode(...bytes.slice(4, 8)) === "ftyp";
      const webm = length >= 4 && bytes[0] === 0x1a && bytes[1] === 0x45 && bytes[2] === 0xdf && bytes[3] === 0xa3;
      if (!mp4 && !webm) throw new Error("文件头不是支持的视频原件，不采用页面返回的验证 / 分片内容");
      const sha = hex(await crypto.subtle.digest("SHA-256", bytes));
      const key = hex(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(source + "\n" + sha)));
      const name = (el("title").value || "微信视频").replace(/[\\/:*?"<>|\x00-\x1f]/g, "_").slice(0, 160) + (mp4 ? ".mp4" : ".webm");
      status("正在保存原件到本人后台；不会自动调用识别模型…");
      const result = await api("/knowledge/capture/files", { method: "POST", headers: { "Content-Type": "application/octet-stream", "X-Content-SHA256": sha, "Idempotency-Key": key, "X-Filename-Base64": utf8Base64(name), "X-Source-Base64": utf8Base64(source) }, body: bytes });
      if (current === epoch) status(`原件${result.duplicate ? "此前已保存，没有重传" : "已保存"}，资料编号：${result.item?.id || "原资料已移除，不恢复"}。请到客户端主动选择识别并核对。`);
    });
  } catch (e) { if (epoch === current) status(e.message || "保存结果未确认，未自动重试；请先查看客户端"); }
});
for (const id of ["title", "content", "publisher"]) el(id).addEventListener("input", () => { el("consent").checked = false; updateButtons(); });
void chrome.storage.session.get("hermesSession").then(data => { session = data.hermesSession && Date.parse(data.hermesSession.expires_at) > Date.now() ? data.hermesSession : null; displaySession(); });
void chrome.storage.local.get("hermesBackend").then(data => { if (typeof data.hermesBackend === "string") el("backend").value = data.hermesBackend; });
