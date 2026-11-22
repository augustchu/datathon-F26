from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
from runpy import run_path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'non-ml' / 'check_traveltime_live.py'


class TravelTimeRunnerTest(unittest.TestCase):
    def test_notebook_without_file_global_can_call_main_with_explicit_arguments(self):
        namespace = {'__name__': '__main__'}
        with (patch.dict('os.environ', {'DATATHON_REPO_ROOT': ''}),
              patch.object(Path, 'cwd', return_value=SCRIPT.parent),
              redirect_stdout(StringIO()) as output):
            exec(compile(SCRIPT.read_text(encoding='utf-8'), str(SCRIPT), 'exec'), namespace)
        self.assertEqual(namespace['ROOT'], ROOT)
        self.assertIn('TravelTime did not run.', output.getvalue())
        with redirect_stdout(StringIO()) as output, self.assertRaises(SystemExit) as exit_status:
            namespace['main'](['--help'])
        self.assertEqual(exit_status.exception.code, 0)
        self.assertIn('--checkpoint-root', output.getvalue())

    def test_databricks_notebook_runs_the_cases_and_saves_receipt_before_reporting_failure(self):
        notebook = ROOT / 'non-ml/notebooks/10_check_traveltime_live.ipynb'
        cells = [cell for cell in json.loads(notebook.read_text())['cells'] if cell['cell_type'] == 'code']
        mock_key = 'example-key-from-secret-scope'
        calls = []

        def check_case(store, config, case):
            self.assertEqual(os.environ.get('TRAVELTIME_API_KEY'), mock_key)
            self.assertTrue(store.path.is_dir())
            calls.append(case)
            return {'case': case, 'outcome': 'inconclusive' if case.endswith('walking') else 'pass'}

        def load_runner(path):
            self.assertEqual(Path(path), SCRIPT)
            runner = run_path(path)
            runner['main'].__globals__['check_case'] = check_case
            return runner

        with TemporaryDirectory() as directory:
            mode, as_of = 'databricks', '2026-11-21'
            snapshot = Path(directory) / mode / as_of
            snapshot.mkdir(parents=True)
            for checkpoint in ('origin_index', 'lookup_candidates_blood_bank'):
                (snapshot / f'{checkpoint}.jsonl').write_text('{"columns": []}\n')
            values = {'repo_root': str(ROOT), 'checkpoint_root': directory,
                      'mode': mode, 'as_of': as_of, 'secret_scope': 'test-scope'}
            dbutils = SimpleNamespace(
                widgets=SimpleNamespace(text=values.setdefault, get=values.__getitem__),
                secrets=SimpleNamespace(get=lambda **kwargs: mock_key),
            )
            namespace = {'dbutils': dbutils}
            with (patch.dict(os.environ, {'TRAVELTIME_APP_ID': '', 'TRAVELTIME_API_KEY': ''}),
                  patch('runpy.run_path', side_effect=load_runner),
                  redirect_stdout(StringIO()) as output):
                exec(''.join(cells[0]['source']), namespace)
                with self.assertRaisesRegex(RuntimeError, 'See the saved receipt above'):
                    exec(''.join(cells[1]['source']), namespace)
                self.assertEqual(os.environ['TRAVELTIME_API_KEY'], '')

            receipt = snapshot / 'traveltime-live.json'
            report = json.loads(receipt.read_text())
            self.assertEqual(calls, ['jaipur-driving', 'gadchiroli-driving', 'gadchiroli-walking'])
            self.assertEqual([case['outcome'] for case in report['cases']], ['pass', 'pass', 'inconclusive'])
            self.assertIn('jaipur-driving — pass', output.getvalue())
            self.assertIn(f'Receipt written to {receipt}', output.getvalue())
            self.assertIn(receipt.read_text(), output.getvalue())
            self.assertNotIn(mock_key, output.getvalue())
