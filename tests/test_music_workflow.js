const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

const code = fs.readFileSync(path.join(__dirname, '../music/workflow.js'), 'utf8');

function workflow({publicPath, href}) {
  const frames = [];
  const buttons = ['player', 'search', 'downloads'].map(view => ({
    dataset: {view},
    setAttribute() {},
  }));
  const error = {hidden: true, textContent: ''};
  const back = {hidden: false, onclick: null};
  const container = {append(frame) { frames.push(frame); }};
  const document = {
    getElementById(id) {
      return id === 'workflow-error' ? error : id === 'music-native-back' ? back : container;
    },
    querySelectorAll() { return buttons; },
    createElement() { return {hidden: false, setAttribute() {}}; },
  };
  const window = {__MICRO_APP_PUBLIC_PATH__: publicPath};
  vm.runInNewContext(code, {
    window,
    location: {href},
    navigator: {userAgent: '', maxTouchPoints: 0},
    document,
    URL,
  });
  buttons.find(button => button.dataset.view === 'search').onclick();
  return frames.map(frame => frame.src);
}

test('Music search stays under the hinted Music plugin path', () => {
  const frames = workflow({
    publicPath: 'https://nas/plugin/test-space/music',
    href: 'https://nas/plugin/test-space/music/workflow.html',
  });
  assert.deepEqual(frames, [
    'https://nas/plugin/test-space/music/vendor-ui/index.html',
    'https://nas/plugin/test-space/music/pansou/vendor-ui/index.html?music=1',
  ]);
});

test('Music search uses the document directory when no public path hint exists', () => {
  const frames = workflow({
    publicPath: '',
    href: 'https://nas/plugin/test-space/music/workflow.html',
  });
  assert.equal(frames[1], 'https://nas/plugin/test-space/music/pansou/vendor-ui/index.html?music=1');
});
