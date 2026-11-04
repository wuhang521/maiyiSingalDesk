import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { KnowledgeSemanticBatch } from "./KnowledgeSemanticBatch";
import { knowledgeApi, type KnowledgeDetail } from "../lib/knowledgeApi";
import { defaultSettings } from "../data/demo";

vi.mock("../lib/knowledgeApi", () => ({ knowledgeApi: { detail: vi.fn(), semanticInfo: vi.fn(), buildSemantic: vi.fn() } }));
afterEach(() => vi.useRealTimers());
const items = [{ id: "a", title: "合成资料甲", revision: 1, status: "ready", deleted_at: null }, { id: "b", title: "合成资料乙", revision: 1, status: "ready", deleted_at: null }] as KnowledgeDetail[];

it("批量索引为后台的十五秒提交间隔留出时间，并可立即停止后续资料", async () => {
  const user = userEvent.setup();
  vi.mocked(knowledgeApi.detail).mockImplementation(async (_settings, id) => items.find(item => item.id === id)!);
  vi.mocked(knowledgeApi.semanticInfo).mockResolvedValue({ state: "absent", source_revision: 1, chunk_count: 0 });
  vi.mocked(knowledgeApi.buildSemantic).mockResolvedValue({ state: "ready", source_revision: 1, chunk_count: 1 });
  render(<KnowledgeSemanticBatch settings={defaultSettings} items={items} disabled={false} onBusy={vi.fn()} />);
  await user.click(screen.getByText("本页资料批量建索引（最多 20 份）"));
  await user.click(screen.getByRole("checkbox", { name: /合成资料甲/ }));
  await user.click(screen.getByRole("checkbox", { name: /合成资料乙/ }));
  await user.click(screen.getByRole("checkbox", { name: "同意发送以上 2 份当前正文并承担可能费用" }));
  await user.click(screen.getByRole("button", { name: "建立所选资料索引" }));
  await waitFor(() => expect(knowledgeApi.buildSemantic).toHaveBeenCalledTimes(1));
  expect(screen.getByRole("button", { name: /停止后续建索引/ })).toBeEnabled();
  await user.click(screen.getByRole("button", { name: /停止后续建索引/ }));
  await waitFor(() => expect(screen.getByRole("button", { name: "建立所选资料索引" })).toBeDisabled());
  expect(knowledgeApi.buildSemantic).toHaveBeenCalledTimes(1);
});
