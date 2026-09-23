# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["lxml==6.0.2", "python-docx==1.2.0", "python-pptx==1.0.2", "PyMuPDF==1.26.4", "pywin32==311; sys_platform == 'win32'"]
# ///
"""Installed Skill entry: typed JSON -> source-derived DOCX plus MD, HTML or PPTX."""
import argparse, json, re, html
from copy import deepcopy
from pathlib import Path
from lxml import etree as E
from ooxml import *
from contract import read_content
from prototypes import PrototypeLibrary

def bookmark_name(identifier):
    if re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,39}',identifier): return identifier
    return 'BJ_'+hashlib.sha256(identifier.encode('utf-8')).hexdigest()[:32]

def chinese_number(value):
    if not 1<=value<10000: raise ValueError('Chinese heading number outside supported range')
    digits='零一二三四五六七八九'; units=['','十','百','千']; out=''; zero=False
    for index,digit in enumerate(str(value)):
        n=int(digit); position=len(str(value))-index-1
        if n:
            if zero: out+='零'
            out+=digits[n]+units[position]; zero=False
        elif out: zero=True
    return out[1:] if out.startswith('一十') else out

def trusted_pack(family):
    pack=ROOT/'assets'/family; profile=json.loads((pack/'profile.json').read_text(encoding='utf-8'))
    for name,key in [('template.docx','template_sha256'),('prototypes.xml','prototypes_sha256')]:
        if sha(pack/name)!=profile[key]: raise ValueError('pack integrity mismatch: '+name)
    return pack,profile

def word(data,out,content_dir):
    check_new_outputs(out, quality=True)
    with staged_outputs(out,Path(out).with_suffix('.quality.json')) as (staged,quality):
        report=_write_word(data,staged,content_dir)
    return report

