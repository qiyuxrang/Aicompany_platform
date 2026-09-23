"""Isolated Office automation. Never attach to or terminate a user's Office instance."""
import argparse,json,sys,shutil
from pathlib import Path
from ooxml import load_package,audit_package,sha,jwrite,xml,field_instructions

def new_render_directory(out):
    out=Path(out)
    if out.exists() or out.is_symlink(): raise ValueError('OUTPUT_EXISTS: use a new render directory')
    out=out.resolve(); out.mkdir(parents=True,exist_ok=False)
    return out

def word_render(source,out,legacy=False):
    out=new_render_directory(out)
    import win32com.client
    source=Path(source).resolve()
    before=sha(source); app=None; doc=None; options={}; expected_toc=0
    if not legacy:
        source_parts=load_package(source); audit_package(source_parts)
        expected_toc=sum(code.split()[0].upper()=='TOC' for code in field_instructions(xml(source_parts['word/document.xml'])))
    # Legacy is opened read-only with macros/link updates disabled. It cannot be XML-audited before conversion.
    if legacy and source.read_bytes()[:8]!=bytes.fromhex('D0CF11E0A1B11AE1'): raise ValueError('not a binary OLE .doc')
    report={'structural_pass':not legacy,'rendered':False,'visually_reviewed':False,'unverified_items':[],'input_sha256':before}
    try:
        print('WORD_DISPATCHEX_START',flush=True)
        app=win32com.client.DispatchEx('Word.Application')
        print('WORD_INSTANCE_READY',flush=True)
        app.Visible=False; app.AutomationSecurity=3; app.DisplayAlerts=0
        for name in ('UpdateLinksAtOpen','UpdateFieldsAtPrint','UpdateLinksAtPrint'):
            options[name]=getattr(app.Options,name); setattr(app.Options,name,False)
        # Always open a private disposable copy; preserve caller's DOCX and source byte-for-byte.
        copy=out/('input.doc' if legacy else 'reviewed.docx')
        if copy==source: raise ValueError('render directory would overwrite source')
        shutil.copy2(source,copy)
        doc=app.Documents.Open(str(copy),ConfirmConversions=False,ReadOnly=legacy,AddToRecentFiles=False)
        if legacy:
            converted=out/'converted.docx'; doc.SaveAs2(str(converted),FileFormat=16); report['converted']=str(converted)
            # Safety before updating fields. No legacy field is evaluated by us.
            doc.Close(False); doc=None
            audit_package(load_package(converted)); report['unverified_items'].append('Legacy converted; source images/facts remain PRIVATE in conversion copy.')
            return report
        doc.Repaginate(); actual_toc=doc.TablesOfContents.Count
        if actual_toc!=expected_toc: raise ValueError(f'Word TOC count {actual_toc} differs from XML intent {expected_toc}')
        for i in range(1,actual_toc+1): doc.TablesOfContents(i).Update()
        field_error=doc.Fields.Update()
        if field_error: raise ValueError('Word field update error at index '+str(field_error))
        for story in doc.StoryRanges:
            current=story
            while current is not None:
                error=current.Fields.Update()
                if error: raise ValueError('Word story field update error at index '+str(error))
                current=current.NextStoryRange
        doc.Repaginate()
        for i in range(1,actual_toc+1):
            toc=doc.TablesOfContents(i); toc.UpdatePageNumbers()
            if any(s in toc.Range.Text for s in ('未找到目录项','未找到目录','No table of contents entries found','Error!','错误！')): raise ValueError('Word TOC contains an unresolved field result')
        report.update(toc_count=actual_toc,field_update_error_index=field_error)
        doc.Save(); pdf=out/'document.pdf'; doc.ExportAsFixedFormat(str(pdf),17)
        report.update(rendered=True,renderer='Microsoft Word '+app.Version,page_count=doc.ComputeStatistics(2),pdf=str(pdf),rendered_docx=str(copy))
    finally:
        try:
            if doc is not None: doc.Close(False)
        finally:
            if app is not None:
                try:
                    for name,value in options.items(): setattr(app.Options,name,value)
                finally: app.Quit()
            if sha(source)!=before: raise RuntimeError('source mutated')
    import fitz
    with fitz.open(report['pdf']) as pdf:
        images=[]
        for i,page in enumerate(pdf):
            dest=out/f'page-{i+1:03}.png'; page.get_pixmap(matrix=fitz.Matrix(1.25,1.25)).save(dest); images.append(str(dest))
        report['images']=images
        report['rendered_docx_sha256']=sha(report['rendered_docx']); report['pdf_sha256']=sha(report['pdf'])
    report['unverified_items']=['Open every page image and record visual review separately','Engineering facts not verified by renderer']
    return report

def formula_results(app, worksheets):
    errors=[]; values={}
    for sheet in worksheets:
        values[sheet.Name]={}
        # Iterate explicitly: SpecialCells conflates no matches with arbitrary COM failures.
        for cell in sheet.UsedRange:
            if not cell.HasFormula: continue
            value=cell.Text; values[sheet.Name][cell.Address]=value
            if app.WorksheetFunction.IsError(cell):
                errors.append([sheet.Name,cell.Address,value])
    return errors,values

