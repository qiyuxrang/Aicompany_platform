import hashlib
import hmac
import json
import os
import re
from collections.abc import Mapping
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from django.conf import settings
from django.db import DatabaseError

from .product_models import DocumentTask
from .product_service import product_user_allowed, reviewer_allowed


ERROR_MESSAGES = {
    "disabled": "授权检索当前未启用。",
    "authorization_required": "当前任务没有有效的检索授权。",
    "auth_failed": "检索服务身份验证失败。",
    "unavailable": "授权检索服务当前不可用。",
    "invalid_response": "检索服务返回内容不符合合同。",
    "source_conflict": "检索来源与授权范围或来源内容冲突。",
}
CONTRACT = "portal-retrieval-v1"
_ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]{2,127}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


class RetrievalError(Exception):
    def __init__(self, code):
        self.code = code if code in ERROR_MESSAGES else "invalid_response"
        super().__init__(ERROR_MESSAGES[self.code])


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, newurl):
        return None


def _urlopen(request, timeout):
    return build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=timeout)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("nonfinite_number")


def _text(value, limit, *, content=False):
    forbidden = any(ord(character) < 32 and (not content or character not in "\t\r\n") for character in value) if isinstance(value, str) else True
    if (not isinstance(value, str) or len(value) > limit or forbidden
            or (not value.strip()) or (not content and value != value.strip())):
        raise RetrievalError("invalid_response")
    return value


def _limit(name, default, minimum, maximum):
    value = getattr(settings, name, default)
    if type(value) is not int or not minimum <= value <= maximum:
        raise RetrievalError("unavailable")
    return value


def _scope_for_owner(owner_id):
    authorizations = getattr(settings, "PRODUCT_RETRIEVAL_AUTHORIZATIONS", {})
    if not isinstance(authorizations, Mapping):
        raise RetrievalError("authorization_required")
    keys = [key for key in (owner_id, str(owner_id)) if key in authorizations]
    if len(keys) != 1:
        raise RetrievalError("authorization_required")
    configured = authorizations[keys[0]]
    if not isinstance(configured, Mapping) or not 1 <= len(configured) <= 100:
        raise RetrievalError("authorization_required")
    scope = []
    for dataset_id, document_ids in configured.items():
        dataset_id = _text(dataset_id, 256)
        if dataset_id == "*" or not isinstance(document_ids, (list, tuple)) or not 1 <= len(document_ids) <= 1000:
            raise RetrievalError("authorization_required")
        documents = []
        for document_id in document_ids:
            document_id = _text(document_id, 256)
            if document_id in documents:
                raise RetrievalError("authorization_required")
            documents.append(document_id)
        if "*" in documents and len(documents) != 1:
            raise RetrievalError("authorization_required")
        scope.append({"dataset_id": dataset_id, "document_ids": sorted(documents)})
    return sorted(scope, key=lambda item: item["dataset_id"])


def _scope_covers(granted, required):
    available = {item["dataset_id"]: set(item["document_ids"]) for item in granted}
    for item in required:
        documents = available.get(item["dataset_id"], set())
        if "*" in item["document_ids"]:
            if "*" not in documents:
                return False
        elif "*" not in documents and not set(item["document_ids"]) <= documents:
            return False
    return True


