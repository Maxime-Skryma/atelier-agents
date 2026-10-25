"""Offline integration tests: no HTTP listener, no real CLI or model request."""
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import antigravity_adapter as agy
from server import Atelier, Problem

OK=[{'event':'init','conversation_id':'c1','init':{'cwd':'/w','permission_mode':'request-review','model':'gemini-3.8-flash'}},
    {'event':'step_update','step_update':{'step_index':1,'state':'ACTIVE','step_type':'agent_response','text_delta':'Bonjour '}},
    {'event':'step_update','step_update':{'step_index':1,'state':'DONE','step_type':'agent_response','text_delta':'le monde.'}},
    {'event':'result','result':{'conversation_id':'c1','status':'SUCCESS','response':'Bonjour le monde.','num_turns':1}}]

class AntigravityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.home=self.root/'home';self.home.mkdir()
        self.env=patch.dict(os.environ,{'HOME':str(self.home)});self.env.start()
        self.app=Atelier(self.root/'data',demo=False,scheduler=False)
        self.agent=self.app.save_object('agents',dict(name='Explorateur',role='Comparer les approches',provider='antigravity',instructions='Vérifie les hypothèses.',model='',color='blue'))
        self.project=self.app.save_object('projects',{'name':'Test multi-assistants'})
    def tearDown(self):
        self.app.close();self.env.stop();self.tmp.cleanup()
    def team_mission(self,**extra):
        team=self.app.save_object('teams',dict(name='Équipe mixte',agent_ids=['analyste',self.agent['id'],'producteur'],mode='relay'))
        with patch('server.shutil.which',side_effect=lambda p:'/test/'+p):
            return self.app.create_mission(dict(name='Mission mixte',brief='Collaborer.',project_id=self.project['id'],team_id=team['id'],execution='live',**extra))
    def fake(self,name,events,code=0,stderr='',action=''):
        f=self.root/name
        f.write_text('#!'+sys.executable+'\nimport json,sys,pathlib\nline=sys.stdin.readline()\ntry: msg=json.loads(line)\nexcept ValueError: msg=line+sys.stdin.read()\n'
                     'pathlib.Path('+repr(str(self.root/(name+'.stdin')))+').write_text(json.dumps(msg))\n'+action+
                     'sys.stderr.write('+repr(stderr)+')\nfor e in '+repr(events)+': print(json.dumps(e),flush=True)\nsys.exit('+str(code)+')\n')
        f.chmod(0o755);return f
    def execute(self,events,code=0,stderr='',m=None):
        m=m or self.team_mission();fake=self.fake('fake-agy',events,code,stderr)
        with patch.object(self.app,'command',return_value=[str(fake)]):
            return self.app.run_process(m['id'],1,self.agent,self.root,'prompt confidentiel',m)
    def test_provider_persists_and_mixes(self):
        self.assertEqual(self.app.store.get('agents',self.agent['id'])['provider'],'antigravity')
        m=self.team_mission();self.assertEqual([s['agent']['provider'] for s in m['steps']],['claude','antigravity','codex'])
    def test_command_has_no_auto_approval_and_uses_stdin(self):
        cmd=agy.command('/p/agy','gemini-3.8-flash',15)
        self.assertNotIn('--dangerously-skip-permissions',cmd);self.assertNotIn('--mode',cmd)
        self.assertIn('--print=',cmd);self.assertEqual(cmd[cmd.index('--input-format')+1],'stream-json')
        self.assertEqual(cmd[cmd.index('--output-format')+1],'stream-json');self.assertIn('--disable-slash-commands',cmd)
        self.assertEqual(cmd[cmd.index('--print-timeout')+1],'15m');self.assertEqual(cmd[cmd.index('--model')+1],'gemini-3.8-flash')
        self.assertNotIn('--model',agy.command('/p/agy','',5))
        self.assertTrue(all('confidentiel' not in a for a in cmd))
        self.assertEqual(json.loads(agy.stdin_payload('Bonjour « test »')),{'event':'user','message':{'content':'Bonjour « test »'}})
    def test_server_resolves_agy_binary(self):
        with patch('server.shutil.which',side_effect=lambda p:'/opt/'+p if p=='agy' else None):
            cmd=self.app.command(self.agent,self.root,True,7)
        self.assertEqual(cmd[0],'/opt/agy')
    def settings(self,data,where='antigravity-cli/settings.json'):
        p=self.home/'.gemini'/where;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(data))
    def test_widened_permissions_fail_closed(self):
        self.assertIsNone(agy.permission_problem())
        self.settings({'toolPermission':'request-review','permissions':{'deny':['command(sudo)'],'allow':['read_file(docs/)']}})
        self.assertIsNone(agy.permission_problem())
        for bad in ({'toolPermission':'always-proceed'},{'allowNonWorkspaceAccess':True},{'permissions':{'allow':['command(git)']}},{'permissions':{'allow':['mcp(*)']}}):
            self.settings(bad)
            with self.assertRaises(Problem):self.app.command(self.agent,self.root,True)
        self.settings({});self.settings({'permissions':{'allow':['command(*)']}},'config/config.json')
        self.assertIn('shared settings',agy.permission_problem())
    def test_stream_success(self):
        self.assertEqual(self.execute(OK),'Bonjour le monde.')
        sent=json.loads((self.root/'fake-agy.stdin').read_text())
        self.assertEqual(sent['event'],'user');self.assertEqual(sent['message']['content'],'prompt confidentiel')
    def test_error_status_even_with_zero_exit(self):
        with self.assertRaisesRegex(Problem,'Quota'):
            self.execute(OK[:2]+[{'event':'result','result':{'status':'ERROR','error':'Quota épuisé'}}])
    def test_authentication_error_explained(self):
        with self.assertRaisesRegex(Problem,'Reconnect the Google account'):
            self.execute([{'event':'result','result':{'status':'ERROR','response':'','error':'authentication failed or timed out'}}],1,'Error: authentication required.')
    def test_missing_terminal_result_rejected(self):
        with self.assertRaisesRegex(Problem,'final result'):self.execute(OK[:3])
    def test_failed_exit_rejected(self):
        with self.assertRaises(Problem):self.execute(OK,3)
    def test_unexpected_permission_mode_stops(self):
        bad=[{'event':'init','init':{'permission_mode':'always-proceed'}}]+OK[1:]
        with self.assertRaisesRegex(Problem,'permission mode'):self.execute(bad)
    def test_tools_and_soft_denials_logged_without_arguments(self):
        m=self.team_mission()
        events=[{'event':'step_update','step_update':{'step_index':2,'state':'ACTIVE','step_type':'tool','tool_name':'run_command','tool_info':{'name':'run_command','parameters':{'CommandLine':'cat secret'}}}},
                {'event':'step_update','step_update':{'step_index':2,'state':'DONE','step_type':'tool','tool_name':'run_command','tool_info':{'error':{'type':'denied','message':'Refusé'}}}}]+OK[1:]
        self.execute(events,0,'warning: headless soft-deny tool confirmation: run_command requires approval\n',m)
        log=self.app.store.events(m['id'])
        self.assertEqual(sum(e['kind']=='tool' for e in log),1)
        self.assertTrue(any('Antigravity permissions' in e['text'] for e in log))
        self.assertNotIn('cat secret',json.dumps(log))
    def test_read_only_mission_locks_workspace(self):
        m=self.team_mission(allow_write=False)
        writer=self.fake('agy',OK,action='try: pathlib.Path("livrable.md").write_text("x")\nexcept OSError: sys.stderr.write("write denied\\n")\n')
        bins={'claude':self.fake('claude',[{'type':'result','is_error':False,'result':'Analyse.'}]),'agy':writer,
              'codex':self.fake('codex',[{'type':'item.completed','item':{'type':'agent_message','text':'Livrable.'}},{'type':'turn.completed'}])}
        with patch('server.shutil.which',side_effect=lambda p:str(bins[p])):self.app.run_mission(m['id'])
        work=self.app.mission_dir(m['id'])/'agents'/('02-'+self.agent['id'])
        self.assertFalse((work/'livrable.md').exists())
        self.assertTrue(os.access(work,os.W_OK))
        self.assertEqual(self.app.store.get('missions',m['id'])['status'],'completed')
    def test_read_only_mission_locks_codex_workspace(self):
        m=self.team_mission(allow_write=False)
        writer=self.fake('codex',[{'type':'item.completed','item':{'type':'agent_message','text':'Livrable.'}},{'type':'turn.completed'}],
                         action='try: pathlib.Path("livrable.md").write_text("x")\nexcept OSError: sys.stderr.write("write denied\\n")\n')
        bins={'claude':self.fake('claude',[{'type':'result','is_error':False,'result':'Analyse.'}]),'agy':self.fake('agy',OK),'codex':writer}
        with patch('server.shutil.which',side_effect=lambda p:str(bins[p])):self.app.run_mission(m['id'])
        steps=self.app.store.get('missions',m['id'])['steps']
        i=next(i for i,s in enumerate(steps) if s['agent']['provider']=='codex')
        work=self.app.mission_dir(m['id'])/'agents'/('%02d-%s'%(i+1,steps[i]['agent']['id']))
        self.assertFalse((work/'livrable.md').exists());self.assertTrue(os.access(work,os.W_OK))
        self.assertEqual(self.app.store.get('missions',m['id'])['status'],'completed')
    def test_read_only_detects_changes(self):
        folder=self.root/'w';folder.mkdir();(folder/'a.txt').write_text('a')
        before=agy.snapshot(folder)
        with agy.read_only(folder):
            with self.assertRaises(OSError):(folder/'b.txt').write_text('b')
        (folder/'b.txt').write_text('b');self.assertNotEqual(agy.snapshot(folder),before)
    def test_diagnostic_levels(self):
        def run(binary,args,env,timeout):
            class R:returncode=0;stdout='Fetching available models...\ngemini-3.8-flash\ngemini-4-argon-high\n';stderr=''
            r=R()
            if args==['--version']:r.stdout='1.4.3\n'
            return r
        with patch('server.shutil.which',side_effect=lambda p:'/bin/agy' if p=='agy' else None):
            self.assertEqual(self.app.connections()[2]['status'],'unchecked')
            with patch.object(agy,'_run',side_effect=run):item=self.app.connections(verify=True)[2]
        self.assertEqual(item['status'],'signed_in');self.assertIsNone(item['verified'])
        self.assertIn('gemini-3.8-flash',item['models']);self.assertEqual(item['version'],'1.4.3')
        self.app.record_verified('antigravity','gemini-3.8-flash','mission de test')
        self.assertEqual(Atelier(self.root/'data',scheduler=False).connections()[2]['verified']['model'],'gemini-3.8-flash')
    def test_signed_out_reported(self):
        def run(binary,args,env,timeout):
            class R:returncode=1;stdout='Fetching available models...\n';stderr='Error: Please sign in to view available models.'
            return R()
        with patch.object(agy,'_run',side_effect=run):self.assertEqual(agy.account_status('/bin/agy',{})['status'],'disconnected')
    def test_connection_test_runs_real_cli_path(self):
        fake=self.fake('agy',[OK[0],{'event':'result','result':{'status':'SUCCESS','response':'prêt'}}])
        with patch('server.shutil.which',side_effect=lambda p:str(fake) if p=='agy' else None):
            r=self.app.test_antigravity('')
        self.assertEqual(r['response'],'prêt');self.assertEqual(self.app.settings['verified']['antigravity']['source'],'test mission')
        failing=self.fake('agy2',[{'event':'result','result':{'status':'ERROR','error':'authentication failed'}}],1)
        with patch('server.shutil.which',side_effect=lambda p:str(failing) if p=='agy' else None):
            with self.assertRaisesRegex(Problem,'Test mission failed'):self.app.test_antigravity('')
    def test_no_api_keys_inherited(self):
        with patch.dict(os.environ,{'GEMINI_API_KEY':'secret','GOOGLE_API_KEY':'secret','AGY_ADC_AUTH':'true'}):
            env=self.app.agent_env()
        for k in ('GEMINI_API_KEY','GOOGLE_API_KEY','AGY_ADC_AUTH'):self.assertNotIn(k,env)

    def test_real_runner_mixed_relay_with_fake_clis(self):
        bins={'claude':self.fake('claude',[{'type':'result','is_error':False,'result':'Analyse transmise par Claude.'}]),
              'agy':self.fake('agy',OK[:1]+[{'event':'result','result':{'status':'SUCCESS','response':'Comparaison reçue de Gemini.'}}]),
              'codex':self.fake('codex',[{'type':'item.completed','item':{'type':'agent_message','text':'Livrable Codex.'}},{'type':'turn.completed'}])}
        with patch('server.shutil.which',side_effect=lambda p:str(bins[p])):
            m=self.team_mission();self.app.run_mission(m['id'])
        finished=self.app.store.get('missions',m['id']);self.assertEqual(finished['status'],'completed')
        self.assertEqual(finished['steps'][1]['result'],'Comparaison reçue de Gemini.')
        sent=json.loads((self.root/'agy.stdin').read_text())['message']['content']
        self.assertIn('Analyse transmise par Claude.',sent)
        codex_prompt=(self.app.mission_dir(m['id'])/'agents/03-producteur/CONSIGNES.md').read_text()
        self.assertIn('Comparaison reçue de Gemini.',codex_prompt)
        self.assertEqual(self.app.settings['verified']['antigravity']['source'],'mission "Mission mixte"')

if __name__=='__main__':unittest.main(verbosity=2)
