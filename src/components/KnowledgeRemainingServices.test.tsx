import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { defaultSettings as settings } from "../data/demo";
import { knowledgeApi } from "../lib/knowledgeApi";
import { KnowledgeCosts } from "./KnowledgeCosts";
import { KnowledgeFolderWatch } from "./KnowledgeFolderWatch";
import { KnowledgeNotifications } from "./KnowledgeNotifications";
import { KnowledgeUsage } from "./KnowledgeUsage";
import { KnowledgeOperations } from "./KnowledgeOperations";
import { KnowledgeLinkImport } from "./KnowledgeLinkImport";

vi.mock("../lib/knowledgeApi", async original => {
  const actual = await original<typeof import("../lib/knowledgeApi")>();
  return { ...actual, knowledgeApi: Object.fromEntries(Object.keys(actual.knowledgeApi).map(name => [name, vi.fn()])) };
});
const api = vi.mocked(knowledgeApi);
const at = "2026-10-28T00:00:00";
const costs = { enabled: true, daily_exposure_cny: "1", monthly_exposure_cny: "2", daily_limit_cny: "5", monthly_limit_cny: "10", daily_unpriced: 0, monthly_unpriced: 0, request_ceiling_cny: { qwen_asr: "0.1" } };
const prefs = { revision: 1, desktop_notifications: false, hermes_notifications: false, admin_alerts: false, channel_id: null, hermes_ready: false, channels: [] };
const usage = { daily_submissions: 1, monthly_submissions: 2, daily_limit: 20, monthly_limit: 200, daily_remaining: 19, monthly_remaining: 198, next_daily_reset: "2026-10-29T00:00:00", timezone: "UTC", accounting: "submission_reservations_not_currency" };
function defer<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(r => { resolve = r; }); return { promise, resolve }; }
function open(text: string) { const el = screen.getByText(text); fireEvent.click(el); const parent = el.closest("details")!; if (!parent.open) { parent.open = true; fireEvent(parent, new Event("toggle")); } expect(parent.open).toBe(true); return parent; }
beforeEach(() => { vi.resetAllMocks(); api.costs.mockResolvedValue(costs); api.preferences.mockResolvedValue(prefs); api.notifications.mockResolvedValue({ items: [] }); api.usage.mockResolvedValue(usage); });
afterEach(() => { vi.restoreAllMocks(); delete window.signalDesk; });

