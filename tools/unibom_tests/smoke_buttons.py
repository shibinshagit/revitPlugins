# -*- coding: utf-8 -*-
"""Smoke test: run the UNIBOM button scripts outside Revit.

pyRevit and the Revit API are replaced by stand-ins, and the model by the
ZUHA fixture. Catches runtime errors in the scripts, report and CSV code.

    python3 tools/unibom_tests/smoke_buttons.py Takeoff.panel/RunBOQ.pushbutton/script.py both
    python3 tools/unibom_tests/smoke_buttons.py Setup.panel/CheckModel.pushbutton/script.py
"""
import sys, types, json, runpy, os, tempfile
HERE=os.path.dirname(os.path.abspath(__file__))
EXT=os.path.join(HERE,'..','..','Uniqube.extension')
sys.path.insert(0, EXT+'/lib')
D=json.load(open(os.path.join(HERE,'fixtures','zuha_26-005_records.json'),encoding='utf-8-sig'))
# --- stubs
class EID:
    def __init__(s,i): s.Value=i
class Out:
    html=[]
    def set_title(s,t): pass
    def set_width(s,w): pass
    def print_html(s,h): Out.html.append(h)
    def linkify(s,ids,title=None): return '<a href="#">%s</a>'%title
class Sel: element_ids=[EID(423435)]
class View: Name='coordination'; Id=1
class Doc: Title='UAE_26-005_ADMIN BUILDING & GSM ROOM_TEST'
revit=types.SimpleNamespace(doc=Doc(), uidoc=None, get_selection=lambda: Sel(), active_view=View())
script=types.SimpleNamespace(get_output=lambda: Out(), exit=lambda: sys.exit(0))
class PB:
    def __init__(s,**k): pass
    def __enter__(s): return s
    def __exit__(s,*a): return False
class WPFWindow:
    def __init__(s,x): assert os.path.isfile(x), x
forms=types.SimpleNamespace(ProgressBar=PB, WPFWindow=WPFWindow, pick_folder=lambda **k: None)
pyrevit=types.ModuleType('pyrevit'); pyrevit.forms=forms; pyrevit.revit=revit; pyrevit.script=script
sys.modules['pyrevit']=pyrevit
DB=types.ModuleType('Autodesk.Revit.DB'); DB.ElementId=EID
for n in ['Autodesk','Autodesk.Revit']: sys.modules[n]=types.ModuleType(n)
sys.modules['Autodesk.Revit.DB']=DB
coll=types.ModuleType('unibom.collect')
coll.eid=lambda e: e.Value
coll.collect=lambda doc, scope, view=None, selected_ids=None: (D['members'], D['walls'])
sys.modules['unibom.collect']=coll
import unibom; unibom.collect=coll
from unibom.ui import dialogs
method=sys.argv[2] if len(sys.argv)>2 else 'both'
OUTDIR=tempfile.mkdtemp(prefix='unibom_')
dialogs.ask_run_boq=lambda *a: dialogs.RunBoqOptions('model', method, True, True, OUTDIR)
runpy.run_path(EXT+'/UNIBOM.tab/'+sys.argv[1], run_name='__main__')
preview=os.path.join(OUTDIR,'output_window.html')
open(preview,'w',encoding='utf-8').write('<html><head><meta charset="utf-8"></head><body>'+Out.html[0]+'</body></html>')
print('ok', OUTDIR, sorted(os.listdir(OUTDIR)))
