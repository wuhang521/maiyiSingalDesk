import { useEffect, useState } from "react";
import type { AppSettings } from "../types";
import { knowledgeApi, type KnowledgeUsage as Usage } from "../lib/knowledgeApi";

export function KnowledgeUsage({ settings, busy }: { settings: AppSettings; busy: boolean }) {
  const [usage, setUsage] = useState<Usage | null>(null);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    if (busy) return;
    const controller = new AbortController();
    setError("");
    knowledgeApi.usage(settings, controller.signal).then((value) => {
      if (!controller.signal.aborted) setUsage(value);
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setError(error instanceof Error ? error.message : "额度读取失败");
    });
    return () => controller.abort();
  }, [settings.backendUrl, settings.backendAuthToken, busy, refresh]);
  const reset = usage ? new Date(/Z$|[+-]\d\d:\d\d$/.test(usage.next_daily_reset)
    ? usage.next_daily_reset : `${usage.next_daily_reset}Z`).toLocaleString("zh-CN") : "";
  return <details className="kb-auxiliary" aria-label="云端处理提交额度">
    <summary>云端处理额度{usage && !error ? ` · 今日剩余 ${usage.daily_remaining} / ${usage.daily_limit} 次` : ""}</summary>
    {error ? <p role="alert">{error}（旧数值不作为可用额度；后台会在提交时再次检查。）</p>
      : usage ? <><p>今日已提交 {usage.daily_submissions} 次，本月已提交 {usage.monthly_submissions} / {usage.monthly_limit} 次。
        本月剩余 {usage.monthly_remaining} 次。今日额度预计在 {reset}（本机时间）重置；按 UTC 日 / 月统计。</p>
        {(!usage.daily_remaining || !usage.monthly_remaining) && <p>当前提交额度已用完。请等待重置或联系管理员，原件保存与本地检索不受影响。</p>}</>
      : <p>正在读取本人额度…</p>}
    <p>这里记录 OCR、云端转写、画面 OCR、AI 整理、AI 综合问答及语义索引 / 查询的提交预留次数，不是人民币费用。
      失败或结果未确认不会自动退还次数，删除资料也不会清零。一次视频画面 OCR 最多调用模型 6 次，实际费用以服务商账单为准。</p>
    <button type="button" className="secondary-button" disabled={busy} onClick={() => setRefresh((n) => n + 1)}>刷新额度</button>
  </details>;
}
