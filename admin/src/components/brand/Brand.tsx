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
          <stop offset="0%" stopColor="#059669" />
          <stop offset="100%" stopColor="#0891b2" />
        </linearGradient>
      </defs>
      <rect x="4" y="4" width="112" height="112" rx="26" fill="url(#brand-bg)" />
      {/* 外围网关环（human-in-the-loop），断口在底部 */}
      <path
        d="M60 22 a38 38 0 1 0 0.01 0"
        fill="none"
        stroke="#ffffff"
        strokeWidth="5"
        strokeDasharray="168 70"
        strokeDashoffset="-84"
      />
      {/* 环上的两个端点：入口与出口 */}
      <circle cx="60" cy="22" r="5" fill="#ffffff" />
      <circle cx="60" cy="98" r="5" fill="#ffffff" opacity="0.9" />
      {/* 中心人形 */}
      <circle cx="60" cy="52" r="11" fill="#ffffff" />
      <path d="M44 84 a16 22 0 0 1 32 0 z" fill="#ffffff" />
      {/* 顶部入口到人的引导线 */}
      <path
        d="M60 27 L60 40"
        stroke="#ffffff"
        strokeWidth="4"
        strokeLinecap="round"
        opacity="0.7"
      />
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
