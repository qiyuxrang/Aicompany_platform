"""Bounded, content-preserving layout edits to generated DOCX, never the PACK.

Uses the stdlib DOM so Office's namespace prefixes and mc:Ignorable bindings
survive serialization. This establishes OOXML configuration, not visual quality.
"""
import hashlib
import io
import re
from xml.dom import Node, minidom
from xml.parsers.expat import ExpatError
from zipfile import BadZipFile, ZipFile


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
P_ORDER = "pStyle keepNext keepLines pageBreakBefore framePr widowControl numPr suppressLineNumbers pBdr shd tabs suppressAutoHyphens kinsoku wordWrap overflowPunct topLinePunct autoSpaceDE autoSpaceDN bidi adjustRightInd snapToGrid spacing ind contextualSpacing mirrorIndents suppressOverlap jc textDirection textAlignment textboxTightWrap outlineLvl divId cnfStyle rPr sectPr pPrChange".split()
R_ORDER = "rStyle rFonts b bCs i iCs caps smallCaps strike dstrike outline shadow emboss imprint noProof snapToGrid vanish webHidden color spacing w kern position sz szCs highlight u effect bdr shd fitText vertAlign rtl cs em lang eastAsianLayout specVanish oMath rPrChange".split()


def _children(node):
    return [child for child in node.childNodes if child.nodeType == Node.ELEMENT_NODE]


def _is(node, name):
    return node.nodeType == Node.ELEMENT_NODE and node.namespaceURI == W and node.localName == name


def _find(node, name):
    return next((child for child in _children(node) if _is(child, name)), None)


def _ensure(node, name):
    found = _find(node, name)
    if found is not None:
        return found
    found = node.ownerDocument.createElementNS(W, "w:" + name)
    order = P_ORDER if _is(node, "pPr") else R_ORDER if _is(node, "rPr") else ["rPr"] if _is(node, "r") else ["pPr"]
    rank = order.index(name) if name in order else len(order)
    successor = next((child for child in _children(node) if (
        order.index(child.localName) if child.namespaceURI == W and child.localName in order else len(order)) > rank), None)
    node.insertBefore(found, successor)
    return found


def _value(node, name, value):
    _ensure(node, name).setAttributeNS(W, "w:val", value)


def _text(node):
    return "".join(child.data for child in node.childNodes if child.nodeType in (Node.TEXT_NODE, Node.CDATA_SECTION_NODE))


def _semantic_digest(document):
    # Layout properties are intentionally excluded; authored text, field
    # instructions/boundaries, references, media placement and sections are not.
    digest = hashlib.sha256()
    names = {"t", "instrText", "fldChar", "fldSimple", "bookmarkStart", "bookmarkEnd",
             "drawing", "pict", "sectPr", "hyperlink", "br", "tab", "footnoteReference", "endnoteReference"}
    for node in document.getElementsByTagNameNS(W, "*"):
        if node.localName not in names:
            continue
        value = node.toxml() if node.localName in {"drawing", "pict", "sectPr"} else (
            node.localName + repr(sorted((attribute.namespaceURI, attribute.localName, attribute.value)
                                         for attribute in node.attributes.values())) + _text(node))
        encoded = value.encode("utf-8")
        digest.update(str(len(encoded)).encode("ascii") + b":" + encoded)
    return digest.hexdigest()


def _toc_ends(document):
    stack, ends = [], []
    for node in document.getElementsByTagNameNS(W, "*"):
        if _is(node, "fldSimple") and re.match(r"^\s*TOC(?:\s|$)", node.getAttributeNS(W, "instr"), re.I):
            raise ValueError("Unsupported simple TOC field")
        if _is(node, "fldChar"):
            kind = node.getAttributeNS(W, "fldCharType")
            if kind == "begin":
                stack.append({"instruction": "", "separated": False})
            elif kind == "separate":
                if not stack or stack[-1]["separated"]:
                    raise ValueError("Invalid field separator")
                stack[-1]["separated"] = True
            elif kind == "end":
                if not stack:
                    raise ValueError("Unbalanced field end")
                field = stack.pop()
                if re.match(r"^\s*TOC(?:\s|$)", field["instruction"], re.I):
                    if not field["separated"]:
                        raise ValueError("TOC lacks a result boundary")
                    ends.append(node)
            else:
                raise ValueError("Unsupported field boundary")
        elif _is(node, "instrText"):
            if not stack or stack[-1]["separated"]:
                raise ValueError("Field instruction outside instruction boundary")
            stack[-1]["instruction"] += _text(node)
    if stack or not ends:
        raise ValueError("Missing or unbalanced TOC field")
    return ends


def _compact_empty(paragraph):
    pp = _ensure(paragraph, "pPr")
    # Empty section terminators must not inherit Heading1's numbering, outline,
    # 18pt margins or keepNext. They still retain their complete section object.
    for child in _children(pp):
        if child.namespaceURI == W and child.localName in {"pStyle", "numPr", "outlineLvl"}:
            pp.removeChild(child)
    for name in ("keepNext", "keepLines", "widowControl", "snapToGrid"):
        _value(pp, name, "0")
    spacing = _ensure(pp, "spacing")
    for name, value in (("before", "0"), ("after", "0"), ("line", "20"), ("lineRule", "exact")):
        spacing.setAttributeNS(W, "w:" + name, value)
    for node in [pp, *[child for child in _children(paragraph) if _is(child, "r")]]:
        rp = _ensure(node, "rPr")
        _value(rp, "sz", "2")
        _value(rp, "szCs", "2")


