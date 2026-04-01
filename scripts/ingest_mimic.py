"""Ingest MIMIC-IV Clinical Database Demo into the cases table.

For each hospital admission this script builds a Case record with:
  - symptoms   : derived from ICD-9/10 diagnosis titles via keyword mapping
  - vitals     : median HR, systolic/diastolic BP, temp, RespRate, SpO2
                 pulled from ICU chartevents (where available) or OMR
  - history    : age, gender, BMI, prior conditions from previous admissions
  - labs       : latest value per key lab item during the admission

The MIMIC demo excludes free-text clinical notes, so symptoms are
synthesised from ICD titles — this is clearly flagged in every record.

Usage:
    python -m scripts.ingest_mimic [--data-dir PATH] [--dry-run]
"""

import argparse
import asyncio
import csv
import gzip
import io
import logging
import statistics
import sys
import uuid
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models.db_models import Case

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger(__name__)


# ── ICD keyword → symptom list mapping ─────────────────────────────────────

ICD_SYMPTOM_MAP: list[tuple[str, list[str]]] = [
    # Cardiovascular
    ("hypertension",         ["elevated blood pressure", "headache", "dizziness", "blurred vision"]),
    ("atrial fibrillation",  ["palpitations", "irregular heartbeat", "shortness of breath", "fatigue"]),
    ("heart failure",        ["shortness of breath", "orthopnoea", "bilateral leg oedema", "fatigue"]),
    ("coronary artery",      ["chest pain", "exertional angina", "dyspnoea on exertion"]),
    ("myocardial infarct",   ["crushing chest pain", "diaphoresis", "nausea", "left arm pain"]),
    ("atherosclerotic",      ["exertional chest pain", "claudication", "dyspnoea on exertion"]),
    # Respiratory
    ("pneumonia",            ["productive cough", "fever", "chills", "dyspnoea", "pleuritic chest pain"]),
    ("pulmonary embolism",   ["sudden dyspnoea", "pleuritic chest pain", "tachycardia", "haemoptysis"]),
    ("asthma",               ["wheezing", "shortness of breath", "cough", "chest tightness"]),
    ("copd",                 ["chronic productive cough", "dyspnoea", "barrel chest", "wheezing"]),
    ("respiratory failure",  ["severe dyspnoea", "hypoxia", "cyanosis", "use of accessory muscles"]),
    # Renal
    ("kidney failure",       ["oliguria", "elevated creatinine", "peripheral oedema", "fatigue", "nausea"]),
    ("acute kidney",         ["decreased urine output", "elevated creatinine", "BUN elevation", "fluid overload"]),
    ("chronic kidney",       ["fatigue", "anaemia", "oedema", "elevated creatinine", "hypertension"]),
    # Endocrine
    ("diabetes mellitus",    ["polyuria", "polydipsia", "hyperglycaemia", "fatigue", "blurred vision"]),
    ("hypothyroidism",       ["fatigue", "weight gain", "cold intolerance", "constipation", "dry skin"]),
    ("hyperthyroidism",      ["weight loss", "heat intolerance", "palpitations", "tremor", "anxiety"]),
    ("obesity",              ["elevated BMI", "exertional dyspnoea", "joint pain", "sleep apnoea"]),
    ("hyperlipidemia",       ["elevated cholesterol", "elevated LDL", "elevated triglycerides"]),
    ("hyperlipidaemia",      ["elevated cholesterol", "elevated LDL", "elevated triglycerides"]),
    # Gastrointestinal
    ("gastrointestinal bleed",["haematemesis", "melaena", "rectal bleeding", "dizziness", "anaemia"]),
    ("liver failure",        ["jaundice", "ascites", "encephalopathy", "coagulopathy"]),
    ("pancreatitis",         ["severe epigastric pain", "nausea", "vomiting", "elevated lipase"]),
    ("sepsis",               ["fever", "tachycardia", "hypotension", "leukocytosis", "altered mental status"]),
    # Neurological
    ("stroke",               ["unilateral weakness", "facial droop", "speech difficulty", "sudden headache"]),
    ("seizure",              ["convulsions", "loss of consciousness", "post-ictal confusion"]),
    ("dementia",             ["memory loss", "confusion", "behavioural change", "disorientation"]),
    # Haematological
    ("anemia",               ["fatigue", "pallor", "shortness of breath", "palpitations", "low haemoglobin"]),
    ("anaemia",              ["fatigue", "pallor", "shortness of breath", "palpitations", "low haemoglobin"]),
    ("coagulopathy",         ["easy bruising", "prolonged bleeding", "elevated INR"]),
    # Infections
    ("urinary tract infection",["dysuria", "urinary frequency", "urgency", "pyuria", "suprapubic pain"]),
    ("cellulitis",           ["erythema", "warmth", "swelling", "localised tenderness", "fever"]),
    ("septicemia",           ["fever", "rigors", "hypotension", "tachycardia", "leukocytosis"]),
    # Musculoskeletal
    ("osteoarthritis",       ["joint pain", "stiffness", "crepitus", "reduced range of motion"]),
    ("gout",                 ["acute joint pain", "erythema", "swelling", "elevated uric acid"]),
    # Mental health
    ("depressive disorder",  ["low mood", "anhedonia", "fatigue", "sleep disturbance", "poor concentration"]),
    ("anxiety",              ["excessive worry", "palpitations", "sweating", "tremor", "insomnia"]),
    # Other common
    ("nicotine dependence",  ["smoker", "chronic cough", "exertional dyspnoea"]),
    ("insulin",              ["insulin-dependent diabetes", "hyperglycaemia", "polyuria", "polydipsia"]),
    ("alcohol",              ["alcohol use", "tremor", "nausea", "elevated LFTs"]),
]


