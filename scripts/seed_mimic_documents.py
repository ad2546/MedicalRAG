"""Seed the documents table with MIMIC-IV derived evidence documents.

For each hospital admission this script builds a text chunk that combines:
  - Primary ICD diagnosis title
  - All secondary diagnoses
  - Key lab values (latest per item)
  - Median vitals from ICU chartevents

Each chunk is embedded with MiniLM-L6-v2 and inserted into the documents table
with disease_category and evidence_type = 'clinical_case'.

Usage:
    python -m scripts.seed_mimic_documents [--data-dir PATH] [--dry-run]
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
from app.models.db_models import Document
from app.services.embedding_service import embedding_service

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger(__name__)


# ── Lab items to include ────────────────────────────────────────────────────

LAB_ITEMS = {
    "51301": ("wbc",           "x10^9/L"),
    "51265": ("platelets",     "x10^9/L"),
    "50912": ("creatinine",    "mg/dL"),
    "51006": ("bun",           "mg/dL"),
    "50813": ("lactate",       "mmol/L"),
    "50885": ("bilirubin",     "mg/dL"),
    "50931": ("glucose",       "mg/dL"),
    "50971": ("potassium",     "mEq/L"),
    "50983": ("sodium",        "mEq/L"),
    "51222": ("hemoglobin",    "g/dL"),
    "51003": ("troponin_t",    "ng/mL"),
    "51002": ("troponin_i",    "ng/mL"),
    "50902": ("chloride",      "mEq/L"),
    "50882": ("bicarbonate",   "mEq/L"),
    "50868": ("anion_gap",     "mEq/L"),
    "50878": ("ast",           "U/L"),
    "50861": ("alt",           "U/L"),
}

VITAL_ITEMS = {
    "220045": ("hr",     "bpm"),
    "220179": ("sbp",    "mmHg"),
    "220180": ("dbp",    "mmHg"),
    "220210": ("rr",     "/min"),
    "223762": ("temp_c", "°C"),
    "220277": ("spo2",   "%"),
}

# ICD keyword → disease_category
CATEGORY_MAP: list[tuple[str, str]] = [
    ("heart",           "cardiovascular"),
    ("cardiac",         "cardiovascular"),
    ("coronary",        "cardiovascular"),
    ("atrial",          "cardiovascular"),
    ("myocardial",      "cardiovascular"),
    ("hypertension",    "cardiovascular"),
    ("atherosclerosis", "cardiovascular"),
    ("aortic",          "cardiovascular"),
    ("pneumonia",       "respiratory"),
    ("pulmonary",       "respiratory"),
    ("asthma",          "respiratory"),
    ("copd",            "respiratory"),
    ("respiratory",     "respiratory"),
    ("bronch",          "respiratory"),
    ("kidney",          "renal"),
    ("renal",           "renal"),
    ("nephro",          "renal"),
    ("diabetes",        "endocrine"),
    ("thyroid",         "endocrine"),
    ("obesity",         "endocrine"),
    ("hyperlipi",       "endocrine"),
    ("sepsis",          "infectious"),
    ("infection",       "infectious"),
    ("cellulitis",      "infectious"),
    ("urinary tract",   "infectious"),
    ("gastro",          "gastrointestinal"),
    ("liver",           "gastrointestinal"),
    ("hepat",          "gastrointestinal"),
    ("pancreati",       "gastrointestinal"),
    ("bowel",           "gastrointestinal"),
    ("stroke",          "neurological"),
    ("seizure",         "neurological"),
    ("dementia",        "neurological"),
    ("neuro",           "neurological"),
    ("anemia",          "hematological"),
    ("anaemia",         "hematological"),
    ("coagulo",         "hematological"),
    ("bleed",           "hematological"),
]


def _category(title: str) -> str:
    t = title.lower()
    for keyword, category in CATEGORY_MAP:
        if keyword in t:
            return category
    return "general"


def _open(path: Path) -> io.TextIOWrapper:
    return gzip.open(path, "rt") if path.suffix == ".gz" else open(path)


def _load_csv(path: Path) -> list[dict]:
    with _open(path) as f:
        return list(csv.DictReader(f))


# ── Loaders ──────────────────────────────────────────────────────────────────

def load_icd_titles(base: Path) -> dict[tuple, str]:
    icd_map = {}
    for row in _load_csv(base / "hosp/d_icd_diagnoses.csv.gz"):
        icd_map[(row["icd_code"], row["icd_version"])] = row["long_title"]
    return icd_map


def load_admission_diagnoses(base: Path, icd_map: dict) -> dict[str, list[str]]:
    """hadm_id → ordered list of diagnosis titles (seq_num ascending)."""
    raw: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for row in _load_csv(base / "hosp/diagnoses_icd.csv.gz"):
        key = (row["icd_code"], row["icd_version"])
        title = icd_map.get(key, row["icd_code"])
        raw[row["hadm_id"]].append((int(row["seq_num"]), title))
    return {
        hadm_id: [t for _, t in sorted(entries)]
        for hadm_id, entries in raw.items()
    }


def load_lab_values(base: Path) -> dict[str, dict[str, tuple[float, str]]]:
    """hadm_id → {lab_name: (latest_value, unit)}."""
    latest: dict[tuple, tuple[str, float, str]] = {}
    logger.info("Loading labevents…")
    for row in _load_csv(base / "hosp/labevents.csv.gz"):
        if row["itemid"] not in LAB_ITEMS or not row["valuenum"] or not row["hadm_id"]:
            continue
        key = (row["hadm_id"], row["itemid"])
        ct = row["charttime"]
        prev = latest.get(key)
        if prev is None or ct > prev[0]:
            latest[key] = (ct, float(row["valuenum"]), row.get("valueuom", ""))

    result: dict[str, dict[str, tuple[float, str]]] = defaultdict(dict)
    for (hadm_id, itemid), (_, value, uom) in latest.items():
        name, default_unit = LAB_ITEMS[itemid]
        result[hadm_id][name] = (round(value, 2), uom or default_unit)
    return result


def load_vital_values(base: Path) -> dict[str, dict[str, tuple[float, str]]]:
    """hadm_id → {vital_name: (median_value, unit)}."""
    buckets: dict[tuple, list[float]] = defaultdict(list)
    logger.info("Loading chartevents (large file)…")
    for row in _load_csv(base / "icu/chartevents.csv.gz"):
        if row["itemid"] not in VITAL_ITEMS or not row["valuenum"] or not row["hadm_id"]:
            continue
        try:
            buckets[(row["hadm_id"], row["itemid"])].append(float(row["valuenum"]))
        except ValueError:
            continue

    result: dict[str, dict[str, tuple[float, str]]] = defaultdict(dict)
    for (hadm_id, itemid), values in buckets.items():
        name, unit = VITAL_ITEMS[itemid]
        result[hadm_id][name] = (round(statistics.median(values), 1), unit)
    return result


# ── Document builder ─────────────────────────────────────────────────────────

def build_document_text(
    diagnoses: list[str],
    labs: dict[str, tuple[float, str]],
    vitals: dict[str, tuple[float, str]],
    admission: dict,
) -> str:
    primary = diagnoses[0] if diagnoses else "Unknown diagnosis"
    secondaries = diagnoses[1:6]  # up to 5 secondary

    lines = [
        f"Clinical case: {primary}",
        f"Admission type: {admission.get('admission_type', 'unknown')}",
    ]

    if secondaries:
        lines.append("Comorbidities: " + "; ".join(secondaries))

    # Vitals block
    if vitals:
        vital_parts = []
        if "hr" in vitals:
            vital_parts.append(f"HR {vitals['hr'][0]} {vitals['hr'][1]}")
        if "sbp" in vitals and "dbp" in vitals:
            vital_parts.append(f"BP {int(vitals['sbp'][0])}/{int(vitals['dbp'][0])} mmHg")
        if "temp_c" in vitals:
            vital_parts.append(f"Temp {vitals['temp_c'][0]}°C")
        if "rr" in vitals:
            vital_parts.append(f"RR {vitals['rr'][0]}/min")
        if "spo2" in vitals:
            vital_parts.append(f"SpO2 {vitals['spo2'][0]}%")
        if vital_parts:
            lines.append("Vitals: " + ", ".join(vital_parts))

    # Labs block
    if labs:
        lab_parts = [
            f"{name} {val} {unit}"
            for name, (val, unit) in sorted(labs.items())
        ]
        lines.append("Labs: " + ", ".join(lab_parts))

    return "\n".join(lines)


# ── Main ─────────────────────────────────────────────────────────────────────

async def seed(data_dir: Path, dry_run: bool, batch_size: int) -> None:
    base = data_dir / "mimic-iv-clinical-database-demo-2.2"
    if not base.exists():
        logger.error("MIMIC data not found at %s", base)
        sys.exit(1)

    icd_map = load_icd_titles(base)
    admission_diagnoses = load_admission_diagnoses(base, icd_map)
    lab_values = load_lab_values(base)
    vital_values = load_vital_values(base)
    all_admissions = _load_csv(base / "hosp/admissions.csv")

    logger.info("Building document chunks for %d admissions…", len(all_admissions))

    docs: list[dict] = []
    for adm in all_admissions:
        hadm_id = adm["hadm_id"]
        diagnoses = admission_diagnoses.get(hadm_id, [])
        if not diagnoses:
            continue

        primary = diagnoses[0]
        category = _category(primary)
        text = build_document_text(
            diagnoses=diagnoses,
            labs=lab_values.get(hadm_id, {}),
            vitals=vital_values.get(hadm_id, {}),
            admission=adm,
        )
        docs.append({
            "content": text,
            "source": f"MIMIC-IV-demo-2.2 hadm_id={hadm_id}",
            "disease_category": category,
            "evidence_type": "clinical_case",
        })

    logger.info("Built %d document chunks", len(docs))

    if dry_run:
        logger.info("DRY RUN — printing first 3 documents, not writing to DB")
        for d in docs[:3]:
            print(f"\n--- [{d['disease_category']}] ---")
            print(d["content"])
        return

    # Embed in batches
    logger.info("Generating embeddings in batches of %d…", batch_size)
    all_embeddings: list[list[float]] = []
    for i in range(0, len(docs), batch_size):
        batch_texts = [d["content"] for d in docs[i : i + batch_size]]
        batch_embs = embedding_service.embed_batch(batch_texts)
        all_embeddings.extend(batch_embs)
        logger.info("  Embedded %d / %d", min(i + batch_size, len(docs)), len(docs))

    # Write to DB
    logger.info("Writing %d documents to database…", len(docs))
    engine = create_async_engine(settings.database_url, echo=False)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    written = 0
    async with Session() as session:
        for doc_data, emb in zip(docs, all_embeddings):
            session.add(Document(
                id=uuid.uuid4(),
                content=doc_data["content"],
                embedding=emb,
                source=doc_data["source"],
                disease_category=doc_data["disease_category"],
                evidence_type=doc_data["evidence_type"],
            ))
            written += 1
        await session.commit()

    await engine.dispose()

    # Summary
    from collections import Counter
    cats = Counter(d["disease_category"] for d in docs)
    logger.info("Done — seeded %d documents", written)
    logger.info("Category breakdown: %s", dict(cats.most_common()))


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed documents table from MIMIC-IV demo")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(__file__).parent.parent / "Data",
        help="Path containing mimic-iv-clinical-database-demo-2.2/",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print docs without writing to DB")
    parser.add_argument("--batch-size", type=int, default=32, help="Embedding batch size")
    args = parser.parse_args()
    asyncio.run(seed(args.data_dir, args.dry_run, args.batch_size))


if __name__ == "__main__":
    main()
