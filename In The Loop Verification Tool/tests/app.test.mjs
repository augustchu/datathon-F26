import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import * as data from '../lib/data.js';
import * as audit from '../lib/audit.js';

// A small DOM adapter exercises the app's actual event handlers without a browser
// dependency. Layout, image rendering, and XLSX uploads are covered by browser QA.
const appSource = readFileSync(new URL('../app.js', import.meta.url), 'utf8')
  .replace(/^import[^\n]+\n/gm, '')
  .replaceAll('import.meta.url', "'http://localhost:4173/app.js'");
const storageKey = 'in-the-loop:active:v1';

class Element {
  constructor() {
    this.events = new Map();
    this.style = {};
    this.dataset = {};
    this.attributes = {};
    this.options = [];
    this.value = '';
    this.checked = false;
    this.disabled = false;
    this.hidden = false;
    this.textContent = '';
    this.innerHTML = '';
    this.classList = { add() {}, remove() {} };
  }
  addEventListener(type, handler) { this.events.set(type, handler); }
  dispatch(type, event = {}) { return this.events.get(type)?.({ target: this, preventDefault() {}, ...event }); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  removeAttribute(name) { delete this.attributes[name]; }
  replaceChildren(...children) { this.options = children; }
  add(option) { this.options.push(option); }
  scrollTo() {}
  showModal() { this.open = true; }
  close() { this.open = false; }
  focus() {}
}

function fixture(count = 64) {
  return data.normalizeRows(Array.from({ length: count }, (_, index) => ({
    id: `asset-${index + 1}`,
    title: `Asset ${index + 1}`,
    screenshot_path: `assets/${index + 1}.png`,
    claim: `Claim ${index + 1}`,
  })));
}

async function harness(records = fixture(), savedStorage, { linkOnly = false } = {}) {
  const elements = new Map();
  const get = selector => {
    if (!elements.has(selector)) elements.set(selector, new Element());
    return elements.get(selector);
  };
  const ratings = ['rejected', 'inconclusive', 'accepted'].map(status => {
    const button = new Element(); button.dataset.rate = status; return button;
  });
  const activeStorage = linkOnly ? `${storageKey}:links` : storageKey;
  const storage = savedStorage || new Map([[activeStorage, JSON.stringify({
    records, selectedId: records[0].id, expanded: 'pending',
    settings: { repository: 'yana-157/in-the-loop-verification-tool', ref: 'main' },
    name: 'Test spreadsheet', isSample: false,
  })]]);
  const objectUrls = new Map(), downloads = [];
  class TestURL extends URL {
    static createObjectURL(value) {
      const url = `blob:test-${objectUrls.size + 1}`;
      objectUrls.set(url, value);
      return url;
    }
    static revokeObjectURL() {}
  }
  const context = vm.createContext({
    ...data, ...audit, Blob,
    document: {
      querySelector: selector => selector === 'dialog[open]' ? null : get(selector),
      querySelectorAll: selector => selector === '[data-rate]' ? ratings : [],
      getElementById: id => get(`#${id}`),
      addEventListener() {},
      body: { append() {} },
      createElement: () => ({ click() { downloads.push({ filename: this.download, blob: objectUrls.get(this.href) }); }, remove() {} }),
    },
    location: { href: `http://localhost:4173/${linkOnly ? '?mode=links' : ''}` },
    URL: TestURL,
    Option: class { constructor(label, value) { this.label = label; this.value = value; } },
    localStorage: {
      getItem: key => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, value),
    },
    // Toast timers are irrelevant to these checks and must not keep Node alive.
    setTimeout: () => 0, clearTimeout() {}, console,
  });
  await vm.runInContext(`(async () => {\n${appSource}\n})()`, context);
  return {
    get, storage, downloads,
    state: () => JSON.parse(storage.get(activeStorage)),
    clickQueue: dataset => get('#navigators').dispatch('click', { target: { closest: () => ({ dataset }) } }),
    rate: status => ratings.find(button => button.dataset.rate === status).dispatch('click'),
  };
}