def _icd_title_to_symptoms(title: str) -> list[str]:
    """Return symptom strings for a given ICD long_title using keyword matching."""
    title_lower = title.lower()
    for keyword, symptoms in ICD_SYMPTOM_MAP:
        if keyword in title_lower:
            return symptoms
    # Fallback: use the title itself as a generic symptom indicator
    return [f"presentation consistent with {title.lower()}"]


# ── Lab and vital item IDs ──────────────────────────────────────────────────

LAB_ITEMS = {
    "51301": "wbc",
    "51265": "platelets",
    "50912": "creatinine",
    "51006": "bun",
    "50813": "lactate",
    "50885": "bilirubin_total",
    "50931": "glucose",
    "50971": "potassium",
    "50983": "sodium",
    "51222": "hemoglobin",
    "51003": "troponin_t",
    "51002": "troponin_i",
    "50902": "chloride",
    "50882": "bicarbonate",
    "50868": "anion_gap",
    "50878": "ast",
    "50861": "alt",
    "51144": "bands_pct",
    "51248": "mch",
}

VITAL_ITEMS = {
    "220045": "hr",
    "220179": "sbp",
    "220180": "dbp",
    "220210": "rr",
    "223762": "temp_c",
    "220277": "spo2",
}


# ── File loader helpers ─────────────────────────────────────────────────────

def _open(path: Path) -> io.TextIOWrapper:
    return gzip.open(path, "rt") if path.suffix == ".gz" else open(path)


def _load_csv(path: Path) -> list[dict]:
    with _open(path) as f:
        return list(csv.DictReader(f))


# ── Data builders ───────────────────────────────────────────────────────────

def build_reference_tables(base: Path) -> dict:
    """Load all lookup / dimension tables into memory."""
    logger.info("Loading reference tables…")

    icd_map: dict[tuple, str] = {}
    for row in _load_csv(base / "hosp/d_icd_diagnoses.csv.gz"):
        icd_map[(row["icd_code"], row["icd_version"])] = row["long_title"]

    patients: dict[str, dict] = {}
    for row in _load_csv(base / "hosp/patients.csv.gz"):
        patients[row["subject_id"]] = {
            "gender": row["gender"],
            "anchor_age": int(row["anchor_age"]) if row["anchor_age"] else None,
            "dod": row["dod"] or None,
        }

    return {"icd_map": icd_map, "patients": patients}


def build_admission_diagnoses(base: Path, icd_map: dict) -> dict[str, list[dict]]:
    """hadm_id → list of {icd_code, icd_version, long_title, seq_num}."""
    result: dict[str, list[dict]] = defaultdict(list)
    for row in _load_csv(base / "hosp/diagnoses_icd.csv.gz"):
        key = (row["icd_code"], row["icd_version"])
        result[row["hadm_id"]].append({
            "icd_code":   row["icd_code"],
            "icd_version": row["icd_version"],
            "long_title": icd_map.get(key, row["icd_code"]),
            "seq_num":    int(row["seq_num"]),
        })
    # Sort by seq_num (primary diagnosis first)
    for hadm_id in result:
        result[hadm_id].sort(key=lambda x: x["seq_num"])
    return result


