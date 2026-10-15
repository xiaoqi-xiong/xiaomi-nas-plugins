const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');

const format = require('../resource-monitor/monitor.js');

test('overview exposes system identity fields while retaining the resource-monitor route', () => {
  const html = fs.readFileSync(path.join(__dirname, '../resource-monitor/index.html'), 'utf8');
  for (const id of ['system-model', 'system-cpu-model', 'system-memory-total', 'system-firmware', 'system-os', 'system-architecture', 'system-kernel', 'overview-uptime']) {
    assert.match(html, new RegExp(`id="${id}"`));
  }
  assert.match(html, /<title>系统监控<\/title>/);
  assert.doesNotMatch(html, /资源监控/);
  assert.match(html, /monitor\.cgi\?asset=icon\.svg/);
});

test('numeric formatting preserves zero and treats missing values as unknown', () => {
  assert.equal(format.finite(0), 0);
  assert.equal(format.finite(' 0 '), 0);
  assert.equal(format.finite(null), null);
  assert.equal(format.finite(undefined), null);
  assert.equal(format.finite(''), null);
  assert.equal(format.finite('   '), null);
  assert.equal(format.finite(false), null);
  assert.equal(format.finite([]), null);
  assert.equal(format.finite('not-a-number'), null);
  assert.equal(format.fmtBytes(0), '0 B');
  assert.equal(format.fmtBytes(null), '—');
  assert.equal(format.fmtRate(0), '0 B/s');
  assert.equal(format.fmtRate(undefined), '—');
  assert.equal(format.fmtPercent(0), '0%');
  assert.equal(format.fmtPercent(null), '—');
  assert.equal(format.displayText(' Xiaomi NAS '), 'Xiaomi NAS');
  assert.equal(format.displayText(null, '未知'), '未知');
  assert.equal(format.displayText('', '未提供'), '未提供');
  assert.equal(format.fmtCoreCount(0), '0 核');
  assert.equal(format.fmtCoreCount(null), '未知');
});

test('aggregates remain unknown when any sampled rate is unknown', () => {
  assert.equal(format.sumKnown([{ rx_rate: 0 }, { rx_rate: 4 }], 'rx_rate'), 4);
  assert.equal(format.sumKnown([{ rx_rate: 0 }, { rx_rate: null }], 'rx_rate'), null);
  assert.equal(format.sumKnown([], 'rx_rate'), null);
});
