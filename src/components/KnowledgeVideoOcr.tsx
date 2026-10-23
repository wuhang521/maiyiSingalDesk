import { useState } from "react";
import type { KnowledgeCapabilities, KnowledgeDetail } from "../lib/knowledgeApi";

export function KnowledgeVideoOcr({ item,caps,busy,onSubmit }: {
  item: KnowledgeDetail; caps: KnowledgeCapabilities | null; busy: boolean; onSubmit: (assetId: string) => Promise<void>;
}) {
  const assets=item.assets.filter((a)=>/\.(mp4|mov|mkv|webm)$/i.test(a.filename));
  const [chosen,setChosen]=useState(assets[0]?.id || "");
  const [consent,setConsent]=useState(false);
  if (!assets.length || item.deleted_at) return null;
  const disabled=busy || ["received","extracting","retry_wait"].includes(item.status) || !caps?.video_ocr_enabled;
  const form=<>
    <h3>视频画面采样 OCR · 非完整识别</h3>
    <p id="kb-video-ocr-disclosure">{caps?.video_ocr_enabled
      ? `本地从不超过 10 分钟的视频中均匀采样最多 ${caps.video_ocr_max_frames || 6} 帧，将去重后的图片发送豆包 OCR。每帧可能单独计费，不发送或识别音轨，可能遗漏未采样到的字幕和讲义。形成新的正文版本，旧正文保留在历史；核对后才入库，失败不自动重试。`
      : "后台尚未启用视频画面采样 OCR 或缺少转换工具。可手动保存关键截图，再用图片 OCR；这不等于识别完整视频。"}</p>
    <form onSubmit={(event)=>{
      event.preventDefault();
      if(disabled || !consent || !assets.some((a)=>a.id===chosen)) return;
      setConsent(false); void onSubmit(chosen);
    }}>
      <label className="kb-ocr-file">采样视频原件<select value={chosen} disabled={disabled} onChange={(event)=>{setChosen(event.target.value);setConsent(false);}}>
        {assets.map((asset)=><option key={asset.id} value={asset.id}>{asset.filename}</option>)}
      </select></label>
      <label className="kb-ocr-consent"><input type="checkbox" checked={consent} disabled={disabled} aria-describedby="kb-video-ocr-disclosure" onChange={(event)=>setConsent(event.target.checked)} />同意发送采样图片并按画面 OCR 计费</label>
      <button className="primary-button" disabled={disabled || !consent || !chosen}>采样画面并识别文字</button>
    </form>
  </>;
  return <section className="kb-ocr" aria-label="视频画面文字识别">
    {["needs_review","ready"].includes(item.status) && item.method.startsWith("video_ocr")
      ? <details><summary>重新识别采样画面（可能再次计费）</summary>{form}</details>:form}
  </section>;
}
