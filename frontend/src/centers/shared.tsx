import { MouseEvent, ReactNode } from "react";

export interface WorkspaceProps { section: string }

export function CenterLink({ href, children, className, current = false }: {
  href: string; children: ReactNode; className?: string; current?: boolean;
}) {
  const target = window.location.pathname.startsWith("/preview/") && href.startsWith("/centers/")
    ? href.replace(/^\/centers\//, "/preview/") : href;
  const follow = (event: MouseEvent<HTMLAnchorElement>) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    window.history.pushState({}, "", target);
    window.dispatchEvent(new PopStateEvent("popstate"));
  };
  return <a href={target} onClick={follow} className={className} aria-current={current ? "page" : undefined}>{children}</a>;
}

export function SectionHeader({ title, description, eyebrow }: { title: string; description: string; eyebrow?: string }) {
  return <div className="center-section-heading"><div>{eyebrow && <p className="eyebrow">{eyebrow}</p>}<h2>{title}</h2><p>{description}</p></div></div>;
}

export function Field({ id, label, hint, children }: { id: string; label: string; hint?: string; children: ReactNode }) {
  return <div className="center-field"><label htmlFor={id}>{label}</label>{children}{hint && <small id={`${id}-hint`}>{hint}</small>}</div>;
}

export function PendingAction({ children, reason }: { children: ReactNode; reason: string }) {
  return <div className="center-pending-action"><button type="button" className="button primary" disabled>{children}</button><p>{reason}</p></div>;
}

export function EmptyPanel({ title, children }: { title: string; children: ReactNode }) {
  return <div className="center-empty"><span className="center-empty-mark" aria-hidden="true">—</span><h3>{title}</h3><p>{children}</p></div>;
}

export function DraftNotice() {
  return <p className="center-draft-note">当前为前端准备稿：仅保存在当前页面内存中，离开工作台或刷新即清空；未上传、未提交、未进入业务流程。</p>;
}
