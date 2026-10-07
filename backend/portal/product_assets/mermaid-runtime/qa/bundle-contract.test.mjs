import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import katex from "katex";
import { template, unset, omit } from "lodash-es";
import { assertProvenance, assertUpstreamMap, REQUIRED, readVerifiedBundle, sha256 } from "../bundle-contract.mjs";

test("template rejects malicious imports parameter keys before code execution",()=>{
  assert.equal(globalThis.__portalMermaidInjection,undefined);
  assert.throws(()=>template("safe",{imports:{"a=globalThis.__portalMermaidInjection=1":undefined}}));
  assert.equal(globalThis.__portalMermaidInjection,undefined);
  assert.equal(template("<%= value %>")({value:"valid"}),"valid");
});

test("coercible array-path components cannot delete prototype properties",()=>{
  const marker = "__portalMermaidPrototypeMarker";
  Object.defineProperty(Object.prototype,marker,{value:"intact",configurable:true});
  try {
    unset({},[{toString:()=>"__proto__"},marker]);
    assert.equal(Object.prototype[marker],"intact");
    omit({},[[{toString:()=>"constructor"},{toString:()=>"prototype"},marker]]);
    assert.equal(Object.prototype[marker],"intact");
    const ordinary = {nested:{value:1}};
    assert.equal(unset(ordinary,["nested","value"]),true);
    assert.deepEqual(ordinary,{nested:{}});
  } finally { delete Object.prototype[marker]; }
});

test("KaTeX ignores inherited trust while an own explicit option remains supported",()=>{
  const expression = String.raw`\href{https://blocked.invalid}{x}`;
  const inherited = katex.renderToString(expression,Object.create({trust:true}));
  assert.doesNotMatch(inherited,/<a\b[^>]*\bhref=/);
  const explicit = katex.renderToString(expression,{trust:true});
  assert.match(explicit,/<a\b[^>]*\bhref=/);
});

test("core source maps refuse hidden precompiled lodash or KaTeX",()=>{
  for (const dependency of ["katex@0.16.47", "lodash-es@4.17.23"]) {
    assert.throws(()=>assertUpstreamMap({sources:[`../../node_modules/.pnpm/${dependency}/node_modules/${dependency.split("@")[0]}/index.js`],sourcesContent:["compiled code"]},"chunk.mjs"));
  }
  assert.throws(()=>assertUpstreamMap({sources:[],sourcesContent:[]},"unknown.mjs"));
  assert.throws(()=>assertUpstreamMap({sources:["src/file.ts"],sourcesContent:[null]},"chunk.mjs"));
  assertUpstreamMap({sources:["src/file.ts"],sourcesContent:["safe"]},"chunk.mjs");
});

test("bundle provenance rejects old transitive versions or legacy distribution",()=>{
  const modules = [{path:"node_modules/katex/dist/katex.mjs",package:"katex",version:"0.18.2"},
    {path:"node_modules/lodash-es/unset.js",package:"lodash-es",version:"4.18.1"}];
  assertProvenance(modules,REQUIRED);
  assert.throws(()=>assertProvenance([...modules,{path:"old",package:"katex",version:"0.16.47"}],REQUIRED));
  assert.throws(()=>assertProvenance(modules.filter((item)=>item.package!=="lodash-es"),REQUIRED));
  assert.throws(()=>assertProvenance([...modules,{path:"node_modules/mermaid/dist/mermaid.min.js",package:"mermaid",version:"12.0.0"}],REQUIRED));
});

test("tampered or substituted runtime bytes fail before browser launch",async()=>{
  const directory = await fs.mkdtemp(path.join(os.tmpdir(),"portal-mermaid-contract-"));
  try {
    const manifest = {schemaVersion:1,bundle:"mermaid.runtime.js",bundleSha256:sha256("approved"),versions:REQUIRED,
      modules:[{path:"katex",package:"katex",version:"0.18.2"},{path:"lodash",package:"lodash-es",version:"4.18.1"}]};
    await fs.writeFile(path.join(directory,"runtime-manifest.json"),JSON.stringify(manifest));
    await fs.writeFile(path.join(directory,"mermaid.runtime.js"),"tampered");
    await assert.rejects(()=>readVerifiedBundle(directory),/hash mismatch/);
    manifest.bundle = "../unapproved.js";
    await fs.writeFile(path.join(directory,"runtime-manifest.json"),JSON.stringify(manifest));
    await assert.rejects(()=>readVerifiedBundle(directory),/invalid runtime manifest/);
  } finally {
    if (path.dirname(path.resolve(directory))!==path.resolve(os.tmpdir()) || !path.basename(directory).startsWith("portal-mermaid-contract-")) {
      throw new Error("refusing to remove unowned temporary test directory");
    }
    await fs.rm(directory,{recursive:true,force:true});
  }
});
