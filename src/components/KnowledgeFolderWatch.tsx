import { useEffect, useRef, useState } from "react";

type State = {
  running: boolean; folder: string; message: string;
  mode?: string; backend?: string; saved?: number; duplicates?: number; failed?: number;
  files: { name: string; size: number; updatedAt: number; status?: string }[];
};

export function KnowledgeFolderWatch({ disabled, onImport, onSaved }: {
  disabled: boolean; onImport: () => void; onSaved: () => void;
}) {
  const [state, setState] = useState<State | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const live = useRef(true);
  const pending = useRef(false);
  const lastSaved = useRef(0);
  const savedCallback = useRef(onSaved);
  savedCallback.current = onSaved;
  function receive(value: State) {
    if (!live.current) return;
    setState(value);
    if ((value.saved || 0) > lastSaved.current) savedCallback.current();
    lastSaved.current = value.saved || 0;
  }
  useEffect(() => {
    live.current = true;
    const refresh = async () => {
      if (pending.current) return;
      try {
        const value = await window.signalDesk?.knowledgeWatchStatus?.();
        if (value) receive(value);
      } catch (error) { if (live.current) setError(error instanceof Error ? error.message : "文件夹状态读取失败，请重新打开客户端"); }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 3000);
    return () => { live.current = false; window.clearInterval(timer); };
  }, []);
  if (!window.signalDesk?.knowledgeWatchStart) return null;
  async function action(mode: "import" | "observe" | "stop") {
    if (pending.current || disabled) return;
    pending.current = true; setBusy(true); setError("");
    try {
      const value = mode === "stop" ? await window.signalDesk!.knowledgeWatchStop!()
        : await window.signalDesk!.knowledgeWatchStart!({ autoImport: mode === "import" });
      receive(value);
    } catch (error) { if (live.current) setError(error instanceof Error ? error.message : "操作未完成，请检查所选目录和后台连接"); }
    finally { pending.current = false; if (live.current) setBusy(false); }
  }
  return <details className="kb-auxiliary" aria-label="知识库文件夹导入">
    <summary>文件夹导入 · {state?.running ? state.mode === "import" ? "自动导入中" : "仅本地观察" : "未开启"}</summary>
    <p>选择一个专用文件夹，在系统确认框中核对接收后台和本人账号后，自动保存其中现有及新增、修改后的支持文件。只处理该目录，不含子目录；相同文件去重，修改内容另存新资料，不删除本地文件。付费识别需另行同意。</p>
    <p>停止、退出账号或关闭应用即结束，下次启动需重新开启。也可以只观察文件名，或手动选择文件上传。</p>
    {error && <p role="alert">{error}</p>}
    {state?.folder && <p className="kb-source-text">目录：{state.folder}{state.mode === "import" && state.backend && <><br />接收后台：{state.backend}</>}</p>}
    <p aria-live="polite">{busy ? "请在系统窗口中完成目录选择与确认…" : state?.message}</p>
    <div className="kb-actions">
      <button className="primary-button" type="button" disabled={disabled || busy} onClick={() => void action("import")}>选择目录并自动导入</button>
      <button className="secondary-button" type="button" disabled={disabled || busy || !state?.running} onClick={() => void action("stop")}>停止文件夹任务</button>
      <button className="secondary-button" type="button" disabled={disabled || busy} onClick={() => void action("observe")}>仅观察文件名</button>
      <button className="secondary-button" type="button" disabled={disabled || busy} onClick={onImport}>手动选择文件上传</button>
    </div>
    {state?.mode === "import" && <p>本次已保存 {state.saved || 0} 个 · 重复 {state.duplicates || 0} 个 · 需核对 {state.failed || 0} 个。保存原件不等于识别入库。</p>}
    {!!state?.files.length && <ul>{state.files.map(file => <li key={file.name}>{file.name} · {(file.size / 1024 / 1024).toFixed(2)} MB · {new Date(file.updatedAt).toLocaleString("zh-CN")}{file.status && <> · {file.status}</>}</li>)}</ul>}
  </details>;
}
