import { afterEach, describe, expect, it, vi } from "vitest";
import { listKnowledgeChunks, listKnowledgeDatasets, listKnowledgeDocuments } from "./knowledge-materials-api";

afterEach(() => vi.unstubAllGlobals());

describe("knowledge materials api", () => {
  it("uses the authorized read-only dataset, document and chunk endpoints", async () => {
    const fetcher = vi.fn().mockImplementation(async () => new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetcher);
    const signal = new AbortController().signal;

    await listKnowledgeDatasets(signal);
    await listKnowledgeDocuments("dataset / 05", 2, "配电 规范", signal);
    await listKnowledgeChunks("dataset / 05", "document / one", 3, signal);

    expect(fetcher.mock.calls.map(call => call[0])).toEqual([
      "/api/product/knowledge/datasets/",
      "/api/product/knowledge/datasets/dataset%20%2F%2005/documents/?page=2&page_size=20&q=%E9%85%8D%E7%94%B5+%E8%A7%84%E8%8C%83",
      "/api/product/knowledge/datasets/dataset%20%2F%2005/documents/document%20%2F%20one/chunks/?page=3&page_size=10",
    ]);
    expect(fetcher.mock.calls.every(call => call[1].method === "GET" && call[1].signal === signal)).toBe(true);
  });
});
