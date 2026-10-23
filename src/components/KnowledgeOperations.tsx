import { useEffect, useState } from "react";
import type { AppSettings } from "../types";
import { knowledgeApi, type KnowledgeOperations as Operations } from "../lib/knowledgeApi";

const labels: Record<string, string> = { queued: "排队", running: "执行中", retry_wait: "等待本地重试", done: "已结束", failed: "失败", cancelled: "已取消" };

export function KnowledgeOperations({ settings, busy }: { settings: AppSettings; busy: boolean }) {
  const [data, setData] = useState<Operations | null>(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    if (!open || busy) return;
    const controller = new AbortController();
    setData(null); setError("");
    knowledgeApi.operations(settings, controller.signal).then((value) => {
      if (!controller.signal.aborted) setData(value);
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setError(error instanceof Error ? error.message : "队列状态读取失败");
    });
    return () => controller.abort();
  }, [settings.backendUrl, settings.backendAuthToken, busy, open, refresh]);
  return <details className="kb-auxiliary" onToggle={(event) => setOpen(event.currentTarget.open)} aria-label="知识库后台队列概况">
    <summary>后台队列概况 · 管理员</summary>
    <p>仅显示全站任务计数，不包含人员、资料标题或正文。结束任务也计入历史统计；“已结束”不等于正文已核对入库。</p>
    {error ? <p role="alert">{error}</p> : data ? <>
      <ul>{Object.entries(data.job_states).map(([state, count]) => <li key={state}>{labels[state] || state}：{count}</li>)}</ul>
      <p>最早排队任务已等待 {Math.floor(data.oldest_wait_seconds / 60)} 分钟。
        原件清理记录 {data.file_cleanup_pending} 条，云端文件清理记录 {data.remote_cleanup_pending} 条。</p>
      <p>{data.worker_health === "recent_heartbeat" ? `最近观察到 ${data.recent_workers || 0} 个识别进程心跳。` : data.worker_health === "stale_heartbeat" ? "识别进程心跳已过期，请检查服务。" : "尚未观察到新版识别进程心跳，请检查后台版本和进程。"} 心跳只表示进程存活，不代表模型权限或识别质量通过验收。</p>
      {data.active_alerts?.length ? <ul>{data.active_alerts.map((alert) => <li key={alert.kind}>{({ worker_missing: "缺少近期识别心跳", queue_wait: "队列等待超时", queue_full: "队列达到容量阈值" } as Record<string, string>)[alert.kind] || alert.kind} · 等待 {Math.floor((alert.metrics.wait_seconds || 0) / 60)} 分钟</li>)}</ul> : <p>当前没有已记录的活动告警。通知与监控 worker 未启用时，这不代表没有故障。</p>}
    </> : <p>展开后读取后台队列…</p>}
    <button className="secondary-button" type="button" disabled={busy} onClick={() => setRefresh((n) => n + 1)}>刷新队列概况</button>
  </details>;
}
