import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { articleApi, type MemberChannelSubscriptions } from "../lib/articleApi";
import { memberApi, type ManagedMember } from "../lib/memberApi";
import { defaultSettings } from "../data/demo";
import { MemberSubscriptionsDialog } from "./MemberSubscriptionsDialog";
import { MemberManagement } from "./MemberManagement";

vi.mock("../lib/articleApi", () => ({ articleApi: { memberSubscriptions: vi.fn(), saveMemberSubscriptions: vi.fn() } }));
vi.mock("../lib/memberApi", () => ({ memberApi: { list: vi.fn() } }));
const member: ManagedMember = { id: 7, name: "付涛涛", enabled: true, removed_at: null, created_at: "", updated_at: "", channels: [],
  user: { id: 19, username: "futaotao", role: "member", enabled: true, last_login_at: null } };
const state: MemberChannelSubscriptions = {
  staff_id: 7, user_id: 19, member_name: "付涛涛", wechat_bound: true, binding_revision: "a".repeat(64), subscribed_channel_ids: ["ai"],
  channels: [
    { id: "ai", name: "AI 决策雷达", schedule: "09:00", enabled: true, subscribed: true, can_subscribe: true, unavailable_reason: "" },
    { id: "medical", name: "医疗前沿频道", schedule: "10:00", enabled: true, subscribed: false, can_subscribe: true, unavailable_reason: "" },
    { id: "restricted", name: "内部频道", schedule: "09:00", enabled: true, subscribed: false, can_subscribe: false, unavailable_reason: "请先开放订阅权限" },
  ],
};
const settings = { ...defaultSettings, backendUrl: "http://127.0.0.1:19080", backendAuthToken: "synthetic-admin" };

