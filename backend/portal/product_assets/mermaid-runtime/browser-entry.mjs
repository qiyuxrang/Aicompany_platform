// Only the ESM core keeps audited third-party imports external to upstream
// chunks. Never import mermaid.min.js or mermaid.esm.min.mjs here.
import mermaid from "mermaid/dist/mermaid.core.mjs";
import katex from "katex";
import lodash from "lodash-es";

window.mermaid = mermaid;
Object.defineProperty(window,"__PORTAL_MERMAID_RUNTIME_INFO__",{value:Object.freeze({
  mermaid:__MERMAID_VERSION__, katex:katex.version, "lodash-es":lodash.VERSION,
}),writable:false,configurable:false});
