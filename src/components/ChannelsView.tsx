import { useState } from "react";
import {
  ArrowDown,
  ArrowDownUp,
  ArrowUp,
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
import type { ChannelDeliveryCatalog, ChannelDeliveryConfig } from "../lib/articleApi";

interface ChannelsViewProps {
  channels: SmartChannel[];
  statuses: Record<string, SubscriptionStatus>;
  catalog?: ChannelDeliveryCatalog;
  catalogLoading?: boolean;
  catalogError?: string;
  pendingChannelId?: string;
  subscriptionError?: { id: string; message: string };
  canManageDelivery: boolean;
  canManageLegacy: boolean;
  managementPending: boolean;
  onCreate?: () => void;
  onEdit: (channel: SmartChannel) => void;
  onConfigure: (channel: ChannelDeliveryConfig) => void;
  onConfigureAudience: (channelId: string) => void;
  onSubscribe: (channel: ChannelDeliveryConfig) => void;
  onSetEnabled: (channel: ChannelDeliveryConfig) => Promise<void>;
  onArchive: (channelId: string) => Promise<void>;
  onReorder: (previousIds: string[], channelIds: string[]) => Promise<void>;
  onRetryCatalog: () => void;
  onToggle: (channelId: string) => void;
  onDelete: (channelId: string) => void;
  onTest: (channel: SmartChannel) => void;
}

export function ChannelsView({
  channels,
  statuses,
  catalog,
  catalogLoading,
  catalogError,
  pendingChannelId,
  subscriptionError,
  canManageDelivery,
  canManageLegacy,
  managementPending,
  onCreate,
  onEdit,
  onConfigure,
  onConfigureAudience,
  onSubscribe,
  onSetEnabled,
  onArchive,
  onReorder,
  onRetryCatalog,
  onToggle,
  onDelete,
  onTest,
}: ChannelsViewProps) {
  const [sortDraft, setSortDraft] = useState<{ previousIds: string[]; channelIds: string[] }>();
  const [managementError, setManagementError] = useState<{ id?: string; message: string }>();
  const canManage = canManageDelivery && catalog?.management_supported === true;
  const canManageAudience = canManageDelivery && catalog?.audience_supported === true;
  const sorting = canManage ? sortDraft : undefined;
  const catalogIds = catalog?.channels.map(({ id }) => id) ?? [];
  const positions = new Map((sorting?.channelIds ?? catalogIds).map((id, index) => [id, index]));
  const sortOutdated = sorting && !sameOrder(sorting.previousIds, catalogIds);
  const busy = managementPending || Boolean(pendingChannelId) || Boolean(catalogLoading);
  const deliveryById = new Map(catalog?.channels.map((channel) => [channel.id, channel]));
  const rows = channels.filter((channel) => !catalog || !channel.isDemo || deliveryById.has(channel.id))
    .map((channel) => ({ id: channel.id, channel, delivery: deliveryById.get(channel.id) }));
  const localIds = new Set(rows.map(({ id }) => id));
  const catalogOnly = catalog?.channels.filter((channel) => !localIds.has(channel.id)) ?? [];
  const visibleRows = [...rows, ...catalogOnly.map((delivery) => ({ id: delivery.id, channel: undefined, delivery }))];
  if (catalog?.management_supported) {
    visibleRows.sort((a, b) => (positions.get(a.id) ?? Number.MAX_SAFE_INTEGER) - (positions.get(b.id) ?? Number.MAX_SAFE_INTEGER));
  }
  const activeCount = visibleRows.filter(({ channel, delivery }) => delivery?.enabled ?? channel?.state === "active").length;
  const connectedCount = visibleRows.filter(({ id }) => statuses[id]?.status === "connected").length;
  const subscribedCount = catalog?.channels.filter((channel) => channel.subscribed).length ?? 0;

  function moveChannel(index: number, direction: -1 | 1) {
    setSortDraft((current) => {
      if (!current) return current;
      const channelIds = [...current.channelIds];
      const next = index + direction;
      if (next < 0 || next >= channelIds.length) return current;
      [channelIds[index], channelIds[next]] = [channelIds[next], channelIds[index]];
      return { ...current, channelIds };
    });
  }

  async function manage(action: () => Promise<void>, id?: string) {
    setManagementError(undefined);
    try {
      await action();
    } catch (error) {
      setManagementError({ id, message: error instanceof Error ? error.message : "频道操作失败，请重试。" });
    }
  }

  return (
    <div className="channels-page">
      <div className="metric-strip">
        <div className="metric-item">
          <span>频道总数</span>
          <strong>{visibleRows.length}</strong>
        </div>
        <div className="metric-item">
          <span>{catalog ? "开放微信订阅" : "已启用"}</span>
          <strong>{activeCount}</strong>
        </div>
        <div className="metric-item">
          <span>实时连接</span>
          <strong>{connectedCount}</strong>
        </div>
        <div className="metric-item wide">
          <span>{catalog ? "我的微信订阅" : "频道推送安排"}</span>
          <strong>{catalog ? `${subscribedCount} 个频道` : nextSchedule(channels)}</strong>
        </div>
      </div>

      <section className="channel-table-section">
        <div className="section-heading">
          <div>
            <h2>全部频道</h2>
            <p>{catalog ? "按需订阅频道，在绑定的微信接收新情报。" : "每个频道对应一组长期关注规则和一个 ntfy 通道。"}</p>
          </div>
          <div className="channel-heading-actions">
            {sorting ? <>
              <button className="secondary-button" type="button" disabled={managementPending} onClick={() => {
                setSortDraft(undefined);
                setManagementError(undefined);
              }}>取消</button>
              <button className="primary-button" type="button"
                disabled={busy || sortOutdated || sameOrder(sorting.previousIds, sorting.channelIds)}
                onClick={() => void manage(async () => {
                  await onReorder(sorting.previousIds, sorting.channelIds);
                  setSortDraft(undefined);
                })}>{managementPending ? "正在保存" : "保存排序"}</button>
            </> : <>
              {canManage && <button className="secondary-button" type="button" disabled={busy || catalogIds.length < 2} onClick={() => {
                setManagementError(undefined);
                setSortDraft({ previousIds: catalogIds, channelIds: catalogIds });
              }}><ArrowDownUp size={15} />调整排序</button>}
              {onCreate && <button className="secondary-button" type="button" disabled={busy} onClick={onCreate}>
                <RadioTower size={15} />创建频道
              </button>}
            </>}
          </div>
        </div>
        {sorting && <p className="channel-notice" role="status">{sortOutdated
          ? "频道列表已更新，请取消后重新排序。"
          : "用上移和下移调整频道，保存后团队成员会看到新顺序。"}</p>}
        {managementError && !managementError.id && <p className="channel-notice channel-action-error" role="alert">{managementError.message}</p>}
        {catalog && !catalog.wechat_bound && <p className="channel-notice" role="status">请联系管理员在左侧「用户管理」打开你的账号，在「消息接收渠道」绑定微信后再订阅。</p>}
        {!catalog && catalogLoading && <p className="channel-notice" role="status">正在读取微信订阅状态…</p>}
        {catalogError && <div className="channel-notice channel-action-error" role="alert"><span>微信订阅状态读取失败。{catalogError}</span><button className="text-button" type="button" onClick={onRetryCatalog}>重新加载</button></div>}

        <div className={`channel-table${catalog ? " with-wechat" : ""}${canManage ? " with-management" : ""}${canManageAudience ? " with-audience" : ""}`} role="table" aria-label="智能频道">
          <div className="channel-table-header" role="row">
            <span>频道</span>
            <span>状态</span>
            <span>{catalog ? "北京时间" : "推送时间"}</span>
            <span>上限</span>
            <span>{catalog ? "内容来源" : "ntfy topic"}</span>
            {catalog && <span>微信订阅</span>}
            <span>{sorting ? "排序" : canManage ? "操作" : ""}</span>
          </div>
          {visibleRows.map(({ id, channel, delivery }) => {
            const status = statuses[id]?.status || "idle";
            const name = delivery?.name || channel?.name || id;
            const state = delivery ? (delivery.enabled ? "active" : "paused") : channel?.state || "draft";
            const label = delivery ? (delivery.enabled ? "已启用" : "已暂停") : channel ? stateLabel(channel, status) : "待配置";
            const source = delivery?.source_type === "website" ? "指定网页" : delivery ? "频道已有情报" : "仅本机";
            const sortIndex = positions.get(id);
            const enabledAction = delivery?.enabled ? "暂停" : "启用";
            return (
              <div className="channel-table-row" role="row" key={id}>
                <div className="channel-identity">
                  <span className={`channel-icon ${channel?.color || "green"}`}>
                    <RadioTower size={16} />
                  </span>
                  <div>
                    <strong>{name}</strong>
                    <span>{delivery ? delivery.description : channel && usesHealthRelay(channel) ? "服务器接收推送 · 历史文章从服务器同步" : channel?.managed ? `服务器受管 · ${channel.description}` : channel?.description}</span>
                  </div>
                </div>
                <div>
                  <span className={`status-pill ${state}`}>
                    <span />
                    {label}
                  </span>
                </div>
                <div className="table-value">
                  <Clock3 size={14} />
                  {delivery ? delivery.schedule : channel?.managed ? `每天北京时间 ${channel.schedule}（服务器推送）` : channel?.schedule}
                </div>
                <div className="table-value">{delivery?.max_items ?? channel?.maxItems} 条</div>
                <div className={catalog ? "table-value" : "topic-value"} title={catalog ? delivery?.source_url ?? undefined : channel?.topic || "尚未连接 topic"}>
                  {catalog ? source : channel?.topic || "未配置"}
                </div>
                {catalog && <div className="channel-wechat-cell">
                  {delivery?.subscription_allowed === false
                    ? <span title="管理员未向你的账号开放此频道的订阅权限">无订阅权限</span>
                    : delivery ? <button className={delivery.subscribed ? "secondary-button" : "primary-button"}
                    type="button" aria-label={`${delivery.subscribed ? "取消订阅" : "订阅"} ${name}`}
                    disabled={Boolean(pendingChannelId || managementPending || sorting) || (!delivery.subscribed && (!delivery.enabled || !catalog.wechat_bound))}
                    title={!delivery.subscribed && !catalog.wechat_bound ? "请先绑定微信" : undefined}
                    onClick={() => onSubscribe(delivery)}>
                    {pendingChannelId === id ? "正在保存" : delivery.subscribed ? "取消订阅" : "订阅"}
                  </button> : <span>未开放</span>}
                  {subscriptionError?.id === id && <span className="channel-action-error" role="alert">{subscriptionError.message}</span>}
                </div>}
                <div className="row-actions">
                  {sorting ? sortIndex !== undefined && <>
                    <button className="icon-button subtle" type="button" title="上移" aria-label={`上移 ${name}`}
                      disabled={busy || sortOutdated || sortIndex === 0} onClick={() => moveChannel(sortIndex, -1)}><ArrowUp size={15} /></button>
                    <button className="icon-button subtle" type="button" title="下移" aria-label={`下移 ${name}`}
                      disabled={busy || sortOutdated || sortIndex === sorting.channelIds.length - 1} onClick={() => moveChannel(sortIndex, 1)}><ArrowDown size={15} /></button>
                  </> : <>
                    {delivery && canManageAudience && <button className="text-button channel-audience-button" type="button"
                      aria-label={`订阅权限 ${name}`} disabled={busy} onClick={() => onConfigureAudience(id)}>订阅权限</button>}
                    {channel && (!catalog || usesHealthRelay(channel)) && <button
                      className="icon-button subtle"
                      type="button"
                      title={usesHealthRelay(channel) ? "检查接收" : "发送测试"}
                      aria-label={usesHealthRelay(channel) ? "检查接收" : "发送测试"}
                      disabled={!usesHealthRelay(channel) && !channel.topic}
                      onClick={() => onTest(channel)}
                    >
                      <Send size={15} />
                    </button>}
                    {delivery && canManageDelivery && <button className="icon-button subtle" type="button"
                      title="配置频道" aria-label={`配置频道 ${name}`} disabled={busy} onClick={() => onConfigure(delivery)}><Edit3 size={15} /></button>}
                    {delivery && canManage && <>
                      <button className="icon-button subtle" type="button"
                        title={delivery.can_delete ? `${enabledAction}频道` : `${enabledAction}微信推送，服务器接收继续运行`}
                        aria-label={`${enabledAction}频道 ${name}`} disabled={busy}
                        onClick={() => void manage(() => onSetEnabled(delivery), id)}>
                        {delivery.enabled ? <Pause size={15} /> : <Play size={15} />}
                      </button>
                      <button className="icon-button subtle danger" type="button" disabled={busy || !delivery.can_delete}
                        aria-label={`删除频道 ${name}`} title={delivery.can_delete ? "删除频道" : "此频道由服务器管理"}
                        onClick={() => {
                          if (window.confirm(`删除频道“${name}”吗？该频道将停止采集和推送，已入库的文章会保留。`)) {
                            void manage(() => onArchive(id), id);
                          }
                        }}><Trash2 size={15} /></button>
                    </>}
                    {!catalog && channel && (canManageLegacy || channel.managed) && <button
                      className="icon-button subtle"
                      type="button"
                      title={channel.managed ? "查看受管配置" : "编辑"}
                      aria-label={channel.managed ? "查看受管配置" : "编辑"}
                      onClick={() => onEdit(channel)}
                    >
                      <Edit3 size={15} />
                    </button>}
                    {!catalog && channel && (canManageLegacy || channel.managed) && <button
                      className="icon-button subtle"
                      type="button"
                      title={channel.managed ? "服务器受管，不能在本机暂停" : channel.state === "active" ? "暂停" : "启用"}
                      aria-label={channel.state === "active" ? "暂停" : "启用"}
                      disabled={channel.managed}
                      onClick={() => onToggle(channel.id)}
                    >
                      {channel.state === "active" ? <Pause size={15} /> : <Play size={15} />}
                    </button>}
                    {!catalog && channel && (canManageLegacy || channel.managed) && <button
                      className="icon-button subtle danger"
                      type="button"
                      aria-label="删除"
                      disabled={channel.managed}
                      title={channel.managed ? "服务器受管，不能在本机删除" : "删除"}
                      onClick={() => onDelete(channel.id)}
                    >
                      <Trash2 size={15} />
                    </button>}
                  </>}
                </div>
                {managementError?.id === id && <span className="channel-row-error channel-action-error" role="alert">{managementError.message}</span>}
              </div>
            );
          })}
        </div>

        {visibleRows.length === 0 && (
          <div className="table-empty">
            <MoreHorizontal size={26} />
            <strong>还没有智能频道</strong>
            {onCreate && <button className="text-button" type="button" onClick={onCreate}>
              创建第一个频道
            </button>}
          </div>
        )}
      </section>
    </div>
  );
}

function sameOrder(left: string[], right: string[]) {
  return left.length === right.length && left.every((id, index) => id === right[index]);
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