describe("管理员为成员设置实际频道订阅", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
    HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
    vi.mocked(articleApi.memberSubscriptions).mockResolvedValue(state);
    vi.mocked(articleApi.saveMemberSubscriptions).mockResolvedValue(state);
  });

  it("读取本人已订阅项，可取消和新增，发送原快照与选中频道", async () => {
    const user = userEvent.setup();
    const saved = vi.fn();
    render(<MemberSubscriptionsDialog member={member} settings={settings} onClose={vi.fn()} onSaved={saved} />);
    expect(await screen.findByRole("checkbox", { name: /AI 决策雷达/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /内部频道/ })).toBeDisabled();
    await user.click(screen.getByRole("checkbox", { name: /AI 决策雷达/ }));
    await user.click(screen.getByRole("checkbox", { name: /医疗前沿频道/ }));
    await user.click(screen.getByRole("button", { name: "保存频道订阅" }));
    expect(articleApi.saveMemberSubscriptions).toHaveBeenCalledWith(settings, state, ["medical"]);
    expect(saved).toHaveBeenCalledOnce();
  });

  it("保存冲突保留选择，重新加载后读取最新快照", async () => {
    const user = userEvent.setup();
    vi.mocked(articleApi.saveMemberSubscriptions).mockRejectedValue(new Error("该成员的订阅已被更新，请重新加载后再编辑"));
    render(<MemberSubscriptionsDialog member={member} settings={settings} onClose={vi.fn()} onSaved={vi.fn()} />);
    await user.click(await screen.findByRole("checkbox", { name: /医疗前沿频道/ }));
    await user.click(screen.getByRole("button", { name: "保存频道订阅" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("订阅已被更新");
    expect(screen.getByRole("checkbox", { name: /医疗前沿频道/ })).toBeChecked();
    await user.click(screen.getByRole("button", { name: "重新加载" }));
    await waitFor(() => expect(screen.getByRole("checkbox", { name: /医疗前沿频道/ })).not.toBeChecked());
  });

  it("无微信时解释原因，已暂停的既有订阅仍可取消", async () => {
    const user = userEvent.setup();
    const paused = { ...state, channels: [{ ...state.channels[0], enabled: false, can_subscribe: false, unavailable_reason: "该频道已暂停微信推送" }] };
    vi.mocked(articleApi.memberSubscriptions).mockResolvedValue(paused);
    const view = render(<MemberSubscriptionsDialog member={member} settings={settings} onClose={vi.fn()} onSaved={vi.fn()} />);
    const selected = await screen.findByRole("checkbox", { name: /AI 决策雷达/ });
    expect(selected).not.toBeDisabled();
    await user.click(selected);
    await user.click(screen.getByRole("button", { name: "保存频道订阅" }));
    expect(articleApi.saveMemberSubscriptions).toHaveBeenCalledWith(settings, paused, []);
    view.unmount();
    vi.mocked(articleApi.memberSubscriptions).mockResolvedValue({ ...state, wechat_bound: false, binding_revision: null,
      subscribed_channel_ids: [], channels: [{ ...state.channels[1], can_subscribe: false, unavailable_reason: "请先绑定微信" }] });
    render(<MemberSubscriptionsDialog member={member} settings={settings} onClose={vi.fn()} onSaved={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("绑定微信后开通订阅"));
    expect(screen.getByRole("checkbox", { name: /医疗前沿频道/ })).toBeDisabled();
  });

  it("加载失败可以重试；保存时阻止重复提交和取消", async () => {
    const user = userEvent.setup();
    const close = vi.fn();
    vi.mocked(articleApi.memberSubscriptions).mockRejectedValueOnce(new Error("网络暂不可用"));
    let resolve!: (value: MemberChannelSubscriptions) => void;
    vi.mocked(articleApi.saveMemberSubscriptions).mockImplementation(() => new Promise((done) => { resolve = done; }));
    render(<MemberSubscriptionsDialog member={member} settings={settings} onClose={close} onSaved={vi.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("网络暂不可用");
    await user.click(screen.getByRole("button", { name: "重新加载" }));
    await screen.findByRole("checkbox", { name: /AI 决策雷达/ });
    await user.dblClick(screen.getByRole("button", { name: "保存频道订阅" }));
    expect(articleApi.saveMemberSubscriptions).toHaveBeenCalledOnce();
    fireEvent(screen.getByRole("dialog"), new Event("cancel", { cancelable: true }));
    expect(close).not.toHaveBeenCalled();
    resolve(state);
    await waitFor(() => expect(screen.queryByRole("button", { name: "正在保存订阅…" })).not.toBeInTheDocument());
  });

  it("用户管理入口打开指定成员，保存后刷新订阅并保留用户表单", async () => {
    const user = userEvent.setup();
    vi.mocked(memberApi.list).mockResolvedValue([member]);
    const changed = vi.fn();
    render(<MemberManagement settings={settings} currentStaffId={99} onMembersChanged={changed} />);
    await user.click(await screen.findByRole("button", { name: "管理 付涛涛" }));
    await user.clear(screen.getByLabelText("姓名或称呼"));
    await user.type(screen.getByLabelText("姓名或称呼"), "尚未保存的名称");
    await user.click(screen.getByRole("button", { name: "频道订阅" }));
    await screen.findByRole("checkbox", { name: /AI 决策雷达/ });
    expect(articleApi.memberSubscriptions).toHaveBeenCalledWith(settings, member.id, expect.any(AbortSignal));
    await user.click(screen.getByRole("button", { name: "保存频道订阅" }));
    expect(await screen.findByRole("status")).toHaveTextContent("成员无需再次订阅");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByLabelText("姓名或称呼")).toHaveValue("尚未保存的名称");
    expect(changed).toHaveBeenCalledOnce();
  });

  it("退出该成员界面后到达的保存结果不触发旧界面的成功回调", async () => {
    const user = userEvent.setup();
    let resolve!: (value: MemberChannelSubscriptions) => void;
    vi.mocked(articleApi.saveMemberSubscriptions).mockImplementation(() => new Promise((done) => { resolve = done; }));
    const saved = vi.fn();
    const view = render(<MemberSubscriptionsDialog member={member} settings={settings} onClose={vi.fn()} onSaved={saved} />);
    await screen.findByRole("checkbox", { name: /AI 决策雷达/ });
    await user.click(screen.getByRole("button", { name: "保存频道订阅" }));
    view.unmount();
    resolve(state);
    await new Promise((done) => setTimeout(done, 0));
    expect(saved).not.toHaveBeenCalled();
  });
});