def _scope_hash(task, owner_scope, reviewer_scope):
    owner = task.owner
    reviewer = task.reviewer
    snapshot = {
        "task_id": str(task.pk),
        "owner_id": owner.pk,
        "owner_session_version": owner.session_version,
        "owner_grant_version": owner.grant_version,
        "owner_scope": owner_scope,
        "reviewer_id": reviewer.pk if reviewer else None,
        "reviewer_session_version": reviewer.session_version if reviewer else None,
        "reviewer_grant_version": reviewer.grant_version if reviewer else None,
        "reviewer_scope": reviewer_scope,
    }
    encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _authorization(task):
    if getattr(settings, "PRODUCT_RETRIEVAL_ENABLED", False) is not True:
        raise RetrievalError("disabled")
    try:
        task_id = task.pk
        current = DocumentTask.objects.select_related("owner", "reviewer").get(pk=task_id)
    except (AttributeError, TypeError, ValueError, DocumentTask.DoesNotExist):
        raise RetrievalError("authorization_required") from None
    except DatabaseError:
        raise RetrievalError("unavailable") from None
    try:
        if not product_user_allowed(current.owner):
            raise RetrievalError("authorization_required")
        scope = _scope_for_owner(current.owner_id)
        reviewer_scope = None
        if current.reviewer_id:
            if not reviewer_allowed(current.reviewer):
                raise RetrievalError("authorization_required")
            reviewer_scope = _scope_for_owner(current.reviewer_id)
            if not _scope_covers(reviewer_scope, scope):
                raise RetrievalError("authorization_required")
    except DatabaseError:
        raise RetrievalError("unavailable") from None
    return {
        "task_id": str(current.pk),
        "owner_id": current.owner_id,
        "reviewer_id": current.reviewer_id,
        "scope": scope,
        "scope_hash": _scope_hash(current, scope, reviewer_scope),
    }


def authorization_current(task, snapshot):
    expected = snapshot.get("scope_hash") if isinstance(snapshot, Mapping) else None
    if not isinstance(expected, str) or not _SHA256.fullmatch(expected):
        return {"current": False, "code": "authorization_required", "scope_hash": ""}
    try:
        current = _authorization(task)
    except RetrievalError as error:
        return {"current": False, "code": error.code, "scope_hash": ""}
    valid = hmac.compare_digest(expected, current["scope_hash"])
    return {"current": valid, "code": "ok" if valid else "authorization_required",
            "scope_hash": current["scope_hash"] if valid else ""}


def _endpoint():
    url = getattr(settings, "PRODUCT_RETRIEVAL_URL", "")
    allowed = getattr(settings, "PRODUCT_RETRIEVAL_ALLOWED_URLS", ())
    try:
        parsed = urlsplit(url)
        if (not isinstance(url, str) or url != url.strip() or not isinstance(allowed, (list, tuple))
                or url not in allowed or parsed.scheme != "https" or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.port not in (None, 443) or "\\" in url or "%" in url
                or any(ord(character) < 33 or ord(character) > 126 for character in url)
                or any(part in (".", "..") for part in parsed.path.split("/"))):
            raise ValueError
    except (TypeError, ValueError):
        raise RetrievalError("unavailable") from None
    return url


def _credential():
    variable = getattr(settings, "PRODUCT_RETRIEVAL_TOKEN_ENV", "")
    token = os.environ.get(variable, "") if isinstance(variable, str) and _ENV_NAME.fullmatch(variable) else ""
    if not 20 <= len(token) <= 512 or any(ord(character) < 33 or ord(character) > 126 for character in token):
        raise RetrievalError("auth_failed")
    return token


def _fetch(url, token, payload, timeout, max_bytes):
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    if len(body) > 65536:
        raise RetrievalError("invalid_response")
    request = Request(url, data=body, headers={
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }, method="POST")
    try:
        with _urlopen(request, timeout) as response:
            if getattr(response, "status", 200) != 200:
                raise RetrievalError("unavailable")
            raw = response.read(max_bytes + 1)
        if not isinstance(raw, bytes) or len(raw) > max_bytes:
            raise RetrievalError("invalid_response")
        return raw
    except RetrievalError:
        raise
    except HTTPError as error:
        code = error.code
        error.close()
        if code in (401, 403):
            raise RetrievalError("auth_failed") from None
        if code in (400, 409, 415, 422):
            raise RetrievalError("invalid_response") from None
        raise RetrievalError("unavailable") from None
    except (URLError, TimeoutError, OSError, HTTPException, ValueError):
        raise RetrievalError("unavailable") from None


def _source_id(source):
    stable = {key: source[key] for key in ("dataset_id", "document_id", "chunk_id", "sha256", "location")}
    encoded = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _source_allowed(source, scope):
    for grant in scope:
        if grant["dataset_id"] == source["dataset_id"]:
            return "*" in grant["document_ids"] or source["document_id"] in grant["document_ids"]
    return False


