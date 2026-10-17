const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '../mteam/index.html'), 'utf8');
const script = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)][0][1];

function render({width=1440, height=900, userAgent='Desktop Browser', touch=0, bridge=false}={}) {
  const elements = new Map();
  const timers = [];
  const document = {getElementById(id) {
    if (!elements.has(id)) elements.set(id, {hidden: id === 'fallback', addEventListener(event, callback) {this[event] = callback;}});
    return elements.get(id);
  }};
  const window = {matchMedia() {return {matches: width <= 760 || (width > height && height <= 560)};}};
  if (bridge) window.flutter_inappwebview = {};
  vm.runInNewContext(script, {window, document, navigator:{userAgent,maxTouchPoints:touch},
    setTimeout(callback) {timers.push(callback);}});
  return {elements,timers};
}

test('phone portrait, narrow screen and landscape use the official mobile site', () => {
  for (const viewport of [{width:320,height:740},{width:390,height:844},{width:844,height:390},
      {width:1024,height:768,userAgent:'iPad'},{bridge:true}]) {
    const {elements} = render(viewport);
    assert.equal(elements.get('mteam-frame').src, 'https://h5.m-team.cc/');
    assert.equal(elements.get('official-link').href, 'https://h5.m-team.cc/');
    assert.equal(elements.get('fallback-link').href, 'https://h5.m-team.cc/');
    assert.equal(elements.get('native-toolbar').hidden, false);
  }
});

test('desktop keeps the desktop site and no initial desktop iframe request is made on phones', () => {
  const {elements} = render();
  assert.equal(elements.get('mteam-frame').src, 'https://kp.m-team.cc/');
  assert.equal(elements.get('official-link').href, 'https://kp.m-team.cc/');
  assert.equal(elements.get('native-toolbar').hidden, true);
  const iframe = html.match(/<iframe\b[^>]*id="mteam-frame"[^>]*>/)[0];
  assert.doesNotMatch(iframe, /\bsrc=/);
});

test('fallback stays available for a delayed page and hides after a load', () => {
  const {elements,timers} = render({width:390,height:844});
  timers[0]();
  assert.equal(elements.get('fallback').hidden, false);
  elements.get('mteam-frame').load();
  assert.equal(elements.get('fallback').hidden, true);
});
