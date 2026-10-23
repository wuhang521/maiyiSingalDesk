import { useState } from "react";
import type { KnowledgeCapabilities, KnowledgeDetail } from "../lib/knowledgeApi";

export function KnowledgeOcr({ item, caps, busy, onSubmit }: {
  item: KnowledgeDetail; caps: KnowledgeCapabilities | null; busy: boolean;
  onSubmit: (assetId: string) => Promise<void>;
}) {
  const assets = item.assets.filter((asset) => /\.(jpe?g|png|pdf)$/i.test(asset.filename) && asset.size <= 20 * 1024 * 1024);
  const [chosen, setChosen] = useState(assets[0]?.id || "");
  const [consent, setConsent] = useState(false);
  if (!assets.length || item.deleted_at) return null;
  const disabled = busy || ["received", "extracting", "retry_wait"].includes(item.status) || !caps?.ocr_enabled;
  const form = <>
    <h3>图片 / 扫描 PDF 识别</h3>
    <p id="kb-ocr-disclosure">{caps?.ocr_enabled
      ? `将选中的整份原件发送到${caps.ocr_provider || "豆包 / 火山方舟"}，可能产生费用。每份文件最多 20 MB，PDF 最多 ${caps.ocr_max_pdf_pages || 10} 页；识别完成后需核对，才参与 Hermes 检索和问答。不会自动重复付费请求，再次识别可能再次计费。`
      : "后台尚未启用豆包 OCR 或缺少凭据，请联系管理员。原始图片 / PDF 已保留，也可手动补充正文。"}</p>
    <form onSubmit={(event) => {
      event.preventDefault();
      if (disabled || !consent || !assets.some((a) => a.id === chosen)) return;
      setConsent(false);
      void onSubmit(chosen);
    }}>
      <label className="kb-ocr-file">识别原始文件<select value={chosen} disabled={disabled} onChange={(e) => { setChosen(e.target.value); setConsent(false); }}>
        {assets.map((asset) => <option key={asset.id} value={asset.id}>{asset.filename}</option>)}
      </select></label>
      <label className="kb-ocr-consent"><input type="checkbox" checked={consent} disabled={disabled} aria-describedby="kb-ocr-disclosure" onChange={(e) => setConsent(e.target.checked)} />同意发送选中的整份原件到云端识别</label>
      <button className="primary-button" disabled={disabled || !consent || !chosen}>发送文件并识别</button>
    </form>
  </>;
  return <section className="kb-ocr" aria-label="云端文字识别">
    {["needs_review", "ready"].includes(item.status) && item.method.startsWith("doubao_ocr")
      ? <details><summary>重新识别原件（可能再次计费）</summary>{form}</details>
      : form}
  </section>;
}
