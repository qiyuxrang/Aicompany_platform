# T-P08 Preserved Failures

1. 首次启动使用系统 Python，因缺少 Django 在业务逻辑前失败；原始日志 `three-output-run-initial-runtime-failed.txt`，SHA-256 `390a513ba7397ced339f9b966e122dcce42ba08e37865e809504df7af2e31004`。随后改用仓库既有 `.venv`，未安装依赖。
2. v10 真实渲染发现两份 Word 共用“三件套”总标题，不能独立识别成果；输出与日志完整保留在 `representative-run-20260924-v10/`、`three-output-run-v10.txt`。
3. v11 首次修正对带“（代表性草稿）”的任务标题采用追加后缀，形成“三件套（代表性草稿）（技术方案）”；输出与日志完整保留在 `representative-run-20260924-v11/`、`three-output-run-v11.txt`。
4. v12 改为仅替换任务标题中的“三件套”标记，两个 Word 分别得到清晰标题；新增回归用例覆盖带括号后缀的实际标题。

上述失败未删除、未改写为 PASS；v12 才作为 T-P08 最终代表性证据。