def normalize_docx_layout(source, heading_bookmarks):
    """Return normalized bytes/evidence or fail without changing the source."""
    if not heading_bookmarks or len(heading_bookmarks) != len(set(heading_bookmarks)):
        raise ValueError("Heading bookmarks must be nonempty and unique")
    try:
        with ZipFile(io.BytesIO(source)) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if (len(entries) > 2048 or len(names) != len(set(names)) or "word/document.xml" not in names
                    or sum(entry.file_size for entry in entries) > 128 * 1024 * 1024
                    or any(entry.flag_bits & 1 for entry in entries)):
                raise ValueError("Unsupported DOCX package")
            parts = {entry.filename: archive.read(entry) for entry in entries}
            comment = archive.comment
        xml = parts["word/document.xml"]
        if len(xml) > 16 * 1024 * 1024 or re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", xml.decode("utf-8"), re.I):
            raise ValueError("Unsupported document XML")
        document = minidom.parseString(xml)
        pending, node_count = [(document.documentElement, 0)], 0
        while pending:
            node, depth = pending.pop()
            node_count += 1
            if depth > 128 or node_count > 1_000_000:
                raise ValueError("Document XML exceeds bounded topology")
            pending.extend((child, depth + 1) for child in _children(node))
        if document.documentElement.getAttribute("xmlns:w") != W:
            raise ValueError("Unsupported Word namespace binding")
        bodies = document.getElementsByTagNameNS(W, "body")
        if len(bodies) != 1:
            raise ValueError("Invalid document body")
        body = bodies[0]
        before = _semantic_digest(document)
        ends = _toc_ends(document)
        paragraphs = [node for node in _children(body) if _is(node, "p")]
        headings = []
        for name in heading_bookmarks:
            if len([node for node in document.getElementsByTagNameNS(W, "bookmarkStart")
                    if node.getAttributeNS(W, "name") == name]) != 1:
                raise ValueError("Missing or ambiguous authored bookmark")
            matching = [paragraph for paragraph in paragraphs if any(
                node.getAttributeNS(W, "name") == name for node in paragraph.getElementsByTagNameNS(W, "bookmarkStart"))]
            if len(matching) != 1 or _find(_ensure(matching[0], "pPr"), "outlineLvl") is None:
                raise ValueError("Missing or ambiguous authored heading")
            headings.append(matching[0])
        first_heading = min(paragraphs.index(node) for node in headings)
        for heading in headings:
            pp = _ensure(heading, "pPr")
            _value(pp, "keepNext", "1")
            _value(pp, "keepLines", "1")
        compact_sections = set()
        for end in ends:
            run, paragraph = end.parentNode, end.parentNode.parentNode
            if (not _is(run, "r") or not _is(paragraph, "p") or paragraph.parentNode is not body
                    or paragraphs.index(paragraph) >= first_heading
                    or any(not (_is(child, "rPr") or child is end or _is(child, "lastRenderedPageBreak"))
                           for child in _children(run))):
                raise ValueError("Unsupported TOC end placement")
            # Word expands an initially empty TOC into multiple paragraphs. Its
            # final field marker otherwise retains the template's 22pt run and
            # consumes an extra page. Give it an explicit small paragraph now.
            siblings = _children(paragraph)
            if siblings[-1] is not run:
                raise ValueError("TOC has trailing content after its end")
            if any(child is not run and not _is(child, "pPr") for child in siblings):
                tail = document.createElementNS(W, "w:p")
                body.insertBefore(tail, paragraph.nextSibling)
                tail.appendChild(run)
            else:
                tail = paragraph
            _compact_empty(tail)
            following = tail.nextSibling
            while following is not None and following.nodeType != Node.ELEMENT_NODE:
                following = following.nextSibling
            if following is not None and _is(following, "p"):
                pp = _find(following, "pPr")
                if pp is not None and _find(pp, "sectPr") is not None:
                    if any(not _is(child, "pPr") and not (_is(child, "r") and all(
                           _is(item, "rPr") or _is(item, "lastRenderedPageBreak") for item in _children(child)))
                           for child in _children(following)):
                        raise ValueError("Section terminator contains content")
                    _compact_empty(following)
                    compact_sections.add(following)
        if _semantic_digest(document) != before:
            raise ValueError("Normalization changed document content")
        parts["word/document.xml"] = document.toxml(encoding="utf-8")
        with io.BytesIO() as output:
            with ZipFile(output, "w") as archive:
                archive.comment = comment
                for entry in entries:
                    archive.writestr(entry, parts[entry.filename])
            normalized = output.getvalue()
        return normalized, {"version": "generated-ooxml-layout-v1", "heading_count": len(headings),
                            "toc_end_count": len(ends), "compact_section_count": len(compact_sections),
                            "semantic_sha256": before, "generated_sha256": hashlib.sha256(source).hexdigest(),
                            "normalized_sha256": hashlib.sha256(normalized).hexdigest(), "visual_review": "not_run"}
    except (BadZipFile, KeyError, IndexError, TypeError, UnicodeError, ExpatError) as error:
        raise ValueError("Invalid generated DOCX") from error
