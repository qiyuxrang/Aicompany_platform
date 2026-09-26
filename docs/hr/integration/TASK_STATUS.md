# HR 集成执行状态

分支：`feature/hr-recruitment-integration`

## 已完成的代码范围

- H1：新增 RecruitmentRequest、0012 迁移、真实 HR Session/CSRF/对象权限 API、输入版本冲突、事务审计。
- H1：旧 JD GET 与版本记录保留，POST/PATCH/生成/编辑/确认均在服务端返回 legacy_read_only；转正 API 本批不改变。
- H2 后端：JDVersion、0013 迁移、平台 hr_jd_draft 路由调用、人工编辑/确认、版本失效、confirmed 列表、招聘平台派生接口。
- H2：模型调用放事务外，返回后复验需求版本及用户授权；渠道版本绑定 confirmed 通用 JD，不自动发布。

## 验证范围

- 隔离环境 `.runtime/hr-integration-tests.env`；Django test 内存数据库，未迁移日常演示库。
- 新招聘/JD/既有人事 API 共 26 项通过；makemigrations --check --dry-run 无漂移。
- 模型调用使用测试替身；不代表真实 HR 模型输出质量验收。
- 已保留 RED→GREEN 过程：缺少入口、旧 JD 写保护、渠道 list 类型输入导致异常，均有对应测试。

## 尚未完成

- H2 前端及真实平台模型联调。
- H3 简历私有存储、TXT/DOCX/PDF 提取；不强制脱敏。
- H4 有界并行筛选、缓存隔离、进度、恢复与历史结果。
- H5 参考图视觉实现。
- H6 360° 问卷及统计。
- H7 钉钉身份、消息/待办、H5 填写与真实联调。
- H8 AI 证据化汇总、XLSX、HR 最终判断。
- H9 全平台测试与独立审查。

## 外部条件

钉钉应用凭据、测试接收人、H5 HTTPS 地址及正式问卷题目/权重尚未提供；不伪造批准、不向真实人员发送测试。现有模型由平台路由选择，不固定厂商或模型名称。独立 HR 原数据不迁入，当前无新增运行时依赖。
