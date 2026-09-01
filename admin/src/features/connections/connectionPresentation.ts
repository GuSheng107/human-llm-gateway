import type { ImConnection, PlatformSpec } from "../../types/gateway";

export const PLATFORM_ORDER = [
  "wecom_ilink",
  "wecom_aibot",
  "http_poll",
  "webhook",
  "websocket",
] as const;

export interface PlatformVisual {
  mark: string;
  eyebrow: string;
  iconClass: string;
  headerClass: string;
  railClass: string;
}

const PLATFORM_VISUALS: Record<string, PlatformVisual> = {
  wecom_ilink: {
    mark: "微",
    eyebrow: "WECHAT ILINK",
    iconClass: "border-emerald-200 bg-emerald-50 text-emerald-700",
    headerClass: "from-emerald-50/80 via-white to-white",
    railClass: "bg-emerald-400",
  },
  wecom_aibot: {
    mark: "企",
    eyebrow: "WECOM AIBOT",
    iconClass: "border-blue-200 bg-blue-50 text-blue-700",
    headerClass: "from-blue-50/80 via-white to-white",
    railClass: "bg-blue-400",
  },
  http_poll: {
    mark: "HTTP",
    eyebrow: "HTTP POLLING",
    iconClass: "border-cyan-200 bg-cyan-50 text-cyan-700",
    headerClass: "from-cyan-50/80 via-white to-white",
    railClass: "bg-cyan-400",
  },
  webhook: {
    mark: "WH",
    eyebrow: "WEBHOOK",
    iconClass: "border-amber-200 bg-amber-50 text-amber-700",
    headerClass: "from-amber-50/80 via-white to-white",
    railClass: "bg-amber-400",
  },
  websocket: {
    mark: "WS",
    eyebrow: "WEBSOCKET",
    iconClass: "border-violet-200 bg-violet-50 text-violet-700",
    headerClass: "from-violet-50/80 via-white to-white",
    railClass: "bg-violet-400",
  },
};

const FALLBACK_VISUAL: PlatformVisual = {
  mark: "IM",
  eyebrow: "CUSTOM PLATFORM",
  iconClass: "border-slate-200 bg-slate-50 text-slate-600",
  headerClass: "from-slate-50 via-white to-white",
  railClass: "bg-slate-300",
};

export function platformVisual(code: string): PlatformVisual {
  return PLATFORM_VISUALS[code] ?? FALLBACK_VISUAL;
}

export function orderPlatforms(
  platforms: PlatformSpec[],
  connections: ImConnection[],
): PlatformSpec[] {
  const byCode = new Map(platforms.map((platform) => [platform.code, platform]));
  for (const connection of connections) {
    if (byCode.has(connection.platform)) continue;
    byCode.set(connection.platform, {
      code: connection.platform,
      label: connection.platform_label,
      description: "扩展平台连接。",
      kind: "server",
      supports_delivery: false,
      supports_login: false,
      requires_binding: false,
      binding_command: null,
      config_schema: [],
    });
  }

  const rank = new Map<string, number>(PLATFORM_ORDER.map((code, index) => [code, index]));
  return [...byCode.values()].sort((left, right) => {
    const leftRank = rank.get(left.code) ?? PLATFORM_ORDER.length;
    const rightRank = rank.get(right.code) ?? PLATFORM_ORDER.length;
    return leftRank - rightRank || left.label.localeCompare(right.label, "zh-CN");
  });
}

export interface SetupEndpoint {
  label: string;
  value: string;
}

export interface SetupCommand {
  label: string;
  value: string;
}

export interface PlatformSetupGuide {
  title: string;
  description: string;
  commandLabel: string | null;
  commandHelp: string | null;
  endpoints: SetupEndpoint[];
  commands: SetupCommand[];
}

function normalizedOrigin(origin: string): string {
  return origin.replace(/\/$/, "");
}

function websocketOrigin(origin: string): string {
  return normalizedOrigin(origin).replace(/^http:/, "ws:").replace(/^https:/, "wss:");
}

function tokenValue(tokens: Record<string, string>, field: string): string {
  return tokens[field] || "<TOKEN>";
}