test('queue controls support arbitrary pages and selections with only one queue expanded', async () => {
  const app = await harness();
  app.clickQueue({ size: '1', status: 'pending' });
  app.clickQueue({ pageChange: '1', status: 'pending' });
  assert.deepEqual(app.state().queues.pending, { page: 2, size: 1 });
  app.clickQueue({ size: '10', status: 'pending' });
  app.get('#navigators').dispatch('change', { target: { dataset: { pageSelect: 'pending' }, value: '6' } });
  assert.match(app.get('#navigators').innerHTML, /data-record="asset-60"/);
  app.clickQueue({ record: 'asset-60' });
  assert.equal(app.get('#asset-id').textContent, 'asset-60');
  app.rate('accepted');
  assert.equal(app.get('#reviewed-count').textContent, 1);
  assert.equal(app.get('#remaining-count').textContent, 63);
  app.clickQueue({ size: '50', status: 'pending' });
  app.clickQueue({ pageChange: '1', status: 'pending' });
  assert.equal(app.state().queues.pending.page, 2);
  assert.match(app.get('#navigators').innerHTML, /data-record="asset-64"/);
  app.clickQueue({ queue: 'accepted' });
  assert.equal((app.get('#navigators').innerHTML.match(/aria-expanded="true"/g) || []).length, 1);
  assert.equal(app.get('#asset-id').textContent, 'asset-60');
  app.clickQueue({ queue: 'rejected' });
  assert.equal((app.get('#navigators').innerHTML.match(/aria-expanded="true"/g) || []).length, 1);
  assert.equal(app.get('#empty-state').hidden, false);
});

test('collapsing the navigator preserves previous and next asset navigation', async () => {
  const app = await harness(fixture(3));
  app.clickQueue({ queue: 'pending' });
  assert.equal((app.get('#navigators').innerHTML.match(/aria-expanded="true"/g) || []).length, 0);
  app.get('#next-asset').dispatch('click');
  assert.equal(app.get('#asset-id').textContent, 'asset-2');
  app.get('#previous-asset').dispatch('click');
  assert.equal(app.get('#asset-id').textContent, 'asset-1');
});

test('reviews, notes, and selection survive a page reload', async () => {
  const app = await harness(fixture(3));
  app.clickQueue({ record: 'asset-2' });
  app.rate('inconclusive');
  app.get('#review-notes').value = 'The screenshot does not identify this branch.';
  app.get('#review-notes').dispatch('input');
  const restored = await harness(undefined, app.storage);
  assert.equal(restored.get('#asset-id').textContent, 'asset-2');
  assert.equal(restored.get('#current-status').textContent, 'Inconclusive');
  assert.equal(restored.get('#review-notes').value, 'The screenshot does not identify this branch.');
  assert.equal(restored.get('#reviewed-count').textContent, 1);
  restored.get('#clear-rating').dispatch('click');
  assert.equal(restored.get('#reviewed-count').textContent, 0);
  assert.equal(restored.state().records[1].status, 'pending');
});

test('local attachments reject ambiguous basenames but allow repeated references to the same asset', async () => {
  const rows = paths => data.normalizeRows(paths.map((path, index) => ({ id: String(index), asset_path: path, claim: 'Claim' })));
  const attachmentEvent = { target: { files: [{ name: 'screen.png', type: 'image/png' }], value: 'screen.png' } };
  const ambiguous = await harness(rows(['one/screen.png', 'two/screen.png']));
  const originalUrl = ambiguous.get('#evidence-image').src;
  ambiguous.get('#screenshot-files').dispatch('change', attachmentEvent);
  assert.equal(ambiguous.get('#evidence-image').src, originalUrl);
  assert.match(ambiguous.get('#toast').textContent, /ambiguous/i);
  const shared = await harness(rows(['one/screen.png', 'one/screen.png']));
  shared.get('#screenshot-files').dispatch('change', attachmentEvent);
  assert.match(shared.get('#evidence-image').src, /^blob:/);
  shared.clickQueue({ record: '1' });
  assert.match(shared.get('#evidence-image').src, /^blob:/);
});

