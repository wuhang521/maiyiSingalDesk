import type {
  AppSettings,
  FeedItem,
  FeedbackValue,
  SmartChannel,
  TrashEntry,
} from "../types";

export interface ServerArticle extends FeedItem {
  deletedAt: number | null;
  purged: boolean;
  stateUpdatedAt: number;
}

interface ArticleListResponse {
  items: ServerArticle[];
  total: number;
}

export interface ArticleStateUpdate {
  read?: boolean;
  saved?: boolean;
  feedback?: FeedbackValue;
  action?: "delete" | "restore" | "permanent";
  stateUpdatedAt: number;
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
      const payload = (await response.json()) as { detail?: string };
      if (payload.detail) detail = payload.detail;
    } catch {
      // 保留 HTTP 状态作为错误信息。
    }
    throw new Error(detail);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

function articleContent(item: FeedItem) {
  return {
    id: item.id,
    channelId: item.channelId,
    title: item.title,
    body: item.body,
    timestamp: item.timestamp,
    source: item.source,
    clickUrl: item.clickUrl,
    tags: item.tags,
    priority: item.priority,
  };
}

function syncItem(item: FeedItem, entry?: TrashEntry) {
  return {
    ...articleContent(item),
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

  async syncArticles(settings: AppSettings, feed: FeedItem[], trash: TrashEntry[]) {
    const trashItems = trash
      .filter((entry): entry is TrashEntry & { item: FeedItem } => Boolean(entry.item))
      .map((entry) => syncItem(entry.item, entry));
    const items = [
      ...feed.filter((item) => !item.id.startsWith("demo-message-")).map((item) => syncItem(item)),
      ...trashItems.filter((item) => !item.id.startsWith("demo-message-")),
    ];
    return request<{ accepted: number }>(
      settings,
      "/api/v1/articles/sync",
      { method: "POST", body: JSON.stringify({ items }) },
      true,
    );
  },

  batchArticles(settings: AppSettings, items: FeedItem[]) {
    const realItems = items.filter((item) => !item.id.startsWith("demo-message-"));
    if (realItems.length === 0) return Promise.resolve({ accepted: 0 });
    return request<{ accepted: number }>(settings, "/api/v1/articles/batch", {
      method: "POST",
      body: JSON.stringify({ items: realItems.map(articleContent) }),
    });
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

  upsertChannel(settings: AppSettings, channel: SmartChannel) {
    const { authToken: _authToken, lastSyncAt: _lastSyncAt, isDemo: _isDemo, ...safe } = channel;
    return request<{ id: string }>(settings, "/api/v1/channels", {
      method: "POST",
      body: JSON.stringify(safe),
    });
  },

  deleteChannel(settings: AppSettings, channelId: string) {
    return request<void>(settings, `/api/v1/channels/${encodeURIComponent(channelId)}`, {
      method: "DELETE",
    });
  },
};
