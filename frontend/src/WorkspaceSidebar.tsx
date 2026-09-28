import { ReactNode, useId, useState } from "react";

/** Shared chrome only: callers supply already-authorized navigation entries. */
export default function WorkspaceSidebar({ title, subtitle, mark, children, workspaces, workspaceLabel = "已授权工作台", footer }: {
  title: string; subtitle: string; mark: ReactNode; children: ReactNode;
  workspaces: ReactNode; workspaceLabel?: string; footer?: ReactNode;
}) {
  const [expanded, setExpanded] = useState(false);
  const bodyId = useId();
  return <aside className="workspace-sidebar" aria-label={`${title}工作区`}>
    <div className="workspace-sidebar-brand"><span aria-hidden="true">{mark}</span><div><strong>{title}</strong><small>{subtitle}</small></div>
      <button className="workspace-menu-toggle" type="button" aria-expanded={expanded} aria-controls={bodyId}
        onClick={() => setExpanded(value => !value)}>{expanded ? "收起菜单" : "展开菜单"}</button>
    </div>
    <div className="workspace-sidebar-body" id={bodyId} data-expanded={expanded} onClick={event => {
      if ((event.target as HTMLElement).closest("a")) setExpanded(false);
    }}>
      {children}
      <nav className="workspace-switcher" aria-label={workspaceLabel}><p>{workspaceLabel}</p>{workspaces}</nav>
      {footer && <div className="workspace-sidebar-footer">{footer}</div>}
    </div>
  </aside>;
}
