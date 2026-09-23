"""Lossless, bounded XML prototype interning; no network, extraction or eval.

Repeated paragraph/cell/row formatting is stored once. A requested prototype is
materialized in memory with its full original topology, text and properties.
Legacy flat prototype files remain readable for locally maintained packs.
"""
import hashlib
from pathlib import Path
import re
from lxml import etree as E
from ooxml import dump, xml, q

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_EXPANDED_NODES = 1_000_000
MAX_EXPANDED_BYTES = 64 * 1024 * 1024
MAX_DEPTH = 128
INTERN_TAGS = {q('w:' + name) for name in ('pPr', 'rPr', 'tcPr', 'trPr', 'p', 'tr', 'tc', 'tbl')}
ID = re.compile(r'f[0-9]+\Z')
KINDS = {'paragraph', 'table', 'section', 'frontMatter'}


def shell(element):
    if not isinstance(element.tag, str):
        raise ValueError('Prototype comments and processing instructions are unsupported')
    result = E.Element(element.tag, attrib=dict(element.attrib), nsmap=element.nsmap)
    result.text, result.tail = element.text, element.tail
    return result


def structural_hash(element):
    """Expanded XML identity independent of prefix spelling and serialization."""
    digest = hashlib.sha256()
    def add(value):
        data = (value or '').encode('utf-8')
        digest.update(str(len(data)).encode('ascii') + b':' + data)
    def visit(node):
        add(node.tag)
        for name, value in sorted(node.attrib.items()):
            add(name); add(value)
            # Preserve namespace meaning when prefixes appear in attribute values.
            for prefix in re.findall(r'(?<![\w-])([A-Za-z_][\w.-]*):', value):
                add(node.nsmap.get(prefix, ''))
            if name.endswith('}Ignorable'):
                for prefix in value.split(): add(node.nsmap.get(prefix, ''))
        add(None); add(node.text); add(node.tail); add(str(len(node)))
        for child in node: visit(child)
    visit(element)
    return digest.hexdigest()


def compact(root):
    if root.tag != 'prototypes' or root.xpath('.//ref | .//fragments'):
        raise ValueError('Expected an expanded prototype library')
    fragments, interned = [], {}
    def fold(node, depth=0):
        if depth > MAX_DEPTH: raise ValueError('Prototype nesting limit exceeded')
        result = shell(node)
        for child in node: result.append(fold(child, depth + 1))
        if node.tag in INTERN_TAGS:
            key_bytes = E.tostring(result, method='c14n', exclusive=True)
            if len(key_bytes) > 160:
                # Compare actual bytes as dictionary keys: no digest collision ambiguity.
                key = interned.get(key_bytes)
                if key is None:
                    key = 'f' + str(len(fragments))
                    interned[key_bytes] = key
                    fragments.append((key, result))
                return E.Element('ref', key=key)
        return result
    index = fold(root)
    store = E.Element('prototypeStore', version='2')
    library = E.SubElement(store, 'fragments')
    for key, node in fragments:
        item = E.SubElement(library, 'fragment', id=key)
        item.append(node)
    store.append(index)
    result = dump(store)
    if len(result) > MAX_FILE_BYTES:
        raise ValueError(f'Compacted prototypes size {len(result)} exceeds {MAX_FILE_BYTES}')
    return result


class PrototypeLibrary:
    def __init__(self, data):
        if len(data) > MAX_EXPANDED_BYTES:
            raise ValueError('Prototype input exceeds safety budget')
        root = xml(data)
        self.fragments, self.costs = {}, {}
        if root.tag == 'prototypeStore':
            if root.attrib != {'version': '2'} or [n.tag for n in root] != ['fragments', 'prototypes']:
                raise ValueError('Unsupported prototype store layout/version')
            if len(data) > MAX_FILE_BYTES:
                raise ValueError('Stored prototype file exceeds import budget')
            for item in root[0]:
                key = item.get('id', '')
                if item.tag != 'fragment' or item.attrib != {'id': key} or not ID.fullmatch(key) or len(item) != 1 or key in self.fragments:
                    raise ValueError('Invalid or duplicate prototype fragment')
                self.fragments[key] = item[0]
            self.index = root[1]
        elif root.tag == 'prototypes':
            self.index = root
        else:
            raise ValueError('Unsupported prototype library')
        self.entries = {}
        for entry in self.index:
            ident = entry.get('id', '')
            key = (entry.tag, ident)
            if entry.tag not in KINDS or not re.fullmatch(r'[A-Za-z0-9]+', ident) or not len(entry) or key in self.entries:
                raise ValueError('Invalid or duplicate prototype ID')
            self.entries[key] = entry
        # Check the entire graph before any output is written, including unused refs.
        for key in self.fragments: self._fragment_cost(key, set())
        self._cost(self.index, set())

    def _fragment_cost(self, key, active):
        if not isinstance(key, str) or not ID.fullmatch(key): raise ValueError('Invalid prototype reference key')
        if key in active: raise ValueError('Cyclic prototype reference')
        if key not in self.fragments: raise ValueError('Missing prototype reference: ' + key)
        if key not in self.costs:
            self.costs[key] = self._cost(self.fragments[key], active | {key})
        return self.costs[key]

    def _cost(self, node, active):
        if len(active) > MAX_DEPTH: raise ValueError('Prototype reference depth exceeded')
        if node.tag == 'ref':
            if set(node.attrib) != {'key'} or len(node) or node.text or node.tail:
                raise ValueError('Malformed prototype reference')
            return self._fragment_cost(node.get('key'), active)
        # Count serialized element payload, not standalone XML declarations and
        # inherited namespace declarations once for every child.
        weight = 16 + 2 * len(E.QName(node).localname.encode('utf-8'))
        weight += sum(len(E.QName(k).localname.encode('utf-8')) + len(v.encode('utf-8')) + 8 for k, v in node.attrib.items())
        weight += len((node.text or '').encode('utf-8')) + len((node.tail or '').encode('utf-8'))
        nodes, size, depth = 1, weight, 1
        for child in node:
            child_nodes, child_bytes, child_depth = self._cost(child, active)
            nodes += child_nodes; size += child_bytes; depth = max(depth, child_depth + 1)
            if nodes > MAX_EXPANDED_NODES or size > MAX_EXPANDED_BYTES:
                raise ValueError('Expanded prototype safety budget exceeded')
        if depth > MAX_DEPTH: raise ValueError('Expanded prototype depth exceeded')
        return nodes, size, depth

    def _expand(self, node):
        if node.tag == 'ref': return self._expand(self.fragments[node.get('key')])
        result = shell(node)
        for child in node: result.append(self._expand(child))
        return result

    def get(self, kind, ident):
        entry = self.entries.get((kind, ident))
        if entry is None: raise ValueError('source family lacks prototype: ' + ident)
        if len(entry) != 1: raise ValueError('Multi-node prototype requires explicit front-matter handling')
        return self._expand(entry[0])

    def expanded(self):
        """Maintenance/round-trip verification only; runtime uses get() on demand."""
        return self._expand(self.index)


def read_prototypes(path):
    return PrototypeLibrary(Path(path).read_bytes()).expanded()
