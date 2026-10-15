#!/usr/bin/python3
"""Owner-scoped, allowlisted Mihomo controller. Never expose credentials/config."""
import http.client,json,os,sys
from pathlib import Path
from urllib.parse import urlsplit,quote,urlencode
ROOT=Path(__file__).resolve().parent
APP_ROOT=ROOT.parent
sys.path.insert(0,str(APP_ROOT))
from settings import get_settings, owner_is_valid
SETTINGS=get_settings(APP_ROOT)
path=urlsplit(os.environ.get('FILE_URI','')).path.lstrip('/')
method=os.environ.get('REQUEST_METHOD','GET')
def respond(status,kind,body):
 if isinstance(body,str):body=body.encode()
 sys.stdout.buffer.write(('Status: '+status+'\r\nContent-Type: '+kind+'\r\nCache-Control: no-store\r\nContent-Length: '+str(len(body))+'\r\n\r\n').encode()+body)
def answer(value,status='200 OK'):respond(status,'application/json; charset=utf-8',json.dumps(value,ensure_ascii=False))
def api():
 try:
  token=json.loads((APP_ROOT/'private/controller.json').read_text())['secret']
  def request(route,verb='GET',payload=None):
   c=http.client.HTTPConnection(SETTINGS['proxy_controller_host'], SETTINGS['ports']['proxy_controller'], timeout=10)
   try:
    c.request(verb,route,body=json.dumps(payload).encode() if payload else None,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    r=c.getresponse();raw=r.read()
    if r.status>=400:raise ValueError('controller')
    return json.loads(raw) if raw else {}
   finally:c.close()
  proxies=request('/proxies')['proxies']
  groups={name:p for name,p in proxies.items() if p.get('type')=='Selector' and name!='GLOBAL'}
  if path=='native/status' and method=='GET':
   v=request('/version');config=request('/configs');connections=request('/connections')
   def node(name):
    p=proxies.get(name,{});h=p.get('history') or [];d=h[-1].get('delay') if h else None
    return {'name':name,'type':p.get('type','Unknown'),'delay':d if isinstance(d,int) and d>0 else None}
   answer({'version':v.get('version',''),'mode':config.get('mode',''),'tun':bool(config.get('tun',{}).get('enable')),'connections':len(connections.get('connections',[])),'download_total':connections.get('downloadTotal',0),'upload_total':connections.get('uploadTotal',0),'groups':[{'name':name,'current':p.get('now',''),'nodes':[node(n) for n in p.get('all',[])]} for name,p in groups.items()]})
   return
  if method!='POST' or path not in ['native/select','native/delay']:
   answer({'error':'不支持此操作'},'405 Method Not Allowed');return
  length=int(os.environ.get('CONTENT_LENGTH') or 0)
  if not 0<length<=4096 or not os.environ.get('CONTENT_TYPE','').startswith('application/json'):
   answer({'error':'请求格式不正确'},'400 Bad Request');return
  data=json.loads(sys.stdin.buffer.read(length));group=data.get('group');name=data.get('name')
  if not isinstance(group,str) or not isinstance(name,str) or group not in groups or name not in groups[group].get('all',[]):
   answer({'error':'请选择已有节点'},'400 Bad Request');return
  if path=='native/select':
   request('/proxies/'+quote(group,safe=''),'PUT',{'name':name})
   actual=request('/proxies')['proxies'].get(group,{}).get('now')
   if actual!=name:raise ValueError('selection')
   answer({'selected':actual,'group':group});return
  # One explicit test, fixed connectivity URL. No arbitrary destination or config API.
  result=request('/proxies/'+quote(name,safe='')+'/delay?'+urlencode({'timeout':6000,'url':'https://www.gstatic.com/generate_204'}))
  d=result.get('delay')
  if not isinstance(d,int) or d<=0:raise ValueError('delay')
  answer({'name':name,'delay':d})
 except (OSError,ValueError,KeyError,TypeError,http.client.HTTPException):
  answer({'error':'代理未响应，或节点测试超时。请稍后重试。'},'502 Bad Gateway')
if not owner_is_valid(SETTINGS, os.environ.get('USER', ''), os.getuid()):
 respond('403 Forbidden','text/plain','Native owner login required')
elif path.startswith('native/'):
 api()
elif method not in ['GET','HEAD']:
 respond('405 Method Not Allowed','text/plain','Unsupported method')
elif path in ['', 'index.html','ui.html','proxy.js','proxy.css','native-bridge.js','icon.png']:
 asset='index.html' if not path else path
 p=APP_ROOT/"mediastack/native-bridge.js" if asset=="native-bridge.js" else ROOT/asset
 kind='text/html; charset=utf-8' if asset.endswith('.html') else 'application/javascript' if asset.endswith('.js') else 'text/css' if asset.endswith('.css') else 'image/png'
 respond('200 OK',kind,p.read_bytes())
else:respond('404 Not Found','text/plain','Unknown proxy route')
