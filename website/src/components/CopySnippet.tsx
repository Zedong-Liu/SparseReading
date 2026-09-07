import { useState } from "react";

export default function CopySnippet({
  label,
  code,
}: {
  label: string;
  code: string;
}) {
  const [copied, setCopied] = useState(false);

  return (
    <div className="copy-snippet">
      <div className="copy-snippet-bar">
        <b>{label}</b>
        <button
          type="button"
          className="cite-copy"
          onClick={async () => {
            await navigator.clipboard.writeText(code);
            setCopied(true);
          }}
        >
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre className="mono">{code}</pre>
    </div>
  );
}
