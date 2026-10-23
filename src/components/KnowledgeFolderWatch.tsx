import { useEffect, useRef, useState } from "react";
type State = { running: boolean; folder: string; message: string; files: { name: string; size: number; updatedAt: number }[] };
export function KnowledgeFolderWatch({ disabled, onImport }: { disabled: boolean; onImport: () => void }) {
  const [state, setState] = useState<State | null>(null); const [busy, setBusy] = useState(false); const [error, setError] = useState(""); const live = useRef(true); const pending = useRef(false);
  useEffect(() => { live.current = true; const refresh = async () => { if (pending.current) return; try { const value = await window.signalDesk?.knowledgeWatchStatus?.(); if (value && live.current) setState(value); } catch (e) { if (live.current) setError(e instanceof Error ? e.message : "本地观察状态不可读"); } }; void refresh(); const timer = window.setInterval(() => void refresh(), 5000); return () => { live.current = false; window.clearInterval(timer); }; }, []);
  if (!window.signalDesk?.knowledgeWatchStart) return null;
  async function action(start: boolean) { if (pending.current || disabled) return; pending.current = true; setBusy(true); setError(""); try { const value = start ? await window.signalDesk!.knowledgeWatchStart!() : await window.signalDesk!.knowledgeWatchStop!(); if (live.current) setState(value); } catch (e) { if (live.current) setError(e instanceof Error ? e.message : "本地观察操作失败"); } finally { pending.current = false; if (live.current) setBusy(false); } }
  return <details className="kb-auxiliary"><summary>本地文件夹观察 · {state?.running ? "观察中，不自动上传" : "未开启"}</summary><p>只在本机查看选定文件夹的文件名、大小和修改时间，不读取内容、不含子目录、不自动上传；应用重启不会自动开启，退出账号会停止。自动上传版本仍需明确指定目录与接收后台。</p>
    {error && <p role="alert">{error}</p>}{state?.folder && <p>所选目录：{state.folder}</p>}<p>{state?.message}</p>
    <button className="secondary-button" disabled={disabled || busy} onClick={() => void action(true)}>选择目录并本地观察</button> <button className="secondary-button" disabled={disabled || busy || !state?.running} onClick={() => void action(false)}>停止观察</button> <button className="primary-button" disabled={disabled || busy} onClick={onImport}>手动选择文件上传</button>
    {!!state?.files.length && <ul>{state.files.map(f => <li key={f.name}>{f.name} · {(f.size / 1024 / 1024).toFixed(2)} MB · {new Date(f.updatedAt).toLocaleString("zh-CN")}</li>)}</ul>}
  </details>;
}
