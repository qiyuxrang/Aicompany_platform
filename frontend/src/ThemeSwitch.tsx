import { useEffect, useRef, useState } from "react";
import Icon from "./Icon";
import "./theme-switch.css";

type Theme = "light" | "dark";

function readTheme(): Theme {
  try {
    return localStorage.getItem("theme") === "dark" ? "dark" : "light";
  } catch {
    return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
  }
}

export default function ThemeSwitch() {
  const [theme, setTheme] = useState<Theme>(readTheme);
  const unsavedPreference = useRef(false);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", theme === "dark" ? "#101927" : "#f4f7fb");
  }, [theme]);

  useEffect(() => {
    const sync = () => { if (!unsavedPreference.current) setTheme(readTheme()); };
    const changed = (event: StorageEvent) => {
      if (event.key === "theme" || event.key === null) setTheme(event.newValue === "dark" ? "dark" : "light");
    };
    window.addEventListener("storage", changed);
    window.addEventListener("pageshow", sync);
    window.addEventListener("focus", sync);
    return () => {
      window.removeEventListener("storage", changed);
      window.removeEventListener("pageshow", sync);
      window.removeEventListener("focus", sync);
    };
  }, []);

  const choose = (next: Theme) => {
    setTheme(next);
    try {
      localStorage.setItem("theme", next);
      unsavedPreference.current = false;
    } catch {
      unsavedPreference.current = true;
    }
  };

  const label = theme === "dark" ? "切换至日间模式" : "切换至夜间模式";
  return <button className="theme-switch" type="button" aria-label={label} title={label} onClick={() => choose(theme === "dark" ? "light" : "dark")}>
    <Icon name={theme === "dark" ? "sun" : "moon"} />
  </button>;
}
