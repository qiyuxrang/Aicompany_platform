# Controlled Mermaid runtime

Portal 的生成器仍使用 Mermaid 12.0.0 的 `initialize` / `run`，现有 20 个 flowchart pattern 与 PNG 输出尺寸保留。生产 renderer 始终 `securityLevel: strict`、`htmlLabels: false`；标题通过 `textContent`，不允许输入改变生成器安全配置。所有 HTTP 请求及 WebSocket 在新建的受控无界面 Chromium context 中拒绝，service workers 禁用；不打开用户的浏览器或登录会话。

原路径 `mermaid/dist/mermaid.min.js` 为上游预编译 UMD，不能通过更新 pnpm transitive lock 声称内部依赖已修复。现在从 **`mermaid/dist/mermaid.core.mjs` 外部依赖入口**用与前端相同版本的 Vite 8.3.0 重新编译单个 IIFE。`pnpm-workspace.yaml`（pnpm 11 的有效 override 位置）将全部 KaTeX 固定为 0.18.2、lodash-es 固定为 4.18.1，同时保留 exact direct pins 供回归测试使用。

构建检查所有 core chunk 的完整 source maps，拒绝已内置的 KaTeX/lodash 来源；两个没有 source map 内容的已审查文件仅允许固定 SHA256：esbuild 属性 helper 与 flowchart import/export facade。构建 provenance 记录实际编译模块的包名、版本与路径，拒绝旧 transitive 版本、缺失 patched dependency 或重新载入 legacy Mermaid bundle。最终运行前校验实际 IIFE 字节哈希，不会回退到 UMD。runtime 的 KaTeX `version` 与 lodash `VERSION` 也从实际加载对象读取，不仅来自 lock 声明。

相关官方修复：[lodash imports 注入](https://github.com/lodash/lodash/security/advisories/GHSA-r5fr-rjxr-66jc)、[lodash array path 原型绕过](https://github.com/lodash/lodash/security/advisories/GHSA-f23m-r3pf-42rh)、[KaTeX inherited trust](https://github.com/KaTeX/KaTeX/security/advisories/GHSA-238p-pmpm-9mq7)。固定版本替换与受控重打包一起完成；`pnpm audit` 单独归零并不证明运行包来源正确。

```powershell
# cwd: backend/portal/product_assets/mermaid-runtime
pnpm install --frozen-lockfile --ignore-scripts
pnpm build
pnpm test
pnpm verify:render -- --chromium 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
```

`dist/` 与 `node_modules/` 不进入 Git。Docker 的 mermaid-build 阶段使用 frozen lock + workspace overrides 安装开发构建工具，执行同一 `pnpm build` 后 prune prod；最终复制该阶段产物到运行镜像，在 backend 源码复制之后完成，不能让宿主 dist 覆盖镜像产物。runtime 镜像不需要 Vite。镜像中的 Linux Chromium、字体、非 root 运行和全三件套仍应独立执行部署验收；本机 Edge 证据不代替 Docker 运行结果。

`pnpm test` 验证恶意 template import key、可转换成 prototype 的 array path、KaTeX inherited trust 不生效/显式 trust 保持正常，以及来源隐藏/旧包/包字节被篡改的拒绝。`verify:render` 使用真实 renderer 生成全部 20 个既有 pattern，核对 PNG 的 2400×1350 尺寸、实际哈希、严格安全配置和零网络请求；另外验证 javascript click 不可用及一个保留域名 HTTP probe 在发出前被拒绝。只使用合成 caption，证据写入仓库 `.runtime/mermaid-acceptance/<UUID>/`，不向 assets 写入验收结果。前后 source hashes 必须一致，任何错误退出非零。

renderer stdout 保留原 `rendered` / `sourcePersisted` / `engine`，新增 `bundleSha256`、`manifestSha256`、`runtimeVersions`、`securityLevel`、`blockedNetworkRequests`，供文档工件记录实际加载包。`dist/runtime-manifest.json` 同时绑定 lock、workspace、entry/build/contract 与上游 core audit；不将构建来源记录误称为所有潜在漏洞的完整证明。