def build_lab_values(base: Path) -> dict[str, dict[str, float]]:
    """hadm_id → {lab_name: latest_numeric_value}."""
    # Keep latest value per (hadm_id, itemid)
    latest: dict[tuple, tuple[str, float]] = {}  # (hadm_id, itemid) → (charttime, value)
    logger.info("Loading labevents (may take a moment)…")
    for row in _load_csv(base / "hosp/labevents.csv.gz"):
        if row["itemid"] not in LAB_ITEMS:
            continue
        if not row["valuenum"] or not row["hadm_id"]:
            continue
        key = (row["hadm_id"], row["itemid"])
        ct = row["charttime"]
        prev = latest.get(key)
        if prev is None or ct > prev[0]:
            latest[key] = (ct, float(row["valuenum"]))

    result: dict[str, dict[str, float]] = defaultdict(dict)
    for (hadm_id, itemid), (_, value) in latest.items():
        lab_name = LAB_ITEMS[itemid]
        result[hadm_id][lab_name] = round(value, 3)
    return result


def build_vital_values(base: Path) -> dict[str, dict[str, float]]:
    """hadm_id → {vital_name: median_value} using ICU chartevents."""
    buckets: dict[tuple, list[float]] = defaultdict(list)
    logger.info("Loading chartevents (large file — may take ~30 s)…")
    for row in _load_csv(base / "icu/chartevents.csv.gz"):
        if row["itemid"] not in VITAL_ITEMS:
            continue
        if not row["valuenum"] or not row["hadm_id"]:
            continue
        try:
            buckets[(row["hadm_id"], row["itemid"])].append(float(row["valuenum"]))
        except ValueError:
            continue

    result: dict[str, dict[str, float]] = defaultdict(dict)
    for (hadm_id, itemid), values in buckets.items():
        vital_name = VITAL_ITEMS[itemid]
        result[hadm_id][vital_name] = round(statistics.median(values), 1)
    return result


def build_omr_bmi(base: Path) -> dict[str, float]:
    """subject_id → latest BMI from OMR (height + weight)."""
    weights: dict[str, tuple[str, float]] = {}  # subject_id → (date, kg)
    heights: dict[str, float] = {}              # subject_id → cm

    for row in _load_csv(base / "hosp/omr.csv.gz"):
        sid = row["subject_id"]
        name = row["result_name"].lower()
        try:
            val = float(row["result_value"])
        except ValueError:
            continue

        if "weight (lbs" in name:
            kg = round(val * 0.453592, 1)
            date = row["chartdate"]
            prev = weights.get(sid)
            if prev is None or date > prev[0]:
                weights[sid] = (date, kg)
        elif "height (inches" in name:
            heights[sid] = round(val * 2.54, 1)  # cm

    bmi_map: dict[str, float] = {}
    for sid, (_, kg) in weights.items():
        if sid in heights and heights[sid] > 0:
            h_m = heights[sid] / 100
            bmi_map[sid] = round(kg / (h_m ** 2), 1)
    return bmi_map


def build_prior_conditions(
    subject_id: str,
    current_hadm_id: str,
    all_admissions: list[dict],
    admission_diagnoses: dict[str, list[dict]],
) -> list[str]:
    """Return unique ICD long_titles from all admissions *before* the current one."""
    current_admittime = next(
        (a["admittime"] for a in all_admissions if a["hadm_id"] == current_hadm_id), ""
    )
    prior: list[str] = []
    for adm in all_admissions:
        if adm["subject_id"] != subject_id:
            continue
        if adm["hadm_id"] == current_hadm_id:
            continue
        if adm["admittime"] >= current_admittime:
            continue
        for dx in admission_diagnoses.get(adm["hadm_id"], []):
            title = dx["long_title"]
            if title not in prior:
                prior.append(title)
    return prior[:20]  # cap to avoid very long lists


# ── Case builder ────────────────────────────────────────────────────────────

