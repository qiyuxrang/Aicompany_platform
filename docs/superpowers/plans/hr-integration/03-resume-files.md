# H3 简历私有文件与文本提取计划

已获用户全量执行授权。本批不调用模型，真实样例限 2～3 份；吞吐与错误测试用合成文件。

## 文件与合同

- `backend/portal/hr_resume_storage.py`：文件名、格式、大小校验，私有 UUID 文件存储及 SHA256 复验。
- `backend/portal/hr_resume_extract.py`：TXT/DOCX 提取；PDF 调用既有隔离文档运行时。
- `backend/portal/hr_assets/extract_pdf.py`：只读 PDF，不加载凭据，无模型调用。
- `backend/portal/tests/test_hr_resume_files.py`：合成样例验证。
- 后续筛选模块在对象鉴权之后使用文件 helper，不把文件 ID 当访问授权。

## 固定边界

单文件最大 2MiB；DOCX 最多 2000 ZIP entries，解压总量最大 20MiB，正文 XML 最大 4MiB，文本最多 100000 字符。PDF 最多 100 页，30秒超时，输出文件最大 1MiB。只支持 TXT/DOCX/带文本层 PDF，旧 DOC、扫描 PDF、加密 PDF、带宏或外链的 DOCX 显式拒绝。不截断正文冒充完整提取。

## RED/GREEN

- [ ] 新测试：TXT 保留姓名联系方式、DOCX 保留段落/表格顺序、畸形/空文件/宏/外链/DTD/ZIP炸弹拒绝、路径逃逸拒绝、物理篡改被发现。
- [ ] 运行测试观察模块缺失 RED。
- [ ] 实现原子私有写入、UUID file_id、下载前重算 hash。目录独立于公开静态文件；复用现有错误语义。
- [ ] 使用标准库读取 OOXML；DTD/实体声明拒绝，提取时按文档节点顺序输出文本。
- [ ] PDF 用现有固定运行时 PyMuPDF，不新增主项目依赖；最小环境子进程、限时、文件输出限量、不输出简历正文日志。
- [ ] 测试替身校验子进程环境不包含 Key/Token，真实 PDF 合成样例单独验证。
- [ ] 精确提交，记录未覆盖扫描 OCR/真实简历/生产容量，不冒充完整简历筛选完成。

运行：`uv run --env-file .runtime/hr-integration-tests.env python backend/manage.py test portal.tests.test_hr_resume_files --verbosity 1`
