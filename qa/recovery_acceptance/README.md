# Portal 合成 PostgreSQL 与私有文件恢复验收

本工具补充 `docs/DEPLOYMENT.md` 的恢复门禁，**不读取或连接生产业务库**。使用 `qa.run_portable_postgres.PortablePostgres` 启动一个自有 UUID、仅回环地址的 PostgreSQL cluster；正常 `config.settings`、完整 Django migrations、12 位以上密码验证与默认 PBKDF2 哈希均不替换。仅在该 cluster 新建源库和不同的恢复 UUID 库。没有 `--clean`、`dropdb`、自动覆盖、现有服务重启或真实库切换。

```powershell
.runtime/cloud-readiness-venv/Scripts/python.exe -m unittest qa.recovery_acceptance.test_safety -v
.runtime/cloud-readiness-venv/Scripts/python.exe qa/recovery_acceptance/run.py --postgres-bin .runtime/cloud-readiness-postgres/binaries/pgsql/bin
```

Python 环境需安装当前项目锁定依赖；PG bin 需包含 `initdb`、`postgres`、`pg_ctl`、`pg_dump`、`pg_restore`。工具自动读取的仅为进程必要系统环境，并显式关闭外部 Agent/模型/RAG/招标请求；不加载 `.env`。合成密码只在内存/子进程环境传递，不出现在 argv 或证据正文。initdb 密码临时文件按公共 PortablePostgres 策略及时删除。Windows 环境不把 POSIX chmod 宣称为生产 ACL/加密验收。

## 实际门禁

1. 建立两个独立用户归属范围，现有 Portal 没有 SaaS `Tenant` 模型，因此不宣称数据库层多租户隔离。通过真实 CSRF 和登录接口使用完整业务 URLConf；正常 HR intake、人工编辑 JD 第 2 版、本人确认、创建批次、上传私有 TXT、删除简历。账户用 User ORM 并执行真实密码 validators/hasher，角色变动走正常 M2M 安全信号；测试历史分类和 400 天时间戳仅在合成行上设置。
2. 保留有效 HR 长期资料、已删除 tombstone、历史失效分类，以及第二用户撤销 HR 角色后的授权版本。不会调用模型或简历筛选 Worker，也不声称检验 HR 模型判断质量。
3. 停止合成 fixture 写入，在一个 repeatable-read 只读事务导出 PostgreSQL snapshot，并使用 `pg_dump --snapshot --format=custom --no-acl`。保留 DB object owners；同一自有 cluster 的 UUID role 用于恢复。全表行数及规范化内容 SHA256、列/约束/索引摘要、owner、sequence 的 last_value/is_called 在源和恢复库必须一致。列按名称比较逻辑属性，避免迁移删列留下的物理序号空洞在 dump 恢复时压紧导致误报；部分索引 predicate 用 PostgreSQL 自己的 `EXPLAIN VERBOSE` 常量折叠比较（无 ANALYZE、不执行查询），保留索引完整定义和类型/运算符语义，避免 `varchar[]→text[]` 在恢复时变成逐项 cast 的等价表达式误报。无法取得唯一表达式仍失败，绝不跳过结构门禁。备份前后的源库及私有文件 manifest 必须一致。
4. product、HR、tender 三个隔离私有目录进行字节 hash、大小和相对路径清单校验；本小样本仅 HR 有实际业务文件，其余目录为空。拒绝 traversal、绝对路径、Windows ADS、链接/junction、未知目录、损坏/额外/缺失文件和已存在的恢复目标。目录复制进全新目的路径，原源目录保留。dump SHA256 在恢复前再次检查。
5. `pg_restore --exit-on-error --single-transaction --no-acl` 到从未存在的新 UUID 库，检查没有待应用 migrations；切换仅验证子进程的 DB/私有目录环境。恢复的活动文件通过授权下载并核对 hash；400 天档案仍有效；其他用户读取返回 404；撤权者返回 403；删除/历史失效文件仍返回 404，重传同 hash 也不能复活。恢复接口会写入仅恢复库的登录 session、节流记录和访问 audit，因此全库精确对比在这些探测之前完成。探测后私有文件仍必须匹配清单。
6. 验收脚本和后端/依赖锁等来源文件的前后 SHA256 必须完全相同。公共 runner 验证 owner marker、精确子进程 PID、cluster 路径及端口后只停止自己启动的 PG；停止、PID 文件消失及端口关闭全部需通过。FAIL 返回非零，不会把 cleanup 失败报 PASS。

每轮保留 `.runtime/recovery-acceptance/<UUID>/` 中的 `recovery-report.json`、公共 `report.json`、命令日志、fixture 元数据、dump、文件 manifest、备份目录、恢复目录和已停止 cluster，以便复核；报告只含合成标识/状态/摘要，不输出原始简历正文或密码。运行失败也保留证据，未经明确授权不自动删除任何轮次目录或 DB。

## 一致性、RPO/RTO 与范围

本验收使用**停写窗口**。文件系统复制和 PostgreSQL exported snapshot 不构成在线跨存储原子快照；私有生产数据恢复必须先排空/停止相关写入并协调 DB、Runtime 与文件卷。合成 fixture 的所有已提交写入均在恢复点之前，所以报告的 `observed_rpo_seconds=0` 仅表示该已知停写小样本没有丢失提交，不能外推线上业务 RPO。`observed_rto_seconds` 实测从建立空恢复库直到完成 dump 恢复、文件复制、完整性/migration/业务授权门禁，不包括发现事故、配置云资源、网络路由切换或恢复生产流量的时间。其数值不是生产承诺或容量证明。

本工具明确**不覆盖** Native licensed Runtime 的 PostgreSQL/checkpoint/store/Redis、在线 WAL/PITR、工程私有卷/成本算法、真实员工材料、云服务器/Kubernetes/Linux镜像、跨节点共享卷、生产角色/密钥/ACL/加密及异地备份保管，也不声称整栈容灾或生产许可通过。旧部署文档中的 `portal_phase1_restore*` 手工演练仍独立保留；本轮 UUID 验收不连接、覆盖或替代它们。上线标准仍需对所有实际存储、目标发布镜像和云拓扑开展隔离恢复并验权。

## 安全负例

`test_safety.py` 用独立临时目录测试生产/相同 DB 目标拒绝、路径与 metadata 拒绝、损坏文件在创建目标之前失败、现有目标不覆盖、缺失/额外文件拒绝、链接拒绝、序列/源码漂移拒绝和有效复制不破坏源。它不连接数据库、不读取业务配置、不创建外部服务；实际恢复正确性由上面的 PostgreSQL 演练证明。