describe("remaining service controls: React runtime with synthetic API/IPC only", () => {
  it("[KBUI-KnowledgeCosts-1,KBUI-KnowledgeCosts-3,KBUI-KnowledgeCosts-4,KBUI-KnowledgeCosts-5] administrator ledger pages preserve offsets and provider metadata", async () => {
    const user = userEvent.setup();
    const row = (n: number) => ({ usage_id: `synthetic-${n}`, operation: "qwen_asr", reserved_cny: "0.1", actual_cny: null, state: "reserved", created_at: at, provider_requests: [{ provider: "qwen", request_id: `public-test-${n}`, response_id: "fixture", http_status: 200, usage: { input_tokens: 1 }, created_at: at }] });
    api.costLedger.mockImplementation(async (_s, offset) => ({ items: offset === 0 ? Array.from({ length: 50 }, (_, n) => row(n)) : [row(50)] }));
    render(<KnowledgeCosts settings={settings} canManage={true} disabled={false} />);
    open("人民币预算与账本（预留不是账单）");
    await screen.findByText(/synthetic-49/);
    const provider = screen.getAllByText("服务商关联记录（不是费用）")[0]; await user.click(provider); expect(provider.closest("details")).toHaveAttribute("open");
    expect(within(provider.closest("details")!).getByText(/public-test-0/)).toBeVisible();
    expect(screen.getByRole("button", { name: "上一批" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "下一批" }));
    await screen.findByText(/synthetic-50/); expect(screen.getByRole("button", { name: "下一批" })).toBeDisabled();
    expect(api.costLedger).toHaveBeenLastCalledWith(settings, 50, false, expect.any(AbortSignal));
    await user.click(screen.getByRole("button", { name: "上一批" })); await screen.findByText(/synthetic-49/);
    await user.click(screen.getByRole("checkbox", { name: /查看管理员费用元数据账本/ }));
    await waitFor(() => expect(api.costLedger).toHaveBeenLastCalledWith(settings, 0, true, expect.any(AbortSignal)));
  });

  it("[KBUI-KnowledgeCosts-7] verified bill drawer validates JSON and resets consent after edits", async () => {
    const user = userEvent.setup(); api.costLedger.mockResolvedValue({ items: [] }); api.reconcileCosts.mockResolvedValue({ reconciled: 1 });
    render(<KnowledgeCosts settings={settings} canManage={true} disabled={false} />); open("人民币预算与账本（预留不是账单）"); open("导入已核实账单金额");
    const input = screen.getByLabelText("账单行 JSON（最多 100 行）"), consent = screen.getByRole("checkbox", { name: /已逐行核对服务商账单/ });
    await user.type(input, "invalid-json"); await user.click(consent); await user.click(screen.getByRole("button", { name: "保存人工对账记录" }));
    expect(await screen.findByRole("alert")).toBeVisible(); expect(api.reconcileCosts).not.toHaveBeenCalled(); expect(consent).not.toBeChecked();
    await user.clear(input); fireEvent.change(input, { target: { value: '[{"usage_id":"fixture","amount_cny":"0.01","invoice_reference":"synthetic:1"}]' } });
    await user.click(consent); await user.click(screen.getByRole("button", { name: "保存人工对账记录" }));
    expect(await screen.findByRole("status")).toHaveTextContent("已保存 1 行"); expect(input).toHaveValue("");
    expect(api.reconcileCosts).toHaveBeenCalledTimes(1);
  });

  it("[KBUI-KnowledgeFolderWatch-1,KBUI-KnowledgeFolderWatch-4,KBUI-KnowledgeFolderWatch-5] observe-only mode calls IPC without auto import, then manual upload callback", async () => {
    const user = userEvent.setup(); const onImport = vi.fn(); const onSaved = vi.fn();
    const idle = { running: false, folder: "", message: "未开启", files: [] };
    const observe = { ...idle, running: true, mode: "observe", folder: "D:/synthetic-selected", message: "只读文件名", files: [{ name: "fixture.txt", size: 10, updatedAt: 0 }] };
    window.signalDesk = { knowledgeWatchStatus: vi.fn().mockResolvedValue(idle), knowledgeWatchStart: vi.fn().mockResolvedValue(observe), knowledgeWatchStop: vi.fn().mockResolvedValue(idle) } as unknown as NonNullable<Window["signalDesk"]>;
    render(<KnowledgeFolderWatch disabled={false} onImport={onImport} onSaved={onSaved} />); await user.click(screen.getByText(/文件夹导入 ·/));
    await user.click(screen.getByRole("button", { name: "仅观察文件名" }));
    expect(window.signalDesk.knowledgeWatchStart).toHaveBeenCalledExactlyOnceWith({ autoImport: false });
    expect(await screen.findByText(/仅本地观察/)).toBeVisible(); expect(screen.getByText(/fixture.txt/)).toBeVisible(); expect(onSaved).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "手动选择文件上传" })); expect(onImport).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "停止文件夹任务" })); expect(window.signalDesk.knowledgeWatchStop).toHaveBeenCalledTimes(1);
  });

  it("[KBUI-KnowledgeNotifications-1,KBUI-KnowledgeNotifications-2,KBUI-KnowledgeNotifications-3,KBUI-KnowledgeNotifications-6,KBUI-KnowledgeNotifications-8] local-only preferences, failed-load refresh and read are explicit", async () => {
    const user = userEvent.setup(); api.preferences.mockRejectedValueOnce(new Error("synthetic preference unavailable")).mockResolvedValue(prefs);
    api.notifications.mockResolvedValue({ items: [{ id: "n1", item_id: null, revision: 1, kind: "finished", message: "合成处理完成", delivery_state: "inbox", created_at: at }] });
    api.savePreferences.mockImplementation(async (_s, value) => ({ ...value, revision: 2 })); api.readNotification.mockResolvedValue({} as never);
    render(<KnowledgeNotifications settings={settings} canManage={true} disabled={false} />); await user.click(screen.getByText(/通知设置与未读消息/));
    await user.click(await screen.findByRole("button", { name: "刷新" }));
    const desktop = await screen.findByRole("checkbox", { name: /客户端桌面通知/ }); await user.click(desktop);
    const admin = screen.getByRole("checkbox", { name: /订阅后台心跳/ }); await user.click(admin);
    expect(screen.getByRole("checkbox", { name: /发送到本人绑定的 Hermes/ })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "保存通知设置" }));
    await waitFor(() => expect(api.savePreferences).toHaveBeenCalledWith(settings, expect.objectContaining({ desktop_notifications: true, admin_alerts: true, hermes_notifications: false })));
    await user.click(screen.getByRole("button", { name: "标记已读" })); await waitFor(() => expect(screen.queryByText("合成处理完成")).not.toBeInTheDocument());
    expect(api.readNotification).toHaveBeenCalledWith(settings, "n1"); expect(api.retryNotification).not.toHaveBeenCalled();
  });

  it("[KBUI-KnowledgeNotifications-4,KBUI-KnowledgeNotifications-5,KBUI-KnowledgeNotifications-9] synthetic non-QQ Hermes choice and duplicate-risk cancellation/confirmation make one mocked request", async () => {
    const user = userEvent.setup(); const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    api.preferences.mockResolvedValue({ ...prefs, hermes_ready: true, channels: [{ id: 7, platform: "wecom-synthetic" }] });
    api.notifications.mockResolvedValueOnce({ items: [{ id: "unknown", item_id: null, revision: 1, message: "合成未确认投递", kind: "done", delivery_state: "unknown", created_at: at }] }).mockResolvedValue({ items: [] });
    api.savePreferences.mockImplementation(async (_s, value) => value); api.retryNotification.mockResolvedValue({} as never);
    render(<KnowledgeNotifications settings={settings} canManage={false} disabled={false} />); await user.click(screen.getByText(/通知设置与未读消息/));
    await user.click(await screen.findByRole("checkbox", { name: /发送到本人绑定的 Hermes/ })); await user.selectOptions(screen.getByLabelText("投递渠道"), "7");
    await user.click(screen.getByRole("button", { name: "保存通知设置" }));
    await waitFor(() => expect(api.savePreferences).toHaveBeenCalledWith(settings, expect.objectContaining({ channel_id: 7, hermes_notifications: true })));
    await user.click(screen.getByRole("button", { name: "确认风险后重发" })); expect(api.retryNotification).not.toHaveBeenCalled();
    confirm.mockReturnValue(true); await user.click(screen.getByRole("button", { name: "确认风险后重发" }));
    await waitFor(() => expect(api.retryNotification).toHaveBeenCalledExactlyOnceWith(settings, "unknown"));
    expect(await screen.findByText(/暂无未读消息/)).toBeVisible();
  });

  it("[KBUI-KnowledgeLinkImport-1,KBUI-KnowledgeLinkImport-4] stop is observed before next synthetic link and saved results are kept", async () => {
    const user = userEvent.setup(); const pending = defer<{ url: string; saved: boolean; note: string }[]>(); let stopped: () => boolean = () => false;
    const submit = vi.fn((_links, stop) => { stopped = stop; return pending.promise; });
    render(<KnowledgeLinkImport disabled={false} onImport={submit} />); await user.click(screen.getByText("批量收集公众号 / 视频号链接"));
    await user.type(screen.getByLabelText("分享链接列表"), "https://weixin.qq.com/sph/synthetic-one\nhttps://weixin.qq.com/sph/synthetic-two");
    await user.click(screen.getByRole("button", { name: "逐个保存链接" })); expect(stopped()).toBe(false);
    await user.click(screen.getByRole("button", { name: "当前链接结束后停止" })); expect(stopped()).toBe(true);
    await act(async () => pending.resolve([{ url: "https://weixin.qq.com/sph/synthetic-one", saved: true, note: "synthetic saved" }]));
    expect(await screen.findByRole("alert")).toHaveTextContent("后续 1 个链接未开始"); expect(screen.getByLabelText("本批链接保存结果")).toHaveTextContent("synthetic saved"); expect(submit).toHaveBeenCalledTimes(1);
  });

  it("[KBUI-KnowledgeUsage-1,KBUI-KnowledgeUsage-2] quota refresh replaces totals and errors stop stale numbers from appearing usable", async () => {
    const user = userEvent.setup(); api.usage.mockResolvedValueOnce(usage).mockRejectedValueOnce(new Error("synthetic quota unavailable")).mockResolvedValue({ ...usage, daily_remaining: 0 });
    render(<KnowledgeUsage settings={settings} busy={false} />); await user.click(await screen.findByText(/云端处理额度 · 今日剩余/));
    expect(screen.getByText(/今日已提交 1 次/)).toBeVisible(); await user.click(screen.getByRole("button", { name: "刷新额度" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("旧数值不作为可用额度"); expect(screen.queryByText(/今日剩余 19/)).toBeNull();
    await user.click(screen.getByRole("button", { name: "刷新额度" })); expect(await screen.findByText(/当前提交额度已用完/)).toBeVisible();
  });

  it("[KBUI-KnowledgeOperations-1] admin drawer reads only on expand and refreshes aggregate queue state", async () => {
    const user = userEvent.setup(); api.operations.mockResolvedValue({ job_states: { queued: 2 }, oldest_wait_seconds: 60, file_cleanup_pending: 0, remote_cleanup_pending: 0, worker_health: "recent_heartbeat", recent_workers: 1 });
    render(<KnowledgeOperations settings={settings} busy={false} />); expect(api.operations).not.toHaveBeenCalled();
    const details = open("后台队列概况 · 管理员"); expect(await screen.findByText("排队：2")).toBeVisible();
    api.operations.mockResolvedValue({ job_states: { queued: 0 }, oldest_wait_seconds: 0, file_cleanup_pending: 0, remote_cleanup_pending: 0, worker_health: "stale_heartbeat" });
    await user.click(screen.getByRole("button", { name: "刷新队列概况" })); expect(await screen.findByText("排队：0")).toBeVisible(); expect(within(details).getByText(/心跳已过期/)).toBeVisible();
  });
});