def _parse_response(raw, scope, max_sources, max_text, max_total_text):
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    except (UnicodeDecodeError, ValueError, TypeError):
        raise RetrievalError("invalid_response") from None
    if (not isinstance(payload, dict) or set(payload) != {"contract", "status", "sources"}
            or payload["contract"] != CONTRACT or payload["status"] not in {"matched", "no_hits", "conflict"}
            or not isinstance(payload["sources"], list) or len(payload["sources"]) > max_sources):
        raise RetrievalError("invalid_response")
    if payload["status"] == "no_hits" and payload["sources"]:
        raise RetrievalError("invalid_response")
    if payload["status"] == "matched" and not payload["sources"]:
        raise RetrievalError("invalid_response")
    if payload["status"] == "conflict" and len(payload["sources"]) < 2:
        raise RetrievalError("invalid_response")
    sources = []
    locators = {}
    stable_ids = {}
    total_text = 0
    for raw_source in payload["sources"]:
        if not isinstance(raw_source, dict) or set(raw_source) != {
                "dataset_id", "document_id", "chunk_id", "text", "sha256", "location"}:
            raise RetrievalError("invalid_response")
        source = {
            "dataset_id": _text(raw_source["dataset_id"], 256),
            "document_id": _text(raw_source["document_id"], 256),
            "chunk_id": _text(raw_source["chunk_id"], 256),
            "text": _text(raw_source["text"], max_text, content=True),
            "sha256": _text(raw_source["sha256"], 64),
            "location": _text(raw_source["location"], 1000),
        }
        if not _SHA256.fullmatch(source["sha256"]):
            raise RetrievalError("invalid_response")
        if not hmac.compare_digest(hashlib.sha256(source["text"].encode()).hexdigest(), source["sha256"]):
            raise RetrievalError("source_conflict")
        if not _source_allowed(source, scope):
            raise RetrievalError("source_conflict")
        total_text += len(source["text"])
        if total_text > max_total_text:
            raise RetrievalError("invalid_response")
        locator = (source["dataset_id"], source["document_id"], source["chunk_id"])
        previous = locators.get(locator)
        if previous is not None:
            if previous != source:
                raise RetrievalError("source_conflict")
            continue
        locators[locator] = dict(source)
        source["id"] = _source_id(source)
        if source["id"] in stable_ids and stable_ids[source["id"]] != locator:
            raise RetrievalError("source_conflict")
        stable_ids[source["id"]] = locator
        sources.append({key: source[key] for key in (
            "id", "dataset_id", "document_id", "chunk_id", "text", "sha256", "location")})
    if payload["status"] == "conflict" and len(sources) < 2:
        raise RetrievalError("invalid_response")
    return {"status": payload["status"], "sources": sources}


def retrieve_for_task(task, query):
    query = _text(query, 4000, content=True)
    before = _authorization(task)
    url = _endpoint()
    token = _credential()
    timeout = _limit("PRODUCT_RETRIEVAL_TIMEOUT_SECONDS", 5, 1, 30)
    max_bytes = _limit("PRODUCT_RETRIEVAL_MAX_RESPONSE_BYTES", 1048576, 1024, 4194304)
    max_sources = _limit("PRODUCT_RETRIEVAL_MAX_SOURCES", 20, 1, 100)
    max_text = _limit("PRODUCT_RETRIEVAL_MAX_SOURCE_TEXT_CHARS", 8000, 1, 50000)
    max_total_text = _limit("PRODUCT_RETRIEVAL_MAX_TOTAL_TEXT_CHARS", 32000, 1, 200000)
    request_payload = {"contract": CONTRACT, "query": query, "scope": before["scope"], "limit": max_sources}
    try:
        raw = _fetch(url, token, request_payload, timeout, max_bytes)
    except RetrievalError:
        after = _authorization(task)
        if not hmac.compare_digest(before["scope_hash"], after["scope_hash"]):
            raise RetrievalError("authorization_required") from None
        raise
    after = _authorization(task)
    if not hmac.compare_digest(before["scope_hash"], after["scope_hash"]):
        raise RetrievalError("authorization_required")
    result = _parse_response(raw, after["scope"], max_sources, max_text, max_total_text)
    return {**result, "scope_hash": after["scope_hash"]}
