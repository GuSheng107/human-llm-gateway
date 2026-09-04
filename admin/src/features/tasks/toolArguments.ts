/** 根据调用方声明的 JSON Schema 生成最小可编辑参数骨架。 */

type JsonSchema = Record<string, unknown>;

function cloneJson<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function initialValue(schema: JsonSchema): unknown {
  if (Object.prototype.hasOwnProperty.call(schema, "default")) {
    return cloneJson(schema.default);
  }
  const enumValues = schema.enum;
  if (Array.isArray(enumValues) && enumValues.length > 0) {
    return cloneJson(enumValues[0]);
  }
  const alternatives = schema.oneOf ?? schema.anyOf;
  if (Array.isArray(alternatives)) {
    const first = alternatives.find(
      (candidate): candidate is JsonSchema =>
        typeof candidate === "object" && candidate !== null && !Array.isArray(candidate),
    );
    if (first) return initialValue(first);
  }
  const type = schema.type;
  if (type === "object" || schema.properties) {
    return buildInitialArguments(schema);
  }
  if (type === "array") return [];
  if (type === "boolean") return false;
  if (type === "integer" || type === "number") return 0;
  if (type === "null") return null;
  return "";
}

/**
 * 只写入必填属性、显式默认值或枚举首项；不构造城市名、路径、命令等
 * 业务示例值。用户选中工具后仍可直接编辑这个 JSON 对象。
 */
export function buildInitialArguments(schema: JsonSchema): Record<string, unknown> {
  const properties = schema.properties;
  if (typeof properties !== "object" || properties === null || Array.isArray(properties)) {
    return {};
  }
  const required = new Set(
    Array.isArray(schema.required)
      ? schema.required.filter((item): item is string => typeof item === "string")
      : [],
  );
  const result: Record<string, unknown> = {};
  for (const [name, rawSchema] of Object.entries(properties)) {
    if (typeof rawSchema !== "object" || rawSchema === null || Array.isArray(rawSchema)) {
      if (required.has(name)) result[name] = "";
      continue;
    }
    const child = rawSchema as JsonSchema;
    const hasDefault = Object.prototype.hasOwnProperty.call(child, "default");
    const hasEnum = Array.isArray(child.enum) && child.enum.length > 0;
    if (required.has(name) || hasDefault || hasEnum) {
      result[name] = initialValue(child);
    }
  }
  return result;
}
