import { describe, expect, it } from "vitest";
import type { FeedItem, NtfyMessage, SmartChannel } from "../types";
import { mapNtfyMessageItems, mergeFeed } from "./ntfy";

const channel: SmartChannel = {
  id: "channel-1",
  name: "测试频道",
  description: "",
  prompt: "test prompt",
  topic: "test-topic",
  server: "https://ntfy.sh",
  authToken: "",
  schedule: "09:00",
  maxItems: 10,
  includeTerms: [],
  excludeTerms: [],
  color: "green",
  state: "active",
  createdAt: 1,
  updatedAt: 1,
};

describe("ntfy mapping", () => {
  it("maps a message into the local feed model", () => {
    const payload: NtfyMessage = {
      id: "m1",
      time: 100,
      event: "message",
      topic: "test-topic",
      title: "标题",
      message: "正文",
      tags: ["books"],
      priority: 4,
      click: "https://example.com",
    };

    expect(mapNtfyMessageItems(channel, payload)).toEqual([
      expect.objectContaining({
      id: "m1",
      channelId: "channel-1",
      title: "标题",
      body: "正文",
      timestamp: 100000,
      clickUrl: "https://example.com",
      read: false,
      }),
    ]);
  });

  it("splits a digest into independently readable articles", () => {
    const payload: NtfyMessage = {
      id: "digest-1",
      time: 100,
      event: "message",
      topic: "test-topic",
      title: "今日学习：健康管理决策情报",
      message: [
        "今日健康管理决策情报（2条）",
        "",
        "### 1. 第一篇文章",
        "",
        "一句话看懂：第一篇摘要。",
        "来源：example-one.com",
        "链接：[打开链接](https://example-one.com/article)",
        "",
        "### 2. 第二篇文章",
        "",
        "主要内容：第二篇正文。",
        "来源：example-two.com",
        "链接：[打开链接](https://example-two.com/article)",
      ].join("\n"),
    };

    const items = mapNtfyMessageItems(channel, payload);

    expect(items).toHaveLength(2);
    expect(items[0]).toMatchObject({
      id: "digest-1:1",
      title: "第一篇文章",
      source: "example-one.com",
      clickUrl: "https://example-one.com/article",
    });
    expect(items[0].body).toContain("一句话看懂");
    expect(items[0].body).not.toContain("第二篇文章");
    expect(items[1]).toMatchObject({
      id: "digest-1:2",
      title: "第二篇文章",
      source: "example-two.com",
      clickUrl: "https://example-two.com/article",
    });
  });

  it("preserves local read and saved state while refreshing server content", () => {
    const existing: FeedItem = {
      id: "m1",
      channelId: "channel-1",
      title: "旧标题",
      body: "旧正文",
      timestamp: 1,
      source: "old",
      tags: [],
      priority: 3,
      read: true,
      saved: true,
      feedback: "useful",
    };
    const incoming = { ...existing, title: "新标题", body: "新正文", read: false, saved: false };

    const [merged] = mergeFeed([existing], [incoming]);
    expect(merged.title).toBe("新标题");
    expect(merged.body).toBe("新正文");
    expect(merged.read).toBe(true);
    expect(merged.saved).toBe(true);
    expect(merged.feedback).toBe("useful");
  });

  it("uses the newer server state when it is more recent than the local cache", () => {
    const existing: FeedItem = {
      id: "m2",
      channelId: "channel-1",
      title: "旧标题",
      body: "旧正文",
      timestamp: 1,
      source: "old",
      tags: [],
      priority: 3,
      read: false,
      saved: false,
      feedback: null,
      stateUpdatedAt: 100,
    };
    const incoming: FeedItem = {
      ...existing,
      read: true,
      saved: true,
      feedback: "useful",
      stateUpdatedAt: 200,
    };

    expect(mergeFeed([existing], [incoming])[0]).toMatchObject({
      read: true,
      saved: true,
      feedback: "useful",
      stateUpdatedAt: 200,
    });
  });

  it("does not restore a locally deleted article during refresh", () => {
    const existing: FeedItem = {
      id: "digest-1:1",
      channelId: "channel-1",
      title: "第一篇文章",
      body: "正文",
      timestamp: 1,
      source: "example.com",
      tags: [],
      priority: 3,
      read: false,
      saved: false,
      feedback: null,
    };

    expect(mergeFeed([], [existing], [existing.id])).toEqual([]);
  });

  it("deduplicates the same article from the database and ntfy", () => {
    const stored: FeedItem = {
      id: "daily:health:2026-08-11:1",
      channelId: "health",
      title: "同一篇文章",
      body: "服务器正文",
      timestamp: 1,
      source: "example.com",
      clickUrl: "https://example.com/same",
      tags: [],
      priority: 3,
      read: true,
      saved: true,
      feedback: null,
      stateUpdatedAt: 10,
    };
    const ntfy = {
      ...stored,
      id: "ntfy-message:1",
      body: "ntfy 正文",
      read: false,
      saved: false,
      stateUpdatedAt: 0,
    };

    const merged = mergeFeed([stored], [ntfy]);
    expect(merged).toHaveLength(1);
    expect(merged[0]).toMatchObject({
      id: stored.id,
      body: "ntfy 正文",
      read: true,
      saved: true,
    });
  });
});
