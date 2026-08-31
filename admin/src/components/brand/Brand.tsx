interface BrandProps {
  size?: "sm" | "md" | "lg";
  withText?: boolean;
  className?: string;
}

const SIZE_CLASSES = {
  sm: "h-8 w-8",
  md: "h-10 w-10",
  lg: "h-11 w-11",
};

/** 应用品牌 Logo，图标与 admin/public/favicon.svg 保持同一份视觉。 */
export function BrandLogo({ size = "md" }: { size?: "sm" | "md" | "lg" }) {
  return (
    <svg viewBox="0 0 120 120" className={SIZE_CLASSES[size]} aria-hidden>
      <defs>
        <linearGradient id="brand-bg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#2563eb" />
          <stop offset="100%" stopColor="#7c3aed" />
        </linearGradient>
      </defs>
      <rect x="4" y="4" width="112" height="112" rx="24" fill="url(#brand-bg)" />
      <circle cx="34" cy="34" r="9" fill="#ffffff" opacity="0.95" />
      <circle cx="86" cy="86" r="9" fill="#ffffff" opacity="0.95" />
      <circle cx="60" cy="52" r="12" fill="#ffffff" />
      <path d="M40 84 a20 16 0 0 1 40 0 z" fill="#ffffff" />
      <path
        d="M42 42 L52 50"
        stroke="#ffffff"
        strokeWidth="3"
        strokeLinecap="round"
        opacity="0.7"
      />
      <path
        d="M68 58 L78 78"
        stroke="#ffffff"
        strokeWidth="3"
        strokeLinecap="round"
        opacity="0.7"
      />
      <path
        d="M72 40 h14 a4 4 0 0 1 4 4 v8 a4 4 0 0 1 -4 4 h-10 l-4 4 v-4 h-0 a4 4 0 0 1 -4 -4 v-8 a4 4 0 0 1 4 -4 z"
        fill="#ffffff"
        opacity="0.55"
      />
      <circle cx="79" cy="48" r="1.6" fill="#2563eb" />
      <circle cx="84" cy="48" r="1.6" fill="#2563eb" />
      <circle cx="89" cy="48" r="1.6" fill="#2563eb" />
    </svg>
  );
}

export function Brand({ size = "md", withText = false, className = "" }: BrandProps) {
  return (
    <div className={`flex items-center gap-3 ${className}`}>
      <BrandLogo size={size} />
      {withText && (
        <div>
          <div className="text-base font-semibold text-slate-800">能工智人</div>
          <div className="mt-0.5 text-xs uppercase tracking-widest text-slate-400">
            operator console
          </div>
        </div>
      )}
    </div>
  );
}
