import type { Capability } from "./types/auth";

export type AppRouteId =
  | "console"
  | "tasks"
  | "connections"
  | "apiKeys"
  | "models"
  | "llmConfigs"
  | "invitations"
  | "users"
  | "account";

export interface NavigationItem {
  id: AppRouteId;
  path: string;
  label: string;
  description: string;
  icon: string;
  capability?: Capability;
}

export interface NavigationGroup {
  label: string;
  items: NavigationItem[];
}

export const NAVIGATION: NavigationGroup[] = [
  {
    label: "工作台",
    items: [
      {
        id: "console",
        path: "/console",
        label: "控制台",
        description: "运行概览",
        icon: "dashboard",
      },
      {
        id: "tasks",
        path: "/tasks",
        label: "任务工作台",
        description: "人工任务与回复提交",
        icon: "reply",
      },
    ],
  },
  {
    label: "接入",
    items: [
      {
        id: "connections",
        path: "/connections",
        label: "连接 IM",
        description: "IM 连接与投递入口",
        icon: "link",
        capability: "connection.manage",
      },
      {
        id: "apiKeys",
        path: "/api-keys",
        label: "API 管理",
        description: "API Key、策略与模型筛选",
        icon: "key",
        capability: "api_key.manage",
      },
      {
        id: "models",
        path: "/models",
        label: "模型目录",
        description: "Fake Model 与模型分组",
        icon: "cpu",
        capability: "model.manage",
      },
      {
        id: "llmConfigs",
        path: "/llm-configs",
        label: "LLM 管理",
        description: "真实 LLM 配置与连通性测试",
        icon: "gateway",
        capability: "model.manage",
      },
    ],
  },
  {
    label: "系统设置",
    items: [
      {
        id: "invitations",
        path: "/settings/invitations",
        label: "邀请码管理",
        description: "邀请码签发与撤销",
        icon: "key",
        capability: "invitation.manage",
      },
      {
        id: "users",
        path: "/settings/users",
        label: "用户管理",
        description: "用户状态与密码管理",
        icon: "users",
        capability: "user.manage",
      },
      {
        id: "account",
        path: "/settings/account",
        label: "账号设置",
        description: "个人资料与安全",
        icon: "settings",
        capability: "account.profile.update",
      },
    ],
  },
];

export const APP_ROUTES = NAVIGATION.flatMap((group) => group.items);

export function canAccess(
  capabilities: Capability[],
  item: NavigationItem,
): boolean {
  return !item.capability || capabilities.includes(item.capability);
}

export function matchNavigation(pathname: string): NavigationItem | undefined {
  return APP_ROUTES.find(
    (item) =>
      item.path === pathname || pathname.startsWith(`${item.path}/`),
  );
}
