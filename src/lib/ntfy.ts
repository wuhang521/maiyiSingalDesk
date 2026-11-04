import type { AppSettings, FeedItem, NtfyMessage, SmartChannel } from "../types";
import { canonicalChannelId } from "./channelIdentity";

const ARTICLE_HEADING = /^###\s+(\d+)[.、]\s*(.+?)\s*$/gm;

export function mapNtfyMessageItems(
  channel: SmartChannel,
  payload: NtfyMessage,
): FeedItem[] {
  const channelId = canonicalChannelId(channel.id, [channel]);
  if (channelId === "health" && payload.tags?.includes("health_status")) return [];
  return splitDigestItem({
    id: payload.id,
    channelId,
    title: payload.title || channel.name,
    body: payload.message || "",
    timestamp: payload.time * 1000,
    source: channel.server.replace(/^https?:\/\//, ""),
    clickUrl: payload.click,
    tags: payload.tags || [],
    priority: payload.priority || 3,
    read: false,
    saved: false,
    feedback: null,
    stateUpdatedAt: 0,
  });
}

export function normalizeFeedItems(items: FeedItem[]): FeedItem[] {
  return items.flatMap(splitDigestItem).map((item) => ({
    ...item,
    stateUpdatedAt: item.stateUpdatedAt || 0,
  }));
}

function splitDigestItem(item: FeedItem): FeedItem[] {
  const matches = Array.from(item.body.matchAll(ARTICLE_HEADING));
  if (matches.length === 0) return [item];

  return matches.map((match, index) => {
    const bodyStart = (match.index || 0) + match[0].length;
    const bodyEnd = matches[index + 1]?.index ?? item.body.length;
    const articleBody = item.body.slice(bodyStart, bodyEnd).trim();
    const clickUrl = articleLink(articleBody) || item.clickUrl;

    return {
      ...item,
      id: `${item.id}:${match[1]}`,
      title: match[2].replace(/[*_`]/g, "").trim(),
      body: formatArticleBody(articleBody),
      source: articleField(articleBody, "来源") || item.source,
      clickUrl,
    };
  });
}

export function mergeFeed(
  current: FeedItem[],
  incoming: FeedItem[],
  excludedIds: Iterable<string> = [],
): FeedItem[] {
  const excluded = new Set(excludedIds);
  const currentById = new Map(current.map((item) => [item.id, item]));
  const idByIdentity = new Map(current.map((item) => [feedIdentity(item), item.id]));
  for (const item of incoming) {
    if (excluded.has(item.id)) continue;
    const existingId = currentById.has(item.id)
      ? item.id
      : idByIdentity.get(feedIdentity(item));
    const existing = existingId ? currentById.get(existingId) : undefined;
    const normalizedItem = existingId ? { ...item, id: existingId } : item;
    const keepExistingState =
      Boolean(existing) && (existing?.stateUpdatedAt || 0) >= (item.stateUpdatedAt || 0);
    currentById.set(
      normalizedItem.id,
      existing
        ? {
            ...existing,
            ...normalizedItem,
            read: keepExistingState ? existing.read : normalizedItem.read,
            saved: keepExistingState ? existing.saved : normalizedItem.saved,
            feedback: keepExistingState ? existing.feedback : normalizedItem.feedback,
            stateUpdatedAt: Math.max(existing.stateUpdatedAt || 0, normalizedItem.stateUpdatedAt || 0),
          }
        : normalizedItem,
    );
    idByIdentity.set(feedIdentity(normalizedItem), normalizedItem.id);
  }
  return Array.from(currentById.values())
    .filter((item) => !excluded.has(item.id))
    .sort((a, b) => b.timestamp - a.timestamp);
}

function articleLink(body: string): string | undefined {
  return body.match(/\[打开(?:链接|原文)\]\((https?:\/\/[^)\s]+)\)/)?.[1];
}

function articleField(body: string, label: string): string | undefined {
  return body.match(new RegExp(`^${label}[：:]\\s*(.+)$`, "m"))?.[1].trim();
}

function formatArticleBody(body: string): string {
  return body
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line && !/^链接[：:]\s*\[打开(?:链接|原文)\]\([^)]+\)\s*$/.test(line))
    .map((line) => {
      const field = line.match(
        /^(一句话看懂|摘要|主体|时间|地点|主要内容|关键结果|业务分类|为什么值得看|信息依据|来源)[：:]\s*(.*)$/,
      );
      if (!field) return line;

      const [, label, value] = field;
      if (label === "一句话看懂" || label === "摘要") {
        return `> **一句话看懂**\n>\n> ${value}`;
      }
      if (["主要内容", "关键结果", "为什么值得看"].includes(label)) {
        return `### ${label}\n\n${value}`;
      }
      return `**${label}：** ${value}`;
    })
    .join("\n\n")
    .trim();
}

function feedIdentity(item: FeedItem): string {
  const title = item.title.trim().toLowerCase();
  const source = item.clickUrl?.trim().toLowerCase() || item.source.trim().toLowerCase();
  return `${title}|${source}`;
}

export function usesHealthRelay(channel: SmartChannel): boolean {
  return canonicalChannelId(channel.id, [channel]) === "health" && channel.managed === true && channel.readViaApi === true;
}

export function channelHistoryWindow(channel: SmartChannel, since: string): string {
  return usesHealthRelay(channel) && ["3d", "7d", "all"].includes(since) ? "24h" : since;
}

export function channelConnection(
  channel: SmartChannel,
  settings: Pick<AppSettings, "defaultServer" | "globalAuthToken"> & Partial<Pick<AppSettings, "backendUrl" | "backendAuthToken">>,
) {
  if (usesHealthRelay(channel)) {
    const backend = settings.backendUrl?.trim().replace(/\/+$/, "");
    const token = settings.backendAuthToken?.trim();
    if (!backend || !token) return null;
    try {
      const target = new URL(backend);
      if (!["http:", "https:"].includes(target.protocol) || target.username || target.password || target.search || target.hash) return null;
    } catch { return null; }
    return { server: backend + "/api/v1/health-relay", topic: "health", token };
  }
  const server = channel.server || settings.defaultServer;
  const tokenServer = channel.authTokenServer ?? server;
  const tokenTopic = channel.authTokenTopic ?? channel.topic;
  const matchingCredential = tokenServer.replace(/\/+$/, "") === server.replace(/\/+$/, "") && tokenTopic === channel.topic;
  return {
    server,
    topic: channel.topic,
    token: (matchingCredential ? channel.authToken : "") || settings.globalAuthToken || undefined,
  };
}
