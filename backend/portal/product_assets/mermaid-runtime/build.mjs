import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "vite";
import { assertProvenance, assertUpstreamMap, assertVersions, sha256 } from "./bundle-contract.mjs";

const root = path.dirname(fileURLToPath(import.meta.url));
const modulesDirectory = path.join(root,"node_modules");
const versions = {};
for (const name of ["mermaid","katex","lodash-es","vite","playwright-core"]) {
  versions[name] = JSON.parse(await fs.readFile(path.join(modulesDirectory,name,"package.json"),"utf8")).version;
}
assertVersions(versions);

const mermaidRoot = path.join(modulesDirectory,"mermaid");
const upstream = [path.join(mermaidRoot,"dist/mermaid.core.mjs")];
const chunkDirectory = path.join(mermaidRoot,"dist/chunks/mermaid.core");
for (const name of (await fs.readdir(chunkDirectory)).filter((name)=>name.endsWith(".mjs")).sort()) {
  upstream.push(path.join(chunkDirectory,name));
}
const upstreamAudit = [];
for (const file of upstream) {
  const mapFile = file + ".map";
  const text = await fs.readFile(mapFile,"utf8");
  const map = JSON.parse(text);
  const codeSha256 = sha256(await fs.readFile(file));
  assertUpstreamMap(map,path.basename(file),codeSha256);
  upstreamAudit.push({path:path.relative(mermaidRoot,file).replaceAll(path.sep,"/"),
    sha256:codeSha256,sourceMapSha256:sha256(text),sources:map.sources.length});
}

const packageCache = new Map();
async function packageFor(id) {
  if (id.startsWith("\0") || !path.isAbsolute(id.split("?")[0])) return null;
  let directory = path.dirname(id.split("?")[0]);
  while (directory !== path.dirname(directory)) {
    if (packageCache.has(directory)) return packageCache.get(directory);
    try {
      const metadata = JSON.parse(await fs.readFile(path.join(directory,"package.json"),"utf8"));
      const result = {name:metadata.name,version:metadata.version};
      packageCache.set(directory,result);
      return result;
    } catch (error) {
      if (error.code !== "ENOENT" && error.code !== "ENOTDIR") throw error;
    }
    directory = path.dirname(directory);
  }
  return null;
}

let provenance = [];
await build({
  root,configFile:false,define:{__MERMAID_VERSION__:JSON.stringify(versions.mermaid)},
  build:{outDir:"dist",emptyOutDir:true,sourcemap:true,target:"es2022",minify:false,
    lib:{entry:path.join(root,"browser-entry.mjs"),name:"PortalMermaidRuntime",formats:["iife"],fileName:()=>"mermaid.runtime.js"},
    rolldownOptions:{output:{codeSplitting:false}},
  },
  plugins:[{name:"portal-runtime-provenance",async generateBundle(_options,bundle) {
    const ids = new Set();
    for (const chunk of Object.values(bundle)) if (chunk.type === "chunk") {
      for (const id of Object.keys(chunk.modules)) ids.add(id);
    }
    for (const id of [...ids].sort()) {
      const metadata = await packageFor(id);
      if (!metadata) continue;
      const normalized = id.replaceAll("\\","/");
      const lastModules = normalized.lastIndexOf("/node_modules/");
      const modulePath = lastModules >= 0 ? normalized.slice(lastModules+1) : path.relative(root,id).replaceAll(path.sep,"/");
      provenance.push({path:modulePath,package:metadata.name,version:metadata.version});
    }
    provenance = provenance.sort((a,b)=>a.path.localeCompare(b.path));
    assertProvenance(provenance,versions);
  }}],
});
const output = path.join(root,"dist");
const bundle = await fs.readFile(path.join(output,"mermaid.runtime.js"));
const manifest = {schemaVersion:1,bundle:"mermaid.runtime.js",bundleSha256:sha256(bundle),
  bundleBytes:bundle.length,versions,lockSha256:sha256(await fs.readFile(path.join(root,"pnpm-lock.yaml"))),
  workspaceSha256:sha256(await fs.readFile(path.join(root,"pnpm-workspace.yaml"))),
  buildSha256:sha256(await fs.readFile(path.join(root,"build.mjs"))),
  contractSha256:sha256(await fs.readFile(path.join(root,"bundle-contract.mjs"))),
  entrySha256:sha256(await fs.readFile(path.join(root,"browser-entry.mjs"))),
  upstreamExternalCoreAudit:upstreamAudit,modules:provenance,
  securityLevel:"strict",networkPolicy:"headless browser rejects all network requests"};
await fs.writeFile(path.join(output,"runtime-manifest.json"),JSON.stringify(manifest,null,2)+"\n");
process.stdout.write(JSON.stringify({built:true,bundleSha256:manifest.bundleSha256,versions,compiledModules:provenance.length})+"\n");
