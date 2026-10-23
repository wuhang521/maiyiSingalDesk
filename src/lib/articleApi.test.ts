import { afterEach, describe, expect, it, vi } from "vitest";
import { defaultSettings } from "../data/demo";
import type { FeedItem, SmartChannel } from "../types";
import { articleApi } from "./articleApi";

const settings = {
  ...defaultSettings,
  backendUrl: "https://api.example.com/signaldesk-task",
  backendAuthToken: "shared-key",
  operatorName: "敏总",
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("article API", () => {
  it("encodes the Chinese operator name in the request header", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          id: "a1",
          channelId: "health",
          title: "标题",
          body: "正文",
          timestamp: 1,
          source: "source",
          tags: [],
          priority: 3,
          read: true,
          saved: false,
          feedback: null,
          deletedAt: null,
          purged: false,
          stateUpdatedAt: 10,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await articleApi.updateState(settings, "a1", { read: true, stateUpdatedAt: 10 });

    expect(fetchMock).toHaveBeenCalledWith(
      "https://api.example.com/signaldesk-task/api/v1/articles/a1/state",
      expect.objectContaining({
        headers: expect.objectContaining({
          Authorization: "Bearer shared-key",
          "X-SignalDesk-Operator": encodeURIComponent("敏总"),
        }),
      }),
    );
  });

  it("never uploads a channel auth token", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: "health" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const channel: SmartChannel = {
      id: "health",
      name: "健康管理",
      description: "",
      prompt: "",
      topic: "topic",
      server: "https://ntfy.sh",
      authToken: "secret-token",
      schedule: "09:00",
      maxItems: 10,
      includeTerms: [],
      excludeTerms: [],
      color: "green",
      state: "active",
      createdAt: 1,
      updatedAt: 1,
    };

    await articleApi.upsertChannel(settings, channel);

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.body).not.toContain("secret-token");
    expect(init.body).not.toContain("authToken");
  });

  it("keeps demo articles local during the first migration", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ accepted: 1 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const base: FeedItem = {
      id: "real-1",
      channelId: "health",
      title: "正式文章",
      body: "正文",
      timestamp: 1,
      source: "source",
      tags: [],
      priority: 3,
      read: false,
      saved: false,
      feedback: null,
    };

    await articleApi.syncArticles(
      settings,
      [base, { ...base, id: "demo-message-1", title: "示例文章" }],
      [],
    );

    const body = JSON.parse(fetchMock.mock.calls[0][1]?.body as string) as {
      items: FeedItem[];
    };
    expect(body.items.map((item) => item.id)).toEqual(["real-1"]);
  });
});
