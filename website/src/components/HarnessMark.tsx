const MARKS = {
  nanobot: {
    label: "NanoBot",
    color: "#3dd68c",
  },
  opencode: {
    label: "OpenCode",
    color: "#9ecbff",
  },
  openclaw: {
    label: "OpenClaw",
    color: "#ff9f6b",
  },
  claude: {
    label: "Claude Code",
    color: "#e8a07c",
  },
} as const;

export type HarnessId = keyof typeof MARKS;

export function HarnessMark({
  id,
  size = 28,
}: {
  id: HarnessId;
  size?: number;
}) {
  const mark = MARKS[id];
  return (
    <svg
      className="harness-mark"
      width={size}
      height={size}
      viewBox="0 0 32 32"
      role="img"
      aria-label={mark.label}
    >
      <title>{mark.label}</title>
      {id === "nanobot" && (
        <>
          <rect x="3" y="7" width="26" height="20" rx="7" fill={mark.color} />
          <circle cx="12" cy="16" r="2.2" fill="#0f1724" />
          <circle cx="20" cy="16" r="2.2" fill="#0f1724" />
          <path d="M12 21h8" stroke="#0f1724" strokeWidth="1.8" strokeLinecap="round" />
          <path d="M11 7V4.5M21 7V4.5" stroke={mark.color} strokeWidth="2.2" strokeLinecap="round" />
          <circle cx="11" cy="3.6" r="1.5" fill={mark.color} />
          <circle cx="21" cy="3.6" r="1.5" fill={mark.color} />
        </>
      )}
      {id === "opencode" && (
        <>
          <rect x="3" y="3" width="26" height="26" rx="7" fill={mark.color} />
          <path
            d="M12 10.5 7.8 16 12 21.5M20 10.5 24.2 16 20 21.5"
            fill="none"
            stroke="#0f1724"
            strokeWidth="2.2"
            strokeLinecap="square"
          />
        </>
      )}
      {id === "openclaw" && (
        <>
          <rect x="3" y="3" width="26" height="26" rx="7" fill={mark.color} />
          <path
            d="M10 22c2.2-5.4 4.8-8.2 8.8-9.6 1.8 2.6 1.4 5.6-.4 7.8-2.4.6-5.2.4-8.4-1.8Z"
            fill="#0f1724"
          />
          <path
            d="M20.6 8.4c2.4 1.2 3.6 3.4 3.2 5.8-1.8.2-3.6-.6-4.8-2.2.2-1.6.6-2.8 1.6-3.6Z"
            fill="#0f1724"
          />
          <circle cx="13.2" cy="13.4" r="1.1" fill={mark.color} />
        </>
      )}
      {id === "claude" && (
        <>
          <rect x="3" y="3" width="26" height="26" rx="7" fill={mark.color} />
          <path
            d="M16 7.4 17.7 14.2 24.6 16 17.7 17.8 16 24.6 14.3 17.8 7.4 16 14.3 14.2Z"
            fill="#0f1724"
          />
        </>
      )}
    </svg>
  );
}
