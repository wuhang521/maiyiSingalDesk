import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { defaultSettings as settings } from "../data/demo";
import { knowledgeApi, type KnowledgeTeam, type KnowledgeSharedItem } from "../lib/knowledgeApi";
import { KnowledgeTeams } from "./KnowledgeTeams";

vi.mock("../lib/knowledgeApi", async original => { const actual = await original<typeof import("../lib/knowledgeApi")>(); return { ...actual, knowledgeApi: Object.fromEntries(Object.keys(actual.knowledgeApi).map(name => [name, vi.fn()])) }; });
const api = vi.mocked(knowledgeApi); const at = "2026-10-28T00:00:00";
const teams: KnowledgeTeam[] = [{ id: "team-one", name: "合成团队甲", revision: 4, can_manage: true, is_owner: true }, { id: "team-two", name: "合成团队乙", revision: 2, can_manage: false, is_owner: false }];
const shared = (n: number): KnowledgeSharedItem => ({ id: `shared-${n}`, team_id: "team-one", title: `合成共享 ${n}`, publisher: "合成来源", tags: [], source_url: "https://weixin.qq.com/sph/synthetic-shared", revision: 4, shared_at: at, summary: `合成摘要 ${n}`, content: `共享正文 ${n}`, can_revoke: false });
beforeEach(() => { vi.resetAllMocks(); api.teams.mockResolvedValue({ items: teams }); api.teamMembers.mockResolvedValue({ items: [] }); api.teamItems.mockResolvedValue({ items: [shared(1)], total: 1 }); api.teamDetail.mockImplementation(async (_s, _team, id) => shared(Number(id.split("-")[1]))); });

it("[KBUI-KnowledgeTeams-1,KBUI-KnowledgeTeams-13,KBUI-KnowledgeTeams-14,KBUI-KnowledgeTeams-15,KBUI-KnowledgeTeams-16] team pages and citation select correct first/middle/last rows and team switch resets page", async () => {
  const user = userEvent.setup(); const all = Array.from({ length: 101 }, (_, n) => shared(n + 1));
  api.teamItems.mockImplementation(async (_s, team, offset) => ({ items: team === "team-one" ? all.slice(offset, offset + 50) : [shared(102)], total: team === "team-one" ? 101 : 1 }));
  api.teamSearch.mockResolvedValue({ items: [{ item_id: "shared-25", revision: 4, locator: "合成共享段落", text: "合成搜索依据" }] });
  render(<KnowledgeTeams settings={settings} disabled={false} onBusy={vi.fn()} />);
  const detail = screen.getByRole("article", { name: "团队共享正文" });
  await within(detail).findByRole("heading", { name: "合成共享 1" });
  await user.click(screen.getByText("合成共享 25", { selector: "strong" }).closest("button")!); expect(await within(detail).findByRole("heading", { name: "合成共享 25" })).toBeVisible();
  await user.click(screen.getByText("合成共享 50", { selector: "strong" }).closest("button")!); expect(await within(detail).findByRole("heading", { name: "合成共享 50" })).toBeVisible();
  expect(screen.getByRole("button", { name: "上一页" })).toBeDisabled(); await user.click(screen.getByRole("button", { name: "下一页" })); await screen.findByText("合成共享 51", { selector: "strong" });
  await user.click(screen.getByRole("button", { name: "下一页" })); await screen.findByText("合成共享 101", { selector: "strong" }); expect(screen.getByRole("button", { name: "下一页" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "上一页" })); await screen.findByText("合成共享 51", { selector: "strong" });
  await user.type(screen.getByLabelText("搜索团队已共享原文"), "合成资料"); await user.click(screen.getByRole("button", { name: "查找团队原文" })); await user.click(await screen.findByRole("button", { name: "合成共享段落 · 版本 4" }));
  expect(await within(detail).findByRole("heading", { name: "合成共享 25" })).toBeVisible();
  await user.selectOptions(screen.getByLabelText("选择团队"), "team-two"); await waitFor(() => expect(api.teamItems).toHaveBeenLastCalledWith(settings, "team-two", 0, expect.any(AbortSignal)));
  expect(await within(detail).findByRole("heading", { name: "合成共享 102" })).toBeVisible(); expect(screen.queryByText(/管理团队成员/)).toBeNull();
});

it("[KBUI-KnowledgeTeams-2,KBUI-KnowledgeTeams-5,KBUI-KnowledgeTeams-7] team creation and member role selection send only explicit synthetic inputs", async () => {
  const user = userEvent.setup(); api.setTeamMember.mockResolvedValue({ revision: 5 }); api.createTeam.mockResolvedValue({ id: "new-team", name: "合成新团队", revision: 1, can_manage: true, is_owner: true });
  render(<KnowledgeTeams settings={settings} disabled={false} onBusy={vi.fn()} />); await screen.findByText(/管理团队成员/);
  const summary = screen.getByText(/管理团队成员/); await user.click(summary); expect(summary.closest("details")).toHaveAttribute("open");
  await user.type(screen.getByLabelText("个人登录账号名"), "synthetic-colleague"); await user.selectOptions(screen.getByLabelText("团队角色"), "manager");
  await user.click(screen.getByRole("button", { name: "添加 / 修改成员" })); await waitFor(() => expect(api.setTeamMember).toHaveBeenCalledExactlyOnceWith(settings, teams[0], "synthetic-colleague", "manager"));
  await user.click(screen.getByText("创建一个团队")); expect(screen.getByText("创建一个团队").closest("details")).toHaveAttribute("open");
  await user.type(screen.getByLabelText("团队名称"), "合成新团队"); await user.click(screen.getByRole("button", { name: "创建团队" }));
  await waitFor(() => expect(api.createTeam).toHaveBeenCalledExactlyOnceWith(settings, "合成新团队")); expect(await screen.findByText("团队已创建，没有自动共享任何个人资料。")).toBeVisible();
});

it("[KBUI-KnowledgeTeams-17] shared source link fires with exact safe-target contract and no real navigation", async () => {
  const user = userEvent.setup(); render(<KnowledgeTeams settings={settings} disabled={false} onBusy={vi.fn()} />);
  const link = await screen.findByRole("link", { name: "查看共享来源" }); const clicked = vi.fn((event: Event) => event.preventDefault()); link.addEventListener("click", clicked); await user.click(link);
  expect(clicked).toHaveBeenCalledTimes(1); expect(link).toHaveAttribute("href", shared(1).source_url); expect(link).toHaveAttribute("target", "_blank"); expect(link).toHaveAttribute("rel", "noreferrer");
});
