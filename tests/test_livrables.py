import io
from pathlib import Path
import re
import shutil
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import formats, inspection, verification
from server import Atelier

SOURCE='Rapport annuel de l’institut. '*80+' Le chômage atteint 7,3 % en 2024. Le budget est de 1 234 millions d’euros. Population : 68,4 millions. « La transition énergétique exige des investissements massifs et immédiats » a déclaré la ministre.'
FRENCH='# Contexte\n\n'+'Le marché de l’énergie est en pleine transformation et les entreprises doivent adapter leur stratégie pour les années qui viennent. '*8+'\n\n<!-- DIAPOSITIVES -->\n\n## Contexte\n\n- Les prix de l’énergie sont en hausse pour les ménages\n\n## Enjeux\n\n- Une transition rapide est nécessaire pour les industriels\n'

class Response(io.BytesIO):
    class headers:
        get_content_type=staticmethod(lambda:'text/html');get_content_charset=staticmethod(lambda:'utf-8')
    def __enter__(self):return self
    def __exit__(self,*a):self.close()

class ClaimsTests(unittest.TestCase):
    def claims(self,text,source=SOURCE):return verification.claims(text,{'https://insee.example/a':source})
    def test_figures_must_appear_in_the_cited_source(self):
        problems,_=self.claims('Le chômage est de 7,3 % (https://insee.example/a).\nIl serait de 8,1 % (https://insee.example/a).\nLa population est de 68 millions (https://insee.example/a).')
        self.assertEqual(len(problems),1);self.assertIn('8,1 %',problems[0])
    def test_years_dates_list_numbers_and_pages_are_ignored(self):
        problems,_=self.claims('1. En 2024, le 12/03/2024 à 10:30, 3 rapports, voir p. 45 et [Rapport 99](https://insee.example/a).')
        self.assertEqual(problems,[])
    def test_footnotes_and_numbered_references_link_a_figure_to_its_source(self):
        problems,_=self.claims('Le budget est de 1 234 millions [1] et de 9 999 millions [^b].\n\n## Sources\n[1] https://insee.example/a\n[^b]: https://insee.example/a')
        self.assertEqual(len(problems),1);self.assertIn('9 999',problems[0])
    def test_altered_quotation_is_reported_but_not_a_translation(self):
        problems,_=self.claims('Elle dit : « La transition énergétique exige des investissements massifs » (https://insee.example/a).\n'
                               'Puis : « La transition écologique exige des investissements colossaux et immédiats » (https://insee.example/a).\n'
                               'Traduit : « The energy transition requires massive and immediate investment » (https://insee.example/a).')
        self.assertEqual(len(problems),1);self.assertIn('écologique',problems[0])
    def test_unreadable_source_leads_to_no_conclusion(self):
        self.assertEqual(self.claims('Il serait de 8,1 % (https://insee.example/a).',source='Accepter les cookies')[0],[])
    def test_figures_without_source_are_a_warning_only(self):
        problems,warnings=self.claims('Les ventes progressent de 12 % cette année.\n| Pays | 45 % |')
        self.assertEqual(problems,[]);self.assertEqual(len(warnings),1);self.assertIn('12 %',warnings[0]);self.assertNotIn('45 %',warnings[0])
    def test_check_reads_the_pages_it_already_fetches(self):
        with patch('verification.public',return_value=True),patch('urllib.request.urlopen',side_effect=lambda *a,**k:Response(SOURCE.encode())):
            problems,unverified,blocked=verification.check('Il serait de 8,1 % (https://insee.example/a).',tempfile.gettempdir())
        self.assertEqual(len(problems),1);self.assertIn('8,1 %',problems[0])
    def test_project_documents_are_sources_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp)/'inputs').mkdir();(Path(tmp)/'inputs'/'notes.md').write_text(SOURCE)
            problems,_,_=verification.check('Le chômage est de 7,3 % (inputs/notes.md) mais de 9,9 % ailleurs (inputs/notes.md).',tmp)
        self.assertEqual(len(problems),1);self.assertIn('9,9 %',problems[0])

class InspectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.out=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def render(self,md,kinds):
        self.assertEqual([e for _,e in formats.render(md,'Test',kinds,self.out,lang='fr')],[None]*len(kinds))
    def test_clean_deliverables_pass(self):
        kinds=['docx','pptx','tex']+(['pdf'] if shutil.which('xelatex') else [])
        self.render(FRENCH,kinds)
        for k in kinds:self.assertEqual(inspection.check(self.out/('livrable.'+k),'fr'),[],k)
    def test_wrong_language_and_placeholders(self):
        self.render(FRENCH.replace('<!-- DIAPOSITIVES -->','[à compléter]\n\n<!-- DIAPOSITIVES -->'),['docx'])
        problems=inspection.check(self.out/'livrable.docx','en')
        self.assertTrue(any('placeholder' in p for p in problems));self.assertTrue(any('instead of English' in p for p in problems))
    def test_broken_slides_are_reported(self):
        self.render(FRENCH,['pptx'])
        source=self.out/'livrable.pptx';broken=self.out/'broken.pptx'
        box='<p:sp><p:nvSpPr><p:cNvPr id="99" name="x"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="11000000" y="100000"/><a:ext cx="3000000" cy="500000"/></a:xfrm></p:spPr><p:txBody><a:bodyPr/>'+'<a:p><a:r><a:rPr sz="2000"/><a:t>TODO a long sentence that cannot fit in such a small box at all.</a:t></a:r></a:p>'*4+'</p:txBody></p:sp>'
        with zipfile.ZipFile(source) as z,zipfile.ZipFile(broken,'w') as out:
            slides=sorted(n for n in z.namelist() if re.fullmatch(r'ppt/slides/slide\d+\.xml',n))
            for item in z.infolist():
                data=z.read(item)
                if item.filename==slides[0]:data=data.replace(b'</p:spTree>',box.encode()+b'</p:spTree>')
                if item.filename==slides[-1]:data=re.sub(rb'<a:t>[^<]*</a:t>',b'<a:t></a:t>',data)
                if item.filename=='ppt/slides/_rels/'+slides[0].split('/')[-1]+'.rels':data=data.replace(b'</Relationships>',b'<Relationship Id="rId99" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="../media/absent.png"/></Relationships>')
                out.writestr(item,data)
        text='\n'.join(inspection.check(broken,'fr'))
        for expected in ('empty slide','beyond slide no. 1','overflowing','missing image','TODO'):self.assertIn(expected,text)
    def test_damaged_file(self):
        (self.out/'x.docx').write_bytes(b'not a zip')
        self.assertIn('damaged',inspection.check(self.out/'x.docx','fr')[0])

class DeliverableStepTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.app=Atelier(self.temp.name,demo=True,scheduler=False)
        self.project=self.app.save_object('projects',{'name':'Projet'})
    def tearDown(self):
        self.app.close();self.temp.cleanup()
    def mission(self,**changes):
        data={'name':'Test','brief':'Un rapport.','project_id':self.project['id'],'team_id':'equipe-essentielle','execution':'demo'};data.update(changes)
        return self.app.create_mission(data)
    def test_final_deliverable_problems_go_back_to_the_agent(self):
        m=self.mission(formats=['pptx']);last=len(m['steps'])-1
        with patch('verification.check',return_value=([],[],[])),patch('inspection.check',side_effect=[['PowerPoint: empty slide(s) no. 2'],[]]) as check:self.app.run_step(m['id'],last)
        step=self.app.store.get('missions',m['id'])['steps'][last]
        self.assertEqual(step['verification']['corrections'],1);self.assertEqual(check.call_args[0][0].name,'livrable.pptx')
        self.assertTrue(any('empty slide' in e['text'] and 'Sent back' in e['text'] for e in self.app.store.events(m['id'])))
    def test_agent_own_file_is_the_one_inspected(self):
        m=self.mission(formats=['docx']);last=len(m['steps'])-1
        work=self.app.mission_dir(m['id'])/'agents'/('%02d-%s'%(last+1,m['steps'][last]['agent']['id']));work.mkdir(parents=True)
        (work/'rapport.docx').write_bytes(b'not a zip')
        with patch('verification.check',return_value=([],[],[])):self.app.run_step(m['id'],last)
        step=self.app.store.get('missions',m['id'])['steps'][last]
        self.assertEqual(step['verification']['corrections'],2);self.assertIn('damaged',step['verification']['problems'][0])
    def test_text_answer_is_checked_at_the_last_step_only(self):
        m=self.mission()
        with patch('verification.check',return_value=([],[],[])),patch('inspection.answer',return_value=[]) as answer:
            self.app.run_step(m['id'],0);answer.assert_not_called()
            self.app.run_step(m['id'],len(m['steps'])-1);answer.assert_called_once()

if __name__=='__main__':unittest.main(verbosity=2)
