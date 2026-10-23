import { useEffect, useState } from "react";
import type { AppSettings } from "../types";
import { knowledgeApi, type KnowledgeDetail, type KnowledgeRevision } from "../lib/knowledgeApi";

const methodLabel: Record<string, string> = { manual: "人工正文", wechat_article: "公众号提取", html: "网页提取", subtitle: "字幕提取", pdf_text: "PDF 正文", text_file: "文本文件", asr: "语音转写" };

export function KnowledgeHistory({ settings, item }: { settings: AppSettings; item: KnowledgeDetail }) {
  const [open, setOpen] = useState(false);
  return <details className="kb-history" onToggle={(e) => setOpen(e.currentTarget.open)}>
    <summary>正文版本记录与对照</summary>
    {open && <HistoryReader settings={settings} item={item} />}
  </details>;
}

function HistoryReader({ settings, item }: { settings: AppSettings; item: KnowledgeDetail }) {
  const [versions, setVersions] = useState(item.history);
  const [more, setMore] = useState(item.history.length === 20);
  const [loadingMore, setLoadingMore] = useState(false);
  const [left, setLeft] = useState(item.history[1]?.revision || item.history[0].revision);
  const [right, setRight] = useState(item.history[0].revision);
  const [bodies, setBodies] = useState<KnowledgeRevision[] | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [moreError, setMoreError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    setBodies(null); setError("");
    Promise.all([knowledgeApi.revision(settings, item.id, left, controller.signal), knowledgeApi.revision(settings, item.id, right, controller.signal)])
      .then((result) => { if (!controller.signal.aborted) setBodies(result); })
      .catch((e) => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "读取版本失败，请重试。"); });
    return () => controller.abort();
  }, [settings.backendUrl, settings.backendAuthToken, item.id, left, right, retry]);

  useEffect(() => {
    if (!loadingMore) return;
    const controller = new AbortController();
    setMoreError("");
    knowledgeApi.revisions(settings, item.id, versions[versions.length - 1].revision, controller.signal)
      .then((result) => {
        if (controller.signal.aborted) return;
        setVersions((old) => [...new Map([...old, ...result.items].map((r) => [r.revision, r])).values()]);
        setMore(result.next_before !== null); setLoadingMore(false);
      }).catch((e) => {
        if (!controller.signal.aborted) { setMoreError(e instanceof Error ? e.message : "读取更多版本失败。"); setLoadingMore(false); }
      });
    return () => controller.abort();
  }, [loadingMore, settings.backendUrl, settings.backendAuthToken, item.id]);

  const selector = (label: string, value: number, change: (value: number) => void) => <label>{label}<select value={value} onChange={(e) => change(Number(e.target.value))}>
    {versions.map((r) => <option key={r.revision} value={r.revision}>版本 {r.revision} · {methodLabel[r.method] || "已保存正文"} · {new Date(`${r.created_at.replace(/Z$/, "")}Z`).toLocaleString("zh-CN")}</option>)}
  </select></label>;

  return <section className="kb-version-reader" aria-label="历史正文对照">
    <p>仅对照已保存的正文，不修改当前资料。历史版本不参与当前检索；来源名称和标签不是正文快照的一部分。</p>
    <div className="kb-version-selectors">{selector("左侧正文版本", left, setLeft)}{selector("右侧正文版本", right, setRight)}</div>
    {moreError && <p role="alert">{moreError}</p>}
    {more && <button className="secondary-button" disabled={loadingMore} onClick={() => setLoadingMore(true)}>{loadingMore ? "正在读取更多…" : "读取更早版本"}</button>}
    {error ? <div role="alert"><p>{error}</p><button className="secondary-button" onClick={() => setRetry((n) => n + 1)}>重试读取正文</button></div> : !bodies ? <p role="status">正在读取历史正文…</p> : <>
      <p role="status">{bodies[0].content === bodies[1].content ? "两侧正文相同。" : "两侧正文不同，可对照核对。"} 左侧 {bodies[0].content.length.toLocaleString()} 字符，右侧 {bodies[1].content.length.toLocaleString()} 字符。此视图不标注逐字差异。</p>
      <div className="kb-version-bodies">{bodies.map((body, index) => <section key={index} aria-label={`${index === 0 ? "左侧" : "右侧"}版本 ${body.revision} 正文`}>
        <h3>{index === 0 ? "左侧" : "右侧"} · 版本 {body.revision}</h3><div className="kb-source-text">{body.content}</div>
      </section>)}</div>
    </>}
  </section>;
}
