import { useState, type InputHTMLAttributes } from "react";
import { Icon } from "../../icons";

type PasswordInputProps = Omit<InputHTMLAttributes<HTMLInputElement>, "type">;

/** 密码输入框：默认隐藏，可由用户临时切换明文显示。 */
export function PasswordInput({ className = "", ...props }: PasswordInputProps) {
  const [visible, setVisible] = useState(false);

  return (
    <div className="relative">
      <input
        {...props}
        type={visible ? "text" : "password"}
        className={`field-input pr-10 ${className}`.trim()}
      />
      <button
        type="button"
        onClick={() => setVisible((current) => !current)}
        className="absolute inset-y-0 right-0 grid w-10 place-items-center text-slate-400 transition hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30"
        aria-label={visible ? "隐藏密码" : "显示密码"}
        aria-pressed={visible}
        title={visible ? "隐藏密码" : "显示密码"}
      >
        <Icon name={visible ? "eyeOff" : "eye"} className="h-4 w-4" />
      </button>
    </div>
  );
}
