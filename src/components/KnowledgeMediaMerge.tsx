import { useEffect, useState } from "react";
import type { AppSettings } from "../types";
import { knowledgeApi, type KnowledgeDetail, type KnowledgeMediaPart } from "../lib/knowledgeApi";
export function KnowledgeMediaMerge({ settings, item, busy, onMerge, onConfirm }: { settings: AppSettings; item: KnowledgeDetail; busy: boolean; onMerge: (speech: string, frames: string) => void; onConfirm: () => void }) {
  const [parts, setParts] = useState<KnowledgeMediaPart[]>([]); const [speech, setSpeech] = useState(""); const [frames, setFrames] = useState(""); const [consent, setConsent] = useState(false); const [error, setError] = useState("");
  useEffect(() => { const controller = new AbortController(); knowledgeApi.mediaParts(settings, item.id, controller.signal).then(r => setParts(r.items)).catch(e => { if (!controller.signal.aborted) setError(e.message); }); return () => controller.abort(); }, [settings.backendUrl, settings.backendAuthToken, item.id, item.revision]);
  const selectedSpeech = parts.find(p => p.id === speech); const selectedFrames = parts.find(p => p.id === frames);
  const match = selectedSpeech && selectedFrames && selectedSpeech.asset_id === selectedFrames.asset_id;
  if (!parts.length && !error) return null;
  return <details className="kb-notice"><summary>合并语音与采样画面</summary><p>分别识别同一个原视频后，选择两份结果。合并不调用模型、不推断两者含义对应；旧结果保留，新正文待核对。</p>
    {error && <p role="alert" className="kb-error">{error}</p>}
    <fieldset disabled={busy}><label>语音转写<select value={speech} onChange={e => { setSpeech(e.target.value); setConsent(false); }}><option value="">请选择</option>{parts.filter(p => p.method === "volc_asr_draft").map(p => <option key={p.id} value={p.id}>版本 {p.revision} · 原件 {p.asset_id.slice(0, 8)}</option>)}</select></label>
      <label>画面 OCR<select value={frames} onChange={e => { setFrames(e.target.value); setConsent(false); }}><option value="">请选择</option>{parts.filter(p => p.method === "video_ocr_draft").map(p => <option key={p.id} value={p.id}>版本 {p.revision} · 原件 {p.asset_id.slice(0, 8)}</option>)}</select></label>
      {selectedSpeech && <details><summary>预览所选转写</summary><p className="kb-body">{selectedSpeech.content}</p></details>}{selectedFrames && <details><summary>预览所选画面文字</summary><p className="kb-body">{selectedFrames.content}</p></details>}
      {speech && frames && !match && <p role="alert">原件不一致，不能合并。</p>}
      <label className="kb-consent"><input type="checkbox" checked={consent} onChange={e => setConsent(e.target.checked)} />我了解合并仍有识别错误，需要核对后入库</label>
      <button className="secondary-button" disabled={!consent || !match || ["received", "extracting", "retry_wait"].includes(item.status)} onClick={() => onMerge(speech, frames)}>生成合并草稿</button>
      {item.method === "media_merge_draft" && item.status === "needs_review" && <button className="primary-button" onClick={() => { if (window.confirm("已经核对当前合并正文与原视频，确认入库并参与检索？")) onConfirm(); }}>已核对，确认入库</button>}
    </fieldset>
  </details>;
}
