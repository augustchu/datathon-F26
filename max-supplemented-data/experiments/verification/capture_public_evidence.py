"""Capture full pages containing cited public evidence in an isolated Chrome profile."""

import argparse
from base64 import b64decode
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from math import ceil, sqrt
from pathlib import Path
from struct import unpack
import subprocess
from tempfile import TemporaryDirectory
import time
from urllib.parse import urlsplit

import requests
import websocket


VIEWPORT = (1360, 850)
MAX_IMAGE_DIMENSION = 16000
MAX_IMAGE_PIXELS = 32_000_000


class Tab:
    def __init__(self, url):
        self.connection = websocket.create_connection(url, timeout=15, suppress_origin=True)
        self.sequence = 0

    def call(self, method, **params):
        self.sequence += 1
        self.connection.send(json.dumps({'id': self.sequence, 'method': method, 'params': params}))
        while True:
            response = json.loads(self.connection.recv())
            if response.get('id') == self.sequence:
                if 'error' in response:
                    raise RuntimeError(response['error'])
                return response.get('result', {})

    def evaluate(self, expression):
        reply = self.call('Runtime.evaluate', expression=expression, returnByValue=True)
        if 'exceptionDetails' in reply:
            raise RuntimeError(reply['exceptionDetails'].get('text', 'Browser expression failed'))
        return reply['result'].get('value')

    def close(self):
        self.connection.close()


