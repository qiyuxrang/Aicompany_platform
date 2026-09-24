# T-P08 Test Results

## 完整产品回归

- 命令：`.venv/Scripts/python.exe backend/manage.py test portal.tests --pattern "test_product*.py" --verbosity 1`
- 结果：111 tests，110 PASS / 1 SKIP，耗时 117.407 秒。
- 原始日志：`backend-product-regression.txt`
- SHA-256：`eb1ac098a24b8f49728de144eda3f02b06e1d74d0a1b342075522d3f6c5ed226`
- 环境：临时测试数据库与 `.runtime/t-p08-test-storage`；未修改正式数据库。

## 三成果证据校验

- 命令：`.venv/Scripts/python.exe validation/product_quality_evidence.py docs/product/T-P08/evidence/representative-run-20260924-v12/version-chain.json`
- 结果：`PASS`；2 sources、21 revisions、3 approvals、3 artifacts、2×9 Word pages、7 PPT slides。
- 原始日志：`quality-evidence.txt`
- SHA-256：`320c938e5f11a4d47bf318c1dd3ccfb98fbe73fe2f4f2bb42768eee480127dd3`

## 代表性链

- v12 原始日志：`three-output-run-v12.txt`
- SHA-256：`b44743f7118f3ab244eed665c76cd36cc6faf4f4bd4ef7eff1d17c3aa7bd33c0`
- 结果：`CORE_PASS_WITH_EXTERNAL_AND_HUMAN_BLOCKERS`。
- 三类 artifact 正式批准均被 409 `formal_release_blocked` 拒绝；该拒绝是预期门禁，不是测试失败。
