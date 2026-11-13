from __future__ import annotations

import csv
from pathlib import Path
import joblib
import numpy as np
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from .dataset import generate_synthetic_dataset

ARTIFACTS_DIR = Path(__file__).resolve().parent / "artifacts"


def load_dataset(csv_path: Path):
    if not csv_path.exists():
        generate_synthetic_dataset(output_path=csv_path)

    rows = []
    with open(csv_path, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append({
                "department": r["department"],
                "event_type": r["event_type"],
                "priority": r["priority"],
                "venue_type": r["venue_type"],
                "day_of_week": int(r["day_of_week"]),
                "start_hour": int(r["start_hour"]),
                "duration_slots": int(r["duration_slots"]),
                "expected_attendance": int(r["expected_attendance"]),
                "staff_count": int(r["staff_count"]),
                "equipment_count": int(r["equipment_count"]),
                "actual_attendance": int(r["actual_attendance"]),
                "convenience_score": float(r["convenience_score"]),
                "risk_label": r["risk_label"],
            })
    return rows


def train_models():
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    data_dir = Path(__file__).resolve().parent.parent.parent / "data"
    csv_path = data_dir / "synthetic_training.csv"
    data = load_dataset(csv_path)

    # -----------------------------------------------------------------------
    # 1. Model A: Attendance Sizing Regressor
    # -----------------------------------------------------------------------
    categorical_features_att = ["department", "event_type", "priority"]
    numeric_features_att = ["expected_attendance", "day_of_week", "start_hour", "duration_slots"]

    preprocessor_att = ColumnTransformer(
        transformers=[
            ("cat", OneHotEncoder(handle_unknown="ignore"), categorical_features_att),
            ("num", StandardScaler(), numeric_features_att),
        ]
    )

    regressor = Pipeline(
        steps=[
            ("preprocessor", preprocessor_att),
            ("regressor", RandomForestRegressor(n_estimators=60, random_state=42)),
        ]
    )

    X_att = [{k: row[k] for k in categorical_features_att + numeric_features_att} for row in data]
    y_att = [row["actual_attendance"] for row in data]

    # Convert list of dicts to tabular format for ColumnTransformer
    import pandas as pd
    df = pd.DataFrame(data)

    regressor.fit(df[categorical_features_att + numeric_features_att], y_att)
    joblib.dump(regressor, ARTIFACTS_DIR / "attendance_model.joblib")

    # -----------------------------------------------------------------------
    # 2. Model B: Conflict-Risk Classifier (Low / Medium / High)
    # -----------------------------------------------------------------------
    risk_features = ["priority", "expected_attendance", "duration_slots", "staff_count", "equipment_count", "start_hour"]
    cat_risk = ["priority"]
    num_risk = ["expected_attendance", "duration_slots", "staff_count", "equipment_count", "start_hour"]

    preprocessor_risk = ColumnTransformer(
        transformers=[
            ("cat", OneHotEncoder(handle_unknown="ignore"), cat_risk),
            ("num", StandardScaler(), num_risk),
        ]
    )

    classifier = Pipeline(
        steps=[
            ("preprocessor", preprocessor_risk),
            ("classifier", RandomForestClassifier(n_estimators=60, random_state=42)),
        ]
    )

    classifier.fit(df[cat_risk + num_risk], df["risk_label"])
    joblib.dump(classifier, ARTIFACTS_DIR / "conflict_risk_model.joblib")

    # -----------------------------------------------------------------------
    # 3. Model C: Learned Slot Preference Score (Ridge Model)
    # -----------------------------------------------------------------------
    pref_features = ["day_of_week", "start_hour", "duration_slots"]
    pref_model = Pipeline(
        steps=[
            ("scaler", StandardScaler()),
            ("ridge", Ridge(alpha=1.0)),
        ]
    )
    pref_model.fit(df[pref_features], df["convenience_score"])
    joblib.dump(pref_model, ARTIFACTS_DIR / "slot_preference_model.joblib")

    print(f"All 3 ML models trained and serialized successfully in: {ARTIFACTS_DIR}")
    return {
        "attendance_model": ARTIFACTS_DIR / "attendance_model.joblib",
        "conflict_risk_model": ARTIFACTS_DIR / "conflict_risk_model.joblib",
        "slot_preference_model": ARTIFACTS_DIR / "slot_preference_model.joblib",
    }


if __name__ == "__main__":
    train_models()
