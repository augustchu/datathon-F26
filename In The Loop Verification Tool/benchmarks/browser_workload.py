"""Measure shared page loading and the extra browser work of full-page capture."""

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import platform
from statistics import median
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import time

import requests

VERIFICATION = Path(__file__).resolve().parents[2] / 'max-supplemented-data' / 'experiments' / 'verification'
sys.path.insert(0, str(VERIFICATION))
from capture_public_evidence import Tab, VIEWPORT, capture_full_page


class QuietServer(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def wait_for(check):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(.005)
    raise TimeoutError('Chrome or the fixture was not ready.')


def write_fixtures(site, count):
    for page in range(count):
        rows = []
        for row in range(120):
            anchor = ' id="target"' if row == 80 else ''
            rows.append(f'<tr{anchor}><td>Example facility {page:02d}-{row:03d}</td>'
                        f'<td>{25 + row}</td><td>{5 + row % 12}</td><td>2025-03-31</td></tr>')
        (site / f'{page}.html').write_text(f'''<!doctype html><html lang="en"><meta charset="utf-8">
<title>Publisher fixture {page}</title><style>
body {{font: 18px system-ui; margin: 24px;}} table {{border-collapse: collapse; width: 100%;}}
td, th {{text-align: left; padding: 7px 12px; border-bottom: 1px solid #ddd;}}
tr:nth-child(even) {{background: #edf3f3;}} #target {{background: #e1efce;}}
</style><body data-page="{page}"><h1>Example annual network disclosure</h1>
<table><thead><tr><th>Facility</th><th>Beds</th><th>Doctors</th><th>Reported date</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></body></html>''', encoding='utf-8')


def cpu_snapshot(browser):
    return {row['id']: row['cpuTime'] for row in browser.call('SystemInfo.getProcessInfo')['processInfo']}


def measure(browser, tab, base_url, count, output, capture):
    before = cpu_snapshot(browser)
    latest = dict(before)
    start = time.perf_counter()
    screenshot_bytes = 0
    for index in range(count):
        tab.call('Page.navigate', url=f'{base_url}/{index}.html#target')
        ready = f'''(() => {{
          const target = document.querySelector(':target');
          return document.readyState === 'complete' && document.body.dataset.page === '{index}'
            && target?.cells[1].textContent === '105';
        }})()'''
        wait_for(lambda: tab.evaluate(ready))
        if capture:
            capture_full_page(tab, output)
            screenshot_bytes += output.stat().st_size
        current = cpu_snapshot(browser)
        latest.update(current)
    elapsed = time.perf_counter() - start
    # Count each process through its last observation, including ones born during the trial.
    return {'chrome_cpu_seconds': sum(cpu - before.get(pid, 0) for pid, cpu in latest.items()),
            'elapsed_seconds': elapsed, 'full_page_png_bytes': screenshot_bytes,
            'processes_missing_at_endpoint': len(latest.keys() - current.keys())}


def benchmark(chrome):
    count, pairs = 20, 6
    with TemporaryDirectory(prefix='in-the-loop-benchmark-') as scratch:
        root = Path(scratch)
        site = root / 'site'
        site.mkdir()
        write_fixtures(site, count)
        server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietServer, directory=site))
        Thread(target=server.serve_forever, daemon=True).start()
        profile = root / 'chrome'
        base_url = f'http://127.0.0.1:{server.server_port}'
        process = subprocess.Popen([str(chrome), '--headless', '--disable-gpu', '--disable-background-networking',
                                    '--no-first-run', '--proxy-server=http://127.0.0.1:9',
                                    '--remote-debugging-port=0', f'--user-data-dir={profile}', 'about:blank'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            port_file = profile / 'DevToolsActivePort'
            wait_for(port_file.is_file)
            port = int(port_file.read_text().splitlines()[0])
            with requests.Session() as session:
                session.trust_env = False
                endpoint = f'http://127.0.0.1:{port}'
                browser = Tab(session.get(f'{endpoint}/json/version', timeout=5).json()['webSocketDebuggerUrl'])
                tab = Tab(session.put(f'{endpoint}/json/new?about:blank', timeout=5).json()['webSocketDebuggerUrl'])
            try:
                tab.call('Emulation.setDeviceMetricsOverride', width=VIEWPORT[0], height=VIEWPORT[1],
                         deviceScaleFactor=1, mobile=False)
                screenshot = root / 'capture.png'
                tab.call('Page.navigate', url=f'{base_url}/0.html#target')
                wait_for(lambda: tab.evaluate('document.readyState === "complete" && !!document.querySelector(":target")'))
                dimensions = capture_full_page(tab, screenshot)
                version = browser.call('Browser.getVersion')['product']
                samples = {'open_link': [], 'open_and_capture': []}
                modes = tuple(samples)
                for index in range(pairs):
                    order = modes if index % 2 == 0 else modes[::-1]
                    for mode in order:
                        samples[mode].append(measure(browser, tab, base_url, count, screenshot,
                                                     capture=mode == 'open_and_capture'))
            finally:
                tab.close()
                browser.close()
        finally:
            process.terminate()
            process.wait(timeout=10)
            server.shutdown()
            server.server_close()
    system = f'macOS {platform.mac_ver()[0]}' if platform.system() == 'Darwin' else platform.platform()
    return {'system': f'{system} ({platform.machine()})', 'browser': version,
            'fixture_size_pixels': [dimensions['image_width'], dimensions['image_height']],
            'pages_per_trial': count, 'paired_trials': pairs, 'samples': samples,
            'medians_per_trial': {mode: {key: median(row[key] for row in rows) for key in rows[0]}
                                  for mode, rows in samples.items()}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chrome', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(benchmark(args.chrome), indent=2) + '\n', encoding='utf-8')
