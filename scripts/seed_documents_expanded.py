"""Generate and seed 50+ synthetic medical documents using the LLM.

Each document is a ~250-word clinical evidence chunk covering a specific
condition/presentation. The LLM writes the content; we embed and store it.

Usage:
    python -m scripts.seed_documents_expanded [--dry-run] [--count 50]
"""

import argparse
import asyncio
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models.db_models import Document
from app.services.embedding_service import embedding_service
from app.services.llm_service import llm_service

# ── Document catalogue ────────────────────────────────────────────────────────
# Each entry: (condition, category, evidence_type, key_presentation_hint)
CATALOGUE = [
    # Cardiovascular (10)
    ("Stable Angina", "cardiovascular", "guideline", "exertional chest tightness, relieved by rest/nitrates, ECG ST changes"),
    ("Unstable Angina / NSTEMI", "cardiovascular", "guideline", "rest chest pain, troponin rise, no ST elevation, ACS management"),
    ("Heart Failure with Reduced EF", "cardiovascular", "guideline", "dyspnoea, reduced LVEF <40%, BNP elevated, ACE inhibitor therapy"),
    ("Atrial Fibrillation", "cardiovascular", "guideline", "irregularly irregular pulse, absent P waves, CHA2DS2-VASc anticoagulation"),
    ("Hypertensive Emergency", "cardiovascular", "guideline", "SBP>180, end-organ damage, IV labetalol/nicardipine, MAP reduction target"),
    ("Cardiac Tamponade", "cardiovascular", "case-study", "Beck's triad, pulsus paradoxus, JVD, echo confirms effusion, pericardiocentesis"),
    ("Infective Endocarditis", "cardiovascular", "guideline", "fever, new murmur, Janeway lesions, Osler nodes, Duke criteria, blood cultures"),
    ("Aortic Stenosis", "cardiovascular", "guideline", "systolic ejection murmur, syncope, angina, AVA <1cm², TAVR vs SAVR"),
    ("Deep Vein Thrombosis", "cardiovascular", "guideline", "unilateral leg swelling, Wells score, D-dimer, duplex ultrasound, LMWH"),
    ("Peripheral Artery Disease", "cardiovascular", "guideline", "claudication, ABI <0.9, ankle-brachial index, cilostazol, revascularisation"),

    # Respiratory (8)
    ("COPD Exacerbation", "respiratory", "guideline", "worsening dyspnoea, increased sputum, hypercapnia, bronchodilators, steroids"),
    ("Asthma Exacerbation", "respiratory", "guideline", "wheeze, PEFR <50%, beta-agonist, IV magnesium in severe, stepwise therapy"),
    ("Community-Acquired Pneumonia", "respiratory", "guideline", "fever, productive cough, CXR consolidation, CURB-65 score, amoxicillin"),
    ("Pleural Effusion", "respiratory", "guideline", "dullness to percussion, transudative vs exudative Light's criteria, thoracocentesis"),
    ("Pneumothorax", "respiratory", "guideline", "sudden pleuritic pain, absent breath sounds, tracheal deviation in tension, needle decompression"),
    ("Tuberculosis Pulmonary", "respiratory", "guideline", "productive cough >3 weeks, night sweats, haemoptysis, AFB smear, RIPE therapy"),
    ("Pulmonary Hypertension", "respiratory", "guideline", "exertional dyspnoea, right heart failure, RVSP>40, PAH-specific therapy"),
    ("Obstructive Sleep Apnoea", "respiratory", "guideline", "snoring, witnessed apnoeas, Epworth >10, polysomnography, CPAP"),

    # Neurological (8)
    ("Subarachnoid Haemorrhage", "neurological", "guideline", "thunderclap headache, worst of life, CT head, LP xanthochromia, nimodipine"),
    ("Guillain-Barré Syndrome", "neurological", "guideline", "ascending weakness post-infection, areflexia, CSF albuminocytologic dissociation, IVIG"),
    ("Myasthenia Gravis", "neurological", "guideline", "fatigable weakness, ptosis, diplopia, AChR antibodies, pyridostigmine"),
    ("Epilepsy Status Epilepticus", "neurological", "guideline", "continuous seizure >5min, benzodiazepine first line, levetiracetam, ICU"),
    ("Parkinson's Disease", "neurological", "guideline", "resting tremor, bradykinesia, rigidity, postural instability, levodopa"),
    ("Multiple Sclerosis Relapse", "neurological", "guideline", "optic neuritis, sensory symptoms, MRI periventricular lesions, IV methylprednisolone"),
    ("Hypertensive Encephalopathy", "neurological", "case-study", "severe hypertension, confusion, PRES on MRI, urgent BP lowering"),
    ("Bell's Palsy", "neurological", "guideline", "acute unilateral facial weakness, LMN, prednisolone within 72h, eye care"),

    # Gastroenterology (7)
    ("Acute Pancreatitis", "gastrointestinal", "guideline", "epigastric pain radiating to back, amylase/lipase >3x, Ranson criteria, IVF"),
    ("Upper GI Bleed", "gastrointestinal", "guideline", "haematemesis, melaena, Rockford score, PPI, urgent endoscopy, resuscitation"),
    ("Inflammatory Bowel Disease — Crohn's", "gastrointestinal", "guideline", "skip lesions, transmural inflammation, perianal disease, anti-TNF, steroids"),
    ("Inflammatory Bowel Disease — Ulcerative Colitis", "gastrointestinal", "guideline", "continuous colonic inflammation, bloody diarrhoea, 5-ASA, colectomy in fulminant"),
    ("Hepatic Encephalopathy", "gastrointestinal", "guideline", "confusion in cirrhosis, asterixis, elevated ammonia, lactulose, rifaximin"),
    ("Cholangitis Ascending", "gastrointestinal", "guideline", "Charcot's triad, Reynolds pentad in severe, ERCP, IV antibiotics, biliary drainage"),
    ("Bowel Obstruction Small", "gastrointestinal", "guideline", "colicky pain, distension, vomiting, AXR laddering, nil by mouth, NG tube"),

    # Endocrine / Metabolic (5)
    ("Hyperosmolar Hyperglycaemic State", "endocrine", "guideline", "glucose>33, osmolality>320, altered consciousness, gradual rehydration"),
    ("Addisonian Crisis", "endocrine", "guideline", "haemodynamic collapse, hyponatraemia, hyperkalaemia, IV hydrocortisone stat"),
    ("Thyroid Storm", "endocrine", "guideline", "hyperthermia, tachycardia, altered consciousness, Burch-Wartofsky >45, PTU, steroids"),
    ("Hypoglycaemia Severe", "endocrine", "guideline", "BG<3, confusion, seizure, IV dextrose or glucagon, identify cause"),
    ("Hypercalcaemia of Malignancy", "endocrine", "guideline", "calcium>3.5, polyuria, confusion, IV fluids, bisphosphonates, dialysis if severe"),

    # Infectious Disease (7)
    ("COVID-19 Severe", "infectious", "guideline", "hypoxaemia, bilateral infiltrates, CRP elevated, dexamethasone, remdesivir, prone"),
    ("Malaria Falciparum", "infectious", "guideline", "cyclical fever, thick blood film, parasite >2%, cerebral malaria risk, IV artesunate"),
    ("HIV Opportunistic Infections", "infectious", "guideline", "low CD4<200, PCP pneumonia, CMV retinitis, ART initiation, prophylaxis"),
    ("Necrotising Fasciitis", "infectious", "guideline", "rapidly spreading erythema, crepitus, LRINEC score, surgical debridement, broad antibiotics"),
    ("Clostridium Difficile Colitis", "infectious", "guideline", "post-antibiotic diarrhoea, PCR positive, oral vancomycin, fidaxomicin, FMT"),
    ("Infective Diarrhoea Salmonella", "infectious", "case-study", "food poisoning, fever, bloody stool, stool culture, supportive, antibiotics in bacteraemia"),
    ("Leptospirosis", "infectious", "case-study", "animal exposure, biphasic illness, Weil's disease, jaundice, renal failure, penicillin"),

    # Renal / Urology (5)
    ("Acute Kidney Injury", "renal", "guideline", "creatinine rise >26 in 48h, oliguria, KDIGO staging, fluid challenge, nephrology"),
    ("Nephrotic Syndrome", "renal", "guideline", "heavy proteinuria >3.5g/day, oedema, hypoalbuminaemia, hyperlipidaemia, renal biopsy"),
    ("Renal Calculus", "renal", "guideline", "loin-to-groin colicky pain, haematuria, CT KUB, analgesia, lithotripsy or ureteroscopy"),
    ("Chronic Kidney Disease Stage 4–5", "renal", "guideline", "eGFR<30, anaemia, metabolic acidosis, CKD-MBD, dialysis planning"),
    ("Glomerulonephritis Rapidly Progressive", "renal", "guideline", "haematuria, red cell casts, AKI, ANCA/anti-GBM, pulse methylprednisolone, cyclophosphamide"),
]


