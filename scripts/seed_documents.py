"""Seed the documents table with sample medical evidence for development/testing.

Usage:
    python -m scripts.seed_documents
"""

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models.db_models import Document
from app.services.embedding_service import embedding_service

SAMPLE_DOCUMENTS = [
    {
        "content": (
            "Pneumonia is an infection that inflames air sacs in one or both lungs. "
            "Classic symptoms include productive cough, fever (>38°C), chills, and dyspnoea. "
            "Chest X-ray shows lobar consolidation. CBC may show leukocytosis (WBC >11k). "
            "First-line treatment: amoxicillin for community-acquired pneumonia."
        ),
        "source": "Harrison's Principles of Internal Medicine",
        "disease_category": "respiratory",
        "evidence_type": "textbook",
    },
    {
        "content": (
            "Pulmonary embolism (PE) presents with sudden onset dyspnoea, pleuritic chest pain, "
            "tachycardia, and haemoptysis. Risk factors: DVT, prolonged immobility, malignancy, "
            "oral contraceptives. D-dimer elevated; CT pulmonary angiography is diagnostic gold standard. "
            "Treatment: anticoagulation (heparin/LMWH) or thrombolysis in massive PE."
        ),
        "source": "UpToDate — Pulmonary Embolism",
        "disease_category": "respiratory",
        "evidence_type": "guideline",
    },
    {
        "content": (
            "Acute MI (STEMI) presents with crushing substernal chest pain radiating to left arm/jaw, "
            "diaphoresis, nausea. ECG: ST elevation ≥1mm in ≥2 contiguous leads. Troponin I/T elevated. "
            "Immediate PCI within 90 minutes is standard of care. "
            "Aspirin 325mg and nitroglycerin sublingual as first aid."
        ),
        "source": "AHA/ACC STEMI Guidelines 2023",
        "disease_category": "cardiovascular",
        "evidence_type": "guideline",
    },
    {
        "content": (
            "Aortic dissection type A: tearing chest pain radiating to the back, unequal blood pressure "
            "in arms, widened mediastinum on CXR. CT angiography confirms. "
            "Type A requires emergency surgery; Type B managed medically with BP control."
        ),
        "source": "ESC Guidelines on Aortic Diseases 2023",
        "disease_category": "cardiovascular",
        "evidence_type": "guideline",
    },
    {
        "content": (
            "Appendicitis classically presents with periumbilical pain migrating to RLQ, anorexia, "
            "low-grade fever, nausea/vomiting. Rovsing's sign, Psoas sign positive. "
            "WBC elevated. CT abdomen is diagnostic. Treatment: laparoscopic appendectomy."
        ),
        "source": "Surgical Review — Sabiston Textbook",
        "disease_category": "gastrointestinal",
        "evidence_type": "textbook",
    },
    {
        "content": (
            "Diabetic ketoacidosis (DKA): blood glucose >250 mg/dL, anion gap metabolic acidosis, "
            "ketonemia/ketonuria. Presents with polyuria, polydipsia, nausea, abdominal pain, Kussmaul breathing. "
            "Treatment: IV fluid resuscitation, insulin infusion, electrolyte replacement (K+)."
        ),
        "source": "ADA Standards of Diabetes Care 2024",
        "disease_category": "endocrine",
        "evidence_type": "guideline",
    },
    {
        "content": (
            "Meningitis bacterial: severe headache, neck stiffness (nuchal rigidity), fever, photophobia. "
            "Kernig's and Brudzinski's signs positive. LP shows cloudy CSF, high WBC, low glucose, high protein. "
            "Empiric treatment: ceftriaxone + vancomycin + dexamethasone immediately."
        ),
        "source": "Infectious Disease Society of America — Meningitis Guidelines",
        "disease_category": "neurological",
        "evidence_type": "guideline",
    },
    {
        "content": (
            "Stroke (ischaemic): sudden unilateral weakness, facial droop, speech difficulty (FAST mnemonic). "
            "CT head rules out haemorrhage. tPA within 4.5 hours if eligible. "
            "NIH Stroke Scale used for severity. MRI DWI most sensitive for acute infarct."
        ),
        "source": "AHA/ASA Acute Ischaemic Stroke Guidelines 2023",
        "disease_category": "neurological",
        "evidence_type": "guideline",
    },
    {
        "content": (
            "Sepsis: life-threatening organ dysfunction caused by dysregulated host response to infection. "
            "qSOFA: RR≥22, AMS, SBP≤100. Septic shock: vasopressors needed, lactate >2 mmol/L. "
            "Sepsis bundle: blood cultures, broad-spectrum antibiotics within 1h, 30 mL/kg crystalloid."
        ),
        "source": "Surviving Sepsis Campaign 2021",
        "disease_category": "infectious",
        "evidence_type": "guideline",
    },
    {
        "content": (
            "Heart failure exacerbation: dyspnoea, orthopnoea, PND, bilateral leg oedema, S3 gallop. "
            "BNP/NT-proBNP elevated. CXR: cardiomegaly, pulmonary oedema, Kerley B lines. "
            "Treatment: IV diuretics (furosemide), oxygen, vasodilators if SBP adequate."
        ),
        "source": "ESC Heart Failure Guidelines 2023",
        "disease_category": "cardiovascular",
        "evidence_type": "guideline",
    },
]


async def seed() -> None:
    engine = create_async_engine(settings.database_url, echo=False)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    contents = [d["content"] for d in SAMPLE_DOCUMENTS]
    print(f"Generating embeddings for {len(contents)} documents...")
    embeddings = embedding_service.embed_batch(contents)

    async with Session() as session:
        for doc_data, emb in zip(SAMPLE_DOCUMENTS, embeddings):
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

    print(f"Seeded {len(SAMPLE_DOCUMENTS)} documents successfully.")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
