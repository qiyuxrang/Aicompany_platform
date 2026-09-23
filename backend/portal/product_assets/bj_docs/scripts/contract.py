"""Strict shared content contract and engineering reference integrity (not truth approval)."""
import re
from strict_json import read_bytes, read_json
from pathlib import Path
from ooxml import ROOT

def obj(value,required,optional=()):
    if not isinstance(value,dict): raise ValueError('expected object')
    if set(value)-set(required)-set(optional): raise ValueError('unknown keys: '+str(set(value)-set(required)-set(optional)))
    if set(required)-set(value): raise ValueError('missing keys: '+str(set(required)-set(value)))
def string(v,limit=100000):
    if not isinstance(v,str) or not v.strip() or len(v)>limit: raise ValueError('nonempty bounded string required')
    if any(ord(c)<32 and c not in '\n\t\r' for c in v): raise ValueError('control characters forbidden')
def array(v):
    if not isinstance(v,list): raise ValueError('expected array')
def validate(data):
    obj(data,('version','family','metadata','sources','requirements','pending','blocks'))
    if data['version']!=1: raise ValueError('unsupported contract version')
    if data['family'] not in ('technical-solution','feasibility','construction-plan','product-specification','disclosure-form','detailed-design'): raise ValueError('unknown family')
    obj(data['metadata'],('id','title','subtitle','date','organization','status'),('owner',))
    for k,v in data['metadata'].items(): string(v,200)
    if data['metadata']['status'] not in ('synthetic','draft','reviewed'): raise ValueError('invalid status; reviewed is a declaration, not proof')
    all_ids=set()
    def identifier(v):
        string(v,80)
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]*',v) or v in all_ids: raise ValueError('invalid or duplicate stable id: '+v)
        all_ids.add(v)
    identifier(data['metadata']['id'])
    for group in ('sources','requirements','pending'):
        array(data[group])
        for x in data[group]:
            obj(x,('id','text')); identifier(x['id']); string(x['text'])
    source_ids={s['id'] for s in data['sources']}; req_ids={s['id'] for s in data['requirements']}
    array(data['blocks'])
    if not data['blocks']: raise ValueError('empty document')
    prevlevel=0; heading_content=True
    for b in data['blocks']:
        if not isinstance(b,dict) or 'type' not in b: raise ValueError('block type required')
        common=('id','type','source_ids','requirement_ids')
        fields={'heading':('text','level'),'paragraph':('text',),'runs':('runs',),'list':('items',),'table':('prototype','columns','units','rows','caption'),'figure':('path','caption','width_mm','alt'),'section':('prototype',),'form':('prototype','cells','caption')}
        if b['type'] not in fields: raise ValueError('unsupported block: '+str(b['type']))
        obj(b,common+fields[b['type']]); identifier(b['id'])
        for key,known in (('source_ids',source_ids),('requirement_ids',req_ids)):
            array(b[key]);
            if any(not isinstance(v,str) for v in b[key]) or set(b[key])-known: raise ValueError('dangling '+key)
        typ=b['type']
        if typ=='heading':
            string(b['text'],160)
            if type(b['level']) is not int or not 1<=b['level']<=3 or b['level']>prevlevel+1: raise ValueError('invalid heading hierarchy')
            if not heading_content and b['level']<=prevlevel: raise ValueError('empty heading section')
            prevlevel=b['level']; heading_content=False
        elif typ!='section': heading_content=True
        if typ in ('paragraph',): string(b['text'])
        if typ=='runs':
            array(b['runs'])
            if not b['runs']: raise ValueError('empty runs')
            for r in b['runs']:
                obj(r,('text',),('bold','italic')); string(r['text'])
                for k in ('bold','italic'):
                    if k in r and type(r[k]) is not bool: raise ValueError('run flag must be boolean')
        if typ=='list':
            array(b['items'])
            if not b['items']: raise ValueError('empty list')
            for i in b['items']: string(i)
        if typ=='table':
            string(b['prototype']); string(b['caption'],240)
            for k in ('columns','units','rows'): array(b[k])
            if not b['columns'] or not b['rows'] or len(b['units'])!=len(b['columns']): raise ValueError('table shape/units required')
            for v in b['columns']+b['units']: string(v,100)
            for row in b['rows']:
                array(row)
                if len(row)!=len(b['columns']): raise ValueError('ragged table')
                for v in row: string(v,10000)
        if typ=='form':
            string(b['prototype']); string(b['caption'],240); array(b['cells'])
            seen_cells=set()
            if not b['cells']: raise ValueError('empty form')
            for cell in b['cells']:
                obj(cell,('row','cell','paragraphs'))
                key=(cell['row'],cell['cell'])
                if any(type(v) is not int or v<0 for v in key) or key in seen_cells: raise ValueError('invalid or duplicate form coordinate')
                seen_cells.add(key); array(cell['paragraphs'])
                if not cell['paragraphs']: raise ValueError('form paragraphs required')
                for value in cell['paragraphs']: string(value,10000)
        if typ=='figure':
            for k in ('path','caption','alt'): string(b[k],400)
            if type(b['width_mm']) not in (int,float) or not 10<=b['width_mm']<=250: raise ValueError('figure width')
            p=Path(b['path'])
            if p.is_absolute() or '..' in p.parts or ':' in b['path'] or '\\' in b['path']: raise ValueError('figure must be content-relative')
            if p.suffix.lower() not in ('.png','.jpg','.jpeg'): raise ValueError('raster figure required')
        if typ=='section': string(b['prototype'])
    if not heading_content: raise ValueError('empty final section')
    return data

def read_content(path): return validate(read_json(read_bytes(path)))
