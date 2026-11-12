import type {
  AppSettings,
  FeedItem,
  FeedbackValue,
  SmartChannel,
  TrashEntry,
} from "../types";
import { canonicalChannelId } from "./channelIdentity";

export interface ServerArticle extends FeedItem {
  deletedAt: number | null;
  purged: boolean;
  stateUpdatedAt: number;
}

interface ArticleListResponse {
  items: ServerArticle[];
  total: number;
}

export interface ArticleBatchResult {
  accepted: number;
  idMap?: Record<string, string>;
}

export interface ArticleStateUpdate {
  read?: boolean;
  saved?: boolean;
  feedback?: FeedbackValue;
  action?: "delete" | "restore" | "permanent";
  stateUpdatedAt: number;
}

export interface ChannelDeliveryDraft {
  id?: string;
  name: string;
  description: string;
  enabled: boolean;
  schedule: string;
  max_items: number;
  source_type: "articles" | "website";
  source_url: string | null;
  selector: string | null;
  include_terms: string[];
  exclude_terms: string[];
}

export interface ChannelDeliveryConfig extends ChannelDeliveryDraft {
  id: string;
  subscribed: boolean;
  can_delete?: boolean;
  subscription_allowed?: boolean;
}

export interface ChannelDeliveryCatalog {
  wechat_bound: boolean;
  can_manage: boolean;
  management_supported?: boolean;
  audience_supported?: boolean;
  archived_ids?: string[];
  channels: ChannelDeliveryConfig[];
}

export interface ChannelAudience {
  allowed_user_ids: number[] | null;
  members: { id: number; name: string; username: string }[];
}

function endpoint(settings: AppSettings, path: string): string {
  return `${settings.backendUrl.trim().replace(/\/+$/, "")}${path}`;
}

