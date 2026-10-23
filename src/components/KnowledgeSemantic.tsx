import { useEffect, useRef, useState } from "react";
import type { AppSettings } from "../types";
import { knowledgeApi, type KnowledgeCapabilities, type KnowledgeDetail, type KnowledgeSemanticInfo } from "../lib/knowledgeApi";

const labels: Record<string, string> = { absent: "未建立", processing: "处理中", ready: "已建立", unknown: "结果未确认", stale_model: "模型已改变，需重新建立" };

export function KnowledgeSemantic({ settings, item, caps, busy, onChange }: {
  settings: AppSettings; item: KnowledgeDetail; caps: KnowledgeCapabilities | null; busy: boolean;
  onChange: (remove: boolean) => Promise<KnowledgeSemanticInfo | null>;
}) {
  const [info, setInfo] = useState<KnowledgeSemanticInfo | null>(null);
  const [consent, setConsent] = useState(false);
  const [error, setError] = useState("");
  const live = useRef(true); const epoch = useRef(0);
  useEffect(() => {
    live.current = true;
    const request = ++epoch.current;
    if (caps?.semantic_enabled === undefined || item.deleted_at) return () => { live.current = false; };
    const controller = new AbortController();
    knowledgeApi.semanticInfo(settings, item.id, controller.signal).then((value) => {
      if (!controller.signal.aborted && request === epoch.current) setInfo(value);
    }).catch((error: unknown) => {
      if (!controller.signal.aborted && request === epoch.current) setError(error instanceof Error ? error.message : "索引状态读取失败");
    });
    return () => { live.current = false; controller.abort(); };
  }, [settings.backendUrl, settings.backendAuthToken, item.id, item.revision, item.deleted_at, caps?.semantic_enabled]);
  if (caps?.semantic_enabled === undefined || item.deleted_at || !item.content) return null;
  async function change(remove: boolean) {
    const request = ++epoch.current;
    setConsent(false); setError("");
    const value = await onChange(remove);
    if (live.current && request === epoch.current && value) setInfo(value);
  }
  const disabled = busy || item.status !== "ready" || !caps.semantic_enabled || item.content.length > 24000 || info?.state === "ready" || info?.state === "processing";
  return <section className="kb-ocr" aria-label="本资料语义索引">
    <h3>语义索引 · 按意思找原文</h3>
    <p id="kb-semantic-disclosure">只在你同意后，把本资料当前正文发送到火山方舟文本向量模型，可能产生费用；不发送整库和原文件。
      单份最多 24000 字符 / 40 个片段，本人最多 {caps.semantic_max_chunks ?? 500} 个片段。结果仅用于相似原文检索，不生成结论。修订正文会清除本索引，不自动重建。</p>
    {!caps.semantic_enabled && <p>后台尚未启用或缺少专用向量模型配置；仍可用关键词查找原文。</p>}
    {info && <p>当前正文版本 {info.source_revision}：{labels[info.state] || info.state}{info.state === "ready" && ` · ${info.chunk_count} 个片段`}。
      {info.state === "unknown" && "未自动重试，再次建立可能再次计费。"}</p>}
    {error && <p role="alert">{error}</p>}
    <form onSubmit={(event) => { event.preventDefault(); if (!disabled && consent) void change(false); }}>
      <label className="kb-ocr-consent"><input type="checkbox" checked={consent} disabled={disabled} aria-describedby="kb-semantic-disclosure" onChange={(event) => setConsent(event.target.checked)} />同意发送本资料正文建立语义索引</label>
      <button className="primary-button" disabled={disabled || !consent}>发送正文并建立索引</button>
    </form>
    {info && info.state !== "absent" && <button className="secondary-button" disabled={busy} onClick={() => {
      if (window.confirm("只移除本资料的语义索引，不删除正文或原件。之后重新建立索引需要再次同意并可能计费。继续吗？")) void change(true);
    }}>移除语义索引（保留原文）</button>}
  </section>;
}