def _write_word(data,out,content_dir):
    out=Path(out).resolve()
    if out.is_relative_to(ROOT/'assets'): raise ValueError('refusing to overwrite master assets')
    from contract import validate
    validate(data)
    pack,profile=trusted_pack(data['family']); parts=load_package(pack/'template.docx'); audit_package(parts)
    root=xml(parts['word/document.xml']); body=root.find('w:body',NS); proto=PrototypeLibrary((pack/'prototypes.xml').read_bytes())
    marker=next(p for p in body.findall('w:p',NS) if text(p)=='{{body}}')
    body.remove(marker); insertion=len(body)-1
    metadata={'owner':'待确认',**data['metadata']}
    fixed_replace(root,metadata)
    for name in list(parts):
        if re.fullmatch(r'word/(header|footer)\d+\.xml',name):
            story=xml(parts[name]); fixed_replace(story,metadata); parts[name]=dump(story)
    def prototype(kind,key):
        return proto.get(kind,key)
    emitted=[]; figures=[]; bookmark_map={}; counters=[0,0,0]; current_section=body.find('w:sectPr',NS)
    def available_width():
        sz=current_section.find('w:pgSz',NS); mar=current_section.find('w:pgMar',NS)
        return int(sz.get(q('w:w')))-int(mar.get(q('w:left')))-int(mar.get(q('w:right')))-int(mar.get(q('w:gutter'),'0'))
    def ptype(key,txt):
        p=paragraph(prototype('paragraph',key),txt)
        return p
    def bookmark(p,ident):
        # Stable content IDs in document bookmarks; no user-provided OOXML.
        name=bookmark_name(ident); bookmark_map[ident]=name
        i=str(len(emitted)+1); start=E.Element(q('w:bookmarkStart')); start.set(q('w:id'),i); start.set(q('w:name'),name)
        end=E.Element(q('w:bookmarkEnd')); end.set(q('w:id'),i); p.insert(1 if p.find('w:pPr',NS) is not None else 0,start); p.append(end)
    for b in data['blocks']:
        typ=b['type']; nodes=[]
        if typ in ('heading','paragraph','runs','list'):
            key='heading'+str(b['level']) if typ=='heading' else ('list' if typ=='list' else 'paragraph')
            base=prototype('paragraph',key)
            if typ=='heading' and profile['paragraphs'][key]['automatic_numbering'] and re.match(r'^(第[一二三四五六七八九十0-9]+[章节]|\d+[.、])',b['text']):
                raise ValueError('heading has automatic numbering; omit textual number')
            if typ=='runs':
                p=paragraph(base,'')
                for r in list(p.findall('w:r',NS)): p.remove(r)
                br=base.find('w:r/w:rPr',NS)
                for item in b['runs']:
                    r=E.SubElement(p,q('w:r')); rp=deepcopy(br) if br is not None else E.Element(q('w:rPr'))
                    for k,tag in [('bold','w:b'),('italic','w:i')]:
                        if k in item:
                            for x in rp.findall(tag,NS): rp.remove(x)
                            E.SubElement(rp,q(tag)).set(q('w:val'),'1' if item[k] else '0')
                    r.append(rp); append_run_text(r,item['text'])
                nodes=[p]
            elif typ=='list':
                auto=profile['paragraphs'][key]['automatic_numbering']
                nodes=[paragraph(base,('' if auto else f'（{i+1}）')+t) for i,t in enumerate(b['items'])]
            else:
                value=b['text']
                if typ=='heading':
                    level=b['level']; counters[level-1]+=1
                    for lower in range(level,3): counters[lower]=0
                    mode=profile['paragraphs'][key]['manual_numbering']
                    if mode:
                        if re.match(r'^(第[一二三四五六七八九十0-9]+章|[一二三四五六七八九十]+、|\d+\.)',value): raise ValueError('manual source numbering is generated; omit duplicate prefix')
                        number=counters[level-1]
                        cn=chinese_number
                        prefix=('第'+cn(number)+'章 ' if mode=='chapter' else cn(number)+'、' if mode=='chinese' else '.'.join(str(n) for n in counters[:level])+' ')
                        value=prefix+value
                nodes=[paragraph(base,value)]
        elif typ=='table':
            t=prototype('table',b['prototype']); tp=profile['tables'][b['prototype']]
            if not tp['variable_rows']: raise ValueError('complex merged form requires fixed topology adapter; expansion refused')
            if len(b['columns'])!=tp['columns']: raise ValueError('table columns differ from actual prototype')
            if tp['width_twips']>available_width()+120: raise ValueError('table width exceeds section; choose matching source section/prototype')
            rows=t.findall('w:tr',NS); header=rows[0]; sample=rows[1] if len(rows)>1 else rows[0]; final_row=rows[-1]
            for row in rows: t.remove(row)
            for i,values in enumerate([b['columns']]+b['rows']):
                row=deepcopy(header if i==0 else final_row if i==len(b['rows']) else sample)
                pr=row.find('w:trPr',NS)
                if pr is None: pr=E.Element(q('w:trPr')); row.insert(0,pr)
                if i==0 and pr.find('w:tblHeader',NS) is None: E.SubElement(pr,q('w:tblHeader'))
                for cell,value in zip(row.findall('w:tc',NS),values):
                    cp=cell.find('w:p',NS)
                    for p in list(cell.findall('w:p',NS)): cell.remove(p)
                    cell.append(paragraph(cp,value))
                t.append(row)
            units=[name+'：'+unit for name,unit in zip(b['columns'],b['units']) if unit not in ('不适用','无','—','-')]
            caption=ptype('caption',b['caption']+('（单位：'+'；'.join(units)+'）' if units else '')); nodes=[caption,t]
        elif typ=='form':
            t=prototype('table',b['prototype']); rows=t.findall('w:tr',NS)
            for entry in b['cells']:
                if entry['row']>=len(rows): raise ValueError('form row outside source topology')
                cells=rows[entry['row']].findall('w:tc',NS)
                if entry['cell']>=len(cells): raise ValueError('form cell outside source topology')
                cell=cells[entry['cell']]; vm=cell.find('w:tcPr/w:vMerge',NS)
                if vm is not None and vm.get(q('w:val'),'continue')!='restart': raise ValueError('cannot write vertical-merge continuation cell')
                originals=cell.findall('w:p',NS)
                for p in originals: cell.remove(p)
                base=originals[0] if originals else E.Element(q('w:p'))
                for i,value in enumerate(entry['paragraphs']): cell.append(paragraph(originals[min(i,len(originals)-1)] if originals else base,value))
            nodes=[ptype('caption',b['caption']),t]
        elif typ=='figure':
            p=ptype('figure',''); nodes=[p,ptype('caption',b['caption'])]
            path=(Path(content_dir)/b['path']).resolve()
            if not path.is_relative_to(Path(content_dir).resolve()) or not path.is_file(): raise ValueError('figure not found within content directory')
            if b['width_mm']*1440/25.4>available_width(): raise ValueError('figure overflow')
            figures.append((b['id'],path,b['width_mm'],b['alt']))
        elif typ=='section':
            next_section=prototype('section',b['prototype'])
            # End the preceding section using its own geometry, then switch the terminal section.
            p=ptype('paragraph',''); pp=p.find('w:pPr',NS)
            if pp is None: pp=E.SubElement(p,q('w:pPr'))
            pp.append(deepcopy(current_section)); nodes=[p]
            body.remove(current_section); current_section=next_section; body.append(current_section)
        if typ=='heading' and b['level']==1:
            pp=nodes[0].find('w:pPr',NS)
            if pp is None: pp=E.Element(q('w:pPr')); nodes[0].insert(0,pp)
            flag=pp.find('w:pageBreakBefore',NS)
            if flag is None:
                flag=E.Element(q('w:pageBreakBefore'))
                preceding={q('w:pStyle'),q('w:keepNext'),q('w:keepLines')}
                pp.insert(sum(child.tag in preceding for child in pp),flag)
            flag.set(q('w:val'),'1')
        for p in nodes:
            if p.tag==q('w:p'): bookmark(p,b['id']); break
        emitted.extend(nodes)
    # Pending remains in source JSON; material limitations belong in authored prose.
    if emitted and emitted[-1].tag==q('w:tbl'):
        # Word requires a closing paragraph after a final table; an inherited
        # large empty paragraph otherwise creates a spurious blank last page.
        tail=E.Element(q('w:p')); pp=E.SubElement(tail,q('w:pPr'))
        sp=E.SubElement(pp,q('w:spacing'))
        for key,value in [('before','0'),('after','0'),('line','20'),('lineRule','exact')]: sp.set(q('w:'+key),value)
        rp=E.SubElement(pp,q('w:rPr')); E.SubElement(rp,q('w:sz')).set(q('w:val'),'2')
        emitted.append(tail)
    for i,node in enumerate(emitted): body.insert(insertion+i,node)
    parts['word/document.xml']=dump(root); audit_package(parts); save_package(out,parts)
    if figures:
        from docx import Document
        from docx.shared import Mm
        doc=Document(out)
        for ident,path,width,alt in figures:
            node=next(p for p in doc.element.body.findall(q('w:p')) if any(x.get(q('w:name'))==bookmark_name(ident) for x in p.findall(q('w:bookmarkStart'))))
            from docx.text.paragraph import Paragraph
            p=Paragraph(node,doc._body); shape=p.add_run().add_picture(str(path),width=Mm(width)); shape._inline.docPr.set('descr',alt)
        doc.save(out)
    audit_package(load_package(out))
    report={'structural_pass':True,'rendered':False,'visually_reviewed':False,'unverified_items':['Office pagination/TOC refresh and visual inspection required','Engineering assertions require independent professional review'],'family':data['family'],'source_sha256':profile['source_sha256'],'output_sha256':sha(out),'block_ids':[b['id'] for b in data['blocks']],'bookmark_map':bookmark_map}
    from handoff import encoded
    report['content_sha256']=hashlib.sha256(encoded(data)).hexdigest()
    report['content_hash_method']='sha256-normalized-content-v1'
    jwrite(Path(out).with_suffix('.quality.json'),report); return report

