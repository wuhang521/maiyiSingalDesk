import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import App from "./App";
import { articleApi, type ChannelDeliveryConfig } from "./lib/articleApi";
import { authApi } from "./lib/authApi";
import { knowledgeApi } from "./lib/knowledgeApi";

const sample: ChannelDeliveryConfig = {
  id: "news-notice", name: "公告监测", description: "指定来源的最新公告", enabled: true,
  schedule: "09:00", max_items: 20, source_type: "website", source_url: "https://example.com/news",
  selector: "#news-list a", subscribed: false, include_terms: [], exclude_terms: [],
};

function workspace(role: "admin" | "member", bound: boolean, channel = sample) {
  const user = { id: 3, username: "test", displayName: "测试人员", staffId: 3, role };
  const authSession = { backendUrl: "http://127.0.0.1:19998", accessToken: "synthetic-personal", expiresAt: "2027-01-01T00:00:00Z", user };
  vi.spyOn(authApi, "me").mockResolvedValue(user);
  const catalog = vi.spyOn(articleApi, "channelDeliveryCatalog").mockResolvedValue({ wechat_bound: bound, can_manage: role === "admin", channels: [channel] });
  const subscription = vi.spyOn(articleApi, "setChannelSubscription").mockImplementation(async (_settings, _id, subscribed) => ({ subscribed }));
  const save = vi.spyOn(articleApi, "saveChannelDelivery").mockImplementation(async (_settings, draft) => ({ ...draft, id: draft.id ?? "created", subscribed: false }));
  Object.defineProperty(window, "signalDesk", { configurable: true, value: {
    loadAuthSession: vi.fn().mockResolvedValue(authSession), clearAuthSession: vi.fn(),
    getAppInfo: vi.fn().mockResolvedValue({ version: "0.6.4", platform: "win32" }),
    onMessage: vi.fn(() => () => undefined), subscribe: vi.fn().mockResolvedValue({}), unsubscribe: vi.fn().mockResolvedValue(true),
  } });
  render(<App />);
  return { catalog, subscription, save };
}

beforeEach(() => {
  localStorage.clear();
  vi.spyOn(articleApi, "syncArticles").mockResolvedValue({ accepted: 0 });
  vi.spyOn(articleApi, "listChannels").mockResolvedValue([]);
  vi.spyOn(articleApi, "listArticles").mockResolvedValue({ total: 0, items: [] });
  vi.spyOn(knowledgeApi, "capabilities").mockResolvedValue({ notifications_available: false, asr_enabled: false, ocr_enabled: false, max_upload_mb: 500, scope: "private", qa_mode: "extractive" });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  Object.defineProperty(window, "signalDesk", { configurable: true, value: undefined });
});

it("lets a bound member opt in and cancel without exposing channel configuration", async () => {
  const user = userEvent.setup();
  const { subscription } = workspace("member", true);
  await user.click(await screen.findByRole("button", { name: "智能频道" }));
  await user.click(await screen.findByRole("button", { name: "订阅 公告监测" }));
  expect(subscription).toHaveBeenCalledWith(expect.objectContaining({ backendAuthToken: "synthetic-personal" }), sample.id, true);
  expect(await screen.findByRole("button", { name: "取消订阅 公告监测" })).toBeEnabled();
  expect(screen.queryByRole("button", { name: /配置频道/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "新建频道" })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "取消订阅 公告监测" }));
  expect(subscription).toHaveBeenLastCalledWith(expect.anything(), sample.id, false);
  expect(await screen.findByRole("button", { name: "订阅 公告监测" })).toBeEnabled();
});

it("shows the binding prerequisite and disables subscribing until the account is linked", async () => {
  const user = userEvent.setup();
  const { subscription } = workspace("member", false);
  await user.click(await screen.findByRole("button", { name: "智能频道" }));
  expect(await screen.findByText(/左侧「用户管理」.*「消息接收渠道」绑定微信后再订阅/)).toBeVisible();
  expect(screen.getByRole("button", { name: "订阅 公告监测" })).toBeDisabled();
  expect(subscription).not.toHaveBeenCalled();
});

it("allows a member to cancel a previous opt-in after a channel is paused", async () => {
  const user = userEvent.setup();
  const { subscription } = workspace("member", true, { ...sample, enabled: false, subscribed: true });
  await user.click(await screen.findByRole("button", { name: "智能频道" }));
  await user.click(await screen.findByRole("button", { name: "取消订阅 公告监测" }));
  expect(subscription).toHaveBeenCalledWith(expect.anything(), sample.id, false);
  expect(await screen.findByRole("button", { name: "订阅 公告监测" })).toBeDisabled();
});

it("keeps an admin draft on a save failure and then creates a URL and selector channel", async () => {
  const user = userEvent.setup();
  const { save } = workspace("admin", true);
  save.mockRejectedValueOnce(new Error("频道服务繁忙，请重试"));
  await user.click(await screen.findByRole("button", { name: "智能频道" }));
  await user.click(await screen.findByRole("button", { name: "新建频道" }));
  await user.type(screen.getByLabelText("频道名称"), "更新监测");
  await user.type(screen.getByLabelText("网页地址"), "https://example.com/updates");
  await user.type(screen.getByLabelText("内容选择器（CSS）"), "main > .notice");
  await user.click(screen.getByRole("button", { name: "保存频道" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("频道服务繁忙，请重试");
  expect(screen.getByLabelText("网页地址")).toHaveValue("https://example.com/updates");
  await user.click(screen.getByRole("button", { name: "保存频道" }));
  await waitFor(() => expect(save).toHaveBeenLastCalledWith(expect.anything(), expect.objectContaining({
    name: "更新监测", source_type: "website", source_url: "https://example.com/updates", selector: "main > .notice",
    schedule: "09:00", max_items: 20,
  })));
  expect(await screen.findByRole("button", { name: "配置频道 更新监测" })).toBeEnabled();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("edits an existing channel's article filters and keeps its ID", async () => {
  const user = userEvent.setup();
  const { save } = workspace("admin", true, { ...sample, source_type: "articles", source_url: null, selector: null });
  await user.click(await screen.findByRole("button", { name: "智能频道" }));
  await user.click(await screen.findByRole("button", { name: "配置频道 公告监测" }));
  await user.type(screen.getByLabelText("关注关键词"), "合作，更新");
  await user.type(screen.getByLabelText("排除关键词"), "广告");
  await user.click(screen.getByRole("button", { name: "保存频道" }));
  await waitFor(() => expect(save).toHaveBeenCalledWith(expect.anything(), expect.objectContaining({
    id: sample.id, source_type: "articles", source_url: null, selector: null,
    include_terms: ["合作", "更新"], exclude_terms: ["广告"],
  })));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: "配置频道 公告监测" })).toHaveLength(1);
});

it("leaves synchronization available when a failed opt-in interrupted a refresh", async () => {
  const user = userEvent.setup();
  const { catalog, subscription } = workspace("member", true);
  await user.click(await screen.findByRole("button", { name: "智能频道" }));
  await screen.findByRole("button", { name: "订阅 公告监测" });
  catalog.mockImplementationOnce(async (_settings, signal) => new Promise((_resolve, reject) => {
    signal?.addEventListener("abort", () => reject(new Error("已取消读取")), { once: true });
  }));
  subscription.mockRejectedValueOnce(new Error("微信订阅暂时不可用"));
  fireEvent.focus(window);
  expect(screen.getByRole("button", { name: "同步" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "订阅 公告监测" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("微信订阅暂时不可用");
  expect(screen.getByRole("button", { name: "同步" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "订阅 公告监测" })).toBeEnabled();
});
