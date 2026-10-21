import base64
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server import Atelier, Problem, password_hash, safe_file

class AtelierTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.app=Atelier(self.temp.name,demo=True,scheduler=False)
        self.project=self.app.save_object('projects',{'name':'Projet de test'})
    def tearDown(self):
        self.app.close();self.temp.cleanup()
    def req(self,path,method='GET',data=None,headers=None):
        raw=json.dumps(data or {}).encode()
        env={'REQUEST_METHOD':method,'PATH_INFO':path.split('?')[0],'QUERY_STRING':path.partition('?')[2], 'wsgi.input':io.BytesIO(raw),'CONTENT_LENGTH':str(len(raw)),'CONTENT_TYPE':'application/json','HTTP_X_ATELIER_REQUEST':'1','HTTP_HOST':'localhost','wsgi.url_scheme':'http','REMOTE_ADDR':'127.0.0.1'}
        env.update(headers or {})
        response={}
        def start(status,h):response.update(status=int(status.split()[0]),headers=dict(h))
        raw=b''.join(self.app(env,start))
        response['body']=json.loads(raw) if response['headers']['Content-Type']=='application/json' else raw
        return response
    def mission(self,**changes):
        data={'name':'Test','brief':'Vérifier le passage des contributions.','project_id':self.project['id'],'team_id':'equipe-essentielle','execution':'demo'};data.update(changes)
        return self.app.create_mission(data)
    def quick_step(self,id,index):
        m=self.app.store.get('missions',id)
        result='contribution '+str(index)
        self.app.store.update('missions',id,lambda m:m['steps'][index].update(status='done',result=result))
    def test_predefined_roles_and_project_context(self):
        a=self.app.save_object('agents',{'name':'Plume','preset':'createur','provider':'claude'})
        self.assertEqual(a['instructions'],'');self.assertEqual(a['role'],'Produce complete, rigorous work')
        with self.assertRaises(Problem):self.app.save_object('agents',{'name':'X','preset':'inconnu'})
        with self.assertRaises(Problem):self.app.save_object('agents',{'name':'Libre'})
        self.app.save_object('projects',{'name':'Renommé','description':'Public : étudiants.'},self.project['id'])
        self.assertEqual(self.app.save_object('projects',{'name':'Renommé 2'},self.project['id'])['description'],'Public : étudiants.')
        team=self.app.save_object('teams',{'name':'Duo','agent_ids':[a['id'],'producteur','relecteur'],'mode':'arbitration'})
        self.assertEqual(self.app.save_object('teams',{'name':'Duo 2','agent_ids':team['agent_ids'],'mode':'arbitration'},team['id'])['description'],'')
        m=self.mission(team_id=team['id'])
        with patch('server.time.sleep'):
            for i in range(3):self.app.run_step(m['id'],i)
        first=(self.app.mission_dir(m['id'])/('agents/01-'+a['id'])/'CONSIGNES.md').read_text()
        last=(self.app.mission_dir(m['id'])/'agents/03-relecteur/CONSIGNES.md').read_text()
        self.assertIn('PROJECT CONTEXT "Renommé 2":\nPublic : étudiants.',first)
        self.assertIn('You are a creator',first);self.assertNotIn('Instructions: ',first)
        self.assertIn('You are the team’s reviewer, synthesizer and editor',last)
        self.assertEqual(set(self.req('/api/state')['body']['roles']),{'createur','relecteur'})
    def test_styled_reference_documents(self):
        import formats,zipfile
        if not formats.pandoc():self.skipTest('Pandoc absent')
        with patch.object(formats,'TEMPLATES',Path(self.temp.name)/'modeles'):
            with zipfile.ZipFile(formats.reference('pptx')) as z:
                self.assertIn(formats.NAVY,z.read('ppt/theme/theme1.xml').decode());self.assertIn('showMasterSp="0"',z.read('ppt/slideLayouts/slideLayout1.xml').decode())
            with zipfile.ZipFile(formats.reference('docx')) as z:self.assertIn(formats.BLUE,z.read('word/theme/theme1.xml').decode())
            out=Path(self.temp.name)/'out';out.mkdir()
            self.assertEqual(formats.render('# A\n\n## B\n\n- c','Titre',['pptx'],out),[('pptx',None)])
    def test_only_requested_deliverable(self):
        m=self.mission();m['formats']=['pptx'];self.app.store.put('missions',m)
        out=self.app.mission_dir(m['id'])/'resultats';out.mkdir(parents=True,exist_ok=True)
        for n in ('livrable.md','rapport.md','diapositives.md'):(out/n).write_text('x')
        self.assertEqual([f['path'] for f in self.app.deliverables(m['id'],m)],['resultats/livrable.md'])
        (out/'livrable.pptx').write_bytes(b'p')
        self.assertEqual([f['path'] for f in self.app.deliverables(m['id'],m)],['resultats/livrable.pptx'])
        work=self.app.mission_dir(m['id'])/'agents'/'03-relecteur';(work/'lo_profile').mkdir(parents=True);(work/'lo_profile/a.pptx').write_bytes(b'l');(work/'deck.pptx').write_bytes(b'agent')
        self.assertEqual([x.name for x in self.app.agent_files(m['id'],2,m['steps'][2]['agent'])],['deck.pptx'])
        with patch('server.shutil.which',return_value='/bin/claude'):cmd=self.app.command(m['steps'][0]['agent'],Path(self.temp.name),False)
        self.assertEqual(cmd[cmd.index('--tools')+1],'Read,Glob,Grep,WebSearch,WebFetch')
    def test_revision_reruns_only_the_final_agent(self):
        m=self.mission(name='')
        self.assertEqual(m['name'],'Vérifier le passage des contributions.')
        with patch('server.time.sleep'):self.app.run_mission(m['id'])
        with self.assertRaises(Problem):self.app.mission_action(m['id'],'revise',{'text':''})
        m=self.app.mission_action(m['id'],'revise',{'text':'Ajoute une diapositive sur les prix.'})
        self.assertEqual((m['status'],len(m['steps']),m['steps'][-1]['agent']['id']),('queued',4,'relecteur'))
        with patch('server.time.sleep'):self.app.run_mission(m['id'])
        m=self.app.store.get('missions',m['id']);self.assertEqual(m['status'],'completed');self.assertTrue(all(s['status']=='done' for s in m['steps']))
        prompt=(self.app.mission_dir(m['id'])/'agents/04-relecteur/CONSIGNES.md').read_text()
        self.assertIn('REVISION REQUESTED BY THE USER: Ajoute une diapositive sur les prix.',prompt);self.assertIn('livrable_actuel/',prompt)
        self.assertTrue((self.app.mission_dir(m['id'])/'agents/04-relecteur/livrable_actuel/livrable.md').exists())
        self.assertIn('## Revision 1',(self.app.mission_dir(m['id'])/'resultats/rapport.md').read_text())
    def test_creators_give_content_and_final_agent_sees_references(self):
        import formats
        team=self.app.save_object('teams',{'name':'Trio','agent_ids':['analyste','producteur','relecteur'],'mode':'arbitration'})
        with patch('formats.previews',return_value=0):
            self.app.add_reference({'format':'pptx','name':'exemple.pptx','content':base64.b64encode(b'x').decode()})
        with self.assertRaises(Problem):self.app.add_reference({'format':'pptx','name':'../x.pptx','content':''})
        self.assertEqual(self.app.references()['pptx'][0]['name'],'exemple.pptx')
        with patch('formats.pandoc',return_value='/bin/pandoc'):m=self.mission(team_id=team['id'],formats=['pptx'])
        for i in range(3):self.app.run_step(m['id'],i)
        read=lambda i:(self.app.mission_dir(m['id'])/'agents'/('%02d-%s'%(i+1,m['steps'][i]['agent']['id']))/'CONSIGNES.md').read_text()
        self.assertIn('Create no file in these formats',read(0));self.assertNotIn('EXPECTED DESIGN LEVEL',read(0))
        self.assertIn('EXPECTED DESIGN LEVEL',read(2));self.assertIn('references/pptx/exemple/exemple.pptx',read(2))
        work=self.app.mission_dir(m['id'])/'agents/03-relecteur'
        self.assertNotIn('references',{x.relative_to(work).parts[0] for x in self.app.agent_files(m['id'],2,m['steps'][2]['agent'])})
        (work/'__pycache__').mkdir();from server import tidy;tidy(work);self.assertFalse((work/'__pycache__').exists())
        self.assertEqual(self.app.remove_reference({'format':'pptx','name':'exemple.pptx'})['pptx'],[])
    def test_language_setting_drives_messages_and_deliverables(self):
        import i18n
        self.assertEqual(self.req('/api/settings/language','PUT',{'language':'de'})['status'],400)
        self.assertEqual(self.req('/api/settings/language','PUT',{'language':'fr'})['body']['language'],'fr')
        try:
            self.assertEqual((self.app.settings['language'],self.req('/api/state')['body']['language']),('fr','fr'))
            self.assertEqual(self.req('/api/missions/inconnu')['body']['error'],'Cet élément est introuvable.')
            self.assertEqual(self.req('/api/state')['body']['roles']['createur']['label'],'Créateur')
            m=self.mission();self.assertEqual(m['language'],'fr')
            self.app.run_step(m['id'],0)
            prompt=(self.app.mission_dir(m['id'])/'agents'/('01-'+m['steps'][0]['agent']['id'])/'CONSIGNES.md').read_text()
            self.assertIn('You are ',prompt);self.assertIn('every deliverable in French.',prompt)
            # Older missions have no language: they were French.
            self.app.store.update('missions',m['id'],lambda x:x.pop('language'));self.app.run_step(m['id'],1)
            self.assertIn('in French.',(self.app.mission_dir(m['id'])/'agents'/('02-'+m['steps'][1]['agent']['id'])/'CONSIGNES.md').read_text())
        finally:
            i18n.set_language('en')
        self.assertEqual(self.req('/api/missions/inconnu')['body']['error'],'This item could not be found.')
        self.assertEqual(self.mission()['language'],'en')
    def test_reasoning_effort_reaches_every_assistant(self):
        import antigravity_adapter
        with self.assertRaises(Problem):self.app.save_object('agents',{'name':'X','preset':'createur','effort':'low'})
        a=self.app.save_object('agents',{'name':'Profond','preset':'createur','provider':'codex','effort':'xhigh'});self.assertEqual(a['effort'],'xhigh')
        with patch('server.shutil.which',return_value='/bin/codex'):cmd=self.app.command(a,Path(self.temp.name),True)
        self.assertIn('model_reasoning_effort="xhigh"',cmd)
        # Agents created before the setting get the default level.
        old=self.app.store.get('agents','analyste');self.assertNotIn('effort',old)
        with patch('server.shutil.which',return_value='/bin/claude'):cmd=self.app.command(old,Path(self.temp.name),True)
        self.assertEqual(cmd[cmd.index('--effort')+1],'high')
        with patch('antigravity_adapter.permission_problem',return_value=None):self.assertEqual(antigravity_adapter.command('/b/agy','',30,'max')[-2:],['--effort','max'])
    def test_agents_give_full_work_and_reviewer_reads_everything(self):
        team=self.app.save_object('teams',{'name':'Trio','agent_ids':['analyste','producteur','relecteur'],'mode':'arbitration'})
        m=self.mission(team_id=team['id'],formats=['pptx'])
        self.assertEqual(m['timeout_minutes'],45)
        for i in range(3):self.app.run_step(m['id'],i)
        read=lambda i:(self.app.mission_dir(m['id'])/'agents'/('%02d-%s'%(i+1,m['steps'][i]['agent']['id']))/'CONSIGNES.md').read_text()
        for i in range(3):self.assertIn('DEPTH:',read(i));self.assertIn('your complete work in full',read(i))
        self.assertNotIn('READ EVERYTHING',read(0));self.assertIn('READ EVERYTHING',read(2))
        self.assertNotIn('8 to 15 slides',read(2));self.assertIn('as many slides as the content needs',read(2))
    def test_authentication_and_logout(self):
        self.app.demo=False;self.app.settings={'password':password_hash('un-mot-de-passe-test')}
        self.assertEqual(self.req('/api/state')['status'],401)
        self.assertEqual(self.req('/api/login','POST',{'password':'non'})['status'],401)
        r=self.req('/api/login','POST',{'password':'un-mot-de-passe-test'})
        self.assertEqual(r['status'],200)
        cookie=r['headers']['Set-Cookie'].split(';')[0]
        self.assertIn('HttpOnly',r['headers']['Set-Cookie'])
        self.assertEqual(self.req('/api/state',headers={'HTTP_COOKIE':cookie})['status'],200)
        self.req('/api/logout','POST',headers={'HTTP_COOKIE':cookie})
        self.assertEqual(self.req('/api/state',headers={'HTTP_COOKIE':cookie})['status'],401)
    def test_csrf(self):
        self.assertEqual(self.req('/api/projects','POST',{'name':'x'}, {'HTTP_X_ATELIER_REQUEST':''})['status'],403)
        self.assertEqual(self.req('/api/projects','POST',{'name':'x'},{'HTTP_ORIGIN':'https://evil.example'})['status'],403)
    def test_proxy_login_origin_and_cookie_path(self):
        self.app.public_url='https://atelier.example/proxy/8765'
        self.app.demo=False
        self.app.settings={'password':password_hash('un-mot-de-passe-test')}
        headers={'HTTP_HOST':'atelier.example','HTTP_ORIGIN':'https://atelier.example'}
        r=self.req('/api/login','POST',{'password':'un-mot-de-passe-test'},headers)
        self.assertEqual(r['status'],200)
        self.assertIn('Path=/proxy/8765/',r['headers']['Set-Cookie'])
        self.assertIn('; Secure',r['headers']['Set-Cookie'])
        headers['HTTP_COOKIE']=r['headers']['Set-Cookie'].split(';')[0]
        self.assertEqual(self.req('/api/state',headers=headers)['status'],200)
        logout=self.req('/api/logout','POST',headers=headers)
        self.assertIn('Path=/proxy/8765/',logout['headers']['Set-Cookie'])
        headers['HTTP_ORIGIN']='https://evil.example'
        self.assertEqual(self.req('/api/login','POST',{'password':'un-mot-de-passe-test'},headers)['status'],403)
    def test_no_live_fallback_in_demo(self):
        with self.assertRaises(Problem):self.mission(execution='live')
    def test_upload_traversal_and_symlink(self):
        root=self.app.project_dir(self.project['id'])
        with self.assertRaises(Problem):safe_file(root,'../settings.json')
        (root/'link').symlink_to('/etc/passwd')
        with self.assertRaises(Problem):safe_file(root,'link')
        path='/api/projects/'+self.project['id']+'/files'
        self.assertEqual(self.req(path,'POST',{'name':'../escape','content':''})['status'],400)
        data={'name':'source.txt','content':base64.b64encode(b'hello').decode()}
        self.assertEqual(self.req(path,'POST',data)['status'],201)
        self.assertEqual(self.req(path,'POST',data)['status'],409)
        self.assertEqual(self.req(path+'?path=source.txt')['body'],b'hello')
    def test_project_inputs_are_snapshotted(self):
        source=self.app.project_dir(self.project['id'])/'data.txt';source.write_text('initial')
        m=self.mission();source.write_text('modified')
        self.assertEqual((self.app.mission_dir(m['id'])/'inputs/data.txt').read_text(),'initial')
    def test_rules_and_agents_snapshotted(self):
        m=self.mission()
        a=self.app.store.get('agents','analyste');a['instructions']='changed';self.app.store.put('agents',a)
        self.assertNotEqual(m['steps'][0]['agent']['instructions'],'changed')
    def test_global_context_snapshotted_into_prompts(self):
        r=self.req('/api/settings/context','PUT',{'global_context':'  Sois scientifique et concis.  '})
        self.assertEqual(r['body']['global_context'],'Sois scientifique et concis.')
        self.assertEqual(self.req('/api/state')['body']['global_context'],'Sois scientifique et concis.')
        m=self.mission();self.req('/api/settings/context','PUT',{'global_context':'changé'})
        self.app.run_step(m['id'],0)
        consignes=(self.app.mission_dir(m['id'])/'agents'/('01-'+m['steps'][0]['agent']['id'])/'CONSIGNES.md').read_text()
        self.assertTrue(consignes.startswith('GLOBAL CONTEXT'));self.assertIn('Sois scientifique et concis.',consignes);self.assertNotIn('changé',consignes)
        self.assertEqual(self.req('/api/settings/context','PUT',{'global_context':'x'*8001})['status'],400)
    def test_older_missions_keep_their_rules(self):
        m=self.mission();self.app.store.update('missions',m['id'],lambda m:m.update(rules=['Ancienne règle commune.']))
        self.app.run_step(m['id'],0)
        self.assertIn('Ancienne règle commune.',(self.app.mission_dir(m['id'])/'agents'/('01-'+m['steps'][0]['agent']['id'])/'CONSIGNES.md').read_text())
    def test_formats_guide_last_agent_and_produce_files(self):
        with self.assertRaises(Problem):self.mission(formats=['exe'])
        m=self.mission(formats=['docx','pptx','tex','pdf'])
        self.assertEqual(m['formats'],['pdf','docx','pptx','tex'])
        last=len(m['steps'])-1;real=self.app.run_step
        def step(id,i):
            real(id,i)
            text='# Résultat\n\nOn a $e^{i\\pi}+1=0$.\n\n$$\\int_0^1 x\\,dx=\\tfrac12$$\n\n<!-- DIAPOSITIVES -->\n\n## Idée\n\n- Point'
            self.app.store.update('missions',id,lambda m:m['steps'][i].update(status='done',result=text if i==last else 'contribution'))
        with patch.object(self.app,'run_step',side_effect=step):self.app.run_mission(m['id'])
        consignes=lambda i:(self.app.mission_dir(m['id'])/'agents'/('%02d-%s'%(i+1,m['steps'][i]['agent']['id']))/'CONSIGNES.md').read_text()
        self.assertNotIn('OUTPUT FORMATS',consignes(0));self.assertIn('OUTPUT FORMATS',consignes(last));self.assertIn('DIAPOSITIVES',consignes(last))
        out=self.app.mission_dir(m['id'])/'resultats'
        # The PDF needs XeLaTeX, absent from CI runners.
        for ext in ('pdf','docx','pptx','tex')[0 if __import__('shutil').which('xelatex') else 1:]:self.assertTrue((out/('livrable.'+ext)).stat().st_size>1000,ext)
        self.assertNotIn('Point',(out/'livrable.md').read_text());self.assertIn('m:oMath',__import__('zipfile').ZipFile(out/'livrable.docx').read('word/document.xml').decode())
        self.assertEqual(self.app.store.get('missions',m['id'])['status'],'completed')
    def test_failed_format_keeps_mission_completed(self):
        m=self.mission(formats=['pdf'])
        with patch('formats.render',return_value=[('pdf','erreur LaTeX')]),patch.object(self.app,'run_step',side_effect=self.quick_step):self.app.run_mission(m['id'])
        self.assertEqual(self.app.store.get('missions',m['id'])['status'],'completed')
        self.assertTrue(any('PDF could not be produced' in e['text'] for e in self.app.store.events(m['id'])))
    def test_failed_sources_go_back_to_the_agent(self):
        m=self.mission()
        with patch('verification.check',side_effect=[(['DOI 10.1/faux : DOI inexistant'],[],[]),([],['https://x.org : site injoignable'],[])]):self.app.run_step(m['id'],0)
        step=self.app.store.get('missions',m['id'])['steps'][0]
        self.assertEqual(step['status'],'done');self.assertEqual(step['verification']['corrections'],1);self.assertEqual(step['verification']['problems'],[])
        texts=[e['text'] for e in self.app.store.events(m['id'])]
        self.assertTrue(any('Sent back to the agent for correction (1/2)' in t and 'DOI inexistant' in t for t in texts));self.assertTrue(any('passed after correction' in t for t in texts))
        self.assertIn('SOURCES:',(self.app.mission_dir(m['id'])/'agents'/('01-'+step['agent']['id'])/'CONSIGNES.md').read_text())
    def test_unfixed_sources_warn_without_blocking(self):
        m=self.mission()
        with patch('verification.check',return_value=(['https://faux.org : domaine inexistant'],[],[])):self.app.run_mission(m['id'])
        m=self.app.store.get('missions',m['id'])
        self.assertEqual(m['status'],'completed');self.assertEqual(m['steps'][0]['verification']['corrections'],2)
        report=(self.app.mission_dir(m['id'])/'resultats'/'rapport.md').read_text()
        self.assertIn('## Automatic check',report);self.assertIn('⚠ https://faux.org',report)
    def test_verification_can_be_disabled(self):
        m=self.mission(verify=False)
        with patch('verification.check') as check:self.app.run_step(m['id'],0)
        check.assert_not_called();self.assertNotIn('SOURCES :',(self.app.mission_dir(m['id'])/'agents'/('01-'+m['steps'][0]['agent']['id'])/'CONSIGNES.md').read_text())
    def test_sources_checker_parses_references(self):
        import verification
        urls,dois,arxiv,files=verification.references('Voir https://doi.org/10.1038/nature14539, https://a.org/b). Et 10.1126/science.1 ; arXiv:1706.03762v2 ; inputs/notes.md.')
        self.assertEqual(urls,['https://a.org/b']);self.assertEqual(dois,['10.1038/nature14539','10.1126/science.1']);self.assertEqual(arxiv,['1706.03762']);self.assertEqual(files,['inputs/notes.md'])
        with patch('verification.public',return_value=False):self.assertEqual(verification.fetch('http://intranet/x')[0],'unknown')
    def test_inaccessible_articles_become_remarks(self):
        import verification
        text='Analyse.\n\n## Articles à télécharger\n\n- Smith 2021 — https://www.jstor.org/stable/1 — méthode utile\n\n<!-- DIAPOSITIVES -->\n## Diapo'
        clean,items=verification.downloads(text)
        self.assertEqual(items,['Smith 2021 — https://www.jstor.org/stable/1 — méthode utile']);self.assertNotIn('Articles à télécharger',clean);self.assertIn('<!-- DIAPOSITIVES -->',clean)
        m=self.mission()
        blocked=['https://www.jstor.org/stable/1 — accès refusé aux agents (403)','https://paywall.org/a — accès refusé aux agents (403)']
        with patch('verification.check',return_value=([],[],blocked)):
            with patch('server.Atelier.verify_step',side_effect=lambda id,m,i,a,w,p,r,inv:{'result':r+'\n\n## Articles à télécharger\n- Smith 2021 — https://www.jstor.org/stable/1 — méthode utile','problems':[],'unverified':[],'blocked':blocked,'corrections':0}):
                self.app.run_mission(m['id'])
        m=self.app.store.get('missions',m['id'])
        self.assertEqual(m['steps'][0]['to_download'],['Smith 2021 — https://www.jstor.org/stable/1 — méthode utile','https://paywall.org/a — accès refusé aux agents (403)'])
        self.assertNotIn('Articles à télécharger',m['steps'][0]['result'])
        report=(self.app.mission_dir(m['id'])/'resultats'/'rapport.md').read_text()
        self.assertIn('## Notes — articles to download yourself',report);self.assertIn('paywall.org',report)
        with patch('verification.public',return_value=True),patch('urllib.request.urlopen',side_effect=verification.urllib.error.HTTPError('u',403,'x',{},None)):
            self.assertEqual(verification.fetch('https://www.jstor.org/stable/1')[0],'unknown')
        with patch('verification.public',return_value=True),patch('urllib.request.urlopen',side_effect=verification.urllib.error.HTTPError('u',403,'x',{'cf-mitigated':'challenge'},None)):
            self.assertEqual(verification.fetch('https://protege.example/a')[0],'blocked')
        with patch('verification.public',return_value=True),patch('urllib.request.urlopen',side_effect=verification.urllib.error.HTTPError('u',402,'x',{},None)):
            self.assertEqual(verification.fetch('https://payant.example/a')[0],'blocked')
        m=self.mission();self.app.store.update('missions',m['id'],lambda m:m['steps'][1].update(to_download=['a','b']))
        self.assertEqual(self.app.mission_action(m['id'],'dismiss',{'step':1,'text':'a'})['steps'][1]['to_download'],['b'])
        with self.assertRaises(Problem):self.app.mission_action(m['id'],'dismiss',{'step':1,'text':'a'})
    def test_delete_missions_projects_agents_teams(self):
        m=self.mission();self.app.store.update('missions',m['id'],lambda m:m.update(status='running'))
        self.assertEqual(self.req('/api/missions/'+m['id'],'DELETE')['status'],400)
        self.assertEqual(self.req('/api/projects/'+self.project['id'],'DELETE')['status'],400)
        self.app.store.update('missions',m['id'],lambda m:m.update(status='completed'))
        other=self.mission();self.app.store.update('missions',other['id'],lambda m:m.update(status='cancelled'));self.assertTrue(self.app.mission_dir(other['id']).exists())
        self.assertEqual(self.req('/api/missions/'+other['id'],'DELETE')['status'],200)
        self.assertFalse(self.app.mission_dir(other['id']).exists());self.assertEqual(self.app.store.events(other['id']),[])
        self.assertEqual(self.req('/api/projects/'+self.project['id'],'DELETE')['status'],200)
        self.assertFalse(any(x['project_id']==self.project['id'] for x in self.app.store.all('missions')));self.assertFalse(self.app.project_dir(self.project['id']).exists())
        # Leaving a relay team is fine; an arbitration team needs three agents.
        self.assertEqual(self.req('/api/agents/producteur','DELETE')['status'],200)
        self.assertEqual(self.app.store.get('teams','equipe-essentielle')['agent_ids'],['analyste','relecteur'])
        t=self.app.save_object('agents',{'name':'C','instructions':'x'});arb=self.app.save_object('teams',{'name':'Arb','agent_ids':['analyste','relecteur',t['id']],'mode':'arbitration'})
        r=self.req('/api/agents/'+t['id'],'DELETE');self.assertEqual(r['status'],400);self.assertIn('Arb',r['body']['error'])
        self.assertEqual(self.req('/api/teams/'+arb['id'],'DELETE')['status'],200);self.assertEqual(self.req('/api/agents/'+t['id'],'DELETE')['status'],200)
    def test_sequential_handoff_and_download(self):
        m=self.mission();calls=[]
        def step(id,i):
            if i:self.assertEqual(self.app.store.get('missions',id)['steps'][i-1]['status'],'done')
            calls.append(i);self.quick_step(id,i)
        with patch.object(self.app,'run_step',side_effect=step):self.app.run_mission(m['id'])
        self.assertEqual(calls,[0,1,2])
        self.assertEqual(self.app.store.get('missions',m['id'])['status'],'completed')
        r=self.req('/api/missions/'+m['id']+'/archive')
        self.assertEqual(r['status'],200);self.assertTrue(r['body'].startswith(b'PK'))
    def test_validation_resume(self):
        m=self.mission(review=True)
        with patch.object(self.app,'run_step',side_effect=self.quick_step):
            self.app.run_mission(m['id'])
            mid=self.app.store.get('missions',m['id']);self.assertEqual(mid['status'],'waiting');self.assertEqual(mid['steps'][1]['status'],'pending')
            self.app.mission_action(m['id'],'approve',{})
            self.app.run_mission(m['id'])
            self.assertEqual(self.app.store.get('missions',m['id'])['steps'][1]['status'],'done')
    def test_parallel_then_synthesis(self):
        t=self.app.store.get('teams','equipe-essentielle');t['mode']='parallel';self.app.store.put('teams',t)
        m=self.mission();barrier=threading.Barrier(2)
        def step(id,i):
            if i<2:barrier.wait(timeout=3)
            else:self.assertTrue(all(s['status']=='done' for s in self.app.store.get('missions',id)['steps'][:2]))
            self.quick_step(id,i)
        with patch.object(self.app,'run_step',side_effect=step):self.app.run_mission(m['id'])
        self.assertEqual(self.app.store.get('missions',m['id'])['status'],'completed')
    def test_arbitration_independent_then_arbiter(self):
        with self.assertRaises(Problem):self.app.save_object('teams',{'name':'Duo','agent_ids':['analyste','producteur'],'mode':'arbitration'})
        t=self.app.store.get('teams','equipe-essentielle');t['mode']='arbitration';self.app.store.put('teams',t)
        m=self.mission();work=lambda i:self.app.mission_dir(m['id'])/'agents'/('%02d-%s'%(i+1,m['steps'][i]['agent']['id']))
        # A resume after a failure must not leak proposition A to agent B.
        self.quick_step(m['id'],0);self.app.run_step(m['id'],1)
        consignes=(work(1)/'CONSIGNES.md').read_text()
        self.assertNotIn('contribution 0',consignes);self.assertIn('INDEPENDENT WORK',consignes)
        self.assertFalse((work(1)/'contributions'/'01.md').exists())
        self.app.run_mission(m['id'])
        arbiter=(work(2)/'CONSIGNES.md').read_text()
        self.assertIn('ARBITRATION',arbiter);self.assertIn('Proposal A — ',arbiter);self.assertIn('Proposal B — ',arbiter)
        self.assertTrue((work(2)/'contributions'/'02.md').exists())
        report=(self.app.mission_dir(m['id'])/'resultats'/'rapport.md').read_text()
        self.assertLess(report.index('Final solution'),report.index('Proposal A'))
        self.assertEqual(self.app.store.get('missions',m['id'])['status'],'completed')
    def test_cancel_prevents_execution(self):
        m=self.mission();self.app.mission_action(m['id'],'cancel',{})
        with patch.object(self.app,'run_step') as step:self.app.run_mission(m['id']);step.assert_not_called()
        self.assertEqual(self.app.store.get('missions',m['id'])['status'],'cancelled')
    def test_restart_pauses_unfinished_missions(self):
        m=self.mission();self.app.store.update('missions',m['id'],lambda m:m.update(status='running'))
        other=Atelier(self.temp.name,demo=True,scheduler=False)
        self.assertEqual(other.store.get('missions',m['id'])['status'],'paused');other.close()
    def test_agent_error_does_not_mark_completed(self):
        m=self.mission()
        with patch.object(self.app,'run_step',side_effect=Problem('Erreur de connexion')):self.app.run_mission(m['id'])
        self.assertEqual(self.app.store.get('missions',m['id'])['status'],'failed')
    def test_cli_adapter_codex(self):
        fake=Path(self.temp.name)/'fake-codex'
        fake.write_text('#!/usr/bin/env python3\nimport sys,json\np=sys.stdin.read()\nprint(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":"Résultat de test"}}))\nprint(json.dumps({"type":"turn.completed"}))\n');fake.chmod(0o755)
        m=self.mission();a=m['steps'][1]['agent'];work=Path(self.temp.name)
        with patch.object(self.app,'command',return_value=[str(fake)]):
            result=self.app.run_process(m['id'],1,a,work,'bonjour',m)
        self.assertEqual(result,'Résultat de test')
    def test_cli_adapter_claude_error(self):
        fake=Path(self.temp.name)/'fake-claude'
        fake.write_text('#!/usr/bin/env python3\nimport sys,json\nsys.stdin.read()\nprint(json.dumps({"type":"result","is_error":True,"result":"quota indisponible"}))\n');fake.chmod(0o755)
        m=self.mission();a=m['steps'][0]['agent']
        with patch.object(self.app,'command',return_value=[str(fake)]):
            with self.assertRaises(Problem):self.app.run_process(m['id'],0,a,Path(self.temp.name),'bonjour',m)
    def test_subscriptions_do_not_inherit_api_keys(self):
        with patch.dict(os.environ,{'OPENAI_API_KEY':'private','ANTHROPIC_API_KEY':'private'}):
            env=self.app.agent_env();self.assertNotIn('OPENAI_API_KEY',env);self.assertNotIn('ANTHROPIC_API_KEY',env)
    def test_boundaries_not_bypass(self):
        a=self.app.store.get('agents','producteur')
        with patch('server.shutil.which',return_value='/bin/codex'):cmd=self.app.command(a,Path(self.temp.name),False)
        self.assertEqual(cmd[cmd.index('--sandbox')+1],'danger-full-access');self.assertIn('approval_policy="never"',cmd);self.assertNotIn('--dangerously-bypass-approvals-and-sandbox',cmd)
        a=self.app.store.get('agents','analyste')
        with patch('server.shutil.which',return_value='/bin/claude'):cmd=self.app.command(a,Path(self.temp.name),True)
        self.assertIn('--restricted',cmd);self.assertNotIn('Bash',','.join(cmd))

if __name__=='__main__':unittest.main(verbosity=2)