export function platformSetupGuide(
  connection: ImConnection,
  origin: string,
  tokens: Record<string, string> = {},
): PlatformSetupGuide {
  const httpOrigin = normalizedOrigin(origin);
  switch (connection.platform) {
    case "wecom_ilink":
      return {
        title: "微信 iLink 配置与接入",
        description: "配置与扫码登录在同一窗口完成。",
        commandLabel: null,
        commandHelp: null,
        endpoints: [],
        commands: [],
      };
    case "wecom_aibot":
      return {
        title: "企业微信配置与接入",
        description: "保存机器人配置后，在企业微信个人会话完成绑定。",
        commandLabel: "在企业微信中发送",
        commandHelp: "命令固定。绑定成功后才能启用连接。",
        endpoints: [],
        commands: [],
      };
    case "webhook": {
      const inboundUrl = `${httpOrigin}/connectors/webhook/${connection.id}/inbound`;
      const token = tokenValue(tokens, "inbound_token");
      return {
        title: "Webhook 配置与接入",
        description: "向入站地址提交消息。首次绑定时填写 binding_code。",
        commandLabel: "binding_code",
        commandHelp: "请求头需携带 X-Webhook-Token。",
        endpoints: [{ label: "入站地址", value: inboundUrl }],
        commands: [
          {
            label: "提交绑定消息",
            value: `curl "${inboundUrl}" -H "Content-Type: application/json" -H "X-Webhook-Token: ${token}" -d '{"external_message_id":"message-001","sender":"user-001","text":"hello","binding_code":"connect webhook"}'`,
          },
        ],
      };
    }
    case "websocket": {
      const token = tokenValue(tokens, "connection_token");
      const sessionUrl = `${websocketOrigin(origin)}/connectors/ws/${connection.id}?token=${token}`;
      return {
        title: "WebSocket 配置与接入",
        description: "连接 WebSocket 地址，首次绑定时填写 binding_code。",
        commandLabel: "binding_code",
        commandHelp: "连接 Token 通过查询参数 token 传入。",
        endpoints: [{ label: "会话地址", value: sessionUrl }],
        commands: [
          {
            label: "WebSocket 握手检查",
            value: `curl --http1.1 --include --no-buffer -H "Connection: Upgrade" -H "Upgrade: websocket" -H "Sec-WebSocket-Version: 13" -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" "${sessionUrl}"`,
          },
        ],
      };
    }
    case "http_poll": {
      const token = tokenValue(tokens, "pull_token");
      const taskUrl = `${httpOrigin}/connectors/http/${connection.id}/tasks`;
      const replyUrl = `${httpOrigin}/connectors/http/${connection.id}/replies`;
      const ackUrl = `${httpOrigin}/connectors/http/${connection.id}/ack`;
      return {
        title: "HTTP 轮询配置与接入",
        description: "使用连接 Token 鉴权，无需扫码或绑定聊天账号。",
        commandLabel: null,
        commandHelp: null,
        endpoints: [
          { label: "拉取任务", value: taskUrl },
          { label: "提交回复", value: replyUrl },
          { label: "确认任务", value: ackUrl },
        ],
        commands: [
          {
            label: "拉取任务",
            value: `curl "${taskUrl}?cursor=0&limit=10" -H "X-Pull-Token: ${token}"`,
          },
          {
            label: "提交回复",
            value: `curl "${replyUrl}" -H "Content-Type: application/json" -H "X-Pull-Token: ${token}" -d '{"external_message_id":"reply-001","task_id":"<TASK_ID>","text":"回复内容"}'`,
          },
          {
            label: "确认任务",
            value: `curl "${ackUrl}" -H "Content-Type: application/json" -H "X-Pull-Token: ${token}" -d '{"task_id":"<TASK_ID>"}'`,
          },
        ],
      };
    }
    default:
      return {
        title: `${connection.platform_label} 配置与接入`,
        description: "完成配置后再启用连接。",
        commandLabel: "绑定命令",
        commandHelp: "发送完整命令后完成绑定。",
        endpoints: [],
        commands: [],
      };
  }
}
