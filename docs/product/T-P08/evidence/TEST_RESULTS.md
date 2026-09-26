# T-P08 Test Results

## 完整产品回归

- 命令：`.venv/Scripts/python.exe backend/manage.py test portal.tests --pattern "test_product*.py" --verbosity 1`
- 结果：112 tests，111 PASS / 1 SKIP，耗时 116.766 秒。
- 原始日志：`backend-product-regression-v16.txt`
- SHA-256：`fd03286f23ec6bd6a371a6a2168e4bf75ff33f4a517e20deb625b4347319c9f9`
- 环境：临时测试数据库与 `.runtime/t-p08-test-storage`；未修改正式数据库。

## 三成果证据校验

- 命令：`.venv/Scripts/python.exe validation/product_quality_evidence.py docs/product/T-P08/evidence/representative-run-20260924-v16/version-chain.json`
- 结果：`PASS`；2 sources、21 revisions、3 approvals、3 artifacts、2×9 Word pages、7 PPT slides。
- 原始日志：`quality-evidence-v16.txt`
- SHA-256：`d676e8867ac59cb171d59b0bf3f8f28b69d076c3f4e2be77521451c9446fa1bf`

## 代表性链

- v16 原始日志：`three-output-run-v16.txt`
- SHA-256：`a4c904c4ed3ae00306cfc34f5ee7634c8dc0eaa418aea30c6214e6417b301b59`
- 结果：`CORE_PASS_WITH_EXTERNAL_AND_HUMAN_BLOCKERS`。
- 三类 artifact 正式批准均被 409 `formal_release_blocked` 拒绝；该拒绝是预期门禁，不是测试失败。

## 格式专项

- `portal.tests.test_product_documents` 共 11 项通过；覆盖两类 Word 的 A4、正文/标题字体字号、18 磅固定行距、阿拉伯数字分级编号、表编号、普通页眉、章首页页眉及冻结 policy 完整性。
- v13、v14、v15 的实际渲染问题均保留在 `INITIAL_FAILURES.md`；未用静态检查替代 Word 重渲染。
