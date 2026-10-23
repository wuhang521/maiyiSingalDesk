import { Plus, RefreshCw, Search, SlidersHorizontal } from "lucide-react";
import type { ViewKey } from "../types";

interface TopbarProps {
  activeView: ViewKey;
  searchQuery: string;
  refreshing: boolean;
  onSearchChange: (query: string) => void;
  onRefresh: () => void;
  onCreateChannel: () => void;
}

const viewCopy: Record<ViewKey, { title: string; subtitle: string }> = {
  today: { title: "今日情报", subtitle: "按价值排序的最新内容" },
  inbox: { title: "全部信息", subtitle: "跨频道检索与阅读" },
  channels: { title: "智能频道", subtitle: "定义长期关注方向和推送规则" },
  tasks: { title: "任务中心", subtitle: "共享任务、负责人和定时提醒" },
  knowledge: { title: "知识库", subtitle: "公众号与视频号素材，保存、检索和追溯来源" },
  content: { title: "内容生成", subtitle: "从账号定位到平台定稿，逐步确认每一期内容" },
  saved: { title: "收藏", subtitle: "留给决策和后续行动的内容" },
  trash: { title: "回收站", subtitle: "删除的文章将在 7 天后自动清除" },
  settings: { title: "设置", subtitle: "服务器、通知和桌面行为" },
};

export function Topbar({
  activeView,
  searchQuery,
  refreshing,
  onSearchChange,
  onRefresh,
  onCreateChannel,
}: TopbarProps) {
  const copy = viewCopy[activeView];
  return (
    <header className="topbar">
      <div className="page-heading">
        <h1>{copy.title}</h1>
        <span>{copy.subtitle}</span>
      </div>
      <div className="topbar-actions">
        {activeView !== "settings" && activeView !== "channels" && activeView !== "content" && activeView !== "knowledge" && (
          <label className="search-field">
            <Search size={16} aria-hidden="true" />
            <input
              aria-label={activeView === "tasks" ? "搜索任务" : activeView === "trash" ? "搜索回收站" : "搜索信息"}
              placeholder={activeView === "tasks" ? "搜索任务编号、标题或要求" : activeView === "trash" ? "搜索已删除的文章" : "搜索标题、正文或来源"}
              value={searchQuery}
              onChange={(event) => onSearchChange(event.target.value)}
            />
            <kbd>Ctrl K</kbd>
          </label>
        )}
        {activeView !== "settings" && activeView !== "tasks" && activeView !== "knowledge" && activeView !== "content" && activeView !== "trash" && (
          <button
            className="icon-button"
            type="button"
            title="筛选"
            aria-label="筛选"
          >
            <SlidersHorizontal size={17} />
          </button>
        )}
        {activeView !== "settings" && activeView !== "tasks" && activeView !== "knowledge" && activeView !== "content" && activeView !== "trash" && (
          <button
            className="icon-button"
            type="button"
            title={refreshing ? "正在同步" : "同步"}
            aria-label="同步"
            disabled={refreshing}
            onClick={onRefresh}
          >
            <RefreshCw size={17} className={refreshing ? "spin" : ""} />
          </button>
        )}
        {activeView !== "tasks" && activeView !== "knowledge" && activeView !== "content" && activeView !== "trash" && (
          <button className="primary-button" type="button" onClick={onCreateChannel}>
            <Plus size={16} />
            新建频道
          </button>
        )}
      </div>
    </header>
  );
}