test('both screenshot views keep a safe original link and reset to fit width', async () => {
  const original = 'https://example.org/hospital?tab=about#capacity';
  const candidate = 'https://example.net/report.pdf#page=4';
  const app = await harness(data.normalizeRows([
    { id: 'first', screenshot_path: 'full-page.png', source_url: original, candidate_source_url: candidate },
    { id: 'second', screenshot_path: 'another-page.png', source_url: 'javascript:alert(1)', candidate_source_url: 'javascript:alert(2)' },
  ]));
  assert.equal(app.get('#source-link').attributes.href, original);
  assert.equal(app.get('#source-link').hidden, false);
  assert.match(app.get('#claim-fields').innerHTML, /href="https:\/\/example\.net\/report\.pdf#page=4"/);
  assert.equal(app.get('#zoom-reset').textContent, 'Fit width');
  app.get('#zoom-in').dispatch('click');
  assert.equal(app.get('#evidence-image').style.width, '125%');
  app.get('#zoom-reset').dispatch('click');
  assert.equal(app.get('#evidence-image').style.width, '100%');
  app.get('#evidence-image').onload();
  app.get('#expand-image').dispatch('click');
  assert.equal(app.get('#expanded-source-link').attributes.href, original);
  assert.equal(app.get('#expanded-image').src, app.get('#evidence-image').src);
  assert.equal(app.get('#actual-size').attributes['aria-pressed'], 'false');
  app.get('#actual-size').dispatch('click');
  assert.equal(app.get('#expanded-image-viewport').dataset.actualSize, 'true');
  app.get('#image-dialog').close();
  app.clickQueue({ record: 'second' });
  assert.equal(app.get('#source-link').hidden, true);
  assert.equal(app.get('#source-link').attributes.href, undefined);
  assert.doesNotMatch(app.get('#claim-fields').innerHTML, /href=/);
  app.get('#evidence-image').onload();
  app.get('#expand-image').dispatch('click');
  assert.equal(app.get('#expanded-source-link').hidden, true);
  assert.equal(app.get('#expanded-source-link').attributes.href, undefined);
  assert.equal(app.get('#actual-size').attributes['aria-pressed'], 'false');
});

test('link-only review opens the precise page and keeps an independent review draft', async () => {
  const original = 'https://example.org/branch?version=3#capacity';
  const records = data.normalizeRows([{ id: 'one', source_url: original, beds: '22' }], { mode: 'links' });
  const app = await harness(records, undefined, { linkOnly: true });
  assert.equal(app.get('#live-source-link').attributes.href, original);
  assert.equal(app.get('#live-source-address').textContent, original);
  assert.equal(app.get('#screenshot-evidence').hidden, true);
  assert.equal(app.get('#link-evidence').hidden, false);
  assert.equal(app.get('#repository-input').disabled, true);
  assert.equal(app.get('#evidence-image').src, undefined);
  const csv = 'id,document,claim\nnew,https://example.net/report.pdf#page=6,40 beds\n';
  await app.get('#spreadsheet-file').dispatch('change', {
    target: { files: [{ name: 'source-review.csv', size: csv.length, text: async () => csv }] },
  });
  app.get('#source-column').value = 'document';
  app.get('#import-form').dispatch('submit');
  assert.equal(app.get('#live-source-link').attributes.href, 'https://example.net/report.pdf#page=6');
  app.get('#review-notes').value = 'The table gives the branch total.';
  app.get('#review-notes').dispatch('input');
  app.rate('accepted');
  assert.equal(app.state().records[0].status, 'accepted');
  assert.equal(app.state().records[0].assetPath, '');
  assert.equal(app.storage.has(storageKey), false);
  const restored = await harness(records, app.storage, { linkOnly: true });
  assert.equal(restored.get('#review-notes').value, 'The table gives the branch total.');
  assert.equal(restored.get('#live-source-link').attributes.href, 'https://example.net/report.pdf#page=6');
});

