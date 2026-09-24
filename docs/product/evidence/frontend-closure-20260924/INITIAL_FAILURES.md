# Initial Failures Preserved

1. 前端首次定向回归：24 tests 中 4 FAIL / 20 PASS。原因是界面标签从“选择审核文档版本”改为“选择审核技术方案版本”，旧断言未同步；修正测试后 24/24 PASS，后续新增刷新失败用例后 25/25 PASS。
2. stale/history 后端首次定向回归：Content-Disposition 使用 RFC 5987 百分号编码，中文文件名断言未解码，且失败导致流未关闭；改为解码后断言并显式关闭流，1/1 PASS。
3. lineage 加固首次定向回归：旧测试直接创建无 artifact lineage 的 report，强校验返回 `stale_pair`；测试改为建立完整 chapter/report/artifact/generation hash 链后再验证批准语义，生产代码保持 fail-closed。
4. 加固后首次产品全量回归：110 tests 中 1 ERROR / 1 SKIP。旧测试仍期望审核授权撤销后可通过普通 URL 下载；按新安全边界改为普通下载 409、`history=1` 返回带 stale 标识的草稿，release-flow 36/36 与最终产品回归通过。原始日志保留为 `backend-product-regression-initial-failed.txt`。
