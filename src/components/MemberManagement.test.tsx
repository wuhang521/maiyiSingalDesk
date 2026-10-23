import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { defaultSettings } from "../data/demo";
import { memberApi, type ManagedMember } from "../lib/memberApi";
import { taskApi } from "../lib/taskApi";
import { MemberManagement } from "./MemberManagement";

vi.mock("../lib/memberApi", () => ({ memberApi: { list: vi.fn(), create: vi.fn(), update: vi.fn(), remove: vi.fn(), restore: vi.fn() } }));
vi.mock("../lib/taskApi", () => ({ taskApi: { addStaffChannel: vi.fn(), deleteStaffChannel: vi.fn() } }));
const settings = { ...defaultSettings, backendUrl: "http://127.0.0.1:19080", backendAuthToken: "test-token" };
const admin: ManagedMember = { id: 1, name: "管理员", enabled: true, removed_at: null, channels: [], created_at: "", updated_at: "", user: { id: 1, username: "admin", role: "admin", enabled: true, last_login_at: null } };
const member: ManagedMember = { ...admin, id: 2, name: "测试成员", user: { ...admin.user!, id: 2, username: "member", role: "member" } };

describe("用户管理", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.mocked(memberApi.list).mockResolvedValue([admin, member]);
  });

  it("添加用户失败时保留输入，并阻止重复提交", async () => {
    const user = userEvent.setup();
    let rejectSave!: (error: Error) => void;
    vi.mocked(memberApi.create).mockImplementation(() => new Promise((_, reject) => { rejectSave = reject; }));
    render(<MemberManagement settings={settings} currentStaffId={1} onMembersChanged={vi.fn()} />);
    await screen.findByText("测试成员");
    await user.click(screen.getByRole("button", { name: "添加用户" }));
    await user.type(screen.getByLabelText("姓名或称呼"), "新同事");
    await user.type(screen.getByLabelText(/登录账号/), "newmember");
    await user.type(screen.getByLabelText("初始密码"), "test-password");
    await user.click(screen.getByRole("button", { name: "确认添加" }));
    expect(screen.getByRole("button", { name: "正在保存…" })).toBeDisabled();
    expect(memberApi.create).toHaveBeenCalledTimes(1);
    rejectSave(new Error("登录账号已存在"));
    expect(await screen.findByRole("alert")).toHaveTextContent("登录账号已存在");
    expect(screen.getByLabelText("姓名或称呼")).toHaveValue("新同事");
    expect(screen.getByLabelText("初始密码")).toHaveValue("test-password");
  });

  it("移除需要确认，当前账号受保护，取消不会发请求", async () => {
    const user = userEvent.setup();
    render(<MemberManagement settings={settings} currentStaffId={1} onMembersChanged={vi.fn()} />);
    await screen.findByText("测试成员");
    expect(screen.getByRole("button", { name: "移除 管理员" })).toBeDisabled();
    vi.mocked(window.confirm).mockReturnValueOnce(false);
    await user.click(screen.getByRole("button", { name: "移除 测试成员" }));
    expect(memberApi.remove).not.toHaveBeenCalled();
    vi.mocked(memberApi.remove).mockResolvedValue({ ...member, removed_at: "2026-09-28T01:00:00", enabled: false });
    await user.click(screen.getByRole("button", { name: "移除 测试成员" }));
    await waitFor(() => expect(memberApi.remove).toHaveBeenCalledWith(settings, 2));
    expect(await screen.findByRole("status")).toHaveTextContent("历史记录已保留");
  });

  it("搜索、已移除筛选和恢复用户", async () => {
    const user = userEvent.setup();
    vi.mocked(memberApi.list).mockResolvedValue([admin, { ...member, removed_at: "2026-09-28T01:00:00", enabled: false }]);
    vi.mocked(memberApi.restore).mockResolvedValue({ ...member, enabled: false });
    render(<MemberManagement settings={settings} currentStaffId={1} onMembersChanged={vi.fn()} />);
    await screen.findByText("管理员", { selector: "strong" });
    expect(screen.queryByText("测试成员")).not.toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("人员状态"), "removed");
    const row = screen.getByRole("row", { name: /测试成员/ });
    await user.click(within(row).getByRole("button", { name: "恢复用户" }));
    await waitFor(() => expect(memberApi.restore).toHaveBeenCalledWith(settings, 2));
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining("恢复后保持停用"));
    await user.type(screen.getByLabelText("搜索用户"), "不存在");
    expect(screen.getByText("没有匹配的用户")).toBeInTheDocument();
  });

  it("绑定渠道后刷新绑定列表，但保留尚未保存的用户资料", async () => {
    const user = userEvent.setup();
    const channel = { id: 3, staff_id: member.id, platform: "qq" as const, recipient_id: "test-recipient", enabled: true };
    vi.mocked(taskApi.addStaffChannel).mockResolvedValue({ ...member, channels: [channel] });
    render(<MemberManagement settings={settings} currentStaffId={1} onMembersChanged={vi.fn()} />);
    await screen.findByText("测试成员");
    await user.click(screen.getByRole("button", { name: "管理 测试成员" }));
    await user.clear(screen.getByLabelText("姓名或称呼"));
    await user.type(screen.getByLabelText("姓名或称呼"), "尚未保存的姓名");
    await user.type(screen.getByLabelText("渠道接收 ID"), "test-recipient");
    vi.mocked(memberApi.list).mockResolvedValue([admin, { ...member, channels: [channel] }]);
    await user.click(screen.getByRole("button", { name: "绑定渠道" }));
    expect(await screen.findByRole("button", { name: "解绑 QQ test-recipient" })).toBeInTheDocument();
    expect(screen.getByLabelText("姓名或称呼")).toHaveValue("尚未保存的姓名");
    expect(screen.getByLabelText("渠道接收 ID")).toHaveValue("");
    expect(memberApi.update).not.toHaveBeenCalled();
  });

  it("保存完成并解除忙碌状态后恢复到添加按钮的键盘焦点", async () => {
    const user = userEvent.setup();
    vi.mocked(memberApi.update).mockResolvedValue(member);
    render(<MemberManagement settings={settings} currentStaffId={1} onMembersChanged={vi.fn()} />);
    await screen.findByText("测试成员");
    await user.click(screen.getByRole("button", { name: "管理 测试成员" }));
    await user.click(screen.getByRole("button", { name: "保存用户设置" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "添加用户" })).toHaveFocus());
    expect(screen.queryByLabelText("姓名或称呼")).not.toBeInTheDocument();
  });

  it("绑定失败时在渠道区域聚焦错误并保留接收 ID", async () => {
    const user = userEvent.setup();
    vi.mocked(taskApi.addStaffChannel).mockRejectedValue(new Error("接收 ID 已存在，请检查后重试"));
    render(<MemberManagement settings={settings} currentStaffId={1} onMembersChanged={vi.fn()} />);
    await screen.findByText("测试成员");
    await user.click(screen.getByRole("button", { name: "管理 测试成员" }));
    await user.type(screen.getByLabelText("渠道接收 ID"), "duplicate-recipient");
    await user.click(screen.getByRole("button", { name: "绑定渠道" }));
    const feedback = await within(screen.getByRole("region", { name: "消息接收渠道" })).findByRole("alert");
    expect(feedback).toHaveTextContent("接收 ID 已存在");
    expect(feedback).toHaveFocus();
    expect(screen.getByLabelText("渠道接收 ID")).toHaveValue("duplicate-recipient");
    expect(screen.getByLabelText("姓名或称呼")).toHaveValue("测试成员");
  });
});
