# 浅色科技商务界面优化

日期：2026-09-21。仅优化现有界面，不新增业务能力、不改框架、账号、权限或数据结构。

## 设计与实现

按 ui-ux-pro-max 本地企业仪表盘设计建议，采用浅灰蓝底色、白色工作卡片、商务蓝按钮和少量青色辅助标识。保留中文系统字体，不加载外部字体或新依赖；没有照搬技能输出中的英文字体组合。减少发光、深色铺底和厚重阴影，保留细线、克制的渐变、明确的标题层级与键盘焦点。

- 覆盖登录、密码、员工门户、运维页面、四类业务工作台、表格、状态提示和详情抽屉。
- Django Admin 首页、列表、表单使用同一浅色配色；默认/自动/浅色均为浅色，用户显式选择深色时仍保留深色能力。之前已选择深色的浏览器可在右上角切换为浅色。
- 修改现有主题变量和深色硬编码，不追加第二套覆盖主题。修正产品并列卡片顶部不齐问题。
- 保留现有中文文案、导航返回、禁用按钮及未接入提示，未改变业务处理或权限判断。

核心颜色：背景 `#f4f7fb`，卡片 `#ffffff`，标题 `#192d47`，次要文字 `#52657c`，主色 `#245caa`，辅助青色 `#147d89`。

## 实际验证

证据目录：`docs/evidence/bridge-20260921-light-ui/`。

| 项目 | 结果 |
| --- | --- |
| 前端回归 | 177/177通过，见 frontend-tests.txt |
| 后台管理回归 | 18/18通过，见 admin-tests.txt；本轮未重复运行后端全量150项 |
| TypeScript / Vite / Django check | 均通过，构建在独立目录完成 |
| 颜色对比度 | 14组检查全部通过，见 contrast.json；按钮6.59:1，次要文字在浅色背景最低5.26:1，输入边框对白底3.18:1 |
| 独立样式审查 | 发现旧焦点规则优先级覆盖新规则；将 :where 调整为 :is 后独立复核层叠成立 |
| 内嵌浏览器 | 已检查登录、运维总览、人员表格/抽屉、四类工作台、后台首页及新增账号表单（未提交），保存截图 |
| 响应式 | 产品表单375、768、1024、1440像素下无整页横向溢出，见 browser-checks.json；不代表所有页面和真机全量验收 |
| 后台主题 | 实际切换自动→深色→浅色，返回运维入口正常 |
| 发布检查 | 18210新页面引用的两项哈希资源均200；固定账号安全指纹与既有记录一致 |
| 清理 | 新隔离测试服务关闭，7个门户和5个旧端测试账号停用、密码不可用、会话清理、秘密撤销；库保留 |

界面实测使用独立数据库中的合成账号/操作记录，不是正式经营数据。浏览器最后一轮截图发生连接超时，Tab键焦点行为未完整确认；不将静态CSS复核声称为键盘全流程通过。对比度脚本只验证指定语义色对，不是完整无障碍认证。保留减少动画偏好样式，未实测系统大字体或移动真机。

## 发布与回退

当前访问 `http://127.0.0.1:18210/`，刷新即可。账号密码保持不变；未重启8100、8018或18210。原 `frontend/dist` 哈希未变；原经营项目未修改。

构建源为 `.runtime/business-light-dist`，日常发布目录仍是 `.runtime/ops-frontend-dist`。新资源先复制、index最后替换，保留此前哈希资源。后台通过collectstatic更新两份CSS，其他164个静态文件未变化；后台静态目录由门户实例共享，因此使用该目录的其他门户Admin也会采用更新的样式。这不是原经营系统的样式改造。

界面回退：

```powershell
Copy-Item -LiteralPath .runtime/business-light-backup/index.html -Destination .runtime/ops-frontend-dist/index.html -Force
Copy-Item -LiteralPath .runtime/light-ui-admin-backup/admin-tech.css -Destination staticfiles/portal/admin-tech.css -Force
Copy-Item -LiteralPath .runtime/light-ui-admin-backup/admin-home.css -Destination staticfiles/portal/admin-home.css -Force
```

后台源样式仍是新版；再次collectstatic会重新发布新版。如需长期回退，仅恢复本轮两份后台源CSS，不使用整仓reset，也不恢复数据库或重置账号。

实际源修改：`frontend/src/tech-theme.css`、`frontend/src/centers/centers.css`、`backend/portal/static/portal/admin-tech.css`、`backend/portal/static/portal/admin-home.css`。新增静态对比度检查为 `validation/check_business_theme.py`。

原整体阶段仍为部分完成：本次只是视觉优化，不代表智能生成、工程测算、招聘筛选、转正审批或浏览器单点登录已实现。未提交或推送Git，保留既有未提交成果。
