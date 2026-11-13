import html
import http.server
import json
import pathlib
import shutil
import subprocess
import tempfile
import threading
import unittest

root = pathlib.Path(__file__).resolve().parent
state={'status':'completed','project':'a','project_name':'A','mode':'development','workflow':'direct','engine':'app-server','requested_profile':'simple','thread_id':'old','events':[],'cursor':0}
script='''
let controller; const calls=[]; const old=STATE;
window.fetch=async (url,options={})=>{
 if(url==='/api/session') return new Response(JSON.stringify(old));
 if(url==='/api/maintenance') return new Response('{}');
 if(url==='/api/events') return new Response(new ReadableStream({start(c){controller=c}}));
 if(url==='/api/run'){ calls.push(JSON.parse(options.body)); return new Response('{}'); }
 if(url==='/api/message'){ calls.push({message:JSON.parse(options.body)}); return new Response('{}'); }
 return new Response('{}');
};
function emit(s){ controller.enqueue(new TextEncoder().encode('event: state\\ndata: '+JSON.stringify(s)+'\\n\\n')); }
async function test(){
 await new Promise(r=>setTimeout(r,100));
 const el=id=>document.getElementById(id), change=id=>el(id).dispatchEvent(new Event('change'));
 const selectors=['project','mode','engine','profile','workflow'];
 if(selectors.some(id=>!el(id).disabled)) throw Error('completed session selectors unlocked');
 el('message').value='continue'; el('send-message').click(); await new Promise(r=>setTimeout(r,20));
 if(JSON.stringify(calls.shift())!==JSON.stringify({message:{prompt:'continue'}})) throw Error('wrong continuation request');
 emit(old); await new Promise(r=>setTimeout(r,20));
 if(el('project').value!=='a' || selectors.some(id=>!el(id).disabled)) throw Error('original session changed');
 for(const [i,p] of ['a','b','c'].entries()){
   el('new-task').click();
   if(['project','mode','profile','workflow'].some(id=>el(id).disabled)) throw Error('new task selectors locked');
   el('project').value=p; change('project'); el('mode').value='development'; change('mode'); el('workflow').value='direct'; change('workflow'); el('profile').value='complex'; el('prompt').value='task';
   emit(old); await new Promise(r=>setTimeout(r,20));
   if(el('project').value!==p || el('profile').value!=='complex' || el('project').disabled) throw Error('draft overwritten '+p);
   el('start').click(); await new Promise(r=>setTimeout(r,20));
   if(calls[i].project!==p || calls[i].profile!=='complex' || calls[i].mode!=='development' || calls[i].workflow!=='direct') throw Error('wrong request '+JSON.stringify(calls[i]));
   emit({...old,project:p,project_name:p}); await new Promise(r=>setTimeout(r,20));
 }
 return calls.map(x=>x.project).join(',');
}
window.addEventListener('load',()=>test().then(x=>report(x)).catch(e=>report('FAIL '+e.stack)));
function report(x){navigator.sendBeacon('/result',x)}
'''.replace('STATE',json.dumps(state))
class WebSelectionBrowserTests(unittest.TestCase):
 def test_project_selection_survives_state_updates(self):
  self.run_browser(script, 'a,b,c')

 def test_reviewed_handoff_and_isolated_new_task(self):
  handoff_state = {**state, 'workflow': 'automatic', 'effective_workflow': 'orchestrated',
                   'handoff': {'source': 'revision-one', 'summary': 'Objetivo: corrigir arquivo.py. Aguardar aprovação.'}}
  handoff_script = script[:script.index('async function test(){')] + r"""
async function test(){
 await new Promise(r=>setTimeout(r,100));
 const el=id=>document.getElementById(id), change=id=>el(id).dispatchEvent(new Event('change'));
 if(!el('composer').classList.contains('hidden')) throw Error('orchestrated composer shown');
 if(el('continue-work').classList.contains('hidden')) throw Error('handoff missing');
 el('continue-work').click();
 if(el('handoff-preview').classList.contains('hidden')) throw Error('preview missing');
 if(el('mode').value!=='reading' || !el('project').disabled) throw Error('unsafe defaults');
 el('prompt').value='seguir com a correção';
 el('start').click(); await new Promise(r=>setTimeout(r,20));
 if(calls.length) throw Error('unconfirmed handoff accepted');
 el('handoff-summary').value+=' Revisado.'; el('handoff-confirm').checked=true;
 el('mode').value='development'; change('mode'); el('workflow').value='direct'; change('workflow');
 emit(old); await new Promise(r=>setTimeout(r,20));
 if(!el('handoff-summary').value.endsWith('Revisado.')) throw Error('draft overwritten');
 el('start').click(); await new Promise(r=>setTimeout(r,20));
 const request=calls.shift();
 if(!request.handoff.confirmed || request.handoff.source!=='revision-one' || !request.handoff.summary.endsWith('Revisado.')) throw Error('missing reviewed handoff');
 if(request.project!=='a' || request.workflow!=='direct' || request.publish_draft_pr) throw Error('invalid new execution');
 emit({...old,workflow:'direct',effective_workflow:'direct',thread_id:'new-thread'});
 await new Promise(r=>setTimeout(r,20));
 if(el('composer').classList.contains('hidden')) throw Error('direct continuation hidden');
 el('new-task').click();
 if(!el('handoff-preview').classList.contains('hidden') || el('handoff-confirm').checked) throw Error('handoff not discarded');
 el('prompt').value='outra tarefa'; el('start').click(); await new Promise(r=>setTimeout(r,20));
 if(calls.shift().handoff) throw Error('context leaked to new task');
 return 'handoff reviewed; new task isolated';
}
window.addEventListener('load',()=>test().then(x=>report(x)).catch(e=>report('FAIL '+e.stack)));
function report(x){navigator.sendBeacon('/result',x)}
"""
  handoff_script = handoff_script.replace('const old='+json.dumps(state), 'const old='+json.dumps(handoff_state))
  self.run_browser(handoff_script, 'handoff reviewed; new task isolated')

 def test_complete_report_preview_privacy_truncation_and_clipboard(self):
  report_state = {
   **state, 'result': 'RESULT_SECRET ' + 'z' * 13000,
   'prompt': 'PROMPT_SECRET completo', 'thread_id': 'THREAD_SECRET',
   'csrf': 'CSRF_SECRET', 'approvals': [{'id':'APPROVAL_SECRET'}],
   'rate_limits': {'token':'QUOTA_SECRET'},
   'changed_files': ['/home/private/CLIENT_SECRET/file.py'],
   'requested_workflow':'automatic', 'effective_workflow':'orchestrated',
   'requested_model':'Luna', 'requested_effort':'low', 'observed_model':'OBSERVED_SECRET\nInjected heading', 'observed_effort':'ROUTE_SECRET\n# injetado', 'route_reason':'ROUTE_SECRET', 'workflow_reason':'WORKFLOW_SECRET', 'publish_result':'PUBLISH_SECRET', 'publish_authorized':True,
   'agents': {'planning': {'role':'Etapa','name':'Planejamento','status':'completed','input_tokens':12}},
   'duration_seconds': 4.2,
  }
  report_script = script[:script.index('async function test(){')] + r"""
let clipboardValue=''; let clipboardFails=false;
Object.defineProperty(navigator, 'clipboard', { configurable:true, value:{writeText: async text=>{if(clipboardFails) throw Error('denied'); clipboardValue=text;}} });
async function test(){
 await new Promise(r=>setTimeout(r,100));
 const el=id=>document.getElementById(id);
 const check=(ok,msg)=>{if(!ok) throw Error(msg)};
 check(!el('report-section').classList.contains('hidden'),'report unavailable after completion');
 el('report-preview-button').click();
 check(!el('report-preview').classList.contains('hidden'),'preview did not open');
 let text=el('report-text').value;
 for(const heading of ['Identificação','Execução','Objetivo','Resultado','Mudanças','Bloqueios','Etapas'])
   check(text.includes(heading),'missing section '+heading);
 check(text.includes('Gerado em:'),'generation timestamp missing');
 check(text.includes('Total da orquestração indisponível.'),'fabricated orchestration total');
 check(text.includes('Modelo/esforço observado: indisponível'),'unobserved model fabricated');
 check(text.includes('PROMPT_SECRET')===false && text.includes('THREAD_SECRET')===false && text.includes('CSRF_SECRET')===false && text.includes('ROUTE_SECRET')===false && text.includes('WORKFLOW_SECRET')===false && text.includes('PUBLISH_SECRET')===false && text.includes('OBSERVED_SECRET')===false && text.includes('# injetado')===false,'private structured fields leaked');
 check(text.includes('APPROVAL_SECRET')===false && text.includes('QUOTA_SECRET')===false,'approval or quota leaked');
 check(text.includes('/home/private')===false && text.includes('CLIENT_SECRET')===false,'absolute path leaked');
 check(text.includes('RESULT_SECRET') && text.includes('[trecho omitido]'),'long result not explicitly truncated');
 check(el('report-preview').textContent.includes('área de transferência'),'clipboard notice missing');
 check(clipboardValue==='','copied without user action');
 el('report-text').value='texto revisto pelo usuário';
 el('report-copy').click(); await new Promise(r=>setTimeout(r,20));
 check(clipboardValue==='texto revisto pelo usuário','edited preview not copied');
 check(el('report-feedback').textContent==='Relatório copiado.','copy success not reported');
 clipboardFails=true; el('report-copy').click(); await new Promise(r=>setTimeout(r,20));
 check(el('report-feedback').textContent.includes('Não foi possível copiar'),'copy failure falsely reported');
 emit({...old,status:'running'}); await new Promise(r=>setTimeout(r,20));
 check(el('report-section').classList.contains('hidden'),'report active before terminal state');
 emit({...old,status:'failed',error:'Falha observada'}); await new Promise(r=>setTimeout(r,20));
 check(!el('report-section').classList.contains('hidden'),'report unavailable after failure');
 el('report-preview-button').click(); text=el('report-text').value;
 check(text.includes('Estado: Falhou') && text.includes('Falha observada'),'failure not represented');
 emit({...old,status:'interrupted',error:''}); await new Promise(r=>setTimeout(r,20));
 check(!el('report-section').classList.contains('hidden'),'report unavailable after cancel');
 el('report-preview-button').click(); text=el('report-text').value;
 check(text.includes('Estado: Cancelada'),'cancellation not represented');
 emit({...old,status:'completed',requested_workflow:'automatic',effective_workflow:'direct',workflow:'automatic',observed_model:'gpt-6-luna',observed_effort:'low',input_tokens:14,output_tokens:5}); await new Promise(r=>setTimeout(r,20));
 el('report-preview-button').click(); text=el('report-text').value;
 check(text.includes('Fluxo efetivo: Direta') && text.includes('Modelo/esforço observado: gpt-6-luna / low'),'direct observed route missing');
 check(text.includes('Entrada 14, cache indisponível, saída 5 tokens'),'direct usage provenance missing');
 return 'report privacy, preview, truncation, states, edited copy and clipboard errors verified';
}
window.addEventListener('load',()=>test().then(x=>report(x)).catch(e=>report('FAIL '+e.message+' '+e.stack)));
function report(x){navigator.sendBeacon('/result',x)}
"""
  report_script=report_script.replace('const old='+json.dumps(state), 'const old='+json.dumps(report_state))
  self.run_browser(report_script, 'report privacy, preview, truncation, states, edited copy and clipboard errors verified')

 def run_browser(self, browser_script, expected):
  firefox = shutil.which('firefox')
  if not firefox:
   self.skipTest('Firefox não está disponível no PATH')

  result = []
  received = threading.Event()

  class Handler(http.server.BaseHTTPRequestHandler):
   def log_message(self, *args): pass

   def do_GET(self):
    if self.path == '/app.js':
     data = (root / 'web/app.js').read_bytes(); typ = 'text/javascript'
    else:
     data = (root / 'web/index.html').read_text().replace('__CSRF__', 'test').replace('__PROJECTS_HTML__', html.escape(json.dumps([{'id': x, 'name': x, 'publish': True} for x in 'abc']), quote=True)).replace('<script src="/app.js" defer></script>', '<script>' + browser_script + '</script><script src="/app.js" defer></script>').encode(); typ = 'text/html'
    self.send_response(200); self.send_header('Content-Type', typ); self.end_headers(); self.wfile.write(data)

   def do_POST(self):
    result.append(self.rfile.read(int(self.headers['Content-Length'])).decode())
    self.send_response(200); self.end_headers()
    received.set()

  server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
  server.daemon_threads = True
  thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.1}, daemon=True)
  try:
   thread.start()
   with tempfile.TemporaryDirectory() as profile:
    process = subprocess.Popen([firefox, '--headless', '--no-remote', '--profile', profile, f'http://127.0.0.1:{server.server_port}/'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
     self.assertTrue(received.wait(8), 'Firefox não enviou o resultado em 8 segundos')
     self.assertEqual(result, [expected])
    finally:
     process.terminate()
     try:
      process.wait(timeout=5)
     except subprocess.TimeoutExpired:
      process.kill()
      process.wait(timeout=5)
  finally:
   if thread.is_alive():
    server.shutdown()
    thread.join(timeout=5)
   server.server_close()


if __name__ == '__main__':
 unittest.main()
