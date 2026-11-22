"""Capacity model bridge: run the team's saved PyTorch MLP without needing torch.

Loads the three artifacts written by the capacity training notebooks
(`ML/notebooks/outputs_models/models/{enriched,base}/`):
    preprocessor.joblib   sklearn ColumnTransformer
    model_meta.joblib     feature lists + architecture notes
    mlp_state_dict.pt     weights for Linear(n,128)-ReLU-Linear(128,64)-ReLU-Linear(64,1)

The network predicts log1p(capacity); outputs are expm1'd back to beds. Feature
building mirrors the notebooks (strip/lowercase, null strings -> NA, text length and
presence features), so raw rows from the facilities table work as-is.

Two display guards are added because ML/MODEL_EVALUATION.md documents that the original
StandardScaler preprocessing can extrapolate wildly (one test row predicted ~1.47M beds):
  * withhold when a continuous input is more than Z_LIMIT standard deviations from the
    training mean, and
  * withhold when the output exceeds MAX_BEDS.
These are display rules, not changes to the model.

Requires scikit-learn==1.7.2 (the version the preprocessor was pickled with).

CLI: write an overlay for notebook 05's `estimate_file`:
    python capacity_model.py --facilities export.csv --out ml_capacity_overlay.csv
"""
from __future__ import annotations

import argparse
import collections
import io
import os
import pickle
import warnings
import zipfile
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

NULL_STRINGS = {"null", "none", "nan", "n/a", "na", "", "-", "unknown"}
TEXT_FEATURES = ["equipment", "capability", "procedure", "specialties", "affiliationTypeIds"]
Z_LIMIT = 10.0      # standard deviations (continuous inputs only)
MAX_BEDS = 5000     # display ceiling for a single facility estimate
MODEL_FILES = ("preprocessor.joblib", "model_meta.joblib", "mlp_state_dict.pt")

GROUP_DEFS = {
    "doctor count": ["numberDoctors"],
    "year established": ["yearEstablished"],
    "engagement / social metrics": [
        "engagement_metrics_n_followers", "engagement_metrics_n_likes", "engagement_metrics_n_engagements",
        "post_metrics_post_count", "number_of_facts_about_the_organization", "distinct_social_media_presence_count"],
    "NFHS district indicators": None,  # every numeric feature not listed elsewhere
    "facility coordinates": ["latitude", "longitude"],
    "facility type": ["facilityTypeId"],
    "operator type": ["operatorTypeId"],
    "state": ["address_stateOrRegion"],
}


def _is_model_dir(d: Path) -> bool:
    return all((d / f).is_file() for f in MODEL_FILES)


def available_models() -> dict[str, Path]:
    """Model folders found next to the dashboard, in the repo, or via CAPACITY_MODEL_DIR."""
    here = Path(__file__).resolve().parent
    roots = [here / "models", here.parent / "ML" / "notebooks" / "outputs_models" / "models"]
    found: dict[str, Path] = {}
    if os.environ.get("CAPACITY_MODEL_DIR") and _is_model_dir(Path(os.environ["CAPACITY_MODEL_DIR"])):
        found["custom"] = Path(os.environ["CAPACITY_MODEL_DIR"])
    for root in roots:
        for name in ("enriched", "base"):
            if _is_model_dir(root / name) and name not in found:
                found[name] = root / name
    return found


def _to_na(series: pd.Series) -> pd.Series:
    return series.astype("string").str.strip().str.lower().where(lambda s: ~s.isin(NULL_STRINGS), other=pd.NA)


def load_state_dict(path) -> dict:
    """Read a torch state_dict (.pt zip format) into numpy arrays."""
    z = zipfile.ZipFile(path)
    pkl = next(n for n in z.namelist() if n.endswith("data.pkl"))
    root = pkl[: -len("data.pkl")]
    dtypes = {"FloatStorage": np.float32, "DoubleStorage": np.float64, "HalfStorage": np.float16,
              "LongStorage": np.int64, "IntStorage": np.int32}

    class Storage:
        def __init__(self, dtype, key):
            self.dtype, self.key = dtype, key

    class Unpickler(pickle.Unpickler):
        def find_class(self, module, name):
            if name == "_rebuild_tensor_v2":
                def rebuild(storage, offset, size, stride, *_):
                    raw = np.frombuffer(z.read(f"{root}data/{storage.key}"), dtype=storage.dtype)
                    n = int(np.prod(size)) if len(size) else 1
                    return raw[offset:offset + n].reshape(tuple(size)).copy()
                return rebuild
            if module == "collections" and name == "OrderedDict":
                return collections.OrderedDict
            if name in dtypes:
                return dtypes[name]
            return super().find_class(module, name)

        def persistent_load(self, pid):
            return Storage(pid[1], pid[2])

    return Unpickler(io.BytesIO(z.read(pkl))).load()


