"""
eval_okahu.py — Run 20 diverse clinical cases through the MedicalRAG pipeline.

Each case hits /workflow/run, which triggers:
  - retrieval → diagnosis → reflection → validation agents
  - Okahu Cloud trace export via monocle-apptrace
  - RAGAS evaluation (faithfulness, answer relevancy, context precision)
  - Pipeline audit written to DB

Usage:
    python scripts/eval_okahu.py [--base-url http://localhost:8000] [--delay 3]
    python scripts/eval_okahu.py --email you@example.com --password secret
"""

import argparse
import json
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

# ── Auth ─────────────────────────────────────────────────────────────────────
# /workflow/run uses Bearer API-key auth (not cookie).
# Default matches WORKFLOW_API_KEY in .env.
WORKFLOW_API_KEY = "edrag-workflow-2024"

# ── Schema types ──────────────────────────────────────────────────────────────

@dataclass
class Vitals:
    bp: str | None = None
    hr: float | None = None
    temp: float | None = None


@dataclass
class History:
    smoker: bool | None = None
    prior_conditions: list[str] = field(default_factory=list)


@dataclass
class Case:
    label: str
    symptoms: list[str]
    vitals: Vitals = field(default_factory=Vitals)
    history: History = field(default_factory=History)
    labs: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict:
        return {
            "symptoms": self.symptoms,
            "vitals": {
                "bp": self.vitals.bp,
                "hr": self.vitals.hr,
                "temp": self.vitals.temp,
            },
            "history": {
                "smoker": self.history.smoker,
                "prior_conditions": self.history.prior_conditions,
            },
            "labs": self.labs,
        }


# ── 20 diverse clinical cases ─────────────────────────────────────────────────

