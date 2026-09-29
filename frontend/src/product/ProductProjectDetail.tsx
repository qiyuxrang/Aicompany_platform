import { useState } from "react";
import { CenterLink } from "../centers/shared";
import { TaskEditor } from "./DocumentWorkspace";
import "./project-workbench.css";

function projectListUrl() {
  const value = new URLSearchParams(window.location.search).get("returnTo");
  if (!value?.startsWith("/centers/product/")) return "/centers/product/projects";
  const url = new URL(value, window.location.origin);
  return url.searchParams.has("task") ? "/centers/product/projects" : `${url.pathname}${url.search}`;
}

export default function ProductProjectDetail({ id }: { id: string }) {
  const [fatal, setFatal] = useState("");
  if (fatal) return <section className="pd-workspace pd-project-fatal"><div className="pd-feedback" role="alert"><h1>无法打开项目</h1><p>{fatal}</p></div><CenterLink href={projectListUrl()} className="button secondary">返回项目列表</CenterLink></section>;
  return <TaskEditor key={id} id={id} requestedArtifact="" deepLinked onFatal={setFatal} business/>;
}
