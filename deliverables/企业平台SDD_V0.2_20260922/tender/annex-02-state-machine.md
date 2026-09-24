# 附件二：业务阶段与人工决定状态机

业务阶段、人工决定、采集健康与文件获取结果是不同维度，不能把 `DECIDED` 当作业务阶段终点后拒绝接收变更。状态由服务端实际事件派生或事务化迁移，前端不得自报批准状态；保留事件、操作者和所针对版本。

## 阶段状态（面向页面与业务推进）

| 状态 | 进入条件 | 允许下一步 |
| --- | --- | --- |
| `DISCOVERED` | S1 取得可定位、已落库的公告 | `FILTERED`；公告更正更新快照但不重复创建项目 |
| `FILTERED` | 完成已配置的业务范围筛选；未知字段不会被当作排除理由 | `PENDING_REVIEW`；确认无关可由人 `IGNORE` |
| `PENDING_REVIEW` | 需人员阅读公告、预检或疑似重复 | 有权人员作 `FOLLOW`/`HOLD`/`IGNORE`；仅 `FOLLOW` 进入 `FOLLOWED` |
| `FOLLOWED` | 人对当前公告版本确认跟进 | 有合法文件则 `FILE_ACQUIRED`，否则 `FILE_PENDING` |
| `FILE_PENDING` | 已关注但没有可合法读取的完整文件 | 文件合法取得后 `FILE_ACQUIRED`；可 `HOLD`/`IGNORE`，不得称完整复核 |
| `FILE_ACQUIRED` | 有受控完整文件及哈希、归属、版本 | `QUALIFICATION_REVIEW`；文件失效/撤权退回 `FILE_PENDING` |
| `QUALIFICATION_REVIEW` | 条款与企业证据逐项核对中，已记录已核实及待确认项 | 人作最终决定后 `DECIDED`；文件失效/更正需重新核对，不能沿用旧满足 |
| `DECIDED` | 有权人员作有理由的当前版本决定 | 版本变化标记决定依据已过期并退回 `PENDING_REVIEW` 或 `QUALIFICATION_REVIEW`；旧决定保留 |

`FOLLOW / HOLD / IGNORE` 是独立的人工决定值：可在 `PENDING_REVIEW`、`FILE_PENDING`、`QUALIFICATION_REVIEW` 做出，`HOLD` 和 `IGNORE` 不要求完整文件。`DECIDED` 仅表示当时版本的决定已记录，并**不表示实际投标或资格通过**；员工改变决定时写新事件而不覆盖历史。被忽略或暂缓的项目仍接收公告变更，变更应提示是否需要重新决策。S1/S2 未实施完整文件链路前，不能为凑齐状态而伪造文件取得或复核阶段。

采集健康（正常/无新增/异常/停采）、文件状态（待获取/已取得/失效）与资格结果（满足/待确认/不满足）分别存储或派生，不混成枚举。任何阶段的采集失败不抹除已确认商机；失败只更新来源运行状态。

**AT-TEN-SM**：非法跳转（例如 `DISCOVERED→FILE_ACQUIRED` 无文件，或未获权者直接写 `DECIDED`）被拒绝；`HOLD`/`IGNORE` 可在未取得文件时由有权人记录；文件更正使旧资格与决定依据过期但保留历史；S1 反复抓取不会重置人工决定。由 T-TEN-12/14 验证发现阶段，由 T-TEN-23、T-TEN-31/33 验证其余迁移。
