import fs from "node:fs/promises";
import path from "node:path";
import { createHash } from "node:crypto";

export const REQUIRED = Object.freeze({mermaid:"12.0.0",katex:"0.18.2","lodash-es":"4.18.1",vite:"8.3.0"});
export const sha256 = (bytes) => createHash("sha256").update(bytes).digest("hex");

export function assertVersions(versions) {
  for (const [name, version] of Object.entries(REQUIRED)) {
    if (versions[name] !== version) throw new Error(`unapproved ${name} version: ${versions[name]}`);
  }
}

export function assertUpstreamMap(map, label, codeSha256) {
  // The pinned upstream release has exactly two unmapped files: esbuild's
  // property helpers and a flowchart re-export facade. Their reviewed bytes
  // are pinned, so an arbitrary unmapped compiled dependency cannot pass.
  const reviewedUnmapped = {
    "chunk-Y2CYZVJY.mjs":"c77ed863f0c084dfc41b31574fc9e964fa293718de14213ad82f1d646857b590",
    "flowDiagram-KWPJA3E3.mjs":"b2d1b7e34df3c25489ca39002fc1ad20e8293ddfd5260562e8b461144be1ed9a",
  };
  const knownFacade = reviewedUnmapped[label] && reviewedUnmapped[label] === codeSha256;
  if (!Array.isArray(map.sources) || (!map.sources.length && !knownFacade) || !Array.isArray(map.sourcesContent)
      || map.sources.length !== map.sourcesContent.length || map.sourcesContent.some((text) => typeof text !== "string")) {
    throw new Error(`incomplete upstream source map: ${label}`);
  }
  // The external-dependency mermaid.core distribution must not hide a second
  // precompiled lodash/KaTeX copy that pnpm overrides cannot replace.
  for (const source of map.sources) {
    if (/(?:node_modules|\.pnpm)[/\\].*(?:katex|lodash(?:-es)?)(?:[/\\@.]|$)/i.test(source)) {
      throw new Error(`embedded dependency in upstream core: ${label}: ${source}`);
    }
  }
}

export function assertProvenance(modules, versions) {
  assertVersions(versions);
  for (const dependency of ["katex", "lodash-es"]) {
    const used = modules.filter((item) => item.package === dependency);
    if (!used.length || used.some((item) => item.version !== REQUIRED[dependency])) {
      throw new Error(`bundle did not compile only approved ${dependency} modules`);
    }
  }
  if (modules.some((item) => /mermaid\.(?:min\.js|esm)/.test(item.path))) {
    throw new Error("prebundled Mermaid distribution is forbidden");
  }
}

export async function readVerifiedBundle(directory) {
  const manifestBytes = await fs.readFile(path.join(directory,"runtime-manifest.json"));
  const manifest = JSON.parse(manifestBytes.toString("utf8"));
  if (manifest.schemaVersion !== 1 || manifest.bundle !== "mermaid.runtime.js") throw new Error("invalid runtime manifest");
  assertProvenance(manifest.modules, manifest.versions);
  const bundle = await fs.readFile(path.join(directory,manifest.bundle));
  if (sha256(bundle) !== manifest.bundleSha256) throw new Error("Mermaid runtime bundle hash mismatch");
  return {manifest,manifestSha256:sha256(manifestBytes),bundle,path:path.join(directory,manifest.bundle)};
}
