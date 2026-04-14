"""
Seed the RAG vector store with PubMed abstracts.

Uses the NCBI E-utilities API (free, no API key required for ≤3 req/s).
Fetches ~10 review/clinical-trial abstracts per search query across 10 disease
categories and upserts them into the documents table.

Usage:
    python scripts/seed_pubmed.py                          # all categories
    python scripts/seed_pubmed.py --limit 5               # 5 results per query
    python scripts/seed_pubmed.py --dry-run               # preview, no insert
    python scripts/seed_pubmed.py --category cardiovascular
    python scripts/seed_pubmed.py --ncbi-key YOUR_KEY     # 10 req/s instead of 3
"""

import argparse
import asyncio
import logging
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterator

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import settings
from app.services.embedding_service import embedding_service

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# NCBI E-utilities endpoints
# ---------------------------------------------------------------------------
ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH_URL  = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

# Rate limit: 3 req/s without API key, 10 req/s with key
_RATE_DELAY = 0.35          # seconds between HTTP requests
_TIMEOUT    = 20.0          # seconds


# ---------------------------------------------------------------------------
# Disease categories → search queries
# ---------------------------------------------------------------------------
DISEASE_QUERIES: dict[str, list[str]] = {
    "cardiovascular": [
        "myocardial infarction STEMI diagnosis management review",
        "heart failure acute decompensated treatment guidelines",
        "atrial fibrillation anticoagulation clinical management",
        "aortic dissection diagnosis emergency management",
        "unstable angina non-STEMI acute coronary syndrome",
    ],
    "respiratory": [
        "community acquired pneumonia diagnosis antibiotic treatment",
        "COPD chronic obstructive pulmonary disease exacerbation management",
        "pulmonary embolism diagnosis anticoagulation treatment",
        "asthma acute exacerbation treatment bronchodilator",
        "pleural effusion diagnosis evaluation causes",
    ],
    "neurological": [
        "ischemic stroke acute thrombolysis tPA management",
        "bacterial meningitis diagnosis cerebrospinal fluid treatment",
        "subarachnoid hemorrhage aneurysm diagnosis management",
        "transient ischemic attack TIA evaluation secondary prevention",
        "hypertensive encephalopathy brain presentation management",
    ],
    "gastrointestinal": [
        "acute appendicitis diagnosis CT laparoscopic surgery",
        "acute pancreatitis severity management fluid resuscitation",
        "upper gastrointestinal bleeding peptic ulcer endoscopy",
        "liver cirrhosis hepatic encephalopathy decompensation",
        "bowel obstruction small intestine diagnosis management",
    ],
    "metabolic": [
        "type 2 diabetes mellitus glycemic control management HbA1c",
        "diabetic ketoacidosis DKA insulin fluid electrolyte treatment",
        "hyperthyroidism thyroid storm Graves disease management",
        "hypothyroidism thyroid hormone replacement levothyroxine",
        "metabolic syndrome insulin resistance cardiovascular risk",
    ],
    "infectious": [
        "sepsis septic shock bundle antibiotics vasopressors management",
        "urinary tract infection UTI diagnosis antibiotic treatment",
        "cellulitis skin soft tissue infection antibiotic management",
        "infective endocarditis diagnosis blood culture echocardiography",
        "HIV opportunistic infections prophylaxis management",
    ],
    "renal": [
        "acute kidney injury AKI diagnosis KDIGO criteria management",
        "chronic kidney disease CKD progression proteinuria management",
        "nephrotic syndrome proteinuria hypoalbuminemia causes treatment",
        "hyperkalemia potassium management cardiac arrhythmia",
        "rhabdomyolysis myoglobulinuria acute kidney injury treatment",
    ],
    "hematological": [
        "iron deficiency anemia diagnosis ferritin treatment",
        "deep vein thrombosis DVT diagnosis ultrasonography anticoagulation",
        "thrombocytopenia ITP platelet count diagnosis treatment",
        "sickle cell disease vaso-occlusive crisis management pain",
        "disseminated intravascular coagulation DIC diagnosis treatment",
    ],
    "autoimmune": [
        "systemic lupus erythematosus SLE diagnosis criteria treatment",
        "rheumatoid arthritis DMARDs biologic treatment management",
        "inflammatory bowel disease Crohn disease ulcerative colitis",
        "multiple sclerosis diagnosis relapsing remitting treatment",
        "vasculitis ANCA associated diagnosis treatment",
    ],
    "musculoskeletal": [
        "gout acute monoarthritis urate crystal diagnosis treatment",
        "septic arthritis joint infection diagnosis arthrocentesis",
        "osteoporosis fracture prevention bisphosphonate treatment",
        "compartment syndrome fasciotomy diagnosis pressure monitoring",
        "osteomyelitis bone infection diagnosis antibiotic treatment",
    ],
}


