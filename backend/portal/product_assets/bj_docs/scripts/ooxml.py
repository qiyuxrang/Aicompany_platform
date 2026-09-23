"""Small, fail-closed OOXML package primitives. No document text is executable."""
from pathlib import Path, PurePosixPath
from zipfile import ZipFile, ZIP_DEFLATED, BadZipFile
from copy import deepcopy
from contextlib import contextmanager
import os
import tempfile
import hashlib, json, re
from lxml import etree as E

NS = {'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
      'r':'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
      's':'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
      'rel':'http://schemas.openxmlformats.org/package/2006/relationships'}
ROOT = Path(__file__).resolve().parents[1]
def q(name):
    prefix, local = name.split(':'); return '{'+NS[prefix]+'}'+local
def xml(data):
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise ValueError('DTD/entity forbidden')
    try:
        return E.fromstring(data, E.XMLParser(resolve_entities=False, no_network=True, load_dtd=False))
    except E.XMLSyntaxError as error:
        raise ValueError('INVALID_PACKAGE_XML: '+str(error)) from error
def dump(root): return E.tostring(root, encoding='UTF-8', xml_declaration=True, standalone=True)
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def jwrite(path, data):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def load_package(path):
    try:
        return read_package(path)
    except BadZipFile as error:
        raise ValueError('INVALID_PACKAGE_ZIP: '+str(error)) from error

def read_package(path):
    with ZipFile(path) as z:
        names=z.namelist()
        if len(names)!=len(set(names)) or len(names)>10000: raise ValueError('duplicate/excessive ZIP entries')
        if sum(i.file_size for i in z.infolist())>256*1024*1024: raise ValueError('package too large')
        for n in names:
            if '\\' in n or ':' in n or PurePosixPath(n).is_absolute() or '..' in PurePosixPath(n).parts:
                raise ValueError('ZIP traversal')
        data={n:z.read(n) for n in names}
        for n,b in data.items():
            if n.endswith(('.xml','.rels')): xml(b)
        return data
def check_new_outputs(path, quality=False):
    path=Path(path)
    outputs=[path, path.with_suffix('.quality.json')] if quality else [path]
    if len({p.resolve() for p in outputs})!=len(outputs):
        raise ValueError('OUTPUT_PATH_COLLISION')
    for target in outputs:
        if target.exists() or target.is_symlink(): raise ValueError('OUTPUT_EXISTS: '+str(target))
        if target.resolve().is_relative_to((ROOT/'assets').resolve()):
            raise ValueError('refusing to overwrite master assets')

@contextmanager
def staged_outputs(*paths):
    targets=[Path(path).absolute() for path in paths]
    if not targets or len({path.parent for path in targets})!=1 or len(set(targets))!=len(targets):
        raise ValueError('OUTPUT_PATH_COLLISION')
    for target in targets: check_new_outputs(target)
    targets[0].parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.bj-output-',dir=targets[0].parent) as folder:
        staged=[Path(folder)/target.name for target in targets]
        yield staged
        published=[]
        try:
            for source,target in zip(staged,targets):
                original=source.stat()
                published.append((original,target))
                try:
                    if os.name=='nt': os.rename(source,target)
                    else: os.link(source,target)
                except FileExistsError as error: raise ValueError('OUTPUT_EXISTS: '+str(target)) from error
        except BaseException:
            for original,target in reversed(published):
                if target.is_symlink() or not target.exists(): continue
                current=target.stat()
                if (os.path.samestat(original,current) and original.st_size==current.st_size
                        and original.st_mtime_ns==current.st_mtime_ns): target.unlink()
            raise

def save_package(path, parts):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp')
    with ZipFile(temp,'w',ZIP_DEFLATED) as z:
        for n,b in sorted(parts.items()): z.writestr(n,b)
    temp.replace(path)
def text(el): return ''.join(el.xpath('.//w:t/text()',namespaces=NS))
def remove(el):
    if el.getparent() is not None: el.getparent().remove(el)
