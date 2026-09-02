import type { Capability } from "./types/auth";

export type AppRouteId =
  | "console"
  | "tasks"
  | "replies"
  | "connections"
  | "apiKeys"
  | "models"
  | "llmConfigs"
  | "tools"
  | "logs"
  | "invitations"
  | "users"
  | "adminConnections"
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
    ],
  },
  {
    label: "任务",
    items: [
      {
        id: "tasks",
        path: "/tasks",
        label: "任务记录",
        description: "查看任务，处理待回复",
        icon: "reply",
      },
      {
        id: "replies",
        path: "/replies",
        label: "回复工作台",
        description: "处理待回复任务",
        icon: "gateway",
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
        description: "IM 接收与投递",
        icon: "link",
        capability: "connection.manage",
      },
      {
        id: "apiKeys",
        path: "/api-keys",
        label: "API 管理",
        description: "Key、策略与模型",
        icon: "key",
        capability: "api_key.manage",
      },
      {
        id: "models",
        path: "/models",
        label: "模型广场",
        description: "Fake Model 和分组",
        icon: "cpu",
        capability: "model.manage",
      },
      {
        id: "llmConfigs",
        path: "/llm-configs",
        label: "LLM 管理",
        description: "上游 LLM 配置",
        icon: "gateway",
        capability: "model.manage",
      },
      {
        id: "tools",
        path: "/tools",
        label: "工具沙箱",
        description: "工具白名单和隔离执行",
        icon: "code",
      },
    ],
  },
  {
    label: "系统设置",
    items: [
      {
        id: "logs",
        path: "/settings/logs",
        label: "日志审计",
        description: "审计与应用日志",
        icon: "list",
      },
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
        id: "adminConnections",
        path: "/settings/im-connections",
        label: "IM 连接监管",
        description: "停用或删除用户连接",
        icon: "link",
        capability: "connection.admin",
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
