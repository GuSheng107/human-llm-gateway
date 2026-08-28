import { Icon } from "../../icons";

export interface PasswordChecks {
  length: boolean;
  letter: boolean;
  digit: boolean;
  symbol: boolean;
}

export function checkPassword(password: string): PasswordChecks {
  return {
    length: password.length >= 10,
    letter: /[A-Za-z]/.test(password),
    digit: /[0-9]/.test(password),
    symbol: /[!"#$%&'()*+,\-./:;<=>?@[\]^_`{|}~]/.test(password),
  };
}

export function passwordValid(password: string): boolean {
  const checks = checkPassword(password);
  return checks.length && checks.letter && checks.digit && checks.symbol;
}

export function PasswordStrength({ password }: { password: string }) {
  if (!password) return null;
  const checks = checkPassword(password);
  const items: { ok: boolean; label: string }[] = [
    { ok: checks.length, label: "至少 10 位" },
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