def prop(el, tag):
    p=el.find(tag,NS)
    return deepcopy(p) if p is not None else None

def safe_properties(el):
    """Retain formatting, never revision payloads or embedded relationships."""
    out=deepcopy(el)
    for n in list(out.iter()):
        local=E.QName(n).localname
        if local.endswith('Change') or local in ('ins','del','moveFrom','moveTo','numPicBullet','pict','drawing','object'):
            remove(n); continue
        for a in list(n.attrib):
            if E.QName(a).localname.startswith('rsid'): del n.attrib[a]
    return out

def append_run_text(run, value):
    """Encode control characters as Word elements, not literal whitespace in w:t."""
    value=value.replace('\r\n','\n').replace('\r','\n')
    for piece in re.split(r'(\n|\t)',value):
        if piece=='\n': E.SubElement(run,q('w:br'))
        elif piece=='\t': E.SubElement(run,q('w:tab'))
        elif piece:
            t=E.SubElement(run,q('w:t')); t.set('{http://www.w3.org/XML/1998/namespace}space','preserve'); t.text=piece

def paragraph(source, value=''):
    """Clone pPr and *each* direct rPr; distribute replacement across existing runs."""
    out=E.Element(q('w:p'))
    pp=source.find('w:pPr',NS)
    if pp is not None: out.append(safe_properties(pp))
    runs=source.findall('w:r',NS)
    if not runs: runs=[E.Element(q('w:r'))]
    remaining=value.replace('\r\n','\n').replace('\r','\n')
    for i,r in enumerate(runs):
        nr=E.SubElement(out,q('w:r')); rp=r.find('w:rPr',NS)
        if rp is not None: nr.append(safe_properties(rp))
        size=len(''.join(r.xpath('w:t/text()',namespaces=NS)))
        piece=remaining if i==len(runs)-1 else remaining[:size]
        remaining=remaining[len(piece):]
        append_run_text(nr,piece)
    return out

def fixed_replace(root, mapping):
    # Treat braces in VALUES as literal text; single pass over original spans.
    for p in root.xpath('.//w:p',namespaces=NS):
        nodes=p.xpath('.//w:t',namespaces=NS); original=''.join(t.text or '' for t in nodes)
        matches=list(re.finditer(r'\{\{([a-z_]+)\}\}', original))
        if not matches: continue
        spans=[]; pos=0
        for n in nodes: spans.append((n,pos,pos+len(n.text or ''))); pos+=len(n.text or '')
        for m in reversed(matches):
            if m[1] not in mapping: raise ValueError('missing fixed placeholder: '+m[1])
            for n,a,b in reversed(spans):
                if a<m.end() and b>m.start():
                    lo=max(0,m.start()-a); hi=min(b-a,m.end()-a)
                    n.text=(n.text or '')[:lo]+(mapping[m[1]] if a<=m.start()<b else '')+(n.text or '')[hi:]

def field_instructions(root):
    """Read actual field-code phases; exclude nested cached TOC hyperlink text."""
    stack=[]; result=[]
    for el in root.iter():
        if el.tag==q('w:fldSimple'): result.append(el.get(q('w:instr'),''))
        elif el.tag==q('w:fldChar'):
            kind=el.get(q('w:fldCharType'))
            if kind=='begin': stack.append({'code':'','collecting':True})
            elif kind=='separate' and stack:
                result.append(stack[-1]['code']); stack[-1]['collecting']=False
            elif kind=='end' and stack:
                item=stack.pop()
                if item['collecting']: result.append(item['code'])
        elif el.tag==q('w:instrText') and stack and stack[-1]['collecting']:
            stack[-1]['code']+=el.text or ''
    return [s.strip() for s in result if s.strip()]

