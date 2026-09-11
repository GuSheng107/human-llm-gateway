/** 根据调用方声明的 JSON Schema 生成完整可编辑参数骨架。 */

type JsonSchema = Record<string, unknown>;

function cloneJson<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function isSchema(value: unknown): value is JsonSchema {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function collectProperties(schema: JsonSchema): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  if (Array.isArray(schema.allOf)) {
    for (const candidate of schema.allOf) {
      if (isSchema(candidate)) Object.assign(result, collectProperties(candidate));
    }
  }
  if (isSchema(schema.properties)) Object.assign(result, schema.properties);
  return result;
}

function initialValue(schema: JsonSchema): unknown {
  if (Object.prototype.hasOwnProperty.call(schema, "default")) {
    return cloneJson(schema.default);
  }
  if (Object.prototype.hasOwnProperty.call(schema, "const")) {
    return cloneJson(schema.const);
  }
  const enumValues = schema.enum;
  if (Array.isArray(enumValues) && enumValues.length > 0) {
    return cloneJson(enumValues[0]);
  }
  const alternatives = schema.oneOf ?? schema.anyOf;
  if (Array.isArray(alternatives)) {
    const first = alternatives.find(
      (candidate): candidate is JsonSchema => isSchema(candidate),
    );
    if (first) return initialValue(first);
  }
  const types = Array.isArray(schema.type) ? schema.type : [schema.type];
  if (types.includes("object") || Object.keys(collectProperties(schema)).length > 0) {
    return buildInitialArguments(schema);
  }
  if (types.includes("array")) return [];
  if (types.includes("boolean")) return false;
  if (types.includes("integer") || types.includes("number")) return 0;
  if (types.includes("null") && types.length === 1) return null;
  return "";
}

/**
 * 写入 schema 声明的全部属性，并递归展开对象字段；不构造城市名、路径、
 * 命令等业务示例值。用户选中工具后仍可直接编辑这个 JSON 对象。
 */
export function buildInitialArguments(schema: JsonSchema): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (const [name, rawSchema] of Object.entries(collectProperties(schema))) {
    result[name] = isSchema(rawSchema) ? initialValue(rawSchema) : "";
  }
  return result;
}