_PROMPT_TEMPLATE = """Write a concise clinical evidence summary (200-250 words) for a medical RAG knowledge base.

Condition: {condition}
Category: {category}
Evidence type: {evidence_type}
Key presentation hint: {hint}

Requirements:
- Write as a dense clinical reference paragraph, NOT as prose narrative
- Include: typical presentation, key diagnostic criteria/tests, first-line treatment
- Use clinical shorthand (e.g., "ECG", "CBC", "IV", "PO")
- Do not include section headers or bullet points — pure flowing text
- Output ONLY the text, no preamble or metadata

Example style (for a different condition):
"Bacterial meningitis presents with fever, severe headache, and nuchal rigidity; Kernig and Brudzinski signs are positive. LP shows cloudy CSF with WBC >1000/µL (predominantly neutrophils), low glucose (<0.6 plasma:CSF ratio), and elevated protein. Immediate empiric therapy: ceftriaxone 2g IV q12h plus vancomycin 15mg/kg IV q8h, with dexamethasone 0.15mg/kg q6h for 4 days to reduce neurological sequelae. Blood cultures before antibiotics; CT head before LP only if papilloedema, focal neurology, or GCS<13 to exclude raised ICP."

Now write the summary for the condition above:"""


async def generate_document(entry: tuple) -> dict | None:
    condition, category, evidence_type, hint = entry
    prompt = _PROMPT_TEMPLATE.format(
        condition=condition,
        category=category,
        evidence_type=evidence_type,
        hint=hint,
    )
    try:
        result = await llm_service.chat(
            system_prompt="You are a clinical knowledge base author. Write precise, dense clinical reference text.",
            user_prompt=prompt,
            response_format="text",
        )
        text = result["content"] if isinstance(result["content"], str) else str(result["content"])
        text = text.strip().strip('"').strip()
        return {
            "content": text,
            "source": f"Synthetic clinical reference — {condition}",
            "disease_category": category,
            "evidence_type": evidence_type,
        }
    except Exception as exc:
        print(f"  [WARN] Failed to generate '{condition}': {exc}")
        return None


