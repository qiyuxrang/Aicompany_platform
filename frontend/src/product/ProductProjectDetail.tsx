import { useState } from "react";
import { CenterLink } from "../centers/shared";
import { TaskEditor } from "./DocumentWorkspace";

export default function ProductProjectDetail({ id }: { id: string }) {
  const [fatal, setFatal] = useState("");
  if (fatal) return <section className="pd-panel"><div className="pd-feedback" role="alert"><h2>无法打开项目</h2><p>{fatal}</p></div><CenterLink href="/centers/product/projects" className="button secondary">返回项目列表</CenterLink></section>;
  return <TaskEditor key={id} id={id} requestedArtifact="" deepLinked onFatal={setFatal} business/>;
}
