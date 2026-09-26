# H4 批次接线执行计划

范围：用户已确认只完成 H1—H5；转正/钉钉冻结。真实样例仅两份；不得把合成测试当真实模型通过。

## 顺序
1. RED：confirmed/current JD 才能创建批次；跨 HR 读写下载 404；未启动批次可上传；上传幂等和版本冲突；下载 hash 复验。
2. GREEN：ScreeningBatch 绑定 JD 及需求快照；ResumeArtifact 绑定 batch 和私有 file_id；数据库唯一约束；迁移只用于隔离库。
3. POST run 仅排队；逐份 lease/fence 原子领取，网络在事务外；调用前后验证 HR 和 JD 快照，有限重试不重复成功项。
4. 结果来源保持页码/hash；模型无有效证据降 UNKNOWN；程序评分，模型不得录用淘汰。导出抵御 CSV 公式注入。
5. React 页面消费实际状态，失败不显示为零；前端按参考稿，转正卡显示暂停而非虚假数字。

## 验证
先新增 test_hr_screening_api，再跑招聘/JD/文件回归。后续 Worker、匹配和前端各自增加测试。数据库/文件/公开日志不得写入原始简历副本。每批保存 checkpoint，不将局部完成写成 H5 完成。
