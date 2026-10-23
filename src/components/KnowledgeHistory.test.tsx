import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { defaultSettings } from "../data/demo";
import { knowledgeApi, type KnowledgeDetail } from "../lib/knowledgeApi";
import { KnowledgeHistory } from "./KnowledgeHistory";

vi.mock("../lib/knowledgeApi", async (original) => ({ ...await original<typeof import("../lib/knowledgeApi")>(), knowledgeApi: { revision: vi.fn(), revisions: vi.fn() } }));
const created_at = "2026-10-08T01:00:00";
const history = [2, 1].map((revision) => ({ revision, method: "manual", created_at }));
const item: KnowledgeDetail = { id: "version-test", title: "版本测试", source_url: "", kind: "note", publisher: "", tags: [], status: "ready", status_note: "", revision: 2, content_revision: 2, content: "新版", summary: "", method: "manual", deleted_at: null, created_at, updated_at: created_at, assets: [], history };
function open() {
  const details = screen.getByText("正文版本记录与对照").closest("details")!;
  details.open = true;
  fireEvent(details, new Event("toggle"));
}

describe("历史正文读取", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(knowledgeApi.revision).mockImplementation(async (_s, _id, revision) => ({ revision, method: "manual", created_at, content: revision === 1 ? "历史原文<script>不执行</script>" : "新版" }));
  });
  it("仅展开时读取，完整正文对照不改变当前版本，正文作为纯文本", async () => {
    const user = userEvent.setup();
    render(<KnowledgeHistory settings={defaultSettings} item={item} />);
    expect(knowledgeApi.revision).not.toHaveBeenCalled();
    open();
    expect(await screen.findByText("历史原文<script>不执行</script>")).toBeInTheDocument();
    expect(screen.getByText(/两侧正文不同/)).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("左侧正文版本"), "2");
    expect(await screen.findByText(/两侧正文相同/)).toBeInTheDocument();
    expect(item.content).toBe("新版");
    expect(document.querySelector("script")).toBeNull();
  });
  it("读取失败说明错误并允许重试", async () => {
    const user = userEvent.setup();
    vi.mocked(knowledgeApi.revision).mockRejectedValueOnce(new Error("读取失败"));
    render(<KnowledgeHistory settings={defaultSettings} item={item} />);
    open();
    expect(await screen.findByRole("alert")).toHaveTextContent("读取失败");
    await user.click(screen.getByRole("button", { name: "重试读取正文" }));
    expect(await screen.findByText("历史原文<script>不执行</script>")).toBeInTheDocument();
  });
  it("读取更早的正文元信息，可选择第21个之后的版本", async () => {
    const user = userEvent.setup();
    const versions = Array.from({ length: 20 }, (_, i) => ({ revision: 23 - i, method: "manual", created_at }));
    vi.mocked(knowledgeApi.revisions).mockResolvedValue({ items: [3, 2, 1].map((revision) => ({ revision, method: "manual", created_at })), total: 23, next_before: null });
    render(<KnowledgeHistory settings={defaultSettings} item={{ ...item, history: versions }} />);
    open();
    await user.click(await screen.findByRole("button", { name: "读取更早版本" }));
    await waitFor(() => expect(screen.getByLabelText("左侧正文版本").querySelectorAll("option")).toHaveLength(23));
    await user.selectOptions(screen.getByLabelText("左侧正文版本"), "1");
    expect(await screen.findByText("历史原文<script>不执行</script>")).toBeInTheDocument();
    expect(knowledgeApi.revisions).toHaveBeenCalledWith(defaultSettings, item.id, 4, expect.any(AbortSignal));
  });
});
