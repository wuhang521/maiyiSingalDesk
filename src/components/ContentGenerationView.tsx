import { useState } from "react";
import { Clapperboard, FileText } from "lucide-react";
import type { AppSettings } from "../types";
import { ContentStudio } from "./content/ContentStudio";
import { VideoGenerationWorkspace } from "./VideoGenerationWorkspace";

export function ContentGenerationView({ settings }: { settings: AppSettings }) {
  const [mode, setMode] = useState<"creation" | "video">("creation");
  return <div className="content-generation-shell">
    <div className="content-mode-bar" role="tablist" aria-label="创作与制作">
      <button type="button" role="tab" aria-selected={mode === "creation"} className={mode === "creation" ? "active" : ""} onClick={() => setMode("creation")}><FileText size={15} />内容创作</button>
      <button type="button" role="tab" aria-selected={mode === "video"} className={mode === "video" ? "active" : ""} onClick={() => setMode("video")}><Clapperboard size={15} />视频制作</button>
      <span>{mode === "creation" ? "账号规则自动带入 · 每步人工确认 · 按平台定稿" : "主题规划 · 分步确认 · 生成成片"}</span>
    </div>
    <div className="content-mode-pane" hidden={mode !== "creation"}><ContentStudio settings={settings} /></div>
    <div className="content-mode-pane" hidden={mode !== "video"}><VideoGenerationWorkspace settings={settings} /></div>
  </div>;
}
