import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ContentGenerationView } from "../ContentGenerationView";
import { ContentPreview } from "./ContentFields";
import { contentApi, type ContentSchema } from "../../lib/contentApi";
import { defaultSettings } from "../../data/demo";

const schema: ContentSchema = {
  account_fields: [{ key: "account_name", label: "账号名称", kind: "text", required: true }],
  episode_fields: [{ key: "topic_brief", label: "本期主题", kind: "text", required: true }],
  steps: [], current_user: { id: 1, name: "内容审核员" },
};

beforeEach(() => {
  sessionStorage.clear();
  vi.spyOn(contentApi, "schema").mockResolvedValue(schema);
  vi.spyOn(contentApi, "members").mockResolvedValue([schema.current_user]);
  vi.spyOn(contentApi, "accounts").mockResolvedValue([]);
  vi.spyOn(contentApi, "episodes").mockResolvedValue([]);
});
afterEach(() => vi.restoreAllMocks());

describe("内容编辑与版本保护", () => {
  it("切换视频制作后保留新账号未保存输入", async () => {
    const user = userEvent.setup();
    render(<ContentGenerationView settings={defaultSettings} />);
    await user.type(await screen.findByLabelText("账号名称", { exact: false }), "我的创作账号");
    await user.click(screen.getByRole("tab", { name: "视频制作" }));
    await user.click(screen.getByRole("tab", { name: "内容创作" }));
    expect(screen.getByLabelText("账号名称", { exact: false })).toHaveValue("我的创作账号");
  });

  it("新账号离开前提示，并能在重新打开后恢复未保存配置", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const mounted = render(<ContentGenerationView settings={defaultSettings} />);
    await user.type(await screen.findByLabelText("账号名称", { exact: false }), "待确认的账号");
    await user.click(screen.getByRole("button", { name: "新建内容" }));
    expect(confirm).toHaveBeenCalled();
    expect(screen.getByLabelText("账号名称", { exact: false })).toHaveValue("待确认的账号");
    await waitFor(() => expect(sessionStorage.length).toBe(1));
    mounted.unmount();
    render(<ContentGenerationView settings={defaultSettings} />);
    expect(await screen.findByLabelText("账号名称", { exact: false })).toHaveValue("待确认的账号");
  });

  it("历史预览显示实际审核人及否值，不隐藏权限和确认差异", () => {
    render(<ContentPreview fields={[
      { key: "reviewer", label: "专业审核人", kind: "reviewer", required: true },
      { key: "required", label: "涉及专门领域", kind: "boolean", required: false },
    ]} value={{ reviewer: 1, required: false }} members={[schema.current_user]} />);
    expect(screen.getByText("内容审核员")).toBeInTheDocument();
    expect(screen.getByText("否")).toBeInTheDocument();
  });
});
