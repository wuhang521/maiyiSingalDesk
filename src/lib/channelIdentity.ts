import type { FeedItem, SmartChannel, TrashEntry } from "../types";

const knownAliases: Record<string, string> = { "demo-health": "health" };

export function canonicalChannelId(id: string, channels: readonly SmartChannel[] = []): string {
  const aliases = new Map<string, string>();
  for (const channel of channels) {
    for (const alias of channel.aliases || []) if (alias !== channel.id) aliases.set(alias, channel.id);
  }
  for (const [alias, target] of Object.entries(knownAliases)) aliases.set(alias, target);
  const visited = new Set<string>();
  while (aliases.has(id) && !visited.has(id)) {
    visited.add(id);
    id = aliases.get(id)!;
  }
  return id;
}

function localCredential(channels: readonly SmartChannel[], preferred: SmartChannel, defaultServer = "") {
  const owner = (preferred.authToken && preferred) || channels.find((channel) => channel.authToken);
  return {
    authToken: owner?.authToken || "",
    authTokenServer: owner ? owner.authTokenServer ?? (owner.server || defaultServer) : undefined,
    authTokenTopic: owner ? owner.authTokenTopic ?? owner.topic : undefined,
  };
}

export function canonicalizeChannels(
  channels: readonly SmartChannel[],
  aliasSources: readonly SmartChannel[] = channels,
  defaultServer = "",
): SmartChannel[] {
  const groups = new Map<string, SmartChannel[]>();
  for (const channel of channels) {
    const id = canonicalChannelId(channel.id, aliasSources);
    groups.set(id, [...(groups.get(id) || []), channel]);
  }
  return Array.from(groups, ([id, group]) => {
    const preferred = group.find((channel) => channel.id === id && channel.managed)
      || group.find((channel) => channel.id === id)
      || group.find((channel) => channel.managed)
      || group[0];
    const aliases = Array.from(new Set([
      ...group.flatMap((channel) => [channel.id, ...(channel.aliases || [])]),
      ...Object.keys(knownAliases).filter((alias) => canonicalChannelId(alias, aliasSources) === id),
    ])).filter((alias) => alias !== id);
    return {
      ...preferred,
      id,
      aliases,
      ...localCredential(group, preferred, defaultServer),
      lastSyncAt: Math.max(...group.map((channel) => channel.lastSyncAt || 0)) || undefined,
    };
  });
}

export function mergeChannelSnapshot(local: readonly SmartChannel[], remote: readonly SmartChannel[]): SmartChannel[] {
  const sources = [...local, ...remote];
  const localById = new Map(canonicalizeChannels(local, sources).map((channel) => [channel.id, channel]));
  const merged = canonicalizeChannels(remote, sources).map((channel) => {
    const previous = localById.get(channel.id);
    localById.delete(channel.id);
    return {
      ...channel,
      ...(previous ? localCredential([previous], previous) : {}),
      lastSyncAt: previous?.lastSyncAt,
      isDemo: channel.managed ? false : previous?.isDemo,
      aliases: Array.from(new Set([...(previous?.aliases || []), ...(channel.aliases || [])])),
    };
  });
  return [...merged, ...localById.values()];
}

export function canonicalizeFeed(items: readonly FeedItem[], channels: readonly SmartChannel[] = []): FeedItem[] {
  return items.map((item) => ({ ...item, channelId: canonicalChannelId(item.channelId, channels) }));
}

export function canonicalizeTrash(entries: readonly TrashEntry[], channels: readonly SmartChannel[] = []): TrashEntry[] {
  return entries.map((entry) => ({
    ...entry,
    ...(entry.item ? { item: canonicalizeFeed([entry.item], channels)[0] } : {}),
  }));
}