def prose(b):
    t=b['type']
    if t in ('heading','paragraph'): return b['text']
    if t=='runs': return ''.join(r['text'] for r in b['runs'])
    if t=='list': return '\n'.join(b['items'])
    if t=='table': return b['caption']+'\n'+' | '.join(b['columns'])+'\n'+'\n'.join(' | '.join(r) for r in b['rows'])
    if t=='form': return b['caption']+'\n'+'\n'.join(f'[{c["row"]},{c["cell"]}] '+ '\n'.join(c['paragraphs']) for c in b['cells'])
    if t=='figure': return b['caption']+' ['+b['alt']+']'
    return ''
def markdown(data,out):
    check_new_outputs(out)
    lines=['# '+data['metadata']['title'],data['metadata']['subtitle'],'状态：'+data['metadata']['status']]
    for b in data['blocks']:
        lines+=['',f'<!-- {b["id"]}; sources={",".join(b["source_ids"])}; requirements={",".join(b["requirement_ids"])} -->',('#'*(b['level']+1)+' ' if b['type']=='heading' else '')+prose(b)]
    lines+=['','## 待确认事项']+[p['id']+' '+p['text'] for p in data['pending']]
    Path(out).write_text('\n'.join(lines),encoding='utf-8')
def html_demo(data,out):
    check_new_outputs(out)
    css=(ROOT/'assets/theme.css').read_text(encoding='utf-8'); esc=html.escape
    blocks=[]
    for b in data['blocks']:
        ident=esc(b['id']); typ=b['type']; s=''
        if typ=='heading': s=f'<h{b["level"]+1}>{esc(b["text"])}</h{b["level"]+1}>'
        elif typ=='table': s='<p>'+esc(b['caption'])+'</p><table><thead><tr>'+''.join('<th>'+esc(v)+'</th>' for v in b['columns'])+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+esc(v)+'</td>' for v in r)+'</tr>' for r in b['rows'])+'</tbody></table>'
        else: s='<p>'+esc(prose(b)).replace('\n','<br>')+'</p>'
        blocks.append(f'<section id="{ident}" data-sources="{esc(",".join(b["source_ids"]))}">{s}</section>')
    Path(out).write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'"><title>'+esc(data['metadata']['title'])+'</title><style>'+css+'</style><main><h1>'+esc(data['metadata']['title'])+'</h1><p>'+esc(data['metadata']['subtitle'])+'</p>'+''.join(blocks)+'<h2>待确认事项</h2>'+''.join('<p>'+esc(p['id']+' '+p['text'])+'</p>' for p in data['pending'])+'</main></html>',encoding='utf-8')
def slides(data,out):
    from presentations import render
    render(data,out)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('format',choices=['docx','md','html','pptx','validate']); ap.add_argument('content'); ap.add_argument('output',nargs='?')
    a=ap.parse_args(); data=read_content(a.content)
    if a.format=='validate': print('VALID'); return
    if not a.output: ap.error('output required')
    out=Path(a.output).resolve(); out.parent.mkdir(parents=True,exist_ok=True)
    if out==Path(a.content).resolve() or out.is_relative_to(ROOT/'assets'): raise ValueError('refusing to overwrite input or assets')
    if a.format=='docx': word(data,out,Path(a.content).resolve().parent)
    elif a.format=='md': markdown(data,out)
    elif a.format=='html': html_demo(data,out)
    else: slides(data,out)
if __name__=='__main__': main()
