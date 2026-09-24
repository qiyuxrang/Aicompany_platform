# Django 实际静态入口浏览器验收

- 日期：2026-09-23
- 最终结论：通过
- 服务入口：`http://127.0.0.1:18723`
- 数据库：独立 SQLite `fix-review-browser-20260923.sqlite3`
- 前端来源：Django 默认 `PORTAL_FRONTEND_DIST`，解析为仓库内 `frontend/dist`
- 构建方式：仅使用 `pnpm --dir frontend build`
- 真实模型调用：关闭

## 最终通过项

1. Django 实际提供的新 bundle 能渲染真实“工作摘要”。
2. 第二个产品任务深链精确选中目标任务。
3. 跨任务 artifact 被拒绝，且不展示目标任务或其他对象信息。
4. 第二个 JD 深链精确选中目标任务。
5. 工程部 overview、estimate、quota 三个入口均显示“开发中 · 已隔离”，无录入、测算、定额推荐、保存或生成操作。
6. P2 与简历页面继续显示明确阻断状态。
7. 无 HR 角色的 assigned_manager 可从工作摘要进入精确转正事项；页面没有新建入口和 JD 导航。
8. 无效 case 深链显示明确错误，转正列表及其他员工信息不再出现。

最终浏览器脚本退出码为 0，以上 8 项全部通过，认证完成后的控制台错误数为 0。截图：`owner-workspaces.png`、`manager-probation.png`。

## 构建产物检查

- 包含：`开发中 · 已隔离`、`工作摘要`、`P2 未启动`、`D-01 / D-04 待批准`。
- 不包含：旧文案 `工程部准备工作台`。

## 失败保留与修正

- 首次夹具运行因验收脚本未将 `backend` 加入 Python 模块路径而失败；迁移和种子已成功，修正脚本后夹具生成成功。
- 第一次浏览器尝试在 SPA 登录后过早检查 URL；改为等待 `pathname` 离开 `/login`。
- 后续两次尝试分别暴露 P2 组合文案和“姓名 · 岗位”组合节点的选择器假设；改为页面实际语义，并加强无效 case 下转正列表必须消失的断言。
- 最终重新执行完整浏览器脚本通过；没有用源码静态检查替代浏览器验收。

验收脚本：`validation/fix_review_browser_acceptance.py`；夹具脚本：`validation/fix_review_browser_fixture.py`。