test('link-only comparisons list both linked claims and surface conflicts in the queue', async () => {
  const rows = data.normalizeRows([
    { id: 'matching-one', branch_id: 'a', field: 'Capacity', value: '35', publisher: 'Provider', source_url: 'https://example.org/a#capacity' },
    { id: 'matching-two', branch_id: 'a', field: 'Capacity', value: '35', publisher: 'Directory', source_url: 'https://example.net/a.pdf#page=4' },
    { id: 'conflict-one', branch_id: 'b', field: 'Capacity', value: '50', publisher: 'Operator', source_url: 'https://example.org/b#capacity' },
    { id: 'conflict-two', branch_id: 'b', field: 'Capacity', value: '65', publisher: 'Registry', source_url: 'https://example.net/b.pdf#page=6', source_date: '2026-06-30' },
  ], { mode: 'links' });
  const app = await harness(rows, undefined, { linkOnly: true });
  assert.equal(app.get('#differences-option').hidden, false);
  assert.equal(app.get('#comparison-state').textContent, 'Values agree');
  assert.match(app.get('#navigators').innerHTML, /queued-difference/);
  app.get('#differences-only').checked = true;
  app.get('#differences-only').dispatch('change');
  assert.equal(app.get('#asset-id').textContent, 'conflict-one');
  assert.equal(app.get('#comparison-state').textContent, 'Values differ');
  assert.match(app.get('#comparison-claims').innerHTML, /https:\/\/example\.org\/b#capacity/);
  assert.match(app.get('#comparison-claims').innerHTML, /https:\/\/example\.net\/b\.pdf#page=6/);
  assert.match(app.get('#comparison-claims').innerHTML, /2026-06-30/);
  assert.match(app.get('#comparison-claims').innerHTML, /<strong>65<\/strong>/);
  assert.doesNotMatch(app.get('#navigators').innerHTML, /data-record="matching-one"/);
  app.get('#differences-only').checked = false;
  app.get('#differences-only').dispatch('change');
  assert.match(app.get('#navigators').innerHTML, /data-record="matching-one"/);
});

function calculateAudit(app, { alpha = '5', floor = '90', target = '98', power = '80' } = {}) {
  for (const [name, value] of Object.entries({ alpha, floor, target, power })) app.get(`#audit-${name}`).value = value;
  app.get('#audit-form').dispatch('submit');
}

test('the accuracy audit freezes the smallest random sample and keeps its views and outcomes independent', async () => {
  const records = data.normalizeRows(Array.from({ length: 161 }, (_, index) => ({
    id: `row-${index}`, case_id: 'one-branch', field: 'Capacity', value: String(index),
    publisher: index % 2 ? 'Operator' : 'Insurer', source_url: `https://example.org/hospital?row=${index}`,
  })), { mode: 'links' });
  records.forEach(record => { record.status = 'accepted'; record.notes = 'Earlier review'; });
  const app = await harness(records, undefined, { linkOnly: true });
  assert.equal(app.get('#source-comparison').hidden, false);
  calculateAudit(app, { alpha: '2' });
  const strict = app.get('#audit-preview').textContent;
  app.get('#audit-form').dispatch('input');
  assert.equal(app.get('#audit-start').disabled, true);
  calculateAudit(app);
  assert.notEqual(strict, app.get('#audit-preview').textContent);
  assert.match(app.get('#audit-preview').textContent, /39 random rows out of 161.*38 confirmed correct/);
  app.get('#audit-start').dispatch('click');
  const snapshotKey = `${app.state().key}:accuracy-audit:v1`;
  const readSnapshot = () => JSON.parse(app.storage.get(snapshotKey));
  const frozen = readSnapshot();
  assert.equal(frozen.reviews.length, 39);
  assert.equal(new Set(frozen.reviews.map(row => row.id)).size, 39);
  assert.ok(frozen.reviews.every(row => row.status === 'pending' && row.notes === ''));
  assert.equal(app.get('#total-count').textContent, 39);
  assert.equal(app.get('#source-comparison').hidden, true);
  assert.equal(app.get('#differences-option').hidden, true);
  const unselected = records.find(record => !frozen.reviews.some(row => row.id === record.id));
  app.clickQueue({ record: unselected.id });
  assert.equal(app.get('#asset-id').textContent, frozen.reviews[0].id);
  app.rate('accepted');
  assert.match(app.get('#audit-result').textContent, /38 sample rows still to review/);
  assert.doesNotMatch(app.get('#audit-result').textContent, /p =/);

  const restored = await harness(records, app.storage, { linkOnly: true });
  assert.equal(restored.get('#reviewed-count').textContent, 1);
  assert.deepEqual(readSnapshot().reviews.map(row => row.id), frozen.reviews.map(row => row.id));
  restored.get('#audit-start').dispatch('click');
  assert.deepEqual(readSnapshot().reviews.map(row => row.id), frozen.reviews.map(row => row.id));
  for (const [index, row] of frozen.reviews.entries()) {
    restored.clickQueue({ record: row.id });
    restored.rate(index < 37 ? 'accepted' : 'inconclusive');
  }
  assert.match(restored.get('#audit-result').textContent, /conclusion depends on unresolved rows/);
  restored.get('#export-button').dispatch('click');
  const [download] = restored.downloads;
  assert.equal(download.filename, 'Test spreadsheet-accuracy-audit.json');
  const receipt = JSON.parse(await download.blob.text());
  assert.equal(receipt.result.status, 'inconclusive');
  assert.equal(receipt.result.correct, 37);
  assert.equal(receipt.result.inconclusive, 2);
  assert.deepEqual(receipt.sample.map(row => row.id), frozen.reviews.map(row => row.id));
  restored.get('#audit-toggle').dispatch('click');
  assert.equal(restored.get('#total-count').textContent, 161);
  assert.equal(restored.get('#reviewed-count').textContent, 161);
  assert.equal(restored.get('#source-comparison').hidden, false);
});

test('screenshot audits retain source evidence and distinguish a census from sampling', async () => {
  const records = data.normalizeRows([{
    id: 'one', screenshot_path: 'image.png', source_url: 'https://example.org/report#capacity', value: '3',
  }]);
  const app = await harness(records);
  calculateAudit(app);
  assert.match(app.get('#audit-preview').textContent, /All rows out of 1/);
  app.get('#audit-start').dispatch('click');
  assert.equal(app.get('#source-link').attributes.href, 'https://example.org/report#capacity');
  app.rate('accepted');
  assert.match(app.get('#audit-result').textContent, /Pass.*p = 0/);
});

test('the import dialog honors canonical mappings from a reviewed CSV export', async () => {
  const app = await harness(fixture(1));
  const custom = data.normalizeRows([{
    id: 'old-id', custom_id: 'chosen-id', asset_path: 'old.png', image: 'chosen.png',
    title: 'Old title', custom_title: 'Chosen title', claim: 'Claim',
  }], { idColumn: 'custom_id', assetColumn: 'image', titleColumn: 'custom_title' });
  custom[0].status = 'rejected';
  const csv = data.toCsv(custom);
  await app.get('#spreadsheet-file').dispatch('change', {
    target: { files: [{ name: 'reviewed.csv', size: csv.length, text: async () => csv }] },
  });
  assert.equal(app.get('#asset-column').value, 'review_asset_path');
  assert.equal(app.get('#id-column').value, 'review_asset_id');
  assert.equal(app.get('#title-column').value, 'review_asset_title');
  app.get('#repository-input').value = 'yana-157/in-the-loop-verification-tool';
  app.get('#ref-input').value = 'main';
  app.get('#import-form').dispatch('submit');
  const [imported] = app.state().records;
  assert.equal(imported.id, 'chosen-id');
  assert.equal(imported.assetPath, 'chosen.png');
  assert.equal(imported.title, 'Chosen title');
  assert.equal(imported.status, 'rejected');
});
