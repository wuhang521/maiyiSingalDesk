import {
  Clock3,
  Edit3,
  MoreHorizontal,
  Pause,
  Play,
  RadioTower,
  Send,
  Trash2,
} from "lucide-react";
import type { SmartChannel, SubscriptionStatus } from "../types";
import { usesHealthRelay } from "../lib/ntfy";

interface ChannelsViewProps {
  channels: SmartChannel[];
  statuses: Record<string, SubscriptionStatus>;
  onCreate: () => void;
  onEdit: (channel: SmartChannel) => void;
  onToggle: (channelId: string) => void;
  onDelete: (channelId: string) => void;
  onTest: (channel: SmartChannel) => void;
}

export function ChannelsView({
  channels,
  statuses,
  onCreate,
  onEdit,
  onToggle,
  onDelete,
  onTest,
}: ChannelsViewProps) {
  const activeCount = channels.filter((channel) => channel.state === "active").length;
  const connectedCount = channels.filter(
    (channel) => statuses[channel.id]?.status === "connected",
  ).length;

  return (
    <div className="channels-page">
      <div className="metric-strip">
        <div className="metric-item">
          <span>频道总数</span>
          <strong>{channels.length}</strong>
        </div>
        <div className="metric-item">
          <span>已启用</span>
          <strong>{activeCount}</strong>
        </div>
        <div className="metric-item">
          <span>实时连接</span>
          <strong>{connectedCount}</strong>
        </div>
        <div className="metric-item wide">
          <span>频道推送安排</span>
          <strong>{nextSchedule(channels)}</strong>
        </div>
      </div>

      <section className="channel-table-section">
        <div className="section-heading">
          <div>
            <h2>全部频道</h2>
            <p>每个频道对应一组长期关注规则和一个 ntfy 通道。</p>
          </div>
          <button className="secondary-button" type="button" onClick={onCreate}>
            <RadioTower size={15} />
            创建频道
          </button>
        </div>

        <div className="channel-table" role="table" aria-label="智能频道">
          <div className="channel-table-header" role="row">
            <span>频道</span>
            <span>状态</span>
            <span>推送时间</span>
            <span>上限</span>
            <span>ntfy topic</span>
            <span aria-label="操作" />
          </div>
          {channels.map((channel) => {
            const status = statuses[channel.id]?.status || "idle";
            return (
              <div className="channel-table-row" role="row" key={channel.id}>
                <div className="channel-identity">
                  <span className={`channel-icon ${channel.color}`}>
                    <RadioTower size={16} />
                  </span>
                  <div>
                    <strong>{channel.name}</strong>
                    <span>{usesHealthRelay(channel) ? "服务器接收推送 · 历史文章从服务器同步" : channel.managed ? `服务器受管 · ${channel.description}` : channel.description}</span>
                  </div>
                </div>
                <div>
                  <span className={`status-pill ${channel.state}`}>
                    <span />
                    {stateLabel(channel, status)}
                  </span>
                </div>
                <div className="table-value">
                  <Clock3 size={14} />
                  {channel.managed ? `每天北京时间 ${channel.schedule}（服务器推送）` : channel.schedule}
                </div>
                <div className="table-value">{channel.maxItems} 条</div>
                <div className="topic-value" title={channel.topic || "尚未连接 topic"}>
                  {channel.topic || "未配置"}
                </div>
                <div className="row-actions">
                  <button
                    className="icon-button subtle"
                    type="button"
                    title={usesHealthRelay(channel) ? "检查接收" : "发送测试"}
                    aria-label={usesHealthRelay(channel) ? "检查接收" : "发送测试"}
                    disabled={!usesHealthRelay(channel) && !channel.topic}
                    onClick={() => onTest(channel)}
                  >
                    <Send size={15} />
                  </button>
                  <button
                    className="icon-button subtle"
                    type="button"
                    title={channel.managed ? "查看受管配置" : "编辑"}
                    aria-label={channel.managed ? "查看受管配置" : "编辑"}
                    onClick={() => onEdit(channel)}
                  >
                    <Edit3 size={15} />
                  </button>
                  <button
                    className="icon-button subtle"
                    type="button"
                    title={channel.managed ? "服务器受管，不能在本机暂停" : channel.state === "active" ? "暂停" : "启用"}
                    aria-label={channel.state === "active" ? "暂停" : "启用"}
                    disabled={channel.managed}
                    onClick={() => onToggle(channel.id)}
                  >
                    {channel.state === "active" ? <Pause size={15} /> : <Play size={15} />}
                  </button>
                  <button
                    className="icon-button subtle danger"
                    type="button"
                    aria-label="删除"
                    disabled={channel.managed}
                    title={channel.managed ? "服务器受管，不能在本机删除" : "删除"}
                    onClick={() => onDelete(channel.id)}
                  >
                    <Trash2 size={15} />
                  </button>
                </div>
              </div>
            );
          })}
        </div>

        {channels.length === 0 && (
          <div className="table-empty">
            <MoreHorizontal size={26} />
            <strong>还没有智能频道</strong>
            <button className="text-button" type="button" onClick={onCreate}>
              创建第一个频道
            </button>
          </div>
        )}
      </section>
    </div>
  );
}

function nextSchedule(channels: SmartChannel[]): string {
  const managed = channels.find((channel) => channel.managed && channel.state === "active");
  if (managed) return `每天北京时间 ${managed.schedule}（服务器）`;
  const times = channels
    .filter((channel) => channel.state === "active")
    .map((channel) => channel.schedule)
    .sort();
  return times[0] ? `每天 ${times[0]}` : "暂无计划";
}

function stateLabel(channel: SmartChannel, connection: string): string {
  if (channel.state === "draft") return "待配置";
  if (channel.state === "paused") return "已暂停";
  if (channel.state === "error") return "异常";
  if (connection === "connected") return "运行中";
  if (connection === "reconnecting") return "重连中";
  return "已启用";
}