def excel_render(source,out):
    out=new_render_directory(out)
    import win32com.client
    source=Path(source).resolve()
    audit_package(load_package(source),'sheet'); before=sha(source); app=None; wb=None
    try:
        print('EXCEL_DISPATCHEX_START',flush=True)
        app=win32com.client.DispatchEx('Excel.Application'); app.Visible=False; app.AutomationSecurity=3; app.DisplayAlerts=False; app.AskToUpdateLinks=False; app.EnableEvents=False
        copy=out/'calculated.xlsx'; shutil.copy2(source,copy)
        wb=app.Workbooks.Open(str(copy),UpdateLinks=0,ReadOnly=False,IgnoreReadOnlyRecommended=True,AddToMru=False)
        app.CalculateFullRebuild()
        errors,values=formula_results(app,wb.Worksheets)
        wb.Save(); pdf=out/'workbook.pdf'; wb.ExportAsFixedFormat(0,str(pdf))
        result={'structural_pass':not errors,'rendered':True,'visually_reviewed':False,'formula_errors':errors,'calculated_values':values,'pdf':str(pdf),'calculated_xlsx':str(copy),'renderer':'Microsoft Excel '+app.Version,'unverified_items':['visual review pending','engineering quantities/prices not approved']}
    finally:
        try:
            if wb is not None: wb.Close(False)
        finally:
            if app is not None: app.Quit()
            if sha(source)!=before: raise RuntimeError('source mutated')
    import fitz
    with fitz.open(result['pdf']) as pdf:
        result['page_count']=len(pdf); result['images']=[]
        for i,p in enumerate(pdf):
            dest=out/f'page-{i+1:03}.png'; p.get_pixmap(matrix=fitz.Matrix(1.25,1.25)).save(dest); result['images'].append(str(dest))
    result['calculated_xlsx_sha256']=sha(result['calculated_xlsx']); result['pdf_sha256']=sha(result['pdf'])
    return result

def powerpoint_render(source,out):
    out=new_render_directory(out)
    import win32com.client
    source=Path(source).resolve()
    audit_package(load_package(source),'presentation'); before=sha(source); app=None; presentation=None; owned=False
    try:
        app=win32com.client.DispatchEx('PowerPoint.Application')
        if app.Presentations.Count: raise RuntimeError('PowerPoint returned an instance with existing presentations; refusing to modify or close it')
        owned=True; app.AutomationSecurity=3
        presentation=app.Presentations.Open(str(source),ReadOnly=True,Untitled=True,WithWindow=False)
        # SaveAs PDF avoids pywin32's optional PrintRange COM marshalling ambiguity.
        pdf=out/'slides.pdf'; presentation.SaveAs(str(pdf),32,0)
        result={'structural_pass':True,'rendered':True,'visually_reviewed':False,'renderer':'Microsoft PowerPoint '+app.Version,'pdf':str(pdf),'input_sha256':before,'unverified_items':['Derived theme, not a supplied company PPT master','Visual page review pending']}
    finally:
        try:
            if presentation is not None: presentation.Close()
        finally:
            if app is not None and owned: app.Quit()
            if sha(source)!=before: raise RuntimeError('source mutated')
    import fitz
    with fitz.open(result['pdf']) as pdf:
        result['page_count']=len(pdf); result['images']=[]
        for i,page in enumerate(pdf):
            dest=out/f'page-{i+1:03}.png'; page.get_pixmap(matrix=fitz.Matrix(1.25,1.25)).save(dest); result['images'].append(str(dest))
    return result

def worker_main():
    ap=argparse.ArgumentParser(); ap.add_argument('kind',choices=['word','legacy','excel','powerpoint']); ap.add_argument('source'); ap.add_argument('out'); a=ap.parse_args()
    if Path(a.out).exists() or Path(a.out).is_symlink():
        print('OUTPUT_EXISTS: use a new render directory',file=sys.stderr); sys.exit(2)
    try:
        report=excel_render(a.source,a.out) if a.kind=='excel' else powerpoint_render(a.source,a.out) if a.kind=='powerpoint' else word_render(a.source,a.out,a.kind=='legacy')
    except Exception as e:
        report={'structural_pass':False,'rendered':False,'visually_reviewed':False,'unverified_items':[type(e).__name__+': '+str(e)]}
        jwrite(Path(a.out)/'render.json',report); print(json.dumps(report,ensure_ascii=False)); sys.exit(2)
    jwrite(Path(a.out)/'render.json',report); print(json.dumps(report,ensure_ascii=False))
    if report.get('structural_pass') is False and a.kind!='legacy': sys.exit(2)
def main():
    # Bound a stuck COM activation. Terminate only this child interpreter, NEVER WINWORD/EXCEL.
    import subprocess
    args=sys.argv[1:]
    if args and args[0]=='--worker':
        sys.argv=[sys.argv[0]]+args[1:]; worker_main(); return
    ap=argparse.ArgumentParser(); ap.add_argument('kind',choices=['word','legacy','excel','powerpoint']); ap.add_argument('source'); ap.add_argument('out'); ap.add_argument('--timeout',type=int,default=90); a=ap.parse_args()
    if not 10<=a.timeout<=1200: ap.error('timeout 10..1200 seconds')
    try:
        r=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--worker',a.kind,str(Path(a.source).resolve()),str(Path(a.out).resolve())],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=a.timeout)
        print(r.stdout); print(r.stderr,file=sys.stderr); sys.exit(r.returncode)
    except subprocess.TimeoutExpired as e:
        log=e.stdout.decode('utf-8',errors='replace') if isinstance(e.stdout,bytes) else (e.stdout or '')
        report={'structural_pass':False,'rendered':False,'visually_reviewed':False,'unverified_items':['Office COM timed out after '+str(a.timeout)+' seconds','Office cleanup unverified if activation never returned; no Office processes killed'],'checkpoint':log,'source_sha256':sha(a.source)}
        jwrite(Path(a.out)/'render.json',report); print(json.dumps(report,ensure_ascii=False)); sys.exit(2)
if __name__=='__main__': main()
