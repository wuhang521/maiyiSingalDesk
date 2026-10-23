import { useEffect, useRef, useState } from "react";
import type { AppSettings } from "../types";
import { knowledgeApi, type KnowledgeCapabilities, type KnowledgeDetail, type KnowledgeOrganization as Organization } from "../lib/knowledgeApi";

export function KnowledgeOrganization({ settings, item, caps, busy, onGenerate, onUseTags }: {
  settings: AppSettings; item: KnowledgeDetail; caps: KnowledgeCapabilities | null; busy: boolean;
  onGenerate: () => Promise<Organization | null>; onUseTags: (tags: string[]) => void;
}) {
  const [result, setResult] = useState<Organization | null>(null);
  const [consent, setConsent] = useState(false);
  const [error, setError] = useState("");
  const live = useRef(true);
  const requestEpoch = useRef(0);
  useEffect(() => {
    live.current = true;
    const epoch=++requestEpoch.current;
    if (caps?.organization_enabled === undefined || item.deleted_at) return () => { live.current = false; };
    const controller = new AbortController();
    knowledgeApi.organization(settings, item.id, controller.signal).then(({ result: value }) => {
      if (!controller.signal.aborted && epoch===requestEpoch.current) setResult(value);
    }).catch((e) => { if (!controller.signal.aborted && epoch===requestEpoch.current) setError(e instanceof Error ? e.message : "整理记录读取失败"); });
    return () => { live.current = false; controller.abort(); };
  }, [settings.backendUrl, settings.backendAuthToken, item.id, item.revision, item.deleted_at, caps?.organization_enabled]);
  if (item.deleted_at || !item.content) return null;
  const disabled = busy || item.status !== "ready" || !caps?.organization_enabled || item.content.length > 24000;
  return <section className="kb-ocr" aria-label="AI 资料整理">
    <h3>AI 摘要、要点与建议标签</h3>
    <p id="kb-organize-disclosure">{caps?.organization_enabled
      ? `将这份资料的全部当前正文发送到${caps.organization_provider || "后台文字模型"}，可能产生费用。只处理已入库正文，最多 24000 字符；结果单独保存，不覆盖原文，不作为检索依据。需核对出处，失败不自动重试。`
      : "后台尚未启用 AI 整理或缺少文字模型凭据；不影响原文阅读与检索。"}</p>
    {item.content.length > 24000 && <p>正文超过单次整理上限，请拆分资料；不会只发送前半部分却称为完整摘要。</p>}
    {error && <p role="alert">{error}</p>}
    <form onSubmit={(event) => {
      event.preventDefault();
      if (disabled || !consent) return;
      const epoch=++requestEpoch.current;
      setConsent(false); setError("");
      void onGenerate().then((value) => { if (live.current && value && epoch===requestEpoch.current) setResult(value); });
    }}>
      <label className="kb-ocr-consent"><input type="checkbox" checked={consent} disabled={disabled} aria-describedby="kb-organize-disclosure" onChange={(e) => setConsent(e.target.checked)} />同意发送本资料当前正文并生成 AI 整理</label>
      <button className="primary-button" disabled={disabled || !consent}>{result ? "重新生成整理（可能再次计费）" : "发送正文并整理"}</button>
    </form>
    {result && <div className="kb-source-text">
      <h3>AI 整理 · 请核对原文</h3><p>{result.status_note}</p>
      {result.source_revision === item.revision && result.state === "draft" && <>
        <h4>摘要</h4><p>{result.payload.summary?.text} {result.payload.summary?.citations.map((i) => `[${i}]`).join("")}</p>
        <h4>核心要点</h4><ul>{result.payload.points?.map((point, index) => <li key={index}>{point.text} {point.citations.map((i) => `[${i}]`).join("")}</li>)}</ul>
        {!!result.payload.tags?.length && <><h4>建议标签</h4><p>{result.payload.tags.join(" · ")}</p><button className="secondary-button" disabled={busy} onClick={() => onUseTags(result.payload.tags || [])}>填入编辑器，核对后保存标签</button></>}
        <details><summary>查看整理依据 · 正文版本 {result.source_revision}</summary>{result.payload.citations?.map((citation) => <p key={citation.index}>[{citation.index}] {citation.locator}<br />{citation.text}</p>)}</details>
      </>}
    </div>}
  </section>;
}