def capture_full_page(tab, output):
    content = tab.call('Page.getLayoutMetrics')['cssContentSize']
    width, height = (ceil(content[axis]) for axis in ('width', 'height'))
    if width <= 0 or height <= 0:
        raise RuntimeError('Chrome reported an empty page.')
    scale = min(1, MAX_IMAGE_DIMENSION / max(width, height), sqrt(MAX_IMAGE_PIXELS / (width * height)))
    image = tab.call('Page.captureScreenshot', format='png', fromSurface=True, captureBeyondViewport=True,
                     clip={'x': 0, 'y': 0, 'width': width, 'height': height, 'scale': scale})
    pixels = b64decode(image['data'])
    if len(pixels) < 24 or not pixels.startswith(b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR'):
        raise RuntimeError('Chrome returned an invalid PNG.')
    image_width, image_height = unpack('>II', pixels[16:24])
    if not all(actual > 0 and abs(actual - intended * scale) <= 1 for actual, intended in
               ((image_width, width), (image_height, height))):
        raise RuntimeError('The screenshot dimensions do not cover the full page.')
    output.write_bytes(pixels)
    return {'capture_scope': 'full_page', 'page_width': width, 'page_height': height,
            'image_width': image_width, 'image_height': image_height, 'image_scale': scale}


def capture_live(session, port, record, output):
    page = session.put(f'http://127.0.0.1:{port}/json/new?about:blank', timeout=4)
    page.raise_for_status()
    target = page.json()
    tab = Tab(target['webSocketDebuggerUrl'])
    try:
        tab.call('Emulation.setDeviceMetricsOverride', width=VIEWPORT[0], height=VIEWPORT[1],
                 deviceScaleFactor=1, mobile=False)
        tab.call('Page.navigate', url=record['url'])
        needle = json.dumps(record['anchor'].casefold())
        locate = """() => {
          const norm = value => (value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
          const choices = [...document.querySelectorAll('h1,h2,h3,h4,p,li,td,dt,dd,span,div')]
            .filter(el => el.getClientRects().length && norm(el.innerText).includes(ANCHOR))
            .sort((a, b) => a.innerText.length - b.innerText.length);
          return choices[0];
        }""".replace('ANCHOR', needle)
        evaluate = f"""(() => {{
          const el = ({locate})();
          if (!el) return null;
          el.scrollIntoView({{block: 'center', inline: 'nearest', behavior: 'instant'}});
          const rect = el.getBoundingClientRect();
          const top = document.elementFromPoint(
            Math.min(innerWidth-1, Math.max(0, rect.left + rect.width / 2)),
            Math.min(innerHeight-1, Math.max(0, rect.top + rect.height / 2)));
          return {{address: location.href, title: document.title,
                   visible: !!top && (el.contains(top) || top.contains(el)),
                   passage: el.innerText.trim().replace(/\\s+/g, ' ').slice(0, 180)}};
        }})()"""
        deadline = time.monotonic() + 18
        state = None
        while time.monotonic() < deadline:
            try:
                state = tab.evaluate(evaluate)
            except (RuntimeError, websocket.WebSocketException):
                state = None
            if state and state['visible']:
                break
            time.sleep(.4)
        if not state or not state['visible']:
            raise RuntimeError('Could not locate the cited passage on screen.')
        time.sleep(1)
        state = tab.evaluate(evaluate)
        if not state or not state['visible']:
            raise RuntimeError('The cited passage moved out of view while the page finished loading.')
        original, landing = urlsplit(record['url']), urlsplit(state['address'])
        if (landing.scheme != 'https' or not landing.hostname
                or landing.hostname.removeprefix('www.') != original.hostname.removeprefix('www.')):
            raise RuntimeError(f'Unexpected publisher redirect: {state["address"]}')
        dimensions = capture_full_page(tab, output)
        return {**state, **dimensions, 'source_url': record['url']}
    finally:
        tab.close()
        session.get(f'http://127.0.0.1:{port}/json/close/{target["id"]}', timeout=4)


def capture(args):
    manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
    requested = {row['case_id'] for row in manifest}
    if args.only and not set(args.only) <= requested:
        raise ValueError('Requested case ID is not in the capture manifest.')
    if args.only:
        manifest = [row for row in manifest if row['case_id'] in set(args.only)]
    for record in manifest:
        address = urlsplit(record['url'])
        if address.scheme != 'https' or not address.hostname:
            raise ValueError('Only public HTTPS evidence URLs are allowed.')
        if not record.get('anchor'):
            raise ValueError(f'{record["case_id"]} needs a visible passage for a fresh full-page capture.')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='capacity-evidence-chrome-') as profile:
        command = [str(args.chrome), '--headless', '--disable-gpu', '--disable-background-networking',
                   '--no-first-run', '--remote-debugging-port=0', f'--user-data-dir={profile}', 'about:blank']
        browser = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            port_file = Path(profile) / 'DevToolsActivePort'
            for _ in range(100):
                if port_file.is_file():
                    break
                time.sleep(.1)
            port = int(port_file.read_text().splitlines()[0])

            def one(record):
                case = record['case_id']
                session = requests.Session()
                session.trust_env = False
                try:
                    output = args.output_dir / f'{case}.png'
                    state = capture_live(session, port, record, output)
                    return {'case_id': case, 'status': 'captured', 'file': output.name,
                            'sha256': sha256(output.read_bytes()).hexdigest(), **state}
                except (OSError, ValueError, RuntimeError, websocket.WebSocketException, requests.RequestException) as exc:
                    return {'case_id': case, 'status': 'unavailable', 'reason': str(exc)}
                finally:
                    session.close()

            with ThreadPoolExecutor(max_workers=3) as workers:
                report = list(workers.map(one, manifest))
        finally:
            browser.terminate()
            browser.wait(timeout=10)
    receipt_path = args.output_dir / 'capture_receipt.json'
    if args.only and receipt_path.is_file():
        replaced = {record['case_id'] for record in report}
        report = [row for row in json.loads(receipt_path.read_text()) if row['case_id'] not in replaced] + report
    receipt_path.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(f'{sum(row["status"] == "captured" for row in report)} of {len(report)} browser captures saved.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('chrome', 'manifest', 'output-dir'):
        parser.add_argument(f'--{name}', type=Path, required=True)
    parser.add_argument('--only', nargs='+')
    capture(parser.parse_args())
