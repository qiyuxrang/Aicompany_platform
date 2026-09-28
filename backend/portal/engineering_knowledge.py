import json
import os
import re
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class KnowledgeError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _open(request, timeout):
    return build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=timeout)


def configuration():
    url = os.environ.get("PORTAL_ENGINEERING_RAGFLOW_URL", "")
    dataset = os.environ.get("PORTAL_ENGINEERING_RAGFLOW_DATASET_ID", "")
    token = os.environ.get("RAGFLOW_ENGINEERING_TOKEN", "")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        raise KnowledgeError("not_configured") from None
    local = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"} and port == 19880
    secure = parsed.scheme == "https" and bool(parsed.hostname)
    if (not (local or secure) or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path != "/api/v1/retrieval" or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", dataset)
            or not 20 <= len(token) <= 512 or any(ord(char) < 33 or ord(char) > 126 for char in token)):
        raise KnowledgeError("not_configured")
    return url, dataset, token


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _request(url, token, *, body=None):
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = Request(url, data=json.dumps(body).encode("utf-8") if body is not None else None,
                      headers=headers, method="POST" if body is not None else "GET")
    try:
        with _open(request, 5) as response:
            if response.status != 200:
                raise KnowledgeError("unavailable")
            raw = response.read(131073)
    except HTTPError as error:
        error.close()
        raise KnowledgeError("unavailable") from None
    except (URLError, TimeoutError, OSError, HTTPException, ValueError):
        raise KnowledgeError("unavailable") from None
    if not isinstance(raw, bytes) or len(raw) > 131072:
        raise KnowledgeError("unavailable")
    try:
        payload = json.loads(raw, object_pairs_hook=_unique_pairs,
                             parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
    except (ValueError, UnicodeError, RecursionError):
        raise KnowledgeError("unavailable") from None
    if not isinstance(payload, dict) or type(payload.get("code")) is not int or payload["code"] != 0:
        raise KnowledgeError("unavailable")
    return payload.get("data")


def dataset_document_count(config):
    url, dataset, token = config
    data = _request(url.removesuffix("retrieval") + "datasets?id=" + dataset, token)
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict) or data[0].get("id") != dataset:
        raise KnowledgeError("unavailable")
    item = data[0]
    documents, chunks = item.get("document_count"), item.get("chunk_count")
    if type(documents) is not int or type(chunks) is not int or documents < 0 or chunks < 0:
        raise KnowledgeError("unavailable")
    return documents if documents and chunks else 0


def retrieve(question, config):
    url, dataset, token = config
    data = _request(url, token, body={"question": question, "dataset_ids": [dataset],
                                      "page": 1, "page_size": 6})
    if not isinstance(data, dict) or not isinstance(data.get("chunks"), list) or len(data["chunks"]) > 6:
        raise KnowledgeError("unavailable")
    sources = []
    for chunk in data["chunks"]:
        if not isinstance(chunk, dict) or chunk.get("dataset_id") != dataset:
            raise KnowledgeError("unavailable")
        document_id, chunk_id = chunk.get("document_id"), chunk.get("id")
        content, title = chunk.get("content"), chunk.get("document_keyword", "")
        if not all(isinstance(value, str) and 0 < len(value) <= bound for value, bound in (
                (document_id, 128), (chunk_id, 128), (content, 16000))):
            raise KnowledgeError("unavailable")
        if not isinstance(title, str) or len(title) > 300:
            raise KnowledgeError("unavailable")
        sources.append({"document_id": document_id, "title": title or "未命名文档", "content": content[:1200],
                        "chunk_id": chunk_id})
    return sources