def build_case(
    admission: dict,
    diagnoses: list[dict],
    labs: dict[str, float],
    vitals: dict[str, float],
    patient: dict,
    prior_conditions: list[str],
    bmi: float | None,
) -> dict:
    # Derive symptoms from all diagnoses for this admission
    symptoms: list[str] = []
    seen: set[str] = set()
    for dx in diagnoses:
        for s in _icd_title_to_symptoms(dx["long_title"]):
            if s not in seen:
                symptoms.append(s)
                seen.add(s)

    # Vitals — format BP as "sbp/dbp" string
    sbp = vitals.get("sbp")
    dbp = vitals.get("dbp")
    bp_str = f"{int(sbp)}/{int(dbp)}" if sbp and dbp else None

    vitals_out: dict = {}
    if bp_str:
        vitals_out["bp"] = bp_str
    if "hr" in vitals:
        vitals_out["hr"] = vitals["hr"]
    if "temp_c" in vitals:
        vitals_out["temp"] = vitals["temp_c"]
    if "rr" in vitals:
        vitals_out["rr"] = vitals["rr"]
    if "spo2" in vitals:
        vitals_out["spo2"] = vitals["spo2"]

    # History
    history_out: dict = {
        "gender":           patient.get("gender"),
        "anchor_age":       patient.get("anchor_age"),
        "admission_type":   admission.get("admission_type"),
        "race":             admission.get("race"),
        "prior_conditions": prior_conditions,
        "data_source":      "MIMIC-IV-demo-2.2",
        "note":             "Symptoms derived from ICD codes — no free-text notes in demo dataset",
    }
    if bmi:
        history_out["bmi"] = bmi

    return {
        "id":       uuid.uuid4(),
        "symptoms": {"items": symptoms or ["no symptoms derivable from ICD codes"]},
        "vitals":   vitals_out,
        "history":  history_out,
        "labs":     labs,
        "diagnoses_ground_truth": [  # stored in history for evaluation
            {"icd_code": d["icd_code"], "icd_version": d["icd_version"], "title": d["long_title"]}
            for d in diagnoses[:5]
        ],
    }


# ── Main ingestion loop ─────────────────────────────────────────────────────

async def ingest(data_dir: Path, dry_run: bool) -> None:
    base = data_dir / "mimic-iv-clinical-database-demo-2.2"
    if not base.exists():
        logger.error("MIMIC data not found at %s", base)
        sys.exit(1)

    refs = build_reference_tables(base)
    icd_map = refs["icd_map"]
    patients = refs["patients"]

    admission_diagnoses = build_admission_diagnoses(base, icd_map)
    lab_values = build_lab_values(base)
    vital_values = build_vital_values(base)
    bmi_map = build_omr_bmi(base)

    all_admissions = _load_csv(base / "hosp/admissions.csv")
    # Index by subject_id for fast prior-condition lookup
    admissions_by_subject: dict[str, list[dict]] = defaultdict(list)
    for adm in all_admissions:
        admissions_by_subject[adm["subject_id"]].append(adm)

    logger.info("Building case records…")
    cases: list[dict] = []
    skipped = 0

    for adm in all_admissions:
        hadm_id = adm["hadm_id"]
        subject_id = adm["subject_id"]
        diagnoses = admission_diagnoses.get(hadm_id, [])

        if not diagnoses:
            skipped += 1
            continue

        patient = patients.get(subject_id, {})
        prior = build_prior_conditions(
            subject_id, hadm_id,
            admissions_by_subject[subject_id],
            admission_diagnoses,
        )

        case = build_case(
            admission=adm,
            diagnoses=diagnoses,
            labs=lab_values.get(hadm_id, {}),
            vitals=vital_values.get(hadm_id, {}),
            patient=patient,
            prior_conditions=prior,
            bmi=bmi_map.get(subject_id),
        )
        cases.append(case)

    logger.info("Built %d cases (%d admissions skipped — no diagnoses)", len(cases), skipped)

    if dry_run:
        logger.info("DRY RUN — printing first 2 cases, not writing to DB")
        for c in cases[:2]:
            print("\n--- Case ---")
            import json
            print(json.dumps({k: v for k, v in c.items() if k != "id"}, indent=2, default=str))
        return

    logger.info("Writing to database…")
    engine = create_async_engine(settings.database_url, echo=False)
    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    written = 0
    async with SessionLocal() as session:
        for c in cases:
            # Merge ground truth into history for storage
            history = {**c["history"], "diagnoses_ground_truth": c["diagnoses_ground_truth"]}
            record = Case(
                id=c["id"],
                symptoms=c["symptoms"],
                vitals=c["vitals"],
                history=history,
                labs=c["labs"],
            )
            session.add(record)
            written += 1

        await session.commit()

    logger.info("Ingested %d cases successfully.", written)
    await engine.dispose()

    # Summary stats
    labs_present = sum(1 for c in cases if c["labs"])
    vitals_present = sum(1 for c in cases if c["vitals"])
    logger.info(
        "Coverage — labs: %d/%d (%.0f%%)  vitals: %d/%d (%.0f%%)",
        labs_present, written, labs_present / written * 100,
        vitals_present, written, vitals_present / written * 100,
    )


# ── Entry point ─────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest MIMIC-IV demo into MedicalRAG cases table")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(__file__).parent.parent / "data",
        help="Path to the folder containing mimic-iv-clinical-database-demo-2.2/",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print first 2 cases without writing to DB",
    )
    args = parser.parse_args()
    asyncio.run(ingest(args.data_dir, args.dry_run))


if __name__ == "__main__":
    main()