class CapacityModel:
    def __init__(self, model_dir):
        self.model_dir = Path(model_dir)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # sklearn version-mismatch warnings
            self.pre = joblib.load(self.model_dir / "preprocessor.joblib")
            self.meta = joblib.load(self.model_dir / "model_meta.joblib")
        sd = load_state_dict(self.model_dir / "mlp_state_dict.pt")
        self.layers = [(sd[f"{i}.weight"].astype(np.float32), sd[f"{i}.bias"].astype(np.float32)) for i in (0, 3, 6)]
        if self.layers[0][0].shape[1] != self.meta["n_features"]:
            raise ValueError("Weights and preprocessor disagree on the number of input features.")
        self.numeric = list(self.meta["numeric_features"])
        self.categorical = list(self.meta["categorical_features"])
        self.n_features = int(self.meta["n_features"])
        self.kind = "enriched" if "hh_electricity_pct" in self.numeric else "base"
        self.groups = self._groups()
        self._continuous = [i for i, c in enumerate(self.numeric) if not c.endswith("_present")]

    # ---- features -------------------------------------------------------------
    def _groups(self) -> dict:
        used = {c for cols in GROUP_DEFS.values() if cols for c in cols}
        text_cols = {f"{t}{s}" for t in TEXT_FEATURES for s in ("_len", "_present")}
        nfhs = [c for c in self.numeric if c not in used and c not in text_cols]
        groups = {k: (v if v is not None else nfhs) for k, v in GROUP_DEFS.items()}
        return {k: [c for c in v if c in self.numeric or c in self.categorical] for k, v in groups.items()
                if any(c in self.numeric or c in self.categorical for c in v)}

    def build_features(self, raw: pd.DataFrame) -> pd.DataFrame:
        """Raw facilities-table rows -> the model's input frame (NaN where unknown)."""
        n = len(raw)
        blank = lambda: pd.Series([pd.NA] * n, dtype="string", index=raw.index)
        out = {}
        for col in self.numeric:
            if col.endswith("_len") or col.endswith("_present"):
                base = col[:-4] if col.endswith("_len") else col[:-8]
                text = _to_na(raw[base]) if base in raw else blank()
                out[col] = text.str.len().fillna(0) if col.endswith("_len") else text.notna().astype(int)
            else:
                out[col] = pd.to_numeric(_to_na(raw[col]) if col in raw else blank(), errors="coerce")
        for col in self.categorical:
            s = _to_na(raw[col]) if col in raw else blank()
            out[col] = s.astype(object).where(s.notna(), np.nan)
        return pd.DataFrame(out, index=raw.index)[self.numeric + self.categorical]

    # ---- inference --------------------------------------------------------------
    def _forward(self, x: np.ndarray) -> np.ndarray:
        for i, (w, b) in enumerate(self.layers):
            x = x @ w.T + b
            if i < len(self.layers) - 1:
                x = np.maximum(x, 0.0)
        return np.clip(np.expm1(x.ravel()), 0, None)

    def predict_detailed(self, raw: pd.DataFrame) -> pd.DataFrame:
        """Per-row estimate plus status ('ok' or a withheld reason) and which inputs were unknown."""
        feats = self.build_features(raw)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            x = self.pre.transform(feats).astype(np.float32)
        raw_beds = self._forward(x)
        z = np.abs(x[:, :len(self.numeric)][:, self._continuous])
        worst = z.argmax(axis=1) if z.shape[1] else np.zeros(len(x), dtype=int)
        zmax = z.max(axis=1) if z.shape[1] else np.zeros(len(x))
        status, reason = [], []
        for i in range(len(x)):
            if zmax[i] > Z_LIMIT:
                col = self.numeric[self._continuous[worst[i]]]
                status.append("withheld_out_of_range")
                reason.append(f"input '{col}' is {zmax[i]:.0f} standard deviations from the training mean")
            elif raw_beds[i] > MAX_BEDS:
                status.append("withheld_implausible")
                reason.append(f"model output ({raw_beds[i]:,.0f} beds) exceeds the {MAX_BEDS:,}-bed display ceiling")
            else:
                status.append("ok")
                reason.append("")
        gaps = self.missing_groups(feats)
        return pd.DataFrame({
            "beds": np.where(np.array(status) == "ok", raw_beds, np.nan), "unguarded_beds": raw_beds,
            "status": status, "reason": reason, "gaps": gaps,
            "doctors_reported": feats["numberDoctors"].notna().to_numpy() if "numberDoctors" in feats else False,
        }, index=raw.index)

    def missing_groups(self, feats: pd.DataFrame) -> list[list[str]]:
        """Per row: which input groups were entirely unknown (filled with training medians/modes)."""
        flags = {g: feats[cols].isna().all(axis=1).to_numpy() for g, cols in self.groups.items()}
        return [[g for g in self.groups if flags[g][i]] for i in range(len(feats))]

    def category_choices(self, col: str) -> list[str]:
        """Frequent categories the encoder learned (rare ones were pooled as 'infrequent')."""
        enc = self.pre.named_transformers_["cat"].named_steps["onehot"]
        idx = self.categorical.index(col)
        cats = list(enc.categories_[idx])
        rare = getattr(enc, "infrequent_categories_", None)
        rare = set(rare[idx]) if rare is not None and rare[idx] is not None else set()
        return [c for c in cats if c not in rare]

    # ---- overlay for the team's pipeline --------------------------------------------
    def build_overlay(self, raw: pd.DataFrame, facility_ids, *, require_doctors=True, valid_ids=None) -> pd.DataFrame:
        """Estimates for facilities with NO reported capacity, in the schema notebook 05 imports.

        Rules follow the project docs: estimates fill missing fields only, are tagged method='ml',
        never decide service eligibility, and are withheld when the guards above trip. By default a
        reported doctor count is also required (the released count overlays were restricted the same way).
        """
        detail = self.predict_detailed(raw)
        capacity_reported = _to_na(raw["capacity"]).notna() if "capacity" in raw else pd.Series(False, index=raw.index)
        keep = (detail["status"] == "ok") & ~capacity_reported
        if require_doctors:
            keep &= detail["doctors_reported"]
        ids = pd.Series(list(facility_ids), index=raw.index, dtype="string")
        if valid_ids is not None:
            keep &= ids.isin(set(valid_ids))
        rows = pd.DataFrame({
            "facility_id": ids[keep],
            "field": "capacity",
            "method": "ml",
            "value": detail.loc[keep, "beds"].round().astype(int),
            "unit": "beds",
            "lower": np.nan,
            "upper": np.nan,
            "provenance": f"{self.kind} capacity MLP ({self.model_dir.name}); log1p target, Huber loss; preprocessor scikit-learn 1.7.2",
            "detail": ("Point estimate for a facility with no reported capacity; no calibrated interval. Team evaluation "
                       "(ML/MODEL_EVALUATION.md) measured held-out MAE of roughly 111-146 beds on reported-count facilities "
                       "and large underestimates for 500+ bed hospitals. Estimate withheld for inputs outside the training "
                       "range. Not a verified count and not evidence of any service."),
        })
        return rows.drop_duplicates(["facility_id", "field", "method"]).reset_index(drop=True)


