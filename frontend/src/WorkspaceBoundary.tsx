import { Component, Suspense, type ReactNode } from "react";

class WorkspaceErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() { return { failed: true }; }

  render() {
    if (this.state.failed) return <section className="state-card" role="alert">
      <h2>页面暂时无法显示</h2>
      <p>请重新加载页面后再试。若仍无法打开，请联系平台管理员。</p>
      <p>刷新前请先保存其他页面中尚未提交的内容。</p>
      <button className="button primary" type="button" onClick={() => window.location.reload()}>重新加载页面</button>
    </section>;
    return this.props.children;
  }
}

export default function WorkspaceBoundary({ children, resetKey = "" }: { children: ReactNode; resetKey?: string }) {
  return <WorkspaceErrorBoundary key={resetKey}>
    <Suspense fallback={<div className="center-empty" role="status" aria-live="polite" aria-busy="true">正在加载工作页面…</div>}>
      {children}
    </Suspense>
  </WorkspaceErrorBoundary>;
}