async function request<T>(
  settings: AppSettings,
  path: string,
  init: RequestInit = {},
  requireOperator = false,
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${settings.backendAuthToken.trim()}`,
  };
  if (requireOperator) {
    headers["X-SignalDesk-Operator"] = encodeURIComponent(settings.operatorName.trim());
  }
  const response = await fetch(endpoint(settings, path), {
    ...init,
    headers: { ...headers, ...init.headers },
  });
  if (!response.ok) {
    let detail = `后台返回 HTTP ${response.status}`;
    try {
      const payload = (await response.json()) as { detail?: unknown };
      if (typeof payload.detail === "string" && payload.detail) detail = payload.detail;
      else if (Array.isArray(payload.detail) && path.startsWith("/api/v1/channel-subscriptions/")) detail = "频道配置格式有误，请检查表单后重试。";
    } catch {
      // 保留 HTTP 状态作为错误信息。
    }
    throw Object.assign(new Error(detail), { status: response.status });
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

function articleContent(item: FeedItem, channels: readonly SmartChannel[] = []) {
  return {
    id: item.id,
    channelId: canonicalChannelId(item.channelId, channels),
    title: item.title,
    body: item.body,
    timestamp: item.timestamp,
    source: item.source,
    clickUrl: item.clickUrl,
    tags: item.tags,
    priority: item.priority,
  };
}

function syncItem(item: FeedItem, entry?: TrashEntry, channels: readonly SmartChannel[] = []) {
  return {
    ...articleContent(item, channels),
    read: item.read,
    saved: item.saved,
    feedback: item.feedback,
    deletedAt: entry?.deletedAt ?? null,
    purged: Boolean(entry?.purged),
    stateUpdatedAt: item.stateUpdatedAt || 0,
  };
}

export const articleApi = {
  contentConfigured(settings: AppSettings): boolean {
    return Boolean(settings.backendUrl.trim() && settings.backendAuthToken.trim());
  },

  configured(settings: AppSettings): boolean {
    return Boolean(
      settings.backendUrl.trim() &&
        settings.backendAuthToken.trim() &&
        settings.operatorName.trim(),
    );
  },

  listArticles(settings: AppSettings, state: "active" | "trash" | "all" = "all") {
    return request<ArticleListResponse>(settings, `/api/v1/articles?state=${state}`, {}, true);
  },

  async syncArticles(settings: AppSettings, feed: FeedItem[], trash: TrashEntry[], channels: readonly SmartChannel[] = []) {
    const trashItems = trash
      .filter((entry): entry is TrashEntry & { item: FeedItem } => Boolean(entry.item))
      .map((entry) => syncItem(entry.item, entry, channels));
    const items = [
      ...feed.filter((item) => !item.id.startsWith("demo-message-")).map((item) => syncItem(item, undefined, channels)),
      ...trashItems.filter((item) => !item.id.startsWith("demo-message-")),
    ];
    return request<{ accepted: number }>(
      settings,
      "/api/v1/articles/sync",
      { method: "POST", body: JSON.stringify({ items }) },
      true,
    );
  },

  async batchArticles(settings: AppSettings, items: FeedItem[], channels: readonly SmartChannel[] = [], resolveIds = false): Promise<ArticleBatchResult> {
    const realItems = items.filter((item) => !item.id.startsWith("demo-message-"));
    if (realItems.length === 0) return resolveIds ? { accepted: 0, idMap: {} } : { accepted: 0 };
    const result = await request<ArticleBatchResult>(settings, `/api/v1/articles/batch${resolveIds ? "?resolve_ids=true" : ""}`, {
      method: "POST",
      body: JSON.stringify({ items: realItems.map((item) => articleContent(item, channels)) }),
    });
    if (resolveIds && realItems.some(item => typeof result.idMap?.[item.id] !== "string" || !result.idMap[item.id].trim())) {
      throw new Error("服务器未返回文章保存编号，请稍后重新同步。");
    }
    return result;
  },

  updateState(settings: AppSettings, articleId: string, update: ArticleStateUpdate) {
    return request<ServerArticle>(
      settings,
      `/api/v1/articles/${encodeURIComponent(articleId)}/state`,
      { method: "PATCH", body: JSON.stringify(update) },
      true,
    );
  },

  listChannels(settings: AppSettings) {
    return request<SmartChannel[]>(settings, "/api/v1/channels");
  },

  upsertChannel(settings: AppSettings, channel: SmartChannel, channels: readonly SmartChannel[] = [channel]) {
    const { authToken: _authToken, authTokenServer: _authTokenServer, authTokenTopic: _authTokenTopic,
      lastSyncAt: _lastSyncAt, isDemo: _isDemo, aliases: _aliases, managed: _managed, readViaApi: _readViaApi, ...safe } = channel;
    return request<{ id: string }>(settings, "/api/v1/channels", {
      method: "POST",
      body: JSON.stringify({ ...safe, id: canonicalChannelId(channel.id, channels) }),
    });
  },

  deleteChannel(settings: AppSettings, channelId: string, channels: readonly SmartChannel[] = []) {
    return request<void>(settings, `/api/v1/channels/${encodeURIComponent(canonicalChannelId(channelId, channels))}`, {
      method: "DELETE",
    });
  },

  channelDeliveryCatalog(settings: AppSettings, signal?: AbortSignal) {
    return request<ChannelDeliveryCatalog>(settings, "/api/v1/channel-subscriptions/catalog", { signal });
  },

  setChannelSubscription(settings: AppSettings, channelId: string, subscribed: boolean) {
    return request<{ subscribed: boolean }>(settings,
      `/api/v1/channel-subscriptions/${encodeURIComponent(channelId)}/subscription`,
      { method: subscribed ? "PUT" : "DELETE" },
    );
  },

  saveChannelDelivery(settings: AppSettings, draft: ChannelDeliveryDraft) {
    return request<ChannelDeliveryConfig>(settings, "/api/v1/channel-subscriptions/configs", {
      method: "POST", body: JSON.stringify(draft),
    });
  },

  setChannelEnabled(settings: AppSettings, channelId: string, enabled: boolean) {
    return request<ChannelDeliveryConfig>(settings, `/api/v1/channel-subscriptions/configs/${encodeURIComponent(channelId)}/enabled`, {
      method: "PUT", body: JSON.stringify({ enabled }),
    });
  },

  archiveChannelDelivery(settings: AppSettings, channelId: string) {
    return request<void>(settings, `/api/v1/channel-subscriptions/configs/${encodeURIComponent(channelId)}`, { method: "DELETE" });
  },

  saveChannelOrder(settings: AppSettings, previousIds: string[], channelIds: string[]) {
    return request<{ channel_ids: string[] }>(settings, "/api/v1/channel-subscriptions/order", {
      method: "PUT", body: JSON.stringify({ previous_ids: previousIds, channel_ids: channelIds }),
    });
  },

  channelAudience(settings: AppSettings, channelId: string, signal?: AbortSignal) {
    return request<ChannelAudience>(settings, `/api/v1/channel-subscriptions/configs/${encodeURIComponent(channelId)}/audience`, { signal });
  },

  saveChannelAudience(settings: AppSettings, channelId: string, previousIds: number[] | null, allowedIds: number[] | null) {
    return request<{ subscription_allowed: boolean }>(settings, `/api/v1/channel-subscriptions/configs/${encodeURIComponent(channelId)}/audience`, {
      method: "PUT", body: JSON.stringify({ previous_user_ids: previousIds, allowed_user_ids: allowedIds }),
    });
  },
};