def _read_table(path: str) -> pd.DataFrame:
    p = Path(path).expanduser()
    if p.suffix == ".parquet":
        return pd.read_parquet(p)
    return pd.read_csv(p, dtype="string")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Write an ML capacity overlay for notebook 05's estimate_file.")
    ap.add_argument("--facilities", required=True, help="Facilities export (CSV or Parquet) with the original columns.")
    ap.add_argument("--out", required=True, help="Overlay CSV to write.")
    ap.add_argument("--model", default="enriched", help="'enriched', 'base', or a model folder path.")
    ap.add_argument("--id-column", default="unique_id")
    ap.add_argument("--id-prefix", default="fdr:", help="Facility IDs in the pipeline are '<source>:<unique_id>'.")
    ap.add_argument("--valid-ids", help="Optional CSV/JSONL with a facility_id column (e.g. the saved entities) to keep only IDs in the snapshot.")
    ap.add_argument("--allow-missing-doctors", action="store_true")
    args = ap.parse_args()
    models = available_models()
    folder = Path(args.model) if Path(args.model).is_dir() else models.get(args.model)
    if folder is None:
        raise SystemExit(f"Model '{args.model}' not found. Available: {sorted(models)}")
    raw = _read_table(args.facilities)
    ids = args.id_prefix + raw[args.id_column].astype(str)
    valid = None
    if args.valid_ids:
        v = Path(args.valid_ids)
        if v.suffix == ".jsonl":
            lines = [l for l in v.read_text(encoding="utf-8").splitlines()[1:] if l.strip()]  # first line is a column header
            valid = pd.read_json(io.StringIO("\n".join(lines)), lines=True, dtype=False)["facility_id"]
        else:
            valid = pd.read_csv(v, dtype=str)["facility_id"]
    overlay = CapacityModel(folder).build_overlay(raw, ids, require_doctors=not args.allow_missing_doctors, valid_ids=valid)
    overlay.to_csv(args.out, index=False)
    print(f"Wrote {len(overlay):,} estimates to {args.out} (from {len(raw):,} facilities).")