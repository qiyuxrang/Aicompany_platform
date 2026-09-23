# T-P02 Test Results

日期：2026-09-23

- `portal.tests.test_product_retrieval`
- `portal.tests.test_product_retrieval_flow`
- 结果：48/48 PASS（内部检索合同与授权边界）。
- 代表性来源上传/API/版本链：已执行，见 `version-chain.json`。
- 真实 RAGFlow：BLOCKED / NOT_VERIFIED；探针状态 `WAITING_INPUT`，错误码 `retrieval_disabled`。
- mock/fixture 未用于声称真实 RAGFlow 通过。
- Browser E2E：NOT_RUN / FRONTEND_DEFERRED。
