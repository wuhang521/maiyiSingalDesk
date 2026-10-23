import type { AppSettings, FeedItem, SmartChannel } from "../types";

const now = Date.now();
const todayStart = new Date();
todayStart.setHours(0, 0, 0, 0);

function recentToday(minutesAgo: number, order: number): number {
  return Math.max(now - minutesAgo * 60_000, todayStart.getTime() + order * 60_000);
}

export const defaultSettings: AppSettings = {
  defaultServer: import.meta.env.VITE_DEFAULT_NTFY_SERVER || "https://ntfy.sh",
  backendUrl:
    import.meta.env.VITE_DEFAULT_BACKEND_URL ||
    "https://113.46.159.100/signaldesk-task",
  backendAuthToken: "",
  operatorName: "",
  globalAuthToken: "",
  notifications: true,
  notificationSound: false,
  launchAtLogin: false,
  historyWindow: "24h",
  compactMode: false,
};

export const demoChannels: SmartChannel[] = [
  {
    id: "demo-health",
    name: "健康管理商业情报",
    description: "企业落地、支付模式、功能医学与长寿医学",
    prompt:
      "持续关注全球健康管理服务公司的产品、融资、合作、支付模式、功能医学和长寿医学，给管理层看。排除个人养生和普通疾病科普。",
    topic: "",
    server: "https://ntfy.sh",
    authToken: "",
    schedule: "09:00",
    maxItems: 20,
    includeTerms: ["健康管理服务", "企业合作", "保险支付", "功能医学", "长寿医学"],
    excludeTerms: ["个人养生", "食谱", "普通疾病科普"],
    color: "green",
    state: "draft",
    createdAt: now - 86400000 * 4,
    updatedAt: now - 86400000,
    isDemo: true,
  },
  {
    id: "demo-creative",
    name: "视觉工具与内容生产",
    description: "图片、视频、设计工具和可编辑工作流",
    prompt:
      "关注能帮助运营编辑图片、生成视频和制作视觉内容的新工具、功能更新和真实案例。",
    topic: "",
    server: "https://ntfy.sh",
    authToken: "",
    schedule: "09:00",
    maxItems: 15,
    includeTerms: ["图片编辑", "视频生成", "设计工具", "运营工作流"],
    excludeTerms: ["纯概念产品", "无实际演示"],
    color: "blue",
    state: "draft",
    createdAt: now - 86400000 * 3,
    updatedAt: now - 86400000,
    isDemo: true,
  },
  {
    id: "demo-ai",
    name: "AI 决策雷达",
    description: "模型、智能体、企业落地与基础设施",
    prompt: "筛选对管理层和运营都重要的 AI 产品、模型、智能体和企业落地消息。",
    topic: "",
    server: "https://ntfy.sh",
    authToken: "",
    schedule: "09:00",
    maxItems: 12,
    includeTerms: ["AI 模型", "智能体", "企业落地", "生产力工具"],
    excludeTerms: ["娱乐新闻", "无来源传闻"],
    color: "amber",
    state: "draft",
    createdAt: now - 86400000 * 2,
    updatedAt: now - 3600000 * 3,
    isDemo: true,
  },
];

export const demoFeed: FeedItem[] = [
  {
    id: "demo-message-1",
    channelId: "demo-health",
    title: "企业健康福利从报销转向持续健康管理",
    body:
      "### 一句话看懂\n一家东南亚保险科技公司把保险、健康追踪、理赔和远程慢病管理整合到同一套企业福利服务中。\n\n**主体：** OneHealth 企业员工健康计划\n\n**主要内容：** 服务覆盖预防、早筛、理赔和持续干预，企业可以在一个后台看到使用率和结果。\n\n**关键结果：** 企业采购的不再是单点保险，而是一条连续健康服务路径。",
    timestamp: recentToday(18, 4),
    source: "示例数据",
    clickUrl: "https://ntfy.sh",
    tags: ["briefcase", "health"],
    priority: 4,
    read: false,
    saved: false,
    feedback: null,
  },
  {
    id: "demo-message-2",
    channelId: "demo-ai",
    title: "企业智能体开始进入可审计工作流",
    body:
      "### 一句话看懂\n新一代企业智能体产品把工具调用、权限、审批和操作日志放进统一控制层。\n\n**为什么值得看：** 真正影响企业采购的不是模型回答得多聪明，而是能否被授权、被追踪、被中止。",
    timestamp: recentToday(72, 3),
    source: "示例数据",
    tags: ["robot", "office"],
    priority: 3,
    read: false,
    saved: true,
    feedback: "useful",
  },
  {
    id: "demo-message-3",
    channelId: "demo-creative",
    title: "可编辑视频生成成为运营工具的新分界线",
    body:
      "### 一句话看懂\n视频生成工具开始输出可分层编辑的镜头、字幕和素材，而不是只提供一段不可修改的成片。\n\n**运营关注：** 测试导出格式、品牌模板、批量生成和团队审批是否真实可用。",
    timestamp: recentToday(145, 2),
    source: "示例数据",
    tags: ["movie_camera", "art"],
    priority: 3,
    read: true,
    saved: false,
    feedback: null,
  },
  {
    id: "demo-message-4",
    channelId: "demo-health",
    title: "雇主 GLP-1 项目开始强调临床适当性与成本",
    body:
      "### 一句话看懂\n药品福利管理方联合肥胖医学和体重管理服务商，为不同规模雇主组合 GLP-1 覆盖方案。\n\n**关键结果：** 部分雇主披露最高约 70% 的成本节省，但需要继续核验适用条件。",
    timestamp: recentToday(240, 1),
    source: "示例数据",
    tags: ["pill", "moneybag"],
    priority: 4,
    read: true,
    saved: true,
    feedback: "useful",
  },
];
