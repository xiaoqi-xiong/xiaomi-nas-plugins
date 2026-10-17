const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const code = fs.readFileSync(require('node:path').join(__dirname, '../music/songloft-ui-bridge.js'), 'utf8');
function app(enabled = true) {
  class Storage {
    constructor() { this.data = new Map(); }
    getItem(k) { return this.data.get(k) ?? null; }
    setItem(k,v) { this.data.set(k,String(v)); }
    removeItem(k) { this.data.delete(k); }
  }
  class XHR {
    open(method,url) { this.url=url; }
    setRequestHeader(k,v) { this.headers ??= {}; this.headers[k]=v; }
  }
  const calls=[], storage=new Storage(); storage.setItem('flutter.access_token','"stale"');
  const window={localStorage:storage,XMLHttpRequest:XHR,alert:assert.fail,fetch:async (url,init={})=>{
    calls.push({url:String(url),init});return {ok:enabled,json:async()=>({access_token:'fresh-app-token',refresh_token:'app-refresh',expires_in:600})};
  }};
  vm.runInNewContext(code,{window,Storage,URL,Headers,Request,Date,
    document:{currentScript:{src:'https://nas/plugin/123/music/songloft-ui-bridge.js'}},
    location:{href:'https://nas/plugin/123/music/vendor-ui/index.html'}});
  return {window,storage,calls};
}
test('official SharedPreferences hydrates before Flutter startup',async()=>{
  const {window,storage,calls}=app();await window.nativeSongloftReady;
  assert.equal(JSON.parse(storage.getItem('flutter.access_token')),'fresh-app-token');
  assert.equal(JSON.parse(storage.getItem('flutter.refresh_token')),'app-refresh');
  assert.equal(calls.length,1);assert.ok(calls[0].url.endsWith('/music/music.cgi?api=session'));
});
test('disabled login clears stale tokens without accidentally forgetting credentials again',async()=>{
  const {window,storage,calls}=app(false);await window.nativeSongloftReady;
  assert.equal(storage.getItem('flutter.access_token'),null);assert.equal(calls.length,1);
});
test('official requests map into the music gateway and remove token query parameters',async()=>{
  const {window,calls}=app();await window.nativeSongloftReady;
  await window.fetch('https://nas/api/v1/songs/1/play?access_token=secret&v=1',{headers:{Authorization:'Bearer app-token'}});
  const request=calls.at(-1);assert.equal(request.url,'https://nas/plugin/123/music/vendor-ui/api/v1/songs/1/play?v=1');
  assert.equal(request.init.headers.get('Authorization'),null);
  assert.equal(request.init.headers.get('X-Music-Authorization'),'Bearer app-token');
  const xhr=new window.XMLHttpRequest();xhr.open('GET','https://nas/api/v1/songs');xhr.setRequestHeader('Authorization','Bearer app-token');
  assert.equal(xhr.headers['X-Music-Authorization'],'Bearer app-token');
});
test('official logout disables remembered music login',async()=>{
  const {window,storage,calls}=app();await window.nativeSongloftReady;
  storage.removeItem('flutter.access_token');await new Promise(r=>setImmediate(r));
  assert.equal(calls.filter(x=>x.init.method==='DELETE').length,1);
});
