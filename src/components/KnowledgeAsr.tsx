import { useState } from "react";
import type { KnowledgeCapabilities, KnowledgeDetail } from "../lib/knowledgeApi";

export function KnowledgeAsr({ item, caps, busy, onSubmit }: {
  item: KnowledgeDetail; caps: KnowledgeCapabilities | null; busy: boolean;
  onSubmit: (assetId: string) => Promise<void>;
}) {
  const assets = item.assets.filter((a) => /\.(mp4|mov|mkv|webm|mp3|m4a|wav|aac|ogg|silk)$/i.test(a.filename));
  const [chosen, setChosen] = useState(assets[0]?.id || "");
  const [consent, setConsent] = useState(false);
  if (!assets.length || item.deleted_at) return null;
  const silkMissing = /\.silk$/i.test(assets.find(a => a.id === chosen)?.filename || "") && !caps?.silk_available;
  const disabled = busy || ["received", "extracting", "retry_wait"].includes(item.status) || !caps?.cloud_asr_enabled;
  const form = <>
    <h3>音频 / 视频转文字</h3>
    <p id="kb-asr-disclosure">{caps?.cloud_asr_enabled
      ? `本地提取所选文件的音轨，发送到${caps.cloud_asr_provider || "豆包语音 / 火山引擎"}，可能产生费用。每份最多 ${caps.cloud_asr_max_minutes || 10} 分钟；视频画面不会发送或识别。转写后需核对，才参与 Hermes 检索。失败不自动重提，再次转写可能再次计费。`
      : "后台尚未启用云端转写、缺少独立 ASR 权限或转换工具。原件已保存，可先补充 SRT / VTT 字幕或正文；OCR Key 不等于语音识别 Key。"}</p>
    <form onSubmit={(event) => {
      event.preventDefault();
      if (disabled || silkMissing || !consent || !assets.some((a) => a.id === chosen)) return;
      setConsent(false); void onSubmit(chosen);
    }}>
      <label className="kb-ocr-file">转写原始文件<select value={chosen} disabled={disabled} onChange={(e) => { setChosen(e.target.value); setConsent(false); }}>
        {assets.map((asset) => <option key={asset.id} value={asset.id}>{asset.filename}</option>)}
      </select></label>
      <label className="kb-ocr-consent"><input type="checkbox" checked={consent} disabled={disabled} aria-describedby="kb-asr-disclosure" onChange={(e) => setConsent(e.target.checked)} />同意发送所选素材的音轨到云端转写</label>
      {silkMissing && <p>原生微信语音已保存；后台需先配置可用的本地 SILK 解码器，再主动转写。也可上传 WAV / MP3。</p>}
      <button className="primary-button" disabled={disabled || silkMissing || !consent || !chosen}>发送音轨并转写</button>
    </form>
  </>;
  return <section className="kb-ocr" aria-label="云端语音转写">
    {["needs_review", "ready"].includes(item.status) && item.method.startsWith("volc_asr")
      ? <details><summary>重新转写音轨（可能再次计费）</summary>{form}</details> : form}
  </section>;
}