# ---------------------------------------------------------------------------
# NCBI helpers
# ---------------------------------------------------------------------------

def _search_pmids(query: str, max_results: int, api_key: str | None) -> list[str]:
    """Return up to max_results PMIDs for a PubMed query."""
    params: dict = {
        "db": "pubmed",
        "term": query,
        "retmax": max_results,
        "retmode": "json",
        "sort": "relevance",
        "usehistory": "n",
    }
    if api_key:
        params["api_key"] = api_key

    try:
        r = httpx.get(ESEARCH_URL, params=params, timeout=_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        return data.get("esearchresult", {}).get("idlist", [])
    except Exception as exc:
        logger.warning("esearch failed for %r: %s", query, exc)
        return []


def _fetch_abstracts(pmids: list[str], api_key: str | None) -> str:
    """Fetch PubMed XML for a batch of PMIDs."""
    if not pmids:
        return ""
    params: dict = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "xml",
        "rettype": "abstract",
    }
    if api_key:
        params["api_key"] = api_key

    try:
        r = httpx.get(EFETCH_URL, params=params, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.text
    except Exception as exc:
        logger.warning("efetch failed for pmids %s: %s", pmids[:3], exc)
        return ""


def _parse_articles(xml_text: str) -> Iterator[dict]:
    """
    Parse PubMed XML and yield dicts with keys:
        pmid, title, abstract, pub_year
    Articles without an abstract are skipped.
    """
    if not xml_text:
        return

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.warning("XML parse error: %s", exc)
        return

    for article in root.findall(".//PubmedArticle"):
        # PMID
        pmid_el = article.find(".//PMID")
        pmid = pmid_el.text.strip() if pmid_el is not None else "unknown"

        # Title
        title_el = article.find(".//ArticleTitle")
        title = "".join(title_el.itertext()).strip() if title_el is not None else ""

        # Abstract — may be structured (multiple AbstractText with Label attr)
        abstract_parts: list[str] = []
        for at in article.findall(".//AbstractText"):
            label = at.get("Label")
            text  = "".join(at.itertext()).strip()
            if not text:
                continue
            if label:
                abstract_parts.append(f"{label}: {text}")
            else:
                abstract_parts.append(text)

        if not abstract_parts:
            continue  # skip articles without an abstract

        abstract = " ".join(abstract_parts)

        # Publication year (best-effort)
        year_el = article.find(".//PubDate/Year")
        pub_year = year_el.text.strip() if year_el is not None else ""

        yield {"pmid": pmid, "title": title, "abstract": abstract, "pub_year": pub_year}


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def _already_seeded(pmid: str) -> bool:
    """Return True if a document with source containing PMID:<pmid> exists."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    engine = create_async_engine(settings.database_url, echo=False)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with Session() as session:
            result = await session.execute(
                text("SELECT 1 FROM documents WHERE source LIKE :pat LIMIT 1"),
                {"pat": f"%PMID:{pmid}%"},
            )
            return result.scalar() is not None
    finally:
        await engine.dispose()


async def _insert_documents(docs: list[dict]) -> int:
    """Embed and insert a batch of document dicts into the database."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from app.models.db_models import Document

    if not docs:
        return 0

    contents = [d["content"] for d in docs]
    embeddings = embedding_service.embed_batch(contents)

    engine = create_async_engine(settings.database_url, echo=False)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    inserted = 0
    try:
        async with Session() as session:
            for doc_data, emb in zip(docs, embeddings):
                doc = Document(
                    id=uuid.uuid4(),
                    content=doc_data["content"],
                    embedding=emb,
                    source=doc_data["source"],
                    disease_category=doc_data["disease_category"],
                    evidence_type="review",   # PubMed abstracts are review-level evidence
                )
                session.add(doc)
                inserted += 1
            await session.commit()
    finally:
        await engine.dispose()

    return inserted


# ---------------------------------------------------------------------------
# Main seeding logic
# ---------------------------------------------------------------------------

async def seed(
    category_filter: str | None = None,
    limit: int = 10,
    dry_run: bool = False,
    api_key: str | None = None,
) -> None:
    total_inserted = 0
    total_skipped  = 0

    categories = (
        {category_filter: DISEASE_QUERIES[category_filter]}
        if category_filter
        else DISEASE_QUERIES
    )

    for category, queries in categories.items():
        logger.info("=" * 60)
        logger.info("Category: %s (%d queries)", category, len(queries))

        batch: list[dict] = []

        for query in queries:
            time.sleep(_RATE_DELAY)
            pmids = _search_pmids(query, limit, api_key)
            if not pmids:
                logger.info("  No results for %r", query)
                continue

            logger.info("  Query %r → %d PMIDs", query, len(pmids))

            time.sleep(_RATE_DELAY)
            xml_text = _fetch_abstracts(pmids, api_key)

            for article in _parse_articles(xml_text):
                pmid   = article["pmid"]
                source = f"PubMed PMID:{pmid} ({article['pub_year']})"

                # Build rich content block
                content = (
                    f"{article['title']}\n\n"
                    f"{article['abstract']}"
                ).strip()

                if len(content) < 100:
                    continue  # too short to be useful

                # Truncate very long abstracts (keep within embedding sweet spot)
                if len(content) > 3000:
                    content = content[:3000] + "…"

                batch.append({
                    "content": content,
                    "source": source,
                    "disease_category": category,
                })

            time.sleep(_RATE_DELAY)

        if dry_run:
            logger.info("  DRY-RUN: would insert %d documents for %s", len(batch), category)
            total_inserted += len(batch)
            continue

        # Filter out already-seeded PMIDs
        unique_batch: list[dict] = []
        for doc in batch:
            # Extract PMID from source string for dedup check
            pmid_part = doc["source"].split("PMID:")[1].split(" ")[0] if "PMID:" in doc["source"] else ""
            if pmid_part and await _already_seeded(pmid_part):
                total_skipped += 1
            else:
                unique_batch.append(doc)

        if unique_batch:
            n = await _insert_documents(unique_batch)
            total_inserted += n
            logger.info("  Inserted %d documents (skipped %d duplicates)", n, len(batch) - n)
        else:
            logger.info("  All %d documents already seeded", len(batch))

    logger.info("=" * 60)
    action = "Would insert" if dry_run else "Inserted"
    logger.info("%s %d documents total (%d duplicates skipped)", action, total_inserted, total_skipped)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Seed PubMed abstracts into the RAG vector store")
    parser.add_argument("--limit",    type=int, default=10, help="Max PMIDs per search query (default: 10)")
    parser.add_argument("--category", type=str, default=None, choices=list(DISEASE_QUERIES.keys()),
                        help="Seed only one disease category")
    parser.add_argument("--dry-run",  action="store_true", help="Preview without inserting")
    parser.add_argument("--ncbi-key", type=str, default=None, help="NCBI API key (unlocks 10 req/s)")
    args = parser.parse_args()

    asyncio.run(seed(
        category_filter=args.category,
        limit=args.limit,
        dry_run=args.dry_run,
        api_key=args.ncbi_key,
    ))


if __name__ == "__main__":
    main()
