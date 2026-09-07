import { useState } from "react";

export default function CiteCopy({ bibtex }: { bibtex: string }) {
  const [copied, setCopied] = useState(false);

  return (
    <button
      type="button"
      className="cite-copy"
      onClick={async () => {
        await navigator.clipboard.writeText(bibtex);
        setCopied(true);
      }}
    >
      {copied ? "Copied" : "Copy BibTeX"}
    </button>
  );
}
