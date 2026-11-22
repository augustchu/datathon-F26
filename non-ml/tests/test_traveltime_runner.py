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
NOTEBOOK = ROOT / 'non-ml/notebooks/10_check_traveltime_live.ipynb'


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
        cells = [cell for cell in json.loads(NOTEBOOK.read_text())['cells'] if cell['cell_type'] == 'code']
        self.assertEqual(len(cells), 1)
        mock_key = 'example-key-from-secret-scope'
        calls = []
        spark = object()

        def check_case(store, config, case):
            self.assertEqual(os.environ.get('TRAVELTIME_API_KEY'), mock_key)
            self.assertTrue(store.path.is_dir())
            calls.append(case)
            return {'case': case, 'outcome': 'inconclusive' if case.endswith('walking') else 'pass'}

        def load_runner(path):
            self.assertEqual(Path(path), SCRIPT)
            runner = run_path(path)
            runner['run_databricks'].__globals__.update(check_case=check_case, PROJECT_VOLUME=volume)
            return runner

        def prepare_snapshot(root, store, config, *, spark):
            self.assertIs(spark, namespace['spark'])
            store.path.mkdir(parents=True)

        with TemporaryDirectory() as directory:
            volume = Path(directory)
            secrets = SimpleNamespace(
                listScopes=lambda: [SimpleNamespace(name='test-scope')],
                list=lambda scope: [SimpleNamespace(key='traveltime-app-id'),
                                    SimpleNamespace(key='traveltime-api-key')],
                get=lambda **kwargs: mock_key,
            )
            namespace = {'spark': spark, 'dbutils': SimpleNamespace(secrets=secrets)}
            with (patch.dict(os.environ, {'TRAVELTIME_APP_ID': '', 'TRAVELTIME_API_KEY': '',
                                         'TRAVELTIME_SECRET_SCOPE': '', 'DATATHON_REPO_ROOT': '',
                                         'DATATHON_OUTPUT_DIR': str(volume / 'missing-snapshot')}),
                  patch.object(Path, 'cwd', return_value=NOTEBOOK.parent),
                  patch('runpy.run_path', side_effect=load_runner),
                  patch('facility_tool.routing_snapshot.prepare_blood_bank_snapshot',
                        side_effect=prepare_snapshot) as prepare,
                  redirect_stdout(StringIO()) as output):
                with self.assertRaisesRegex(RuntimeError, 'See the saved receipt above'):
                    exec(''.join(cells[0]['source']), namespace)
                self.assertEqual(os.environ['TRAVELTIME_API_KEY'], '')

            prepare.assert_called_once()
            receipt, = volume.glob('traveltime-checks/*/traveltime-live.json')
            report = json.loads(receipt.read_text())
            self.assertEqual(report['snapshot_source'], 'marketplace_tables')
            self.assertEqual(calls, ['jaipur-driving', 'gadchiroli-driving', 'gadchiroli-walking'])
            self.assertEqual([case['outcome'] for case in report['cases']], ['pass', 'pass', 'inconclusive'])
            self.assertIn('jaipur-driving — pass', output.getvalue())
            self.assertIn(f'Receipt written to {receipt}', output.getvalue())
            self.assertIn(receipt.read_text(), output.getvalue())
            self.assertNotIn(mock_key, output.getvalue())

    def test_missing_credentials_are_reported_without_contacting_the_provider(self):
        runner = run_path(str(SCRIPT))['run_databricks']
        secrets = SimpleNamespace(listScopes=lambda: [])
        with (TemporaryDirectory() as directory,
              patch.dict(os.environ, {'TRAVELTIME_APP_ID': '', 'TRAVELTIME_API_KEY': '',
                                     'TRAVELTIME_SECRET_SCOPE': ''}),
              patch.dict(runner.__globals__, {'PROJECT_VOLUME': Path(directory)}),
              patch('facility_tool.travel._post_json') as post,
              redirect_stdout(StringIO()) as output):
            with self.assertRaisesRegex(RuntimeError, 'TravelTime credentials are missing'):
                runner(object(), SimpleNamespace(secrets=secrets))
            receipt, = Path(directory).glob('traveltime-checks/*/traveltime-live.json')
            report = json.loads(receipt.read_text())
            self.assertIn('TravelTime credentials are missing', report['run_error'])
            self.assertIn(receipt.read_text(), output.getvalue())
            self.assertEqual(report['cases'], [])
            post.assert_not_called()
