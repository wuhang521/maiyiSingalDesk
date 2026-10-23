import {
  ArchiveRestore,
  Bookmark,
  BookmarkCheck,
  Check,
  Circle,
  ExternalLink,
  Inbox,
  ThumbsDown,
  ThumbsUp,
  Trash2,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { trashDaysRemaining } from "../lib/trash";
import type { FeedItem, FeedbackValue, SmartChannel } from "../types";

type FeedFilter = "all" | "unread" | "saved";

interface MessageWorkspaceProps {
  items: FeedItem[];
  channels: SmartChannel[];
  selectedItemId: string | null;
  filter: FeedFilter;
  compactMode: boolean;
  onFilterChange: (filter: FeedFilter) => void;
  onSelect: (itemId: string) => void;
  onToggleRead: (itemId: string) => void;
  onToggleSaved: (itemId: string) => void;
  onFeedback: (itemId: string, feedback: FeedbackValue) => void;
  onDelete: (itemId: string) => void;
  onOpenLink: (url: string) => void;
  mode?: "feed" | "trash";
  deletedAtById?: Record<string, number>;
  onRestore?: (itemId: string) => void;
  onDeletePermanently?: (itemId: string) => void;
  onEmptyTrash?: () => void;
}

export function MessageWorkspace({
  items,
  channels,
  selectedItemId,
  filter,
  compactMode,
  onFilterChange,
  onSelect,
  onToggleRead,
  onToggleSaved,
  onFeedback,
  onDelete,
  onOpenLink,
  mode = "feed",
  deletedAtById = {},
  onRestore = () => undefined,
  onDeletePermanently = () => undefined,
  onEmptyTrash = () => undefined,
}: MessageWorkspaceProps) {
  const channelById = new Map(channels.map((channel) => [channel.id, channel]));
  const selectedItem = items.find((item) => item.id === selectedItemId) || items[0];
  const isTrash = mode === "trash";

  return (
    <div className={`message-workspace ${compactMode ? "compact" : ""}`}>
      <section className="feed-pane" aria-label="消息列表">
        <div className="feed-toolbar">
          {isTrash ? (
            <span className="trash-toolbar-label">保留 7 天</span>
          ) : (
            <div className="segmented-control" aria-label="消息筛选">
              {([
                ["all", "全部"],
                ["unread", "未读"],
                ["saved", "收藏"],
              ] as const).map(([value, label]) => (
                <button
                  type="button"
                  key={value}
                  className={filter === value ? "active" : ""}
                  onClick={() => onFilterChange(value)}
                >
                  {label}
                </button>
              ))}
            </div>
          )}
          <div className="feed-toolbar-end">
            <span className="result-count">{items.length} 条</span>
            {isTrash && items.length > 0 && (
              <button className="trash-empty-button" type="button" onClick={onEmptyTrash}>
                清空
              </button>
            )}
          </div>
        </div>

        <div className="feed-list">
          {items.length === 0 ? (
            <div className="empty-pane">
              <Inbox size={30} />
              <strong>{isTrash ? "回收站为空" : "这里暂时没有内容"}</strong>
              <span>{isTrash ? "删除的文章会在这里保留 7 天。" : "调整筛选，或同步已连接的频道。"}</span>
            </div>
          ) : (
            items.map((item) => {
              const channel = channelById.get(item.channelId);
              return (
                <article
                  className={`feed-row ${selectedItem?.id === item.id ? "selected" : ""} ${item.read ? "read" : "unread"}`}
                  key={item.id}
                  onClick={() => onSelect(item.id)}
                >
                  {isTrash ? (
                    <span className="trash-indicator" aria-hidden="true"><Trash2 size={12} /></span>
                  ) : (
                    <button
                      className="read-indicator"
                      type="button"
                      title={item.read ? "标为未读" : "标为已读"}
                      aria-label={item.read ? "标为未读" : "标为已读"}
                      onClick={(event) => {
                        event.stopPropagation();
                        onToggleRead(item.id);
                      }}
                    >
                      {item.read ? <Circle size={11} /> : <span className="unread-dot" />}
                    </button>
                  )}
                  <div className="feed-row-content">
                    <div className="feed-meta">
                      <span className={`channel-chip ${channel?.color || "slate"}`}>
                        {channel?.name || "未知频道"}
                      </span>
                      <time>{formatRelativeTime(item.timestamp)}</time>
                    </div>
                    <h2>{item.title}</h2>
                    <p>{plainPreview(item.body)}</p>
                    <div className="feed-footer">
                      <span>{item.source}</span>
                      {isTrash ? (
                        <span className="trash-expiry">
                          还有 {trashDaysRemaining(deletedAtById[item.id] || Date.now())} 天自动清除
                        </span>
                      ) : item.saved && <BookmarkCheck size={14} aria-label="已收藏" />}
                    </div>
                  </div>
                </article>
              );
            })
          )}
        </div>
      </section>

      <section className="reader-pane" aria-label="内容详情">
        {!selectedItem ? (
          <div className="reader-empty">
            <Inbox size={34} />
            <strong>{isTrash ? "回收站为空" : "选择一条信息开始阅读"}</strong>
          </div>
        ) : (
          <>
            <div className="reader-toolbar">
              <div className="reader-source">
                <span className={`channel-dot ${channelById.get(selectedItem.channelId)?.color || "slate"}`} />
                <span>{channelById.get(selectedItem.channelId)?.name}</span>
                <span className="divider-dot">·</span>
                <time>{formatDateTime(selectedItem.timestamp)}</time>
              </div>
              <div className="reader-actions">
                {isTrash ? (
                  <button
                    className="reader-action-button restore"
                    type="button"
                    aria-label="恢复文章"
                    onClick={() => onRestore(selectedItem.id)}
                  >
                    <ArchiveRestore size={15} />
                    恢复
                  </button>
                ) : (
                  <>
                    <button
                      className={`reader-action-button ${selectedItem.read ? "active" : ""}`}
                      type="button"
                      aria-label={selectedItem.read ? "标为未读" : "标为已读"}
                      aria-pressed={selectedItem.read}
                      onClick={() => onToggleRead(selectedItem.id)}
                    >
                      <Check size={15} />
                      {selectedItem.read ? "已读" : "标为已读"}
                    </button>
                    <button
                      className={`reader-action-button ${selectedItem.saved ? "active" : ""}`}
                      type="button"
                      aria-label={selectedItem.saved ? "取消收藏" : "收藏"}
                      aria-pressed={selectedItem.saved}
                      onClick={() => onToggleSaved(selectedItem.id)}
                    >
                      {selectedItem.saved ? <BookmarkCheck size={15} /> : <Bookmark size={15} />}
                      {selectedItem.saved ? "已收藏" : "收藏"}
                    </button>
                  </>
                )}
                {selectedItem.clickUrl && (
                  <button
                    className="reader-action-button"
                    type="button"
                    onClick={() => onOpenLink(selectedItem.clickUrl!)}
                  >
                    <ExternalLink size={15} />
                    打开原文
                  </button>
                )}
                <div className="reader-danger-action">
                  <button
                    className="reader-action-button danger"
                    type="button"
                    aria-label={isTrash ? "永久删除文章" : "删除本机消息"}
                    onClick={() => isTrash ? onDeletePermanently(selectedItem.id) : onDelete(selectedItem.id)}
                  >
                    <Trash2 size={15} />
                    {isTrash ? "永久删除" : "删除"}
                  </button>
                </div>
              </div>
            </div>
            <div className="reader-scroll">
              <div className="reader-content">
                <div className="reader-kicker">
                  <span>{selectedItem.source}</span>
                  {selectedItem.priority >= 4 && <span className="priority-label">重点</span>}
                </div>
                <h1>{selectedItem.title}</h1>
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  components={{
                    a: ({ href, children }) =>
                      href && isExternalHttpUrl(href) ? (
                        <a
                          href={href}
                          rel="noreferrer"
                          onClick={(event) => {
                            event.preventDefault();
                            onOpenLink(href);
                          }}
                        >
                          {children}
                        </a>
                      ) : (
                        <span>{children}</span>
                      ),
                  }}
                >
                  {selectedItem.body}
                </ReactMarkdown>

                {!isTrash && <div className="feedback-panel">
                  <div>
                    <strong>这篇文章对你有帮助吗？</strong>
                    <span>反馈会用于调整该频道后续排序。</span>
                  </div>
                  <div className="feedback-actions">
                    <button
                      className={`feedback-button ${selectedItem.feedback === "useful" ? "active positive" : ""}`}
                      type="button"
                      onClick={() =>
                        onFeedback(
                          selectedItem.id,
                          selectedItem.feedback === "useful" ? null : "useful",
                        )
                      }
                      aria-pressed={selectedItem.feedback === "useful"}
                    >
                      <ThumbsUp size={15} />
                      有帮助
                    </button>
                    <button
                      className={`feedback-button ${selectedItem.feedback === "not-useful" ? "active negative" : ""}`}
                      type="button"
                      onClick={() =>
                        onFeedback(
                          selectedItem.id,
                          selectedItem.feedback === "not-useful" ? null : "not-useful",
                        )
                      }
                      aria-pressed={selectedItem.feedback === "not-useful"}
                    >
                      <ThumbsDown size={15} />
                      不相关
                    </button>
                  </div>
                </div>}
              </div>
            </div>
          </>
        )}
      </section>
    </div>
  );
}

function plainPreview(markdown: string): string {
  return markdown
    .replace(/[#>*_`\[\]()]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 112);
}

function formatRelativeTime(timestamp: number): string {
  const difference = Date.now() - timestamp;
  const minutes = Math.floor(difference / 60000);
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  return new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric" }).format(timestamp);
}

function formatDateTime(timestamp: number): string {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "long",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(timestamp);
}

function isExternalHttpUrl(value: string): boolean {
  try {
    return ["http:", "https:"].includes(new URL(value).protocol);
  } catch {
    return false;
  }
}
