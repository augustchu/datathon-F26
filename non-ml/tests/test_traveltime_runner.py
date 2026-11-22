from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'non-ml' / 'check_traveltime_live.py'


class TravelTimeRunnerTest(unittest.TestCase):
    def test_notebook_without_file_global_can_call_main_with_explicit_arguments(self):
        namespace = {'__name__': '__main__'}
        with patch.dict('os.environ', {'DATATHON_REPO_ROOT': ''}), patch.object(Path, 'cwd', return_value=SCRIPT.parent):
            exec(compile(SCRIPT.read_text(encoding='utf-8'), str(SCRIPT), 'exec'), namespace)
        self.assertEqual(namespace['ROOT'], ROOT)
        with redirect_stdout(StringIO()) as output, self.assertRaises(SystemExit) as exit_status:
            namespace['main'](['--help'])
        self.assertEqual(exit_status.exception.code, 0)
        self.assertIn('--checkpoint-root', output.getvalue())
