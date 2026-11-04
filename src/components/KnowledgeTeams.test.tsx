import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { KnowledgeTeams } from "./KnowledgeTeams";
import { knowledgeApi, type KnowledgeTeam } from "../lib/knowledgeApi";
import { defaultSettings } from "../data/demo";

vi.mock("../lib/knowledgeApi", () => ({ knowledgeApi: {
  teams: vi.fn(), teamMembers: vi.fn(), teamItems: vi.fn(), removeTeamMember: vi.fn(), closeTeam: vi.fn(),
} }));

it("移除成员后等待团队版本刷新，才能以新版本关闭团队", async () => {
  const user = userEvent.setup();
  const team: KnowledgeTeam = { id: "synthetic-team", name: "合成团队", revision: 1, is_owner: true, can_manage: true };
  let resolveRefresh!: (value: { items: KnowledgeTeam[] }) => void;
  const refresh = new Promise<{ items: KnowledgeTeam[] }>(resolve => { resolveRefresh = resolve; });
  vi.mocked(knowledgeApi.teams).mockResolvedValueOnce({ items: [team] }).mockReturnValueOnce(refresh).mockResolvedValue({ items: [] });
  vi.mocked(knowledgeApi.teamItems).mockResolvedValue({ items: [], total: 0 });
  const colleague = { user_id: 2, username: "synthetic-colleague", name: "合成同事", role: "member" as const, enabled: true, is_owner: false };
  vi.mocked(knowledgeApi.teamMembers).mockResolvedValueOnce({ items: [colleague] }).mockResolvedValue({ items: [] });
  vi.mocked(knowledgeApi.removeTeamMember).mockResolvedValue({ revision: 2 });
  vi.mocked(knowledgeApi.closeTeam).mockResolvedValue(undefined);
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  try {
    render(<KnowledgeTeams settings={defaultSettings} disabled={false} onBusy={vi.fn()} />);
    await user.click(await screen.findByText(/管理团队成员/));
    await user.click(await screen.findByRole("button", { name: "移除成员" }));
    await waitFor(() => expect(knowledgeApi.teams).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("button", { name: "关闭团队（保留个人资料）" })).toBeDisabled();
    await act(async () => { resolveRefresh({ items: [{ ...team, revision: 2 }] }); });
    await waitFor(() => expect(screen.getByRole("button", { name: "关闭团队（保留个人资料）" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "关闭团队（保留个人资料）" }));
    await waitFor(() => expect(knowledgeApi.closeTeam).toHaveBeenCalledWith(defaultSettings, { ...team, revision: 2 }));
    await waitFor(() => expect(screen.getByLabelText("选择团队")).toHaveValue(""));
  } finally { confirm.mockRestore(); }
});
