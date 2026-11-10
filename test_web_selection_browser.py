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
     data = (root / 'web/index.html').read_text().replace('__CSRF__', 'test').replace('__PROJECTS_HTML__', html.escape(json.dumps([{'id': x, 'name': x, 'publish': True} for x in 'abc']), quote=True)).replace('<script src="/app.js" defer></script>', '<script>' + script + '</script><script src="/app.js" defer></script>').encode(); typ = 'text/html'
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
     self.assertEqual(result, ['a,b,c'])
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
