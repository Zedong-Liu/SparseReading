import { useEffect, useState } from "react";

function fmtK(n: number): string {
  return `${(n / 1000).toFixed(1)}k`;
}

export default function SavedTicker({
  naiveTokens,
  srTokens,
}: {
  naiveTokens: number;
  srTokens: number;
}) {
  const target = Math.max(0, naiveTokens - srTokens);
  const [value, setValue] = useState(0);

  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    if (media.matches) {
      setValue(target);
      return;
    }
    setValue(0);
    const started = performance.now();
    const duration = 3800;
    let frame = 0;
    const tick = (now: number) => {
      const t = Math.min(1, (now - started) / duration);
      const eased = 1 - (1 - t) ** 2.2;
      setValue(Math.round(target * eased));
      if (t < 1) frame = window.requestAnimationFrame(tick);
    };
    frame = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(frame);
  }, [target]);

  return (
    <a className="saved-ticker" href="#demo" data-claim-id="case-flagship-loogle5q-flash-reduction">
      <strong className="saved-ticker-row">
        <b>+{fmtK(value)}</b>
        <span>tokens saved</span>
      </strong>
      <small>cumulative vs Full Read · flagship cell</small>
    </a>
  );
}
