# 运维库备份恢复补验

## 结论

- 2026-09-21 使用登记的 `.runtime/ops-validation.env`，对 `127.0.0.1:55438/portal_ops_20260921_085811` 完成只读备份和全新隔离恢复。
- 最终命令：`uv run --env-file .runtime/ops-validation.env python validation/ops_restore_rehearsal.py`。
- 最终恢复库 `portal_ops_restore_20260921_040512` 保留；真实 `pg_restore --exit-on-error` 成功，未覆盖或删除任何数据库。
- 20 张 `public` 表的集合、行数和稳定 SHA-256 指纹全部相同；迁移 `0003_modulecheck_operationalissue_audit_indexes` 在源库和恢复库均已登记。
- `portal_modulecheck` 为 3 行，`portal_operationalissue` 为 1 行，两表指纹与同一源快照完全一致。
- 完整证据见 `restore-rehearsal.json`；敏感 custom-format dump 位于被 Git 忽略且限制 ACL 的 `backups/closure-backup/20260921T040512Z/portal_ops_20260921_085811.dump`，未复制环境文件或凭据。

## 比较边界

源表指纹与 `pg_dump` 共用一个 `REPEATABLE READ READ ONLY` 导出快照，并在全部源表上持有与在线读写兼容的 `ACCESS SHARE` 锁。因此恢复比较对应同一备份时点；该过程允许正常并发增删改，但快照建立后提交的变化不属于本次比较，也未用“运行后源库未变化”作为通过条件。脚本没有写源库、停服务或变更账号，独立复核确认正式 `bjrunner` 仍启用且密码可用。

恢复库包含完整验收数据，数据库注释已标记敏感；`PUBLIC` 的数据库、`public` schema、表和序列权限已撤销，正常 PostgreSQL ACL 下仅 owner 可访问。备份目录禁用继承并限制为执行账号访问，不应复制到仓库、工单或非受控位置。

## 失败留痕

- `portal_ops_restore_20260921_040409`：新建后在写数据库注释时因参数语法失败，尚未执行恢复；空库保留，`PUBLIC` 数据库权限已撤销。
- `portal_ops_restore_20260921_040436`：20 表恢复完成且 ACL 已收紧，随后只读 ACL 证明查询因空 ACL 数组处理失败；库与对应受限备份均保留。
- 按禁止删除、禁止覆盖原则，没有清理上述数据库；修正后使用第三个全新库完整重跑通过。
