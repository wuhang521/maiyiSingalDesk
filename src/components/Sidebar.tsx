import {
  Archive,
  Bookmark,
  BookOpen,
  CalendarDays,
  Inbox,
  RadioTower,
  Settings,
  ListChecks,
  MonitorUp,
  Trash2,
  WandSparkles,
} from "lucide-react";
import mayiLogo from "../assets/mayi-logo.webp";
import type { SmartChannel, SubscriptionStatus, ViewKey } from "../types";

interface SidebarProps {
  activeView: ViewKey;
  channels: SmartChannel[];
  unreadCount: number;
  savedCount: number;
  trashCount: number;
  statuses: Record<string, SubscriptionStatus>;
  onViewChange: (view: ViewKey) => void;
  onChannelSelect: (channelId: string) => void;
  onToggleTaskWidget: () => void;
}

const navigation: Array<{
  id: ViewKey;
  label: string;
  icon: typeof Inbox;
}> = [
  { id: "today", label: "今日", icon: CalendarDays },
  { id: "inbox", label: "全部信息", icon: Inbox },
  { id: "channels", label: "智能频道", icon: RadioTower },
  { id: "tasks", label: "任务中心", icon: ListChecks },
  { id: "knowledge", label: "知识库", icon: BookOpen },
  { id: "content", label: "内容生成", icon: WandSparkles },
  { id: "saved", label: "收藏", icon: Bookmark },
  { id: "trash", label: "回收站", icon: Trash2 },
];

export function Sidebar({
  activeView,
  channels,
  unreadCount,
  savedCount,
  trashCount,
  statuses,
  onViewChange,
  onChannelSelect,
  onToggleTaskWidget,
}: SidebarProps) {
  const connected = channels.filter(
    (channel) => statuses[channel.id]?.status === "connected",
  ).length;

  return (
    <aside className="sidebar">
      <div className="brand-block">
        <div className="brand-mark" aria-hidden="true">
          <img src={mayiLogo} alt="" />
        </div>
        <div>
          <div className="brand-name">迈医协同</div>
          <div className="brand-caption">迈医智能协同平台</div>
        </div>
      </div>

      <nav className="primary-nav" aria-label="主导航">
        {navigation.map((item) => {
          const Icon = item.icon;
          const count = item.id === "inbox"
            ? unreadCount
            : item.id === "saved"
              ? savedCount
              : item.id === "trash"
                ? trashCount
                : 0;
          return (
            <button
              className={`nav-button ${activeView === item.id ? "active" : ""}`}
              key={item.id}
              onClick={() => onViewChange(item.id)}
              type="button"
            >
              <Icon size={17} />
              <span>{item.label}</span>
              {count > 0 && <span className="nav-count">{count}</span>}
            </button>
          );
        })}
      </nav>

      <div className="sidebar-section">
        <div className="section-label-row">
          <span className="section-label">频道</span>
          <button
            className="icon-button subtle"
            type="button"
            title="管理频道"
            aria-label="管理频道"
            onClick={() => onViewChange("channels")}
          >
            <Archive size={14} />
          </button>
        </div>
        <div className="channel-shortcuts">
          {channels.map((channel) => (
            <button
              className="channel-shortcut"
              key={channel.id}
              type="button"
              onClick={() => onChannelSelect(channel.id)}
            >
              <span className={`channel-dot ${channel.color}`} aria-hidden="true" />
              <span className="channel-shortcut-name">{channel.name}</span>
              {channel.state === "draft" && <span className="tiny-label">示例</span>}
            </button>
          ))}
        </div>
      </div>

      <div className="sidebar-footer">
        <div className="connection-summary">
          <span className={`connection-light ${connected > 0 ? "online" : "idle"}`} />
          <div>
            <strong>{connected > 0 ? `${connected} 个频道在线` : "等待连接频道"}</strong>
            <span>ntfy 实时通道</span>
          </div>
        </div>
        <button className="nav-button" type="button" onClick={onToggleTaskWidget}>
          <MonitorUp size={17} />
          <span>桌面挂件</span>
        </button>
        <button
          className={`nav-button ${activeView === "settings" ? "active" : ""}`}
          type="button"
          onClick={() => onViewChange("settings")}
        >
          <Settings size={17} />
          <span>设置</span>
        </button>
      </div>
    </aside>
  );
}