async def seed(dry_run: bool = False, count: int | None = None) -> None:
    catalogue = CATALOGUE[:count] if count else CATALOGUE
    print(f"Generating content for {len(catalogue)} documents...")

    engine = create_async_engine(settings.database_url, echo=False)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Check existing documents to avoid duplicates
    async with Session() as session:
        result = await session.execute(text("SELECT COUNT(*) FROM documents"))
        existing = result.scalar()
        print(f"Existing documents in DB: {existing}")

    generated = []
    for i, entry in enumerate(catalogue):
        condition = entry[0]
        print(f"  [{i+1}/{len(catalogue)}] Generating: {condition}")
        doc = await generate_document(entry)
        if doc:
            generated.append(doc)
        # Rate limit: 1 req/sec to avoid OCI throttling
        if i < len(catalogue) - 1:
            time.sleep(1)

    print(f"\nGenerated {len(generated)}/{len(catalogue)} documents successfully.")

    if dry_run:
        print("\n[DRY RUN] Sample output:")
        for d in generated[:2]:
            print(f"\n  --- {d['source']} ---")
            print(f"  {d['content'][:200]}...")
        return

    print("\nGenerating embeddings...")
    contents = [d["content"] for d in generated]
    embeddings = embedding_service.embed_batch(contents)

    print("Inserting into database...")
    async with Session() as session:
        for doc_data, emb in zip(generated, embeddings):
            doc = Document(
                id=uuid.uuid4(),
                content=doc_data["content"],
                embedding=emb,
                source=doc_data["source"],
                disease_category=doc_data["disease_category"],
                evidence_type=doc_data["evidence_type"],
            )
            session.add(doc)
        await session.commit()

    print(f"\nSeeded {len(generated)} documents successfully.")
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed expanded medical documents")
    parser.add_argument("--dry-run", action="store_true", help="Generate but do not insert")
    parser.add_argument("--count", type=int, default=None, help="Limit number of docs to generate")
    args = parser.parse_args()
    asyncio.run(seed(dry_run=args.dry_run, count=args.count))
