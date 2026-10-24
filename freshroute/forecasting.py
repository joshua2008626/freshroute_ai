"""Demand forecasting engine.

Target variable: quantity *consumed* for one food item at one meal on one date.

Methods (chosen automatically, always reported to the user):

1. ``baseline_weekday_median``  median consumption of that item/meal on the same weekday
   (needs >= 3 same-weekday observations).
2. ``baseline_recent_mean``     mean of the 7 most recent observations of that item/meal
   (needs >= 3 observations).
3. ``limited_history_mean``     mean of 1-2 observations. Flagged as very weak.
4. ``per_student_rate``         median(consumed / student_count) x expected students. Only used
   when the operator supplies an expected student count and history has attendance data.
5. ``ml_random_forest``         scikit-learn RandomForestRegressor. Only used when there is enough
   data AND it beat the baseline on a chronological held-out period.

Leakage safeguards
------------------
* Every lag / rolling feature for a row is computed with ``shift(1)`` inside its item+meal
  series, so a row's own target and anything later never enter its features.
* Evaluation uses a chronological split: models are fitted on dates strictly before the split
  date and scored on rows on/after it. The baseline is scored walk-forward, using only
  observations dated strictly before each scored row.
* Metrics are reported only when computed on those held-out rows.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

LOG = logging.getLogger(__name__)

MIN_BASELINE_OBS = 3            # observations for a normal baseline
MIN_WEEKDAY_OBS = 3             # same-weekday observations for the weekday median
RECENT_WINDOW = 7
MIN_EVAL_ROWS = 15              # held-out rows needed to report any metric
MIN_ML_TRAIN_ROWS = 100         # usable training rows needed to even try the ML model
MIN_ML_TEST_ROWS = 30           # held-out rows needed to compare ML with the baseline
TEST_FRACTION = 0.30            # latest 30% of dates are held out
ML_MIN_IMPROVEMENT = 0.02       # ML must beat baseline MAE by >= 2% to be selected
RANGE_MIN_OBS = 8
RANDOM_STATE = 42

METHOD_LABELS = {
    "ml_random_forest": "Random Forest regression (scikit-learn)",
    "baseline_weekday_median": "Baseline: same-weekday median",
    "baseline_recent_mean": "Baseline: recent average",
    "limited_history_mean": "Very limited history: simple average",
    "per_student_rate": "Per-student consumption rate",
    "no_data": "No forecast possible",
}


# ----------------------------------------------------------------- helpers

def _to_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def _f(value) -> Optional[float]:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(out) or math.isinf(out)) else out


def data_support(n: int) -> dict:
    """Describes how much history exists. This is NOT an accuracy or confidence score."""
    if n < 5:
        label = "Limited"
    elif n < 15:
        label = "Moderate"
    else:
        label = "Good"
    return {
        "label": label,
        "basis": f"{n} past observation(s) of this item at this meal. "
                 "This reflects data volume only - it is not an accuracy or confidence score.",
    }


def baseline_predict(history: pd.DataFrame, target: date) -> tuple:
    """Baseline estimate from `history` (must already contain only past rows).

    Returns (value or None, method_code, weekday_obs).
    """
    n = len(history)
    if n == 0:
        return None, "no_data", 0
    hist = history.sort_values("date_dt")
    same_dow = hist[hist["date_dt"].dt.dayofweek == target.weekday()]
    if len(same_dow) >= MIN_WEEKDAY_OBS:
        return float(same_dow["consumed"].tail(8).median()), "baseline_weekday_median", len(same_dow)
    if n >= MIN_BASELINE_OBS:
        return float(hist["consumed"].tail(RECENT_WINDOW).mean()), "baseline_recent_mean", len(same_dow)
    return float(hist["consumed"].mean()), "limited_history_mean", len(same_dow)


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    """Leak-free features: every history-based value uses only earlier rows of the same item+meal."""
    out = df.sort_values(["item_id", "meal", "date_dt"]).copy()
    group = out.groupby(["item_id", "meal"], sort=False)["consumed"]
    out["n_prior"] = group.cumcount()
    out["lag1"] = group.shift(1)
    out["roll5"] = group.transform(lambda s: s.shift(1).rolling(5, min_periods=1).mean())
    out["dow"] = out["date_dt"].dt.dayofweek
    out["dow_mean"] = out.groupby(["item_id", "meal", "dow"], sort=False)["consumed"].transform(
        lambda s: s.shift(1).expanding().mean()
    )
    out["dow_mean"] = out["dow_mean"].fillna(out["roll5"])
    return out.sort_values(["date_dt", "item_id", "meal"]).reset_index(drop=True)


def _metrics(actual: np.ndarray, predicted: np.ndarray) -> dict:
    err = predicted - actual
    total = float(np.abs(actual).sum())
    return {
        "mae": round(float(np.abs(err).mean()), 3),
        "rmse": round(float(np.sqrt((err ** 2).mean())), 3),
        "wape_pct": round(float(np.abs(err).sum() / total * 100), 2) if total > 0 else None,
        "bias": round(float(err.mean()), 3),
    }


# ------------------------------------------------------------------ engine

@dataclass
class _Model:
    estimator: object
    columns: list
    items: list
    meals: list


class ForecastEngine:
    """Forecasting for one dataset restricted to one unit."""

    def __init__(self, records: pd.DataFrame, unit: str, model_dir: Optional[Path] = None):
        frame = records[records["unit"] == unit].copy() if not records.empty else records.copy()
        self.unit = unit
        self.frame = frame.sort_values(["date_dt", "item_id", "meal"]).reset_index(drop=True) if not frame.empty else frame
        self.features = add_features(self.frame) if not self.frame.empty else self.frame
        self.model_dir = Path(model_dir) if model_dir else None
        self._evaluation: Optional[dict] = None
        self._model: Optional[_Model] = None

    # -------------------------------------------------------- ML plumbing
    @staticmethod
    def _scale(rows: pd.DataFrame) -> np.ndarray:
        """Per-row demand level (same-weekday history mean). The model predicts consumption as a
        multiple of this level, so items of very different sizes share one model."""
        return np.maximum(rows["dow_mean"].to_numpy(float), 1e-6)

    def _design(self, rows: pd.DataFrame, items: list, meals: list) -> tuple:
        scale = self._scale(rows)
        base = np.column_stack([
            rows["dow"].to_numpy(float),
            (rows["dow"] >= 5).to_numpy(float),
            rows["lag1"].to_numpy(float) / scale,
            rows["roll5"].to_numpy(float) / scale,
        ])
        item_cols = np.column_stack([(rows["item_id"] == i).to_numpy(float) for i in items]) if items else np.empty((len(rows), 0))
        meal_cols = np.column_stack([(rows["meal"] == m).to_numpy(float) for m in meals]) if meals else np.empty((len(rows), 0))
        names = ["dow", "is_weekend", "lag1_rel", "roll5_rel"] + [f"item_{i}" for i in items] + [f"meal_{m}" for m in meals]
        return np.hstack([base, item_cols, meal_cols]), names

    def _fit(self, rows: pd.DataFrame) -> _Model:
        from sklearn.ensemble import RandomForestRegressor

        items = sorted(rows["item_id"].unique().tolist())
        meals = sorted(rows["meal"].unique().tolist())
        X, names = self._design(rows, items, meals)
        est = RandomForestRegressor(
            n_estimators=200, min_samples_leaf=2, random_state=RANDOM_STATE, n_jobs=1
        )
        est.fit(X, rows["consumed"].to_numpy(float) / self._scale(rows))
        return _Model(est, names, items, meals)

    def _usable(self, rows: pd.DataFrame) -> pd.DataFrame:
        """Rows that have at least 2 earlier observations, so lag features are defined."""
        return rows[(rows["n_prior"] >= 2) & rows["lag1"].notna() & rows["roll5"].notna()]

    # ----------------------------------------------------------- evaluation
    def evaluate(self) -> dict:
        if self._evaluation is not None:
            return self._evaluation
        self._evaluation = self._run_evaluation()
        return self._evaluation

    def _run_evaluation(self) -> dict:
        result = {
            "status": "insufficient",
            "unit": self.unit,
            "n_records": int(len(self.frame)),
            "selected": "baseline",
            "message": "",
            "thresholds": {
                "min_eval_rows": MIN_EVAL_ROWS, "min_ml_train_rows": MIN_ML_TRAIN_ROWS,
                "min_ml_test_rows": MIN_ML_TEST_ROWS, "test_fraction": TEST_FRACTION,
            },
            "split_date": None, "n_train": 0, "n_test": 0,
            "baseline": None, "ml": None, "prepared_gap": None,
            "series": [], "feature_importance": [],
        }
        if self.frame.empty or len(self.frame) < 20:
            result["message"] = (
                f"Only {len(self.frame)} record(s) in {self.unit}. At least {MIN_EVAL_ROWS} held-out "
                "observations (roughly 3-4 weeks of daily records for a few items) are needed before an "
                "error metric can be reported. Forecasts use simple baselines and no accuracy is claimed."
            )
            return result

        dates = sorted(self.features["date_dt"].unique())
        cut = int(len(dates) * (1 - TEST_FRACTION))
        cut = min(max(cut, 1), len(dates) - 1)
        split = pd.Timestamp(dates[cut])
        train_all = self.features[self.features["date_dt"] < split]
        test_all = self.features[(self.features["date_dt"] >= split) & (self.features["n_prior"] >= MIN_BASELINE_OBS)]
        result["split_date"] = split.date().isoformat()
        result["n_test"] = int(len(test_all))

        if len(test_all) < MIN_EVAL_ROWS:
            result["message"] = (
                f"Only {len(test_all)} held-out observation(s) after chronological split on "
                f"{result['split_date']} (need {MIN_EVAL_ROWS}). No error metric is reported; "
                "forecasts use simple baselines."
            )
            return result

        # Walk-forward baseline: each held-out row sees only strictly earlier observations.
        by_key = {k: g.sort_values("date_dt") for k, g in self.frame.groupby(["item_id", "meal"])}
        base_pred, actual = [], []
        for row in test_all.itertuples(index=False):
            hist = by_key[(row.item_id, row.meal)]
            hist = hist[hist["date_dt"] < row.date_dt]
            value, _, _ = baseline_predict(hist, row.date_dt.date())
            base_pred.append(value)
            actual.append(row.consumed)
        base_pred = np.array(base_pred, float)
        actual = np.array(actual, float)
        result["baseline"] = _metrics(actual, base_pred)
        result["n_test"] = int(len(actual))
        result["status"] = "baseline_only"
        result["selected"] = "baseline"

        prepared = test_all["prepared"].to_numpy(float)
        result["prepared_gap"] = {
            "mae": round(float(np.abs(prepared - actual).mean()), 3),
            "mean_over_preparation": round(float((prepared - actual).mean()), 3),
        }

        ml_pred = None
        train_ml = self._usable(train_all)
        result["n_train"] = int(len(train_ml))
        if len(train_ml) >= MIN_ML_TRAIN_ROWS and len(test_all) >= MIN_ML_TEST_ROWS:
            try:
                model = self._fit(train_ml)
                test_ml = test_all.dropna(subset=["lag1", "roll5"])
                X, _ = self._design(test_ml, model.items, model.meals)
                pred = np.clip(model.estimator.predict(X) * self._scale(test_ml), 0, None)
                # Score both methods on exactly the same rows.
                keep = test_all.index.isin(test_ml.index)
                a2, b2 = actual[keep], base_pred[keep]
                result["baseline"] = _metrics(a2, b2)
                result["ml"] = _metrics(a2, pred)
                result["n_test"] = int(len(a2))
                ml_pred = pred
                actual, base_pred, test_all = a2, b2, test_all[keep]
                better = result["ml"]["mae"] <= result["baseline"]["mae"] * (1 - ML_MIN_IMPROVEMENT)
                result["status"] = "ml_selected" if better else "ml_not_better"
                result["selected"] = "ml" if better else "baseline"
                importances = sorted(
                    zip(model.columns, model.estimator.feature_importances_), key=lambda t: -t[1]
                )[:8]
                result["feature_importance"] = [
                    {"feature": n.replace("item_", "item #").replace("meal_", "meal: "), "importance": round(float(v), 4)}
                    for n, v in importances
                ]
            except Exception:  # pragma: no cover - defensive
                LOG.exception("ML evaluation failed; falling back to baseline")
                result["message"] = "The machine-learning model could not be evaluated; the baseline is used."
        else:
            result["message"] = (
                f"The Random Forest needs >= {MIN_ML_TRAIN_ROWS} training rows and >= {MIN_ML_TEST_ROWS} "
                f"held-out rows (have {len(train_ml)} and {len(test_all)}). The documented baseline is used "
                "instead; its held-out error is shown above."
            )

        if result["status"] == "ml_selected":
            result["message"] = "Random Forest beat the baseline on the held-out period and is used for forecasts."
        elif result["status"] == "ml_not_better":
            result["message"] = (
                "Random Forest did not beat the baseline by the required margin on held-out data, so the "
                "simpler baseline is used. This is the honest outcome, not a failure."
            )

        # Daily series for the predicted-vs-actual chart (held-out period only, summed per date).
        frame = pd.DataFrame({
            "date": test_all["date_dt"].dt.strftime("%Y-%m-%d").to_numpy(),
            "actual": actual, "baseline": base_pred,
        })
        if ml_pred is not None:
            frame["ml"] = ml_pred
        daily = frame.groupby("date").sum(numeric_only=True).reset_index()
        result["series"] = [
            {"date": r["date"], "actual": round(float(r["actual"]), 1),
             "baseline": round(float(r["baseline"]), 1),
             "ml": round(float(r["ml"]), 1) if "ml" in daily.columns else None}
            for _, r in daily.iterrows()
        ]
        return result

    # -------------------------------------------------------------- final model
    def _final_model(self) -> Optional[_Model]:
        if self._model is not None:
            return self._model
        rows = self._usable(self.features)
        if len(rows) < MIN_ML_TRAIN_ROWS:
            return None
        self._model = self._fit(rows)
        if self.model_dir:
            try:
                import joblib

                self.model_dir.mkdir(parents=True, exist_ok=True)
                joblib.dump(
                    {"model": self._model.estimator, "columns": self._model.columns,
                     "unit": self.unit, "n_rows": int(len(rows)),
                     "trained_at": datetime.now().isoformat(timespec="seconds")},
                    self.model_dir / f"random_forest_{self.unit}.joblib",
                )
            except Exception:  # pragma: no cover
                LOG.warning("Could not persist model", exc_info=True)
        return self._model

    def _ml_predict(self, hist: pd.DataFrame, item_id: int, meal: str, target: date) -> Optional[float]:
        model = self._final_model()
        if model is None or item_id not in model.items or meal not in model.meals or len(hist) < 2:
            return None
        hist = hist.sort_values("date_dt")
        roll5 = float(hist["consumed"].tail(5).mean())
        same = hist[hist["date_dt"].dt.dayofweek == target.weekday()]["consumed"]
        row = pd.DataFrame([{
            "item_id": item_id, "meal": meal, "dow": target.weekday(),
            "lag1": float(hist["consumed"].iloc[-1]), "roll5": roll5,
            "dow_mean": float(same.mean()) if len(same) else roll5,
        }])
        X, _ = self._design(row, model.items, model.meals)
        return float(max(model.estimator.predict(X)[0] * self._scale(row)[0], 0.0))

    # ------------------------------------------------------------------ public
    def forecast(self, item_id: int, item_name: str, meal: str, target_date,
                 expected_students: Optional[int] = None) -> dict:
        target = _to_date(target_date)
        if self.frame.empty:
            hist = self.frame
        else:
            hist = self.frame[
                (self.frame["item_id"] == item_id) & (self.frame["meal"] == meal)
                & (self.frame["date_dt"] < pd.Timestamp(target))
            ].sort_values("date_dt")
        n = len(hist)
        ev = self.evaluate()
        out = {
            "item": item_name, "item_id": item_id, "meal": meal, "unit": self.unit,
            "target_date": target.isoformat(), "weekday": target.strftime("%A"),
            "n_obs": int(n), "estimate": None, "method": "no_data",
            "method_label": METHOD_LABELS["no_data"], "range": None,
            "data_support": data_support(n), "warnings": [], "explanation": [],
            "history": [], "used_expected_students": False,
            "evaluation": {"status": ev["status"], "selected": ev["selected"],
                           "n_test": ev["n_test"], "split_date": ev["split_date"],
                           "baseline_mae": (ev["baseline"] or {}).get("mae"),
                           "ml_mae": (ev["ml"] or {}).get("mae")},
        }
        if n == 0:
            out["warnings"].append(
                f"There is no history for {item_name} at {meal} before {target.isoformat()}. "
                "Import or record at least 3 observations to get a baseline forecast."
            )
            out["explanation"].append("No estimate was produced because no earlier records exist for this item and meal.")
            return out

        out["history"] = [
            {"date": r.date, "weekday": r.date_dt.strftime("%a"), "prepared": r.prepared,
             "consumed": r.consumed, "leftovers": r.leftovers, "source": r.source}
            for r in hist.tail(10).itertuples(index=False)
        ]

        estimate, method, wd_obs = None, "no_data", 0
        students_hist = hist.dropna(subset=["student_count"])
        students_hist = students_hist[students_hist["student_count"] > 0]

        if expected_students and expected_students > 0 and len(students_hist) >= 5:
            rate = float((students_hist["consumed"] / students_hist["student_count"]).tail(20).median())
            estimate, method = rate * float(expected_students), "per_student_rate"
            out["used_expected_students"] = True
            out["explanation"].append(
                f"Median consumption per student over the last {min(len(students_hist), 20)} recorded days was "
                f"{rate:.3f} {self.unit}; multiplied by the {int(expected_students)} students you expect."
            )
        else:
            if expected_students and expected_students > 0:
                out["warnings"].append(
                    "Expected student count was ignored: fewer than 5 past records of this item include "
                    "student attendance, so a per-student rate cannot be estimated."
                )
            ml_value = None
            if ev["selected"] == "ml" and n >= 3:
                ml_value = self._ml_predict(hist, item_id, meal, target)
            if ml_value is not None:
                estimate, method = ml_value, "ml_random_forest"
                out["explanation"].append(
                    "A Random Forest trained only on earlier records predicted this value. It was selected "
                    f"because it had lower error than the baseline on {ev['n_test']} held-out observations "
                    f"(MAE {ev['ml']['mae']} vs {ev['baseline']['mae']} {self.unit})."
                )
            else:
                estimate, method, wd_obs = baseline_predict(hist, target)
                if method == "baseline_weekday_median":
                    out["explanation"].append(
                        f"Median of the last {min(wd_obs, 8)} {target.strftime('%A')} observations of this item at {meal}."
                    )
                elif method == "baseline_recent_mean":
                    out["explanation"].append(
                        f"Average of the {min(n, RECENT_WINDOW)} most recent observations. Fewer than {MIN_WEEKDAY_OBS} "
                        f"{target.strftime('%A')}s are on record, so weekday-specific patterns cannot be used."
                    )
                else:
                    out["explanation"].append(
                        f"Only {n} observation(s) exist, so the simple average is shown. Treat it as a rough starting point."
                    )
                    out["warnings"].append("Very limited history: this number is a rough starting point, not a reliable forecast.")
                if ev["status"] in ("insufficient",):
                    out["explanation"].append("Not enough data exists to evaluate forecast error, so no accuracy figure is shown.")
                elif ev["selected"] == "baseline" and ev["baseline"]:
                    out["explanation"].append(
                        f"Baseline held-out MAE across the dataset: {ev['baseline']['mae']} {self.unit} "
                        f"(computed on {ev['n_test']} observations from {ev['split_date']} onward)."
                    )

        out["estimate"] = round(float(estimate), 2) if estimate is not None else None
        out["method"] = method
        out["method_label"] = METHOD_LABELS[method]

        if n >= RANGE_MIN_OBS:
            window = hist["consumed"].tail(20)
            out["range"] = {
                "low": round(float(np.percentile(window, 10)), 1),
                "high": round(float(np.percentile(window, 90)), 1),
                "basis": f"10th-90th percentile of the last {len(window)} recorded quantities. "
                         "A description of past variation, not a statistical prediction interval.",
            }
        return out


# --------------------------------------------------------------- engine cache

_CACHE: dict = {}


def _signature(df: pd.DataFrame) -> tuple:
    if df.empty:
        return (0, 0, 0.0, 0.0)
    return (len(df), int(df["id"].max()), round(float(df["consumed"].sum()), 3),
            round(float(df["prepared"].sum()), 3))


def get_engine(records: pd.DataFrame, unit: str, cache_key: str, model_dir: Optional[Path] = None) -> ForecastEngine:
    """Reuse a fitted engine while the underlying data is unchanged."""
    key = (cache_key, unit)
    sig = _signature(records[records["unit"] == unit] if not records.empty else records)
    cached = _CACHE.get(key)
    if cached and cached[0] == sig:
        return cached[1]
    engine = ForecastEngine(records, unit, model_dir)
    _CACHE[key] = (sig, engine)
    return engine


def clear_cache() -> None:
    _CACHE.clear()
