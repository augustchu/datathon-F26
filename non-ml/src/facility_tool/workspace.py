"""Shared notebook settings and small, inspectable JSON Lines checkpoints."""

import json
import gzip
import os
import re
import tempfile
from datetime import date
from pathlib import Path

import pandas as pd


INPUT_MODES = {'demo', 'public', 'export', 'databricks'}


def read_config(root):
    config = json.loads((Path(root) / 'non-ml/config/project.json').read_text(encoding='utf-8'))
    date.fromisoformat(config['as_of'])
    if config['input_mode'] not in INPUT_MODES:
        raise ValueError(f'Choose an input mode from {sorted(INPUT_MODES)}.')
    return config


def show(frame, limit=20):
    """Use ordinary Jupyter display in a notebook and plain text in a script."""
    view = frame.head(limit) if isinstance(frame, pd.DataFrame) else frame
    try:
        from IPython.display import display
    except ImportError:
        print(view)
    else:
        display(view)


class Store:
    """Read and write frames in a mode-specific directory with schema metadata."""

    def __init__(self, root, mode, as_of, output_dir=None):
        if mode not in INPUT_MODES:
            raise ValueError(f'Unknown input mode: {mode}')
        as_of = date.fromisoformat(as_of).isoformat()
        home = Path(output_dir or os.environ.get('DATATHON_OUTPUT_DIR') or Path(root) / 'non-ml/outputs')
        self.path = home / mode / as_of

    def _path(self, name):
        if re.fullmatch(r'[a-z][a-z0-9_]*', name) is None:
            raise ValueError('Checkpoint names may contain lowercase letters, digits and underscores.')
        return self.path / f'{name}.jsonl'

    def _replace(self, target, content):
        self.path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=self.path, delete=False) as temp:
            pending = Path(temp.name)
            try:
                temp.write(content.encode('utf-8'))
                temp.close()
                pending.replace(target)
            finally:
                pending.unlink(missing_ok=True)

    def write(self, name, frame):
        if not isinstance(frame, pd.DataFrame) or frame.columns.has_duplicates:
            raise ValueError('The checkpoint must have a unique column name for every field.')
        target = self._path(name)
        data = frame.to_json(orient='records', lines=True, force_ascii=False, date_format='iso', double_precision=15)
        header = json.dumps({'columns': list(frame.columns)}, ensure_ascii=False)
        self._replace(target, header + '\n' + data)

    def write_csv(self, name, frame):
        """Save a portable table alongside the snapshot checkpoints."""
        target = self._path(name).with_suffix('.csv')
        self._replace(target, frame.to_csv(index=False))
        return target

    def read(self, name, *, optional=False):
        target = self._path(name)
        if not target.exists():
            if optional:
                return pd.DataFrame()
            raise FileNotFoundError(f'{target} is missing. Run the notebook that creates {name}, using the same input mode and as-of date.')
        with target.open(encoding='utf-8') as stream:
            columns = json.loads(stream.readline())['columns']
            rows = [json.loads(line) for line in stream if line.strip()]
        # Object dtype keeps identifiers, phone numbers, list fields and legitimate nulls distinct.
        return pd.DataFrame(rows, columns=columns, dtype=object)


def load_inputs(mode, config, spark=None):
    """An explicit Databricks mode is the only path that reads Unity Catalog."""
    if mode == 'demo':
        from .examples import demo_inputs
        return demo_inputs()
    if mode == 'public':
        return {key: pd.DataFrame() for key in ('facilities', 'pins', 'nfhs')}
    if mode == 'export':
        if not os.environ.get('DATATHON_FACILITY_EXPORT'):
            raise ValueError('Set DATATHON_FACILITY_EXPORT to a private facilities export.')
        options = {'facilities': 'DATATHON_FACILITY_EXPORT', 'pins': 'DATATHON_PIN_EXPORT',
                   'nfhs': 'DATATHON_NFHS_EXPORT'}
        exports = {}
        for key, option in options.items():
            file = os.environ.get(option)
            exports[key] = _read_export(file) if file else pd.DataFrame()
            if len(exports[key]) > int(config['row_limits'][key]):
                raise ValueError(f'The exported {key} table exceeds the configured row limit.')
        facilities = exports['facilities']
        if not {'countries', 'capacity', 'numberDoctors'}.issubset(facilities.columns):
            raise ValueError('The export must preserve the original facility columns and column order.')
        return exports
    if mode != 'databricks' or spark is None:
        raise ValueError('Use demo, public or export locally. To read Unity Catalog, choose databricks inside a Databricks notebook with table access.')

    def read(key):
        limit = int(config['row_limits'][key])
        table = spark.table(config['tables'][key])
        # Spark rows become plain Python values so workspace checkpoints preserve nested data.
        rows = table.limit(limit + 1).collect()
        if len(rows) > limit:
            raise ValueError(f'{key} is larger than the notebook collection limit ({limit}). Inspect the table before increasing it.')
        return pd.DataFrame([row.asDict(recursive=True) for row in rows], columns=table.columns)

    return {'facilities': read('facilities'), 'pins': read('pins'),
            'nfhs': read('nfhs') if config['include_nfhs'] else pd.DataFrame()}


def _read_export(filename):
    path = Path(filename).expanduser()
    if path.suffix == '.parquet':
        return pd.read_parquet(path)
    if path.suffix == '.csv':
        return pd.read_csv(path, dtype='string')
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt', encoding='utf-8') as stream:
        snapshot = json.load(stream)
    return pd.DataFrame(snapshot['rows'], columns=snapshot['columns'], dtype=object)
