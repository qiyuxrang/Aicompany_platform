import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import WorkspaceBoundary from "./WorkspaceBoundary";
import "./styles.css";
import "./tech-theme.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <WorkspaceBoundary><App /></WorkspaceBoundary>
  </StrictMode>,
);