CASES: list[Case] = [
    # ── Cardiovascular ───────────────────────────────────────────────────────
    Case(
        label="01 STEMI",
        symptoms=["crushing chest pain", "left arm radiation", "diaphoresis", "nausea"],
        vitals=Vitals(bp="90/60", hr=110, temp=36.8),
        history=History(smoker=True, prior_conditions=["hypertension", "hyperlipidemia"]),
        labs={"troponin_I": "8.4 ng/mL", "ECG": "ST elevation II, III, aVF"},
    ),
    Case(
        label="02 Atrial Fibrillation",
        symptoms=["palpitations", "irregular heartbeat", "mild dyspnea", "fatigue"],
        vitals=Vitals(bp="135/85", hr=148, temp=37.0),
        history=History(smoker=False, prior_conditions=["hypertension"]),
        labs={"ECG": "irregularly irregular rhythm, no P waves", "TSH": "0.9 mIU/L"},
    ),
    Case(
        label="03 Aortic Dissection",
        symptoms=["tearing chest pain radiating to back", "unequal blood pressure in arms", "syncope"],
        vitals=Vitals(bp="180/100", hr=102, temp=37.1),
        history=History(smoker=True, prior_conditions=["Marfan syndrome"]),
        labs={"D-dimer": "4200 ng/mL", "CXR": "widened mediastinum"},
    ),

    # ── Pulmonary ────────────────────────────────────────────────────────────
    Case(
        label="04 Pulmonary Embolism",
        symptoms=["sudden dyspnea", "pleuritic chest pain", "hemoptysis", "leg swelling"],
        vitals=Vitals(bp="100/65", hr=118, temp=37.4),
        history=History(smoker=False, prior_conditions=["recent knee surgery"]),
        labs={"D-dimer": "3800 ng/mL", "SpO2": "88%", "ECG": "S1Q3T3 pattern"},
    ),
    Case(
        label="05 COPD Exacerbation",
        symptoms=["worsening dyspnea", "increased sputum production", "purulent sputum", "wheeze"],
        vitals=Vitals(bp="145/90", hr=95, temp=37.8),
        history=History(smoker=True, prior_conditions=["COPD", "emphysema"]),
        labs={"ABG_pH": "7.32", "PaCO2": "58 mmHg", "SpO2": "86%"},
    ),
    Case(
        label="06 Community Acquired Pneumonia",
        symptoms=["productive cough with rust-coloured sputum", "fever", "rigors", "pleuritic pain"],
        vitals=Vitals(bp="118/75", hr=100, temp=39.2),
        history=History(smoker=False, prior_conditions=[]),
        labs={"WBC": "18.4 K/uL", "CRP": "142 mg/L", "CXR": "RLL consolidation"},
    ),

    # ── Neurological ─────────────────────────────────────────────────────────
    Case(
        label="07 Ischemic Stroke",
        symptoms=["sudden right-sided facial droop", "arm weakness", "slurred speech", "headache"],
        vitals=Vitals(bp="195/110", hr=88, temp=37.0),
        history=History(smoker=True, prior_conditions=["atrial fibrillation", "diabetes"]),
        labs={"glucose": "210 mg/dL", "INR": "1.1"},
    ),
    Case(
        label="08 Subarachnoid Haemorrhage",
        symptoms=["thunderclap headache", "neck stiffness", "photophobia", "vomiting", "loss of consciousness"],
        vitals=Vitals(bp="190/115", hr=65, temp=37.3),
        history=History(smoker=False, prior_conditions=["polycystic kidney disease"]),
        labs={"CT_head": "hyperdensity in basal cisterns"},
    ),
    Case(
        label="09 Bacterial Meningitis",
        symptoms=["high fever", "severe headache", "neck stiffness", "photophobia", "petechial rash"],
        vitals=Vitals(bp="105/70", hr=122, temp=40.1),
        history=History(smoker=False, prior_conditions=[]),
        labs={"CSF_WBC": "2800 cells/mm3", "CSF_glucose": "18 mg/dL", "CSF_protein": "320 mg/dL"},
    ),

    # ── Gastrointestinal ─────────────────────────────────────────────────────
    Case(
        label="10 Acute Pancreatitis",
        symptoms=["severe epigastric pain radiating to back", "nausea", "vomiting", "fever"],
        vitals=Vitals(bp="110/70", hr=108, temp=38.5),
        history=History(smoker=False, prior_conditions=["gallstones", "alcohol use disorder"]),
        labs={"lipase": "2400 U/L", "amylase": "980 U/L", "WBC": "14.2 K/uL"},
    ),
    Case(
        label="11 GI Bleed - Upper",
        symptoms=["haematemesis", "melena", "dizziness", "weakness", "pallor"],
        vitals=Vitals(bp="88/55", hr=130, temp=36.6),
        history=History(smoker=True, prior_conditions=["peptic ulcer disease", "NSAID use"]),
        labs={"Hgb": "7.2 g/dL", "BUN": "48 mg/dL", "INR": "1.3"},
    ),
    Case(
        label="12 Appendicitis",
        symptoms=["periumbilical pain migrating to RLQ", "anorexia", "nausea", "low grade fever"],
        vitals=Vitals(bp="122/78", hr=94, temp=37.9),
        history=History(smoker=False, prior_conditions=[]),
        labs={"WBC": "13.5 K/uL", "CRP": "68 mg/L", "USS": "non-compressible appendix 9mm"},
    ),

    # ── Renal & Metabolic ────────────────────────────────────────────────────
    Case(
        label="13 Diabetic Ketoacidosis",
        symptoms=["polyuria", "polydipsia", "nausea", "abdominal pain", "fruity breath", "altered consciousness"],
        vitals=Vitals(bp="100/65", hr=115, temp=37.0),
        history=History(smoker=False, prior_conditions=["type 1 diabetes"]),
        labs={"glucose": "520 mg/dL", "pH": "7.18", "bicarbonate": "8 mEq/L", "ketones": "large"},
    ),
    Case(
        label="14 Acute Kidney Injury",
        symptoms=["oliguria", "oedema", "fatigue", "confusion", "nausea"],
        vitals=Vitals(bp="165/100", hr=78, temp=36.9),
        history=History(smoker=False, prior_conditions=["CKD stage 3", "contrast CT 48h ago"]),
        labs={"creatinine": "6.8 mg/dL", "BUN": "88 mg/dL", "K+": "6.1 mEq/L"},
    ),

    # ── Infectious Disease ───────────────────────────────────────────────────
    Case(
        label="15 Sepsis",
        symptoms=["fever", "confusion", "hypotension", "rapid breathing", "decreased urine output"],
        vitals=Vitals(bp="80/50", hr=128, temp=39.8),
        history=History(smoker=False, prior_conditions=["immunocompromised", "recent UTI"]),
        labs={"lactate": "4.2 mmol/L", "WBC": "22.1 K/uL", "procalcitonin": "18.4 ng/mL"},
    ),
    Case(
        label="16 Infective Endocarditis",
        symptoms=["fever", "fatigue", "night sweats", "new heart murmur", "Janeway lesions", "splinter haemorrhages"],
        vitals=Vitals(bp="128/80", hr=98, temp=38.7),
        history=History(smoker=False, prior_conditions=["IV drug use", "bicuspid aortic valve"]),
        labs={"blood_cultures": "3/3 positive Staph aureus", "echo": "vegetations on aortic valve"},
    ),

    # ── Haematology / Oncology ───────────────────────────────────────────────
    Case(
        label="17 Pulmonary Sarcoidosis",
        symptoms=["dry cough", "dyspnoea on exertion", "bilateral hilar lymphadenopathy", "erythema nodosum", "fatigue"],
        vitals=Vitals(bp="122/76", hr=76, temp=37.1),
        history=History(smoker=False, prior_conditions=[]),
        labs={"ACE": "128 U/L", "calcium": "10.9 mg/dL", "CXR": "bilateral hilar adenopathy"},
    ),
    Case(
        label="18 Anaemia - Iron Deficiency",
        symptoms=["fatigue", "pallor", "pica", "koilonychia", "exertional dyspnea", "palpitations"],
        vitals=Vitals(bp="115/72", hr=102, temp=36.8),
        history=History(smoker=False, prior_conditions=["heavy menstrual bleeding"]),
        labs={"Hgb": "7.8 g/dL", "MCV": "68 fL", "ferritin": "4 ng/mL", "TIBC": "480 mcg/dL"},
    ),

    # ── Rheumatology ─────────────────────────────────────────────────────────
    Case(
        label="19 Systemic Lupus Erythematosus",
        symptoms=["malar rash", "photosensitivity", "joint pain", "oral ulcers", "pleuritis", "hair loss"],
        vitals=Vitals(bp="130/82", hr=86, temp=37.5),
        history=History(smoker=False, prior_conditions=[]),
        labs={"ANA": "1:640 speckled", "anti-dsDNA": "positive", "C3": "low", "CBC": "leukopenia"},
    ),

    # ── Paediatric ───────────────────────────────────────────────────────────
    Case(
        label="20 Kawasaki Disease",
        symptoms=["prolonged fever >5 days", "bilateral conjunctivitis", "strawberry tongue",
                  "cervical lymphadenopathy", "palmar erythema", "desquamation of fingertips"],
        vitals=Vitals(bp="100/62", hr=130, temp=39.5),
        history=History(smoker=None, prior_conditions=[]),
        labs={"ESR": "98 mm/hr", "CRP": "180 mg/L", "platelets": "620 K/uL", "WBC": "18.0 K/uL"},
    ),
]


