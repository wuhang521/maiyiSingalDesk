import { useEffect, useRef, useState } from "react";
import type { AppSettings } from "../types";
import { knowledgeApi, type KnowledgeNotification, type KnowledgePreference } from "../lib/knowledgeApi";

const delivery: Record<string, string> = { inbox: "客户端通知", pending: "等待投递", sending: "正在投递", sent: "Hermes 已投递", unknown: "投递结果未确认", failed: "投递失败", cancelled: "外部投递已取消" };
export function KnowledgeNotifications({ settings, canManage, disabled }: { settings: AppSettings; canManage: boolean; disabled: boolean }) {
  const [prefs, setPrefs] = useState<KnowledgePreference | null>(null);
  const [items, setItems] = useState<KnowledgeNotification[]>([]);
  const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  const live = useRef(true); const running = useRef(false);
  useEffect(() => {
    live.current = true; const controller = new AbortController();
    knowledgeApi.preferences(settings, controller.signal).then(p => { if (live.current) setPrefs(p); }).catch(e => { if (!controller.signal.aborted) setError(e.message); });
    const refresh = () => knowledgeApi.notifications(settings, controller.signal).then(r => { if (live.current) setItems(r.items); }).catch(e => { if (!controller.signal.aborted) setError(e.message); });
    void refresh(); const timer = window.setInterval(() => { if (!running.current) void refresh(); }, 30000);
    return () => { live.current = false; controller.abort(); window.clearInterval(timer); };
  }, [settings.backendUrl, settings.backendAuthToken]);
  async function action(fn: () => Promise<void>) {
    if (running.current || disabled) return;
    running.current = true; setBusy(true); setError("");
    try { await fn(); } catch (e) { if (live.current) setError(e instanceof Error ? e.message : "操作失败，请刷新后重试。"); }
    finally { running.current = false; if (live.current) setBusy(false); }
  }
  return <details className="kb-notice"><summary>通知设置与未读消息 · {items.length}</summary>
    <p>仅通知本人；默认关闭。通知只包含处理状态和资料编号，不包含正文。投递结果未确认时不会自动重发。</p>
    {error && <p className="kb-error" role="alert">{error}<button className="secondary-button" disabled={busy || disabled} onClick={() => void action(async () => { const p = await knowledgeApi.preferences(settings); const r = await knowledgeApi.notifications(settings); if (live.current) { setPrefs(p); setItems(r.items); } })}>刷新</button></p>}
    {prefs ? <form onSubmit={e => { e.preventDefault(); void action(async () => { const result = await knowledgeApi.savePreferences(settings, prefs); if (live.current) setPrefs({ ...result, channels: prefs.channels }); }); }}>
      <fieldset disabled={busy || disabled}>
        <label className="kb-consent"><input type="checkbox" checked={prefs.desktop_notifications} onChange={e => setPrefs({ ...prefs, desktop_notifications: e.target.checked })} />客户端桌面通知（还需开启应用全局通知）</label>
        <label className="kb-consent"><input type="checkbox" disabled={!prefs.hermes_ready} checked={prefs.hermes_notifications} onChange={e => setPrefs({ ...prefs, hermes_notifications: e.target.checked })} />发送到本人绑定的 Hermes 私聊渠道</label>
        {!prefs.hermes_ready && <p>后台通知投递未启用，仍可查看客户端消息。</p>}
        {prefs.hermes_notifications && <label>投递渠道<select required value={prefs.channel_id ?? ""} onChange={e => setPrefs({ ...prefs, channel_id: Number(e.target.value) || null })}><option value="">请选择本人渠道</option>{prefs.channels?.map(c => <option key={c.id} value={c.id}>{c.platform} · 渠道 {c.id}</option>)}</select></label>}
        {canManage && <label className="kb-consent"><input type="checkbox" checked={prefs.admin_alerts} onChange={e => setPrefs({ ...prefs, admin_alerts: e.target.checked })} />订阅后台心跳和队列告警（不含其他人的资料）</label>}
        <button className="secondary-button" type="submit">{busy ? "正在保存…" : "保存通知设置"}</button>
      </fieldset>
    </form> : <p role="status">正在读取通知设置…</p>}
    {!items.length && <p>暂无未读消息。处理结束后，已开启的通知会显示在这里。</p>}
    <ul>{items.map(n => <li key={n.id}><p>{n.message}</p><small>{delivery[n.delivery_state] || "状态待确认"}</small> <button className="secondary-button" disabled={disabled || busy} onClick={() => void action(async () => { await knowledgeApi.readNotification(settings, n.id); if (live.current) setItems(current => current.filter(row => row.id !== n.id)); })}>标记已读</button>
      {["unknown", "failed"].includes(n.delivery_state) && <button className="secondary-button" disabled={disabled || busy} onClick={() => { if (window.confirm("原投递可能已经成功，重发可能出现重复通知。确认仍要重发？")) void action(async () => { await knowledgeApi.retryNotification(settings, n.id); const r = await knowledgeApi.notifications(settings); if (live.current) setItems(r.items); }); }}>确认风险后重发</button>}
    </li>)}</ul>
  </details>;
}
