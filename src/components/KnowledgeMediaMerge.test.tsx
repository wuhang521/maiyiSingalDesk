import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { KnowledgeMediaMerge } from "./KnowledgeMediaMerge";
import { knowledgeApi, type KnowledgeDetail, type KnowledgeMediaPart } from "../lib/knowledgeApi";
import { defaultSettings } from "../data/demo";

vi.mock("../lib/knowledgeApi", () => ({ knowledgeApi: { mediaParts: vi.fn() } }));

it("识别同一正文版本结束后刷新合并片段，不要求用户重开资料", async () => {
  const user = userEvent.setup();
  const speech: KnowledgeMediaPart = { id: "speech", asset_id: "same-video", revision: 2, method: "qwen_asr_draft", content: "合成转写", created_at: "2026-10-28T01:00:00" };
  const frame: KnowledgeMediaPart = { ...speech, id: "frame", revision: 4, method: "video_ocr_draft", content: "合成画面文字" };
  const item = { id: "item", revision: 4, status: "extracting", updated_at: "2026-10-28T01:00:00" } as KnowledgeDetail;
  vi.mocked(knowledgeApi.mediaParts).mockResolvedValueOnce({ items: [speech] }).mockResolvedValue({ items: [speech, frame] });
  const props = { settings: defaultSettings, item, busy: false, onMerge: vi.fn(), onConfirm: vi.fn() };
  const view = render(<KnowledgeMediaMerge {...props} />);
  await user.click(await screen.findByText("合并语音与采样画面"));
  view.rerender(<KnowledgeMediaMerge {...props} item={{ ...item, status: "needs_review", updated_at: "2026-10-28T01:00:01" }} />);
  await waitFor(() => expect(knowledgeApi.mediaParts).toHaveBeenCalledTimes(2));
  const selectors = screen.getAllByRole("combobox");
  expect(selectors[0].querySelectorAll("option")).toHaveLength(2);
  await waitFor(() => expect(selectors[1].querySelectorAll("option")).toHaveLength(2));
  view.rerender(<KnowledgeMediaMerge {...props} item={{ ...item, status: "needs_review", method: "media_merge_draft", updated_at: "2026-10-28T01:00:02" }} />);
  expect(screen.getByText("合并语音与采样画面").closest("details")).toHaveAttribute("open");
  expect(screen.getByRole("button", { name: "已核对，确认入库" })).toBeVisible();
});
