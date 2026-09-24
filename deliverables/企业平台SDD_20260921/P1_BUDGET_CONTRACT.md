# P1 金额预算合同

## 边界

- 本合同只为 P1 外部模型调用预留获批金额上界，不估算 token、供应商价格或实际账单。
- 每次调用按该路由获批的单次费用上界全额预留。调用失败、超时、取消或重试均不退款，因为平台不能确定外部费用是否已经发生。
- 预算账本只保存在 `task.checkpoint["budget"]`，不从 `checkpoint["calls"]`、token 数或响应内容推导。
- 本实现不调用网络、模型或收费服务，也不修改主 worker。

## 配置合同

`settings.PRODUCT_COST_POLICY` 默认按空字典处理。空配置、缺字段、未知路由或非法字段均视为没有预算批准，抛出 `BudgetError("budget_authorization_required")`。

批准后由业务/运维直接提供以下对象；占位符必须替换为真实批准记录中的值，不得由代码补价：

```python
PRODUCT_COST_POLICY = {
    "approval_ref": "<approved-reference>",
    "currency": "<approved-currency>",
    "max_task_cost": "<approved-positive-finite-decimal>",
    "route_cost_caps": {
        "<route>": "<approved-positive-finite-decimal-per-call-cap>",
    },
}
```

- `approval_ref`、`currency` 和路由名必须是非空、无首尾空白的字符串。
- `max_task_cost` 及每个路由上界必须是正、有限的 Decimal 字符串；整数、浮点数、零、负数、NaN 和 Infinity 均拒绝。
- 币种按字符串精确匹配；代码不做汇率换算。

## 集成接口

主 worker 在每次外部调用前，完成权限和任务状态检查后，在已有任务行锁事务内调用：

```python
from portal.product_budget import BudgetError, reserve_call

with transaction.atomic():
    task = DocumentTask.objects.select_for_update().get(pk=task_id)
    budget_status = reserve_call(task, route)
```

调用者负责 `select_for_update()`；`reserve_call()` 不另开事务或加锁。成功时函数更新 `task.checkpoint["budget"]` 并执行：

```python
task.save(update_fields=["checkpoint", "updated_at"])
```

返回值只含可检查的非敏感状态：

```python
{
    "remaining": "<current-policy-limit-minus-cumulative-reserved>",
    "reserved": "<cumulative-reserved>",
    "evidence": {
        "sequence": 1,
        "route": "<route>",
        "currency": "<currency>",
        "amount": "<this-call-reservation>",
        "reserved": "<cumulative-reserved>",
    },
}
```

必须在该事务提交后才发起外部调用。若后续调用失败、任务取消或重试，不得删除或冲销这次预留。

## 持久账本

账本版本为 1，包含当前批准引用、币种、当前总上限、累计预留和逐次证据。每条证据保存当次 `approval_ref`、路由、币种、单次预留、累计预留及当时总上限。读取时会重算证据累计；版本、序号、金额或累计不一致时抛出 `budget_ledger_invalid`，不会覆盖旧账本。

政策变更遵守以下规则：

- 同币种的新 `approval_ref` 可以接管后续调用，但累计金额和历史证据原样保留。
- 新总上限低于已预留金额时抛出 `budget_exceeded`；等于已预留金额时下一次正金额预留同样超限。
- 已有账本与新政策币种不一致时抛出 `budget_currency_mismatch`，不得通过换币重置累计。
- 路由上界变化只影响后续预留；历史证据仍保留当次批准和金额。

## 错误语义

| code | 含义 | 是否保存 |
| --- | --- | --- |
| `budget_authorization_required` | 没有完整、有效且覆盖当前路由的批准政策 | 否 |
| `budget_exceeded` | 新预留将超过总上限，或新上限低于已预留金额 | 否 |
| `budget_currency_mismatch` | 新政策币种与任务既有账本不一致 | 否 |
| `budget_ledger_invalid` | 既有账本结构或累计证据无效 | 否 |

调用者应将这些 `BudgetError.code` 映射到既有任务失败/等待处理语义；捕获错误后不得继续外部调用。

## 隔离测试

`backend/portal/tests/test_product_budget.py` 使用 fake task 和测试专用虚构金额，只验证合同、持久化调用和拒绝路径，不连接网络、模型、支付或厂商服务。测试金额不代表任何真实价格或批准。
