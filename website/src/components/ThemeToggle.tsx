import { useEffect, useState } from "react";

type Theme = "dark" | "light";

function readTheme(): Theme {
  if (typeof document === "undefined") return "dark";
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

export default function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>("dark");

  useEffect(() => {
    setTheme(readTheme());
  }, []);

  const next = theme === "dark" ? "light" : "dark";

  return (
    <button
      type="button"
      className="theme-toggle"
      aria-pressed={theme === "light"}
      aria-label={`Switch to ${next} mode`}
      onClick={() => {
        document.documentElement.dataset.theme = next;
        window.localStorage.setItem("sr-theme", next);
        setTheme(next);
      }}
    >
      {next === "light" ? "Light" : "Dark"}
    </button>
  );
}
