import { Icon } from "../../icons";

export interface PasswordChecks {
  length: boolean;
  letter: boolean;
  digit: boolean;
  symbol: boolean;
}

// 与后端 app/domain/values.py password_problems 保持一致的本地预检；
// 后端仍是权威判定，前端只做即时反馈。
const MAX_PASSWORD_CODEPOINTS = 128;
const BLOCKED_PASSWORDS = new Set([
  "password",
  "changeme",
  "change-me-now",
  "admin123",
  "123456789",
  "qwertyuiop",
  "iloveyou",
]);

// 码点数量与后端 len(unicodedata NFC) 对齐，避免 emoji 等字符计数不一致。
function codePointLength(value: string): number {
  return [...value.normalize("NFC")].length;
}

// 与后端 string.punctuation 完全一致（含反斜杠），用 code unit 逐字符比对更直观。
const SYMBOL_CHARS = new Set([..."!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"]);

function hasSymbol(value: string): boolean {
  for (const ch of value) if (SYMBOL_CHARS.has(ch)) return true;
  return false;
}

export function checkPassword(password: string): PasswordChecks {
  return {
    length:
      codePointLength(password) >= 10 && codePointLength(password) <= MAX_PASSWORD_CODEPOINTS,
    letter: /[A-Za-z]/.test(password),
    digit: /[0-9]/.test(password),
    symbol: hasSymbol(password),
  };
}

export function passwordValid(password: string, username = ""): boolean {
  const checks = checkPassword(password);
  if (!(checks.length && checks.letter && checks.digit && checks.symbol)) return false;
  const lowered = password.normalize("NFC").toLowerCase();
  if (BLOCKED_PASSWORDS.has(lowered)) return false;
  if (username && lowered === username.toLowerCase()) return false;
  return true;
}

export function PasswordStrength({ password }: { password: string }) {
  if (!password) return null;
  const checks = checkPassword(password);
  const items: { ok: boolean; label: string }[] = [
    { ok: checks.length, label: `10-${MAX_PASSWORD_CODEPOINTS} 位` },
    { ok: checks.letter, label: "英文字母" },
    { ok: checks.digit, label: "数字" },
    { ok: checks.symbol, label: "符号" },
  ];
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1">
      {items.map((item) => (
        <span
          key={item.label}
          className={`flex items-center gap-1 text-caption ${
            item.ok ? "text-emerald-600" : "text-slate-400"
          }`}
        >
          <Icon name={item.ok ? "check" : "close"} className="h-3 w-3" />
          {item.label}
        </span>
      ))}
    </div>
  );
}
