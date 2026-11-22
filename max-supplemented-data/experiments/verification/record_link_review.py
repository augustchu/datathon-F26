"""Record a documented count adjudication in In The Loop's link-only view."""

import argparse
from collections import Counter
import csv
from functools import partial
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from threading import Thread

import requests

from capture_public_evidence import Tab
from record_in_the_loop import QuietServer, import_queue, screenshot, wait_for


def record(args):
    with args.queue.open(encoding='utf-8', newline='') as stream:
        queue = list(csv.DictReader(stream))
    decisions = {row['case_id']: row for row in json.loads(args.decisions.read_text())}
    if len(queue) != len(decisions) or {row['id'] for row in queue} != decisions.keys():
        raise ValueError('The queue and decision IDs must match exactly.')
    for row in queue:
        decision = decisions[row['id']]
        if row['source_url'] != decision['evidence_url'] or decision['status'] not in {'supported', 'unresolved'}:
            raise ValueError(f'Invalid review evidence or status for {row["id"]}.')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    with TemporaryDirectory(prefix='datathon-link-review-') as scratch:
        profile = Path(scratch) / 'chrome'
        server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietServer, directory=args.app.resolve()))
        Thread(target=server.serve_forever, daemon=True).start()
        browser = subprocess.Popen([str(args.chrome), '--headless', '--disable-background-networking',
                                    '--remote-debugging-port=0', f'--user-data-dir={profile}', 'about:blank'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            devtools = profile / 'DevToolsActivePort'
            wait_for(devtools.is_file, 'Chrome DevTools')
            port = int(devtools.read_text().splitlines()[0])
            with requests.Session() as http:
                http.trust_env = False
                reply = http.put(f'http://127.0.0.1:{port}/json/new?about:blank', timeout=5)
                reply.raise_for_status()
                tab = Tab(reply.json()['webSocketDebuggerUrl'])
                try:
                    tab.call('Emulation.setDeviceMetricsOverride', width=1400, height=1000, deviceScaleFactor=1, mobile=False)
                    tab.call('Browser.setDownloadBehavior', behavior='allow', downloadPath=str(output))
                    tab.call('Page.navigate', url=f'http://127.0.0.1:{server.server_port}/?mode=links')
                    import_queue(tab, args.queue)
                    for index, row in enumerate(queue):
                        identity = json.dumps(row['id'])
                        wait_for(lambda: tab.evaluate(f'document.querySelector("#asset-id").textContent === {identity}'), row['id'])
                        if tab.evaluate('document.querySelector("#live-source-link").href') != row['source_url']:
                            raise ValueError(f'App source link differs for {row["id"]}.')
                        decision = decisions[row['id']]
                        status = 'accepted' if decision['status'] == 'supported' else 'inconclusive'
                        note = decision['rationale']
                        tab.evaluate(f'''(() => {{
                          const notes = document.querySelector('#review-notes');
                          notes.value = {json.dumps(note)};
                          notes.dispatchEvent(new Event('input', {{bubbles: true}}));
                          document.querySelector('[data-rate="{status}"]').click();
                        }})()''')
                        if index + 1 < len(queue):
                            tab.evaluate('document.querySelector("#next-asset").click()')
                    if int(tab.evaluate('document.querySelector("#reviewed-count").textContent')) != len(queue):
                        raise ValueError('Some reviews were left pending in the app.')
                    screenshot(tab, output / 'in-the-loop-completed.png')
                    tab.evaluate('document.querySelector("#export-button").click()')
                    export = output / f'{args.queue.stem}-reviewed.csv'
                    wait_for(export.is_file, 'In The Loop export')
                finally:
                    tab.close()
        finally:
            browser.terminate()
            browser.wait(timeout=10)
            server.shutdown()
            server.server_close()
    with export.open(encoding='utf-8-sig', newline='') as stream:
        records = list(csv.DictReader(stream))
    if len(records) != len(queue) or any(
            row['review_status'] != ('accepted' if decisions[row['id']]['status'] == 'supported' else 'inconclusive')
            or row['review_notes'] != decisions[row['id']]['rationale'] or not row['reviewed_at'] for row in records):
        raise ValueError('The exported records differ from their evidence decisions.')
    report = {'cases': len(records), 'app_statuses': dict(Counter(row['review_status'] for row in records)),
              'reviewer': 'Codex public-document desk review', 'mode': 'links', 'export': export.name}
    (output / 'receipt.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('app', 'chrome', 'queue', 'decisions', 'output'):
        parser.add_argument(f'--{name}', type=Path, required=True)
    record(parser.parse_args())
