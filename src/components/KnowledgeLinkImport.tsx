import { useEffect, useRef, useState } from "react";

export interface LinkImportResult { url: string; saved: boolean; note: string }

export function KnowledgeLinkImport({ disabled, onImport }: {
  disabled: boolean; onImport: (links: string[], stopped: () => boolean) => Promise<LinkImportResult[]>;
}) {
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [running, setRunning] = useState(false);
  const [results, setResults] = useState<LinkImportResult[]>([]);
  const stop = useRef(false);
  const live = useRef(true);
  useEffect(() => { live.current = true; return () => { live.current = false; stop.current = true; }; }, []);
  async function submit() {
    if (disabled || running) return;
    const links = [...new Set(text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean))];
    if (!links.length || links.length > 20) { setError("每行一个链接，每批需要 1 至 20 个不同链接；尚未开始保存。"); return; }
    try {
      for (const value of links) {
        const url = new URL(value);
        if (url.protocol !== "https:" || !["mp.weixin.qq.com", "weixin.qq.com"].includes(url.hostname)
          || url.username || url.password || (url.port && url.port !== "443")) throw new Error();
      }
    } catch { setError("只接收 HTTPS 公众号或视频号分享链接，请检查每一行；尚未开始保存。"); return; }
    stop.current = false; setRunning(true); setError(""); setResults([]);
    try {
      const result = await onImport(links, () => stop.current);
      if (live.current) {
        setResults(result);
        if (result.length < links.length) setError(`后续 ${links.length - result.length} 个链接未开始保存；已经保存的资料不会删除。`);
      }
    } catch { if (live.current) setError("本批结果未确认，请刷新资料列表核对。未自动重试。"); }
    finally { if (live.current) setRunning(false); }
  }
  return <details className="kb-auxiliary" aria-label="批量收集微信链接">
    <summary>批量收集公众号 / 视频号链接</summary>
    <p>每行一个链接，每批最多 20 个，保存到本人知识库。公众号尝试提取公开正文；视频号保存来源后，仍需补充原视频、字幕或文字。不绕过微信验证，不自动发起付费识别。</p>
    <form onSubmit={(event) => { event.preventDefault(); void submit(); }}>
      <label>分享链接列表<textarea rows={4} maxLength={80000} value={text} disabled={disabled || running} onChange={(event) => setText(event.target.value)} /></label>
      <button type="submit" className="secondary-button" disabled={disabled || running || !text.trim()}>{running ? "正在逐个保存…" : "逐个保存链接"}</button>
      {running && <button type="button" className="secondary-button" onClick={() => { stop.current = true; }}>当前链接结束后停止</button>}
    </form>
    {error && <p role="alert">{error}</p>}
    {!!results.length && <ul aria-label="本批链接保存结果">{results.map((result, index) => <li key={index}>{result.url}<br />{result.note}</li>)}</ul>}
  </details>;
}
