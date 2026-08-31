import type { AssistantPageContext } from "../../types/gateway";
import { currentEditBridge } from "./bridge";

/**
 * 页面上下文注册表（后端 redaction.py feature 白名单的前端对应）。
 *
 * 每个 feature 声明：从 route 提取的白名单 resource 字段。
 * 发送消息时按当前路由取快照；切换路由自动替换（不累积）。
 * secret 类字段（api_key 明文/密码/token）结构上不存在于前端状态，
 * 注册表只采集白名单键——与后端 schema 拒收形成同一 allowlist 语义。
 */

interface FeatureSpec {
  /** 路由匹配（前缀），如 /tasks */
  match: (pathname: string) => boolean;
  /** 从路由/查询串提取白名单字段 */
  resource: (pathname: string, search: string) => Record<string, string>;
}

const params = (search: string): Record<string, string> => {
  const out: Record<string, string> = {};
  new URLSearchParams(search).forEach((value, key) => {
    out[key] = value;
  });
  return out;
};

const FEATURES: Record<string, FeatureSpec> = {
  console: {
    match: (p) => p === "/console" || p === "/",
    resource: () => ({}),
  },
  task_list: {
    match: (p) => p === "/tasks",
    resource: (_p, search) => {
      const q = params(search);
      const out: Record<string, string> = {};
      if (q["state"]) out["state_filter"] = q["state"];
      if (q["search"]) out["search"] = q["search"].slice(0, 64);
      return out;
    },
  },
  task_detail: {
    // 详情由 drawer 打开（route 仍为 /tasks）；独立回复页有真实路由 /tasks/:id/reply。
    match: (p) => p === "/tasks" || /^\/tasks\/[^/]+\/reply$/.test(p),
    resource: (p): Record<string, string> => {
      const out: Record<string, string> = {};
      const taskId = p.match(/^\/tasks\/([^/]+)\/reply$/)?.[1];
      if (taskId) out.task_id = taskId;
      return out;
    },
  },
  api_keys: {
    match: (p) => p === "/api-keys",
    resource: () => ({}),
  },
  llm_configs: {
    match: (p) => p === "/llm-configs",
    resource: () => ({}),
  },
  connections: {
    match: (p) => p === "/connections",
    resource: () => ({}),
  },
  models: {
    match: (p) => p === "/models",
    resource: () => ({}),
  },
  invitations: {
    match: (p) => p === "/settings/invitations",
    resource: () => ({}),
  },
  users: {
    match: (p) => p === "/settings/users",
    resource: () => ({}),
  },
  account: {
    match: (p) => p === "/settings/account",
    resource: () => ({}),
  },
};

/** feature -> context_version（结构变更时 bump，后端按版本解释）。 */
export const CONTEXT_VERSIONS: Record<string, number> = {
  console: 1,
  task_list: 1,
  task_detail: 1,
  api_keys: 1,
  llm_configs: 1,
  connections: 1,
  models: 1,
  invitations: 1,
  users: 1,
  account: 1,
};

/** 当前路由对应的 feature；未注册路由返回 null（不发送上下文）。 */
export function featureForRoute(pathname: string): string | null {
  for (const [feature, spec] of Object.entries(FEATURES)) {
    if (spec.match(pathname)) {
      return feature;
    }
  }
  return null;
}

/**
 * 构造当前页面上下文快照：路由 + feature + 白名单 resource +
 * 未提交编辑内容（编辑器桥上报，仅任务回复编辑器提供）。
 */
export function buildContextSnapshot(
  pathname: string,
  search: string,
): AssistantPageContext | null {
  const feature = featureForRoute(pathname);
  if (!feature) {
    return null;
  }
  const spec = FEATURES[feature];
  const bridge = currentEditBridge();
  const unsavedEdit = bridge?.getDraft ? bridge.getDraft() : null;
  // 任务详情资源字段由编辑器桥补充（drawer 无路由参数）。
  const bridgeResource = bridge?.getResource ? bridge.getResource() : {};
  return {
    route: pathname,
    feature,
    resource: { ...spec.resource(pathname, search), ...bridgeResource },
    unsaved_edit: unsavedEdit,
    context_version: CONTEXT_VERSIONS[feature] ?? 1,
  };
}