def field_paragraph(source, instruction):
    p=paragraph(source,'')
    for r in list(p.findall('w:r',NS)): p.remove(r)
    properties=source.find('w:r/w:rPr',NS)
    for kind in ('begin','instruction','separate','end'):
        r=E.SubElement(p,q('w:r'))
        if properties is not None: r.append(safe_properties(properties))
        if kind=='instruction':
            code=E.SubElement(r,q('w:instrText')); code.set('{http://www.w3.org/XML/1998/namespace}space','preserve'); code.text=' '+instruction+' '
        else: E.SubElement(r,q('w:fldChar')).set(q('w:fldCharType'),kind)
    return p

ALLOWED_FIELDS={'TOC','PAGE','NUMPAGES','REF','SEQ','PAGEREF','SECTIONPAGES','STYLEREF'}
def check_field(instruction):
    instruction=instruction.strip()
    if not instruction: raise ValueError('empty field instruction')
    name=instruction.split()[0].upper()
    if name=='HYPERLINK':
        if not re.fullmatch(r'HYPERLINK\s+\\l\s+(?:"[A-Za-z_][A-Za-z0-9_.-]*"|[A-Za-z_][A-Za-z0-9_.-]*)\s*',instruction,re.I):
            raise ValueError('only internal bookmark hyperlinks are allowed')
    elif name not in ALLOWED_FIELDS: raise ValueError('unsafe field: '+instruction)

def audit_package(parts, kind='word'):
    for name,b in parts.items():
        low=name.lower()
        if any(s in low for s in ('vbaproject','embeddings/','externallink','connections.xml','customxml/','comments','activex/')):
            raise ValueError('unsafe package part: '+name)
        if not name.endswith(('.xml','.rels')): continue
        root=xml(b)
        if name.endswith('.rels'):
            for rel in root:
                if rel.get('TargetMode')=='External': raise ValueError('external relationship')
                target=rel.get('Target','')
                if re.match(r'^[a-z]+:',target,re.I) or '\\' in target: raise ValueError('unsafe relationship target')
        if kind=='sheet':
            from formulas import validate_formula
            for formula in root.iter(q('s:f')):
                if formula.attrib: raise ValueError('unsupported shared/array formula; normalize before rendering')
                validate_formula(formula.text)
            for formula in root.iter(q('s:definedName')):
                if formula.get('name') not in ('_xlnm.Print_Area','_xlnm.Print_Titles'):
                    raise ValueError('unsupported workbook defined name')
        if kind=='word':
            # Fields may be split across runs. Inspect complete fields, not first fragment only.
            stack=[]
            for el in root.iter():
                if el.tag==q('w:fldChar'):
                    k=el.get(q('w:fldCharType'))
                    if k=='begin': stack.append('')
                    elif k=='end':
                        if not stack: raise ValueError('unbalanced field')
                        instruction=stack.pop().strip()
                        check_field(instruction)
                elif el.tag==q('w:instrText'):
                    if not stack: raise ValueError('orphan field instruction')
                    stack[-1]+=el.text or ''
                elif el.tag==q('w:fldSimple'):
                    instruction=el.get(q('w:instr'),'').strip()
                    check_field(instruction)
                elif E.QName(el).localname in ('altChunk','object','oleObject','attachedTemplate'):
                    raise ValueError('unsafe active content')
            if stack: raise ValueError('unclosed field')
    return True

def prune_parts(parts, allowed):
    kept={n:b for n,b in parts.items() if allowed(n)}
    # Rebuild relationship lists and content types, retaining original relationship IDs.
    import posixpath
    for n in list(kept):
        if n.endswith('.rels'):
            r=xml(kept[n]); parent='' if n=='_rels/.rels' else n.split('/_rels/')[0]
            for rel in list(r):
                target=posixpath.normpath(posixpath.join(parent,rel.get('Target','').lstrip('/')))
                if rel.get('Target','').startswith('/'): target=rel.get('Target')[1:]
                if rel.get('TargetMode')=='External' or target not in kept: r.remove(rel)
            kept[n]=dump(r)
    ct=xml(kept['[Content_Types].xml'])
    for el in list(ct):
        if el.get('PartName') and el.get('PartName').lstrip('/') not in kept: ct.remove(el)
    kept['[Content_Types].xml']=dump(ct)
    return kept
