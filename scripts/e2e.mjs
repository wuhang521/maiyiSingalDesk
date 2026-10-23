import { _electron as electron } from "@playwright/test";
import { createServer } from "node:http";
import { mkdir, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import process from "node:process";

const root = process.cwd();
const artifacts = path.join(root, "artifacts");
const profile = await mkdtemp(path.join(tmpdir(), "signaldesk-e2e-"));
const mock = createNtfyMock();
await mkdir(artifacts, { recursive: true });
await mock.start();

let electronApp;
try {
  electronApp = await electron.launch({
    args: [root],
    env: {
      ...process.env,
      SIGNALDESK_E2E_USER_DATA: profile,
      SIGNALDESK_RELEASE_API_URL: `${mock.url}/releases/latest`,
      SIGNALDESK_BACKEND_URL: mock.url,
      ELECTRON_DISABLE_SECURITY_WARNINGS: "true",
    },
  });
  const page = await electronApp.firstWindow();
  const errors = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("response", (response) => {
    if (response.status() >= 400) errors.push(`HTTP ${response.status()}: ${response.url()}`);
  });

  await page.getByRole("heading", { name: "登录工作台" }).waitFor();
  await page.waitForTimeout(500);
  await page.screenshot({ path: path.join(artifacts, "login.png"), fullPage: true });
  await page.getByLabel("账号").fill("boss");
  await page.getByLabel("密码", { exact: true }).fill("password-2026");
  await page.getByRole("button", { name: "继续" }).click();
  await page.waitForSelector("text=今日情报");
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.screenshot({ path: path.join(artifacts, "dashboard.png"), fullPage: true });

  await page.getByRole("button", { name: "智能频道" }).click();
  await page.waitForSelector("text=全部频道");
  await page.screenshot({ path: path.join(artifacts, "channels.png"), fullPage: true });

  await page.getByRole("button", { name: "新建频道" }).click();
  await page.getByLabel("关注需求").fill(
    "关注全球机器人公司的融资、产品发布和商业落地。排除消费玩具和无来源传闻。",
  );
  await page.getByRole("button", { name: /生成频道规则/ }).click();
  await page.waitForSelector("text=确认频道规则");
  await page.getByLabel("服务器").fill(mock.url);
  await page.getByLabel("Topic").fill("e2e-topic");
  await page.screenshot({ path: path.join(artifacts, "channel-dialog.png"), fullPage: true });
  await page.getByRole("button", { name: /保存频道/ }).click();
  await page.waitForSelector("text=频道已创建");

  await waitFor(() => mock.streamCount() > 0, 5000, "实时订阅未建立");
  const channelRow = page.locator(".channel-table-row").filter({ hasText: "机器人" }).first();
  await channelRow.getByRole("button", { name: "发送测试" }).click();
  await page.waitForSelector("text=测试消息已发送");
  await waitFor(() => mock.publishedCount() === 1, 3000, "测试消息未到达 ntfy mock");

  await page.getByRole("button", { name: "全部信息" }).click();
  await page.waitForSelector("text=频道连接成功。这是一条来自迈医协同桌面端的测试消息。");
  await page.getByRole("button", { name: "同步" }).click();
  await page.waitForSelector("text=同步完成");

  await page.locator(".feed-row").filter({ hasText: "频道连接成功" }).first().click();
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "删除本机消息" }).click();
  await page.getByRole("button", { name: /^回收站/ }).click();
  await page.waitForSelector("text=还有 7 天自动清除");
  await page.screenshot({ path: path.join(artifacts, "article-trash.png"), fullPage: true });
  await page.getByRole("button", { name: "恢复文章" }).click();

  await page.getByRole("button", { name: "设置" }).click();
  await page.waitForSelector("text=账号 boss");

  const widgetOpened = electronApp.waitForEvent("window");
  await page.getByRole("button", { name: "桌面挂件", exact: true }).click();
  const widgetPage = await widgetOpened;
  widgetPage.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  widgetPage.on("pageerror", (error) => errors.push(error.message));
  widgetPage.on("response", (response) => {
    if (response.status() >= 400) {
      errors.push(`Widget HTTP ${response.status()}: ${response.url()}`);
    }
  });
  await widgetPage.getByText("准备运营素材").waitFor();
  await widgetPage.getByRole("button", { name: "刷新" }).click();
  const nativeWidget = await electronApp.browserWindow(widgetPage);
  const resizable = await nativeWidget.evaluate((window) => window.isResizable());
  if (!resizable) throw new Error("桌面挂件未启用窗口缩放");
  await nativeWidget.evaluate((window) => window.setSize(520, 520));
  await widgetPage.getByRole("textbox", { name: "快速添加任务" }).fill("E2E 透明挂件任务");
  await widgetPage.getByRole("textbox", { name: "快速添加任务" }).press("Enter");
  await widgetPage.getByText("E2E 透明挂件任务").waitFor();
  await widgetPage.getByRole("button", { name: "收起挂件" }).click();
  await widgetPage.getByRole("button", { name: "展开挂件" }).click();
  await widgetPage.waitForTimeout(250);
  await widgetPage.screenshot({ path: path.join(artifacts, "task-widget.png") });
  await widgetPage.getByRole("button", { name: "关闭桌面挂件" }).click();

  await page.getByRole("button", { name: "任务中心", exact: true }).click();
  await page.waitForSelector("text=整理客户反馈");
  await page.getByRole("button", { name: "刷新任务" }).click();
  await page.getByRole("button", { name: "按人员", exact: true }).click();
  await page.locator(".task-group h3").filter({ hasText: "杰哥" }).waitFor();
  await page.screenshot({ path: path.join(artifacts, "tasks-by-person.png"), fullPage: true });
  await page.getByRole("button", { name: "按项目", exact: true }).click();
  await page.locator(".task-group h3").filter({ hasText: "增长项目" }).waitFor();
  await page.screenshot({ path: path.join(artifacts, "tasks-by-project.png"), fullPage: true });

  await page.route("**/api/v1/content/**", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify(route.request().url().endsWith("/schema")
      ? { account_fields: [], episode_fields: [], steps: [], current_user: { id: 3, name: "敏总" } }
      : []),
  }));
  await page.getByRole("button", { name: "内容生成", exact: true }).click();
  await page.getByRole("heading", { name: "配置第一个创作账号" }).waitFor();
  await page.locator(".toast").first().waitFor({ state: "detached", timeout: 10000 });
  await page.screenshot({ path: path.join(artifacts, "content-generation.png"), fullPage: true });
  await page.getByRole("tab", { name: "视频制作" }).click();
  await page.getByRole("textbox", { name: "视频主题或原始内容" }).fill("秋季血压管理指南");
  await page.getByRole("button", { name: "生成当前镜头", exact: true }).waitFor();

  await page.getByRole("button", { name: "设置" }).click();
  await page.getByRole("button", { name: "检查更新" }).click();
  await page.waitForSelector("text=发现新版本 v9.9.9，下载后覆盖安装即可。");

  if (errors.length) throw new Error(`Renderer errors:\n${errors.join("\n")}`);
} finally {
  if (electronApp) await electronApp.close();
  await mock.stop();
  await rm(profile, { recursive: true, force: true });
}

