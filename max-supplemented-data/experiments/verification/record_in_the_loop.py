"""Import the frozen queue and record the documented desk review through the app UI."""

import argparse
from base64 import b64decode
from collections import Counter
import csv
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from threading import Thread
import time

import requests

from capture_public_evidence import Tab


class QuietServer(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def wait_for(check, description):
    for _ in range(120):
        result = check()
        if result:
            return result
        time.sleep(.10)
    raise RuntimeError(f'Timed out waiting for {description}.')


def screenshot(tab, path):
    capture = tab.call('Page.captureScreenshot', format='png', fromSurface=True)
    path.write_bytes(b64decode(capture['data']))


def import_queue(tab, queue):
    wait_for(lambda: tab.evaluate('document.querySelector("#total-count")?.textContent === "5"'), 'the sample app')
    tab.evaluate('document.querySelector("#import-button").click()')
    document = tab.call('DOM.getDocument')['root']['nodeId']
    file_input = tab.call('DOM.querySelector', nodeId=document, selector='#spreadsheet-file')['nodeId']
    tab.call('DOM.setFileInputFiles', files=[str(queue.resolve())], nodeId=file_input)
    wait_for(lambda: tab.evaluate('!document.querySelector("#confirm-import").disabled'), 'the CSV import preview')
    tab.evaluate('document.querySelector("#confirm-import").click()')
    wait_for(lambda: tab.evaluate('document.querySelector("#dataset-meta").textContent.includes("Imported")'),
             'the imported dataset')


def image_is_loaded(tab):
    return tab.evaluate('''(() => {
      const img = document.querySelector('#evidence-image');
      return img.complete && img.naturalWidth > 0 && !img.hidden;
    })()''')


def apply_findings(tab, queue_rows, findings):
    expected = {}
    for index, row in enumerate(queue_rows):
        identity = json.dumps(row['id'])
        wait_for(lambda: tab.evaluate(f'document.querySelector("#asset-id").textContent === {identity}'), row['id'])
        wait_for(lambda: image_is_loaded(tab), f'the screenshot for {row["id"]}')
        found = findings[row['case_id']]
        reference = found['supported_beds']
        decision = ('inconclusive' if reference is None else
                    'accepted' if int(row['candidate_beds']) == reference else 'rejected')
        note = found['reason']
        if reference is not None:
            note = f'Reviewed operator total: {reference}. ' + note
        if decision == 'rejected':
            note += ' This decision compares the candidate with the reviewed assertion. Its historical value may differ.'
        tab.evaluate(f'''(() => {{
          const notes = document.querySelector('#review-notes');
          notes.value = {json.dumps(note)};
          notes.dispatchEvent(new Event('input', {{bubbles: true}}));
          document.querySelector('[data-rate="{decision}"]').click();
        }})()''')
        wait_for(lambda: tab.evaluate(f'document.querySelector(\'[data-rate="{decision}"]\').getAttribute("aria-pressed") === "true"'),
                 f'the saved decision for {row["id"]}')
        if index + 1 < len(queue_rows):
            tab.evaluate('document.querySelector("#next-asset").click()')
        expected[row['id']] = decision
    return expected


def run_review(args):
    with args.queue.open(encoding='utf-8', newline='') as handle:
        queue = list(csv.DictReader(handle))
    findings = {row['case_id']: row for row in json.loads(args.evidence.read_text())['challenge']}
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)

    with TemporaryDirectory(prefix='datathon-loop-review-') as scratch:
        temporary = Path(scratch)
        site, profile = temporary / 'site', temporary / 'chrome'
        site.mkdir()
        for name in ('index.html', 'app.js', 'styles.css', 'favicon.svg', 'lib', 'vendor', 'samples'):
            (site / name).symlink_to(args.app.resolve() / name)
        (site / 'review-assets').symlink_to(args.captures.resolve())
        server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietServer, directory=site))
        Thread(target=server.serve_forever, daemon=True).start()
        browser = subprocess.Popen([str(args.chrome), '--headless', '--disable-gpu', '--no-first-run',
                                    '--disable-background-networking', '--remote-debugging-port=0',
                                    f'--user-data-dir={profile}', 'about:blank'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            port_file = profile / 'DevToolsActivePort'
            wait_for(port_file.is_file, 'the isolated browser')
            port = int(port_file.read_text().splitlines()[0])
            http = requests.Session()
            http.trust_env = False
            with http:
                response = http.put(f'http://127.0.0.1:{port}/json/new?about:blank', timeout=4)
                response.raise_for_status()
                tab = Tab(response.json()['webSocketDebuggerUrl'])
                try:
                    tab.call('Emulation.setDeviceMetricsOverride', width=1440, height=1020, deviceScaleFactor=1, mobile=False)
                    tab.call('Browser.setDownloadBehavior', behavior='allow', downloadPath=str(output))
                    tab.call('Page.navigate', url=f'http://127.0.0.1:{server.server_port}/')
                    import_queue(tab, args.queue)
                    assert int(tab.evaluate('document.querySelector("#total-count").textContent')) == len(queue)
                    wait_for(lambda: image_is_loaded(tab), 'the first queue screenshot')
                    screenshot(tab, output / 'in-the-loop-queue.png')
                    expected = apply_findings(tab, queue, findings)
                    wait_for(lambda: int(tab.evaluate('document.querySelector("#reviewed-count").textContent')) == len(queue),
                             'completed review progress')
                    wait_for(lambda: image_is_loaded(tab), 'the final queue screenshot')
                    screenshot(tab, output / 'in-the-loop-desk-review.png')
                    tab.evaluate('document.querySelector("#export-button").click()')
                    export = output / f'{args.queue.stem}-reviewed.csv'
                    wait_for(export.is_file, 'the app review export')
                finally:
                    tab.close()
        finally:
            browser.terminate()
            browser.wait(timeout=10)
            server.shutdown()
            server.server_close()

    with export.open(encoding='utf-8-sig', newline='') as handle:
        results = list(csv.DictReader(handle))
    if ({row['id']: row['review_status'] for row in results} != expected
            or not all(row['reviewed_at'] and row['review_notes'] for row in results)):
        raise ValueError('The app export differs from the documented desk review.')
    receipt = {'cases': len(findings), 'candidate_rows': len(results), 'status_counts': dict(Counter(expected.values())),
               'reviewer': 'Codex desk review of public primary evidence', 'export': export.name,
               'completed_in_app': True, 'screenshot_loaded_for_every_candidate': True}
    (output / 'app_review_receipt.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('chrome', 'app', 'queue', 'evidence', 'captures', 'output-dir'):
        parser.add_argument(f'--{name}', type=Path, required=True)
    run_review(parser.parse_args())