# ── Runner ────────────────────────────────────────────────────────────────────

def run_case(
    client: httpx.Client,
    base_url: str,
    case: Case,
    idx: int,
    total: int,
    api_key: str,
    max_retries: int = 4,
) -> dict:
    """POST /workflow/run for one case, with exponential backoff on 429/500."""
    payload = case.to_payload()
    print(f"\n[{idx:02d}/{total}] {case.label}")
    print(f"  symptoms: {', '.join(case.symptoms[:3])}{'...' if len(case.symptoms) > 3 else ''}")

    wait = 15  # initial backoff in seconds
    for attempt in range(1, max_retries + 1):
        try:
            resp = client.post(
                f"{base_url}/workflow/run",
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=120.0,
            )

            if resp.status_code == 429 or (resp.status_code == 500 and "429" in resp.text):
                if attempt < max_retries:
                    print(f"  rate-limited (attempt {attempt}/{max_retries}) — waiting {wait}s...")
                    time.sleep(wait)
                    wait = min(wait * 2, 120)
                    continue
                print(f"  RATE-LIMITED after {max_retries} attempts — skipping")
                return {"case": case.label, "status": "rate_limited"}

            resp.raise_for_status()
            data = resp.json()

            final_dx = data.get("final_diagnosis") or data.get("diagnoses") or []
            top = final_dx[0] if final_dx else {}
            condition = top.get("condition") or top.get("name") or "n/a"
            confidence = top.get("confidence", "?")
            n_final = len(final_dx)
            n_initial = len(data.get("initial_diagnosis") or [])
            n_reflection = len(data.get("reflection_diagnosis") or [])
            print(f"  top_dx:   {condition} ({confidence})")
            print(f"  stages:   initial={n_initial} reflection={n_reflection} final={n_final}")
            print(f"  cache:    {'HIT' if data.get('cache_hit') else 'miss'}")
            return {"case": case.label, "status": "ok", "response": data}

        except httpx.HTTPStatusError as exc:
            print(f"  ERROR {exc.response.status_code}: {exc.response.text[:200]}")
            return {"case": case.label, "status": "http_error", "code": exc.response.status_code}
        except Exception as exc:
            print(f"  ERROR: {exc}")
            return {"case": case.label, "status": "error", "detail": str(exc)}

    return {"case": case.label, "status": "exhausted"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Okahu Cloud evaluation harness — 20 clinical cases")
    parser.add_argument("--base-url", default="http://localhost:8000", help="API base URL")
    parser.add_argument("--delay", type=float, default=3.0, help="Seconds between cases (rate-limit buffer)")
    parser.add_argument("--cases", type=int, default=0, help="Run only first N cases (0 = all)")
    parser.add_argument("--api-key", default=WORKFLOW_API_KEY, help="Workflow Bearer API key")
    args = parser.parse_args()

    cases = CASES[: args.cases] if args.cases else CASES
    total = len(cases)

    print("=" * 65)
    print(f"  MedicalRAG × Okahu Cloud — {total} case evaluation")
    print(f"  endpoint : {args.base_url}/workflow/run")
    print(f"  auth     : Bearer API key")
    print(f"  delay    : {args.delay}s between cases")
    print("=" * 65)

    results: list[dict] = []

    with httpx.Client() as client:
        for i, case in enumerate(cases, start=1):
            result = run_case(client, args.base_url, case, i, total, args.api_key)
            results.append(result)
            if i < total:
                print(f"  [waiting {args.delay}s]", end="\r")
                time.sleep(args.delay)

    # ── Summary ───────────────────────────────────────────────────────────────
    ok = sum(1 for r in results if r["status"] == "ok")
    rate_limited = sum(1 for r in results if r["status"] == "rate_limited")
    fail = total - ok - rate_limited

    print("\n" + "=" * 65)
    print(f"  DONE — {ok}/{total} succeeded, {rate_limited} rate-limited, {fail} other errors")
    print("=" * 65)

    if fail:
        print("\nFailed cases:")
        for r in results:
            if r["status"] != "ok":
                print(f"  {r['case']} → {r['status']} {r.get('code', r.get('detail', ''))}")

    # Write results JSON for offline review
    out_path = "eval_results.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nFull results → {out_path}")
    print("Check traces  → https://app.okahu.ai")


if __name__ == "__main__":
    main()