function createNtfyMock() {
  const streams = new Set();
  const messages = [];
  const articles = [];
  const tasks = [
    createTask({
      id: 1,
      task_no: "T001",
      title: "整理客户反馈",
      content: "汇总本周重点客户意见",
      assignee_id: 1,
      assignee_name: "杰哥",
      project_name: "增长项目",
      priority: 3,
    }),
    createTask({
      id: 2,
      task_no: "T002",
      title: "确认下周排期",
      assignee_id: 1,
      assignee_name: "杰哥",
      project_name: "增长项目",
    }),
    createTask({
      id: 3,
      task_no: "T003",
      title: "准备运营素材",
      assignee_id: 2,
      assignee_name: "敏总",
      priority: 3,
    }),
    ...Array.from({ length: 8 }, (_, index) => createTask({
      id: 4 + index,
      task_no: `T00${4 + index}`,
      title: `透明挂件滚动任务 ${index + 1}`,
      assignee_id: 2,
      assignee_name: "敏总",
      priority: 3,
    })),
  ];
  let server;
  let baseUrl = "";

  return {
    get url() {
      return baseUrl;
    },
    streamCount: () => streams.size,
    publishedCount: () => messages.length,
    async start() {
      server = createServer(async (request, response) => {
        const url = new URL(request.url || "/", `http://${request.headers.host}`);
        if (request.method === "GET" && url.pathname === "/releases/latest") {
          response.writeHead(200, { "Content-Type": "application/json" });
          response.end(
            JSON.stringify({
              tag_name: "v9.9.9",
              html_url: `${baseUrl}/releases/v9.9.9`,
              assets: [
                {
                  name: "SignalDesk-Setup-9.9.9.exe",
                  browser_download_url: `${baseUrl}/downloads/SignalDesk-Setup-9.9.9.exe`,
                },
              ],
            }),
          );
          return;
        }

        if (request.method === "GET" && url.pathname === "/e2e-topic/json") {
          response.writeHead(200, {
            "Content-Type": "application/x-ndjson; charset=utf-8",
            "Cache-Control": "no-cache",
          });
          if (url.searchParams.get("poll") === "1") {
            response.end(messages.map((message) => JSON.stringify(message)).join("\n"));
            return;
          }
          response.write(`${JSON.stringify({ event: "open", topic: "e2e-topic" })}\n`);
          streams.add(response);
          request.on("close", () => streams.delete(response));
          return;
        }

        if (request.method === "POST" && url.pathname === "/api/v1/auth/login") {
          response.writeHead(200, { "Content-Type": "application/json" });
          response.end(JSON.stringify({
            access_token: "e2e-personal-token",
            token_type: "bearer",
            expires_at: "2026-11-16T08:00:00",
            user: {
              id: 2,
              username: "boss",
              display_name: "敏总",
              staff_id: 2,
              role: "admin",
            },
          }));
          return;
        }

        if (request.method === "GET" && url.pathname === "/api/v1/auth/me") {
          response.writeHead(200, { "Content-Type": "application/json" });
          response.end(JSON.stringify({
            id: 2,
            username: "boss",
            display_name: "敏总",
            staff_id: 2,
            role: "admin",
          }));
          return;
        }

        if (request.method === "GET" && url.pathname === "/api/v1/tasks") {
          const assigneeId = Number(url.searchParams.get("assignee_id") || 0);
          const visibleItems = assigneeId
            ? tasks.filter((item) => item.assignee_id === assigneeId)
            : tasks;
          response.writeHead(200, { "Content-Type": "application/json" });
          response.end(JSON.stringify({ items: visibleItems, total: visibleItems.length }));
          return;
        }

        if (request.method === "POST" && url.pathname === "/api/v1/tasks") {
          const payload = JSON.parse(await readBody(request));
          const created = createTask({
            id: tasks.length + 1,
            task_no: `T${String(tasks.length + 1).padStart(3, "0")}`,
            title: payload.title,
            content: payload.content || "",
            assignee_id: payload.assignee_id,
            assignee_name: payload.assignee_id === 2 ? "敏总" : "杰哥",
            creator_name: payload.creator_name,
            priority: payload.priority,
            due_at: payload.due_at,
            remind_enabled: payload.remind_enabled,
          });
          tasks.push(created);
          response.writeHead(201, { "Content-Type": "application/json" });
          response.end(JSON.stringify(created));
          return;
        }

        if (request.method === "GET" && url.pathname === "/api/v1/staff") {
          response.writeHead(200, { "Content-Type": "application/json" });
          response.end(JSON.stringify([
            createStaff(1, "杰哥"),
            createStaff(2, "敏总"),
          ]));
          return;
        }

        if (request.method === "GET" && url.pathname === "/api/v1/articles") {
          response.writeHead(200, { "Content-Type": "application/json" });
          response.end(JSON.stringify({ items: articles, total: articles.length }));
          return;
        }

        if (request.method === "POST" && url.pathname === "/api/v1/articles/sync") {
          response.writeHead(200, { "Content-Type": "application/json" });
          response.end(JSON.stringify({ accepted: 0 }));
          return;
        }

        if (request.method === "POST" && url.pathname === "/api/v1/articles/batch") {
          const payload = JSON.parse(await readBody(request));
          for (const item of payload.items || []) {
            const index = articles.findIndex((article) => article.id === item.id);
            const stored = {
              ...item,
              read: false,
              saved: false,
              feedback: null,
              deletedAt: null,
              purged: false,
              stateUpdatedAt: 0,
            };
            if (index >= 0) articles[index] = stored;
            else articles.push(stored);
          }
          response.writeHead(200, { "Content-Type": "application/json" });
          response.end(JSON.stringify({ accepted: payload.items?.length || 0 }));
          return;
        }

        if (request.method === "PATCH" && /^\/api\/v1\/articles\/.+\/state$/.test(url.pathname)) {
          response.writeHead(200, { "Content-Type": "application/json" });
          response.end(JSON.stringify({}));
          return;
        }

        if (url.pathname === "/api/v1/channels") {
          response.writeHead(200, { "Content-Type": "application/json" });
          response.end(request.method === "GET" ? "[]" : JSON.stringify({ id: "e2e-topic" }));
          return;
        }

        if (request.method === "POST" && url.pathname === "/e2e-topic") {
          const body = await readBody(request);
          const message = {
            id: `e2e-${messages.length + 1}`,
            time: Math.floor(Date.now() / 1000),
            event: "message",
            topic: "e2e-topic",
            title: decodeHeaderValue(request.headers.title || "迈医协同测试消息"),
            message: body,
            priority: Number(request.headers.priority || 3),
            tags: String(request.headers.tags || "").split(",").filter(Boolean),
          };
          messages.push(message);
          const line = `${JSON.stringify(message)}\n`;
          streams.forEach((stream) => stream.write(line));
          response.writeHead(200, { "Content-Type": "application/json" });
          response.end(JSON.stringify(message));
          return;
        }

        response.writeHead(404, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ error: "not found" }));
      });
      await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
      const address = server.address();
      baseUrl = `http://127.0.0.1:${address.port}`;
    },
    async stop() {
      streams.forEach((stream) => stream.end());
      streams.clear();
      if (server) await new Promise((resolve) => server.close(resolve));
    },
  };
}

function createTask(update) {
  return {
    id: 0,
    task_no: "T000",
    title: "测试任务",
    content: "",
    assignee_id: 1,
    assignee_name: "测试人员",
    creator_name: "E2E",
    status: "todo",
    priority: 1,
    due_at: null,
    remind_enabled: true,
    remind_interval_minutes: 120,
    next_remind_at: null,
    last_reminded_at: null,
    reminder_count: 0,
    completed_at: null,
    source_platform: "client",
    deleted_at: null,
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
    ...update,
  };
}

function createStaff(id, name) {
  return {
    id,
    name,
    enabled: true,
    channels: [],
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
  };
}

async function readBody(request) {
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  return Buffer.concat(chunks).toString("utf8");
}

function decodeHeaderValue(value) {
  const text = String(value);
  const match = text.match(/^=\?UTF-8\?B\?(.+)\?=$/i);
  return match ? Buffer.from(match[1], "base64").toString("utf8") : text;
}

async function waitFor(predicate, timeout, message) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeout) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error(message);
}
