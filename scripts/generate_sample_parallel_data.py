#!/usr/bin/env python3
"""Generate curated sample mock data for parallel analysis client demo.

Calls Gemini 3.5 Flash to produce 10 realistic business-name scenarios with
name and address variations, then expands them into lead + POS Excel files
that match the schema expected by ``mock_data/load_mock_data.py``.

Output per warehouse:
  mock_data/{warehouse}/leads_corrected.xlsx   (50 leads)
  mock_data/{warehouse}/pos_corrected.xlsx     (~1000 POS rows)

Usage:
  python scripts/generate_sample_parallel_data.py --warehouses 115,569,947
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
from datetime import datetime, timedelta, UTC
from pathlib import Path
from typing import Any

import pandas as pd

from lead_match_runtime.business_rules import load_business_rules, get_fiscal_year

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

_RULES = load_business_rules()
FISCAL_YEAR = get_fiscal_year(_RULES)
LEADS_PER_SCENARIO = 5
NUM_SCENARIOS = 10
BACKGROUND_POS_COUNT = 400

LEAD_SOURCES = ["Partner", "Referral", "ServiceNow", "Web", "Outbound"]
LEAD_TYPES = ["New Business", "Existing Account", "Expansion"]
SHOP_TYPES = ["In-Warehouse", "Delivery", "Pickup"]
INDUSTRIES = [
    ("Retail", "Retail merchandise and local storefront operations"),
    ("Food Service", "Restaurants, cafes, catering, and food preparation"),
    ("Health", "Clinics, pharmacies, and wellness providers"),
    ("Construction", "Contractors, trades, and project services"),
    ("Professional Services", "Office-based business services and consulting"),
    ("Automotive", "Vehicle sales, repair, and parts operations"),
    ("Education", "Training, tutoring, and educational services"),
    ("Real Estate", "Property management and brokerage services"),
    ("Logistics", "Delivery, storage, and logistics providers"),
    ("Manufacturing", "Light manufacturing and fabrication"),
]

GEMINI_PROMPT = """\
You are generating test data for a lead-to-POS business matching system that uses \
vector embeddings (cosine similarity) to score how similar two business names and \
addresses are.

Generate exactly 10 business scenarios as a JSON array. Each scenario demonstrates \
a specific type of name/address variation that the matching engine must handle.

For each scenario provide:
- "scenario_id": integer 1-10
- "demo_purpose": one sentence explaining what this scenario demonstrates to a client
- "lead_business_name": the canonical business name on the lead record
- "lead_address": street address for the lead (e.g. "1234 Oak Avenue")
- "lead_city": city name
- "lead_state": two-letter US state code
- "lead_zip": 5-digit zip code
- "lead_email": business email
- "lead_phone": phone in format "555-123-4567"
- "pos_variants": array of 4-6 POS variations, each with:
  - "business_name": the POS business name (varied from lead)
  - "address_line_one": the POS street address (varied from lead)
  - "city": POS city (usually same, occasionally abbreviated)
  - "state": POS state
  - "zip_code": POS zip (sometimes ZIP+4)
  - "email": POS email (same or different)
  - "phone": POS phone (same or different)
  - "variation_type": brief label (e.g. "abbreviation", "suffix_change", "exact_copy")

The 10 scenarios MUST cover these variation types (one scenario per type):
1. **Pvt/Private, Ltd/Limited abbreviation** — e.g. "Apex Private Limited" vs "Apex Pvt Ltd"
2. **Saint/St, School/Schl** — e.g. "St. Peter's School" vs "Saint Peters School"
3. **Regional abbreviation + word truncation** — e.g. "Pacific Northwest" vs "Pac NW"
4. **Ampersand & vs and** — e.g. "Johnson & Associates" vs "Johnson and Associates"
5. **Inc/Incorporated, word dropping** — e.g. "Green Valley Foods Inc" vs "Green Valley Organics"
6. **Centre/Center, Med/Medical** — e.g. "Sunrise Medical Centre" vs "Sunrise Med Center"
7. **Ordinal First/1st, Co/Company** — e.g. "First National Co" vs "1st National Company"
8. **LLC drop, plural variation** — e.g. "Heritage Kitchen LLC" vs "Heritage Kitchens"
9. **Deliberate NO-MATCH** — completely different businesses (POS names share NO words with lead). \
This is the negative example showing the system correctly rejects non-matches.
10. **Near-exact with address variation only** — same business name, but address has \
Street/St, Suite added, ZIP+4 differences

For address variations across ALL scenarios, mix in:
- Street/St, Avenue/Ave, Boulevard/Blvd, Road/Rd
- Suite/Ste numbers added or removed
- ZIP5 vs ZIP+4 (e.g. "98101" vs "98101-3344")
- Occasional minor city variation

For email/phone: in ~half the POS variants, keep the same email/phone as the lead \
(to demonstrate the +5 deterministic boost). In the other half, use a different \
email/phone (to show neutral disagreement — no penalty).

Return ONLY valid JSON — no markdown fences, no commentary.\
"""

FALLBACK_SCENARIOS = [
    {
        "scenario_id": 1,
        "demo_purpose": "Shows how abbreviations Pvt/Private and Ltd/Limited are handled by embeddings",
        "lead_business_name": "Apex Private Limited",
        "lead_address": "1200 Commerce Drive",
        "lead_city": "Portland",
        "lead_state": "OR",
        "lead_zip": "97201",
        "lead_email": "info@apexpvt.com",
        "lead_phone": "503-555-1200",
        "pos_variants": [
            {"business_name": "Apex Pvt Ltd", "address_line_one": "1200 Commerce Dr", "city": "Portland", "state": "OR", "zip_code": "97201", "email": "info@apexpvt.com", "phone": "503-555-1200", "variation_type": "abbreviation"},
            {"business_name": "Apex Pvt. Ltd.", "address_line_one": "1200 Commerce Drive STE 100", "city": "Portland", "state": "OR", "zip_code": "97201-4455", "email": "sales@apexgroup.com", "phone": "503-555-9999", "variation_type": "abbreviation_punctuated"},
            {"business_name": "APEX PRIVATE LIMITED", "address_line_one": "1200 Commerce DR", "city": "Portland", "state": "OR", "zip_code": "97201", "email": "info@apexpvt.com", "phone": "503-555-1200", "variation_type": "uppercase"},
            {"business_name": "Apex Private Ltd", "address_line_one": "1200 Commerce Drive", "city": "Portland", "state": "OR", "zip_code": "97201", "email": "admin@apexcorp.com", "phone": "503-555-8888", "variation_type": "partial_abbreviation"},
        ],
    },
    {
        "scenario_id": 2,
        "demo_purpose": "Demonstrates Saint/St and School/Schl apostrophe handling in embeddings",
        "lead_business_name": "St. Peter's International School",
        "lead_address": "450 Academy Boulevard",
        "lead_city": "Denver",
        "lead_state": "CO",
        "lead_zip": "80203",
        "lead_email": "admin@stpeters.edu",
        "lead_phone": "303-555-4500",
        "pos_variants": [
            {"business_name": "Saint Peters International School", "address_line_one": "450 Academy Blvd", "city": "Denver", "state": "CO", "zip_code": "80203", "email": "admin@stpeters.edu", "phone": "303-555-4500", "variation_type": "saint_expanded"},
            {"business_name": "St Peters Intl School", "address_line_one": "450 Academy Boulevard", "city": "Denver", "state": "CO", "zip_code": "80203-1122", "email": "office@stpetersintl.edu", "phone": "303-555-7777", "variation_type": "abbreviated"},
            {"business_name": "St. Peter's Intl Schl", "address_line_one": "450 Academy BLVD STE 2", "city": "Denver", "state": "CO", "zip_code": "80203", "email": "admin@stpeters.edu", "phone": "303-555-4500", "variation_type": "heavily_abbreviated"},
            {"business_name": "Saint Peter's School", "address_line_one": "450 Academy Blvd", "city": "Denver", "state": "CO", "zip_code": "80203", "email": "info@stpeterschool.org", "phone": "303-555-6666", "variation_type": "word_dropped"},
        ],
    },
    {
        "scenario_id": 3,
        "demo_purpose": "Regional abbreviation and word truncation: Pacific Northwest vs Pac NW",
        "lead_business_name": "Pacific Northwest Auto Repair",
        "lead_address": "8900 Rainier Avenue",
        "lead_city": "Seattle",
        "lead_state": "WA",
        "lead_zip": "98118",
        "lead_email": "service@pnwauto.com",
        "lead_phone": "206-555-8900",
        "pos_variants": [
            {"business_name": "Pacific NW Auto Repair", "address_line_one": "8900 Rainier Ave", "city": "Seattle", "state": "WA", "zip_code": "98118", "email": "service@pnwauto.com", "phone": "206-555-8900", "variation_type": "regional_abbrev"},
            {"business_name": "Pacific Northwest Automotive Repair", "address_line_one": "8900 Rainier Avenue", "city": "Seattle", "state": "WA", "zip_code": "98118-2200", "email": "info@pacnwauto.com", "phone": "206-555-1111", "variation_type": "word_expanded"},
            {"business_name": "Pac NW Auto", "address_line_one": "8900 Rainier Ave STE 5", "city": "Seattle", "state": "WA", "zip_code": "98118", "email": "service@pnwauto.com", "phone": "206-555-8900", "variation_type": "truncated"},
            {"business_name": "PNW Auto Repair Inc", "address_line_one": "8900 Rainier AVE", "city": "Seattle", "state": "WA", "zip_code": "98118", "email": "billing@pnwrepair.com", "phone": "206-555-3333", "variation_type": "initials_plus_suffix"},
        ],
    },
    {
        "scenario_id": 4,
        "demo_purpose": "Ampersand (&) vs 'and' and suffix abbreviation handling",
        "lead_business_name": "Johnson & Associates Construction",
        "lead_address": "3200 Industrial Parkway",
        "lead_city": "Phoenix",
        "lead_state": "AZ",
        "lead_zip": "85009",
        "lead_email": "info@johnsonassoc.com",
        "lead_phone": "602-555-3200",
        "pos_variants": [
            {"business_name": "Johnson and Associates Construction", "address_line_one": "3200 Industrial Pkwy", "city": "Phoenix", "state": "AZ", "zip_code": "85009", "email": "info@johnsonassoc.com", "phone": "602-555-3200", "variation_type": "and_for_ampersand"},
            {"business_name": "Johnson & Assoc Construction", "address_line_one": "3200 Industrial Parkway", "city": "Phoenix", "state": "AZ", "zip_code": "85009-6677", "email": "billing@johnsonconst.com", "phone": "602-555-4444", "variation_type": "suffix_abbreviated"},
            {"business_name": "Johnson Associates Constr", "address_line_one": "3200 Industrial PKWY", "city": "Phoenix", "state": "AZ", "zip_code": "85009", "email": "info@johnsonassoc.com", "phone": "602-555-3200", "variation_type": "ampersand_dropped"},
            {"business_name": "Johnson & Associates Const LLC", "address_line_one": "3200 Industrial Parkway STE 200", "city": "Phoenix", "state": "AZ", "zip_code": "85009", "email": "legal@johnsonllc.com", "phone": "602-555-5555", "variation_type": "suffix_added"},
        ],
    },
    {
        "scenario_id": 5,
        "demo_purpose": "Inc/Incorporated expansion and word dropping/rebranding",
        "lead_business_name": "Green Valley Organic Foods Inc",
        "lead_address": "750 Farm Road",
        "lead_city": "Sacramento",
        "lead_state": "CA",
        "lead_zip": "95814",
        "lead_email": "orders@greenvalley.com",
        "lead_phone": "916-555-7500",
        "pos_variants": [
            {"business_name": "Green Valley Organic Foods Incorporated", "address_line_one": "750 Farm Rd", "city": "Sacramento", "state": "CA", "zip_code": "95814", "email": "orders@greenvalley.com", "phone": "916-555-7500", "variation_type": "inc_expanded"},
            {"business_name": "Green Valley Organics", "address_line_one": "750 Farm Road", "city": "Sacramento", "state": "CA", "zip_code": "95814-3300", "email": "info@gvorganics.com", "phone": "916-555-2222", "variation_type": "words_dropped"},
            {"business_name": "GV Organic Foods", "address_line_one": "750 Farm RD STE A", "city": "Sacramento", "state": "CA", "zip_code": "95814", "email": "orders@greenvalley.com", "phone": "916-555-7500", "variation_type": "initials"},
            {"business_name": "Green Valley Foods", "address_line_one": "750 Farm Road", "city": "Sacramento", "state": "CA", "zip_code": "95814", "email": "sales@gvfoods.com", "phone": "916-555-6666", "variation_type": "organic_dropped"},
        ],
    },
    {
        "scenario_id": 6,
        "demo_purpose": "Centre/Center and Medical/Med abbreviation handling",
        "lead_business_name": "Sunrise Medical Centre",
        "lead_address": "200 Health Plaza",
        "lead_city": "Austin",
        "lead_state": "TX",
        "lead_zip": "78701",
        "lead_email": "reception@sunrisemedical.com",
        "lead_phone": "512-555-2000",
        "pos_variants": [
            {"business_name": "Sunrise Medical Center", "address_line_one": "200 Health Plz", "city": "Austin", "state": "TX", "zip_code": "78701", "email": "reception@sunrisemedical.com", "phone": "512-555-2000", "variation_type": "centre_to_center"},
            {"business_name": "Sunrise Med Center", "address_line_one": "200 Health Plaza", "city": "Austin", "state": "TX", "zip_code": "78701-5500", "email": "billing@sunrisemed.com", "phone": "512-555-9000", "variation_type": "medical_abbreviated"},
            {"business_name": "Sunrise Medical Ctr", "address_line_one": "200 Health PLAZA STE 300", "city": "Austin", "state": "TX", "zip_code": "78701", "email": "reception@sunrisemedical.com", "phone": "512-555-2000", "variation_type": "center_abbreviated"},
            {"business_name": "Sunrise Med Centre", "address_line_one": "200 Health Plz", "city": "Austin", "state": "TX", "zip_code": "78701", "email": "info@sunrisehealth.com", "phone": "512-555-7777", "variation_type": "mixed_spelling"},
        ],
    },
    {
        "scenario_id": 7,
        "demo_purpose": "Ordinal (First/1st) and Company/Co abbreviation",
        "lead_business_name": "First National Insurance Company",
        "lead_address": "500 Financial Drive",
        "lead_city": "Chicago",
        "lead_state": "IL",
        "lead_zip": "60601",
        "lead_email": "claims@firstnational.com",
        "lead_phone": "312-555-5000",
        "pos_variants": [
            {"business_name": "1st National Insurance Co", "address_line_one": "500 Financial Dr", "city": "Chicago", "state": "IL", "zip_code": "60601", "email": "claims@firstnational.com", "phone": "312-555-5000", "variation_type": "ordinal_and_abbrev"},
            {"business_name": "First National Insurance", "address_line_one": "500 Financial Drive", "city": "Chicago", "state": "IL", "zip_code": "60601-8800", "email": "support@fni.com", "phone": "312-555-1111", "variation_type": "company_dropped"},
            {"business_name": "First Natl Insurance Company", "address_line_one": "500 Financial DR STE 1200", "city": "Chicago", "state": "IL", "zip_code": "60601", "email": "claims@firstnational.com", "phone": "312-555-5000", "variation_type": "national_abbreviated"},
            {"business_name": "1st Natl Insurance", "address_line_one": "500 Financial Drive", "city": "Chicago", "state": "IL", "zip_code": "60601", "email": "agent@firstnatl.com", "phone": "312-555-4444", "variation_type": "double_abbreviation"},
        ],
    },
    {
        "scenario_id": 8,
        "demo_purpose": "LLC suffix drop, ampersand, and plural variation",
        "lead_business_name": "Heritage Kitchen & Catering LLC",
        "lead_address": "1800 Culinary Lane",
        "lead_city": "Nashville",
        "lead_state": "TN",
        "lead_zip": "37203",
        "lead_email": "events@heritagekitchen.com",
        "lead_phone": "615-555-1800",
        "pos_variants": [
            {"business_name": "Heritage Kitchen and Catering", "address_line_one": "1800 Culinary Ln", "city": "Nashville", "state": "TN", "zip_code": "37203", "email": "events@heritagekitchen.com", "phone": "615-555-1800", "variation_type": "llc_dropped_and"},
            {"business_name": "Heritage Kitchen Catering LLC", "address_line_one": "1800 Culinary Lane", "city": "Nashville", "state": "TN", "zip_code": "37203-4400", "email": "catering@heritage.com", "phone": "615-555-3333", "variation_type": "ampersand_dropped"},
            {"business_name": "Heritage Kitchens & Catering", "address_line_one": "1800 Culinary LN STE B", "city": "Nashville", "state": "TN", "zip_code": "37203", "email": "events@heritagekitchen.com", "phone": "615-555-1800", "variation_type": "plural"},
            {"business_name": "Heritage Kitchen", "address_line_one": "1800 Culinary Lane", "city": "Nashville", "state": "TN", "zip_code": "37203", "email": "info@heritagekitchens.com", "phone": "615-555-2222", "variation_type": "catering_dropped"},
        ],
    },
    {
        "scenario_id": 9,
        "demo_purpose": "Deliberate NO-MATCH: completely unrelated businesses showing correct rejection",
        "lead_business_name": "Brightstar Digital Solutions",
        "lead_address": "999 Innovation Way",
        "lead_city": "San Jose",
        "lead_state": "CA",
        "lead_zip": "95112",
        "lead_email": "hello@brightstar.io",
        "lead_phone": "408-555-9990",
        "pos_variants": [
            {"business_name": "Mountain View Hardware Store", "address_line_one": "2200 Castro Street", "city": "Mountain View", "state": "CA", "zip_code": "94041", "email": "sales@mvhardware.com", "phone": "650-555-2200", "variation_type": "no_match"},
            {"business_name": "Blue Ocean Seafood Market", "address_line_one": "88 Fishermans Wharf", "city": "San Francisco", "state": "CA", "zip_code": "94133", "email": "fresh@blueocean.com", "phone": "415-555-8800", "variation_type": "no_match"},
            {"business_name": "Golden State Tire Center", "address_line_one": "3400 El Camino Real", "city": "Santa Clara", "state": "CA", "zip_code": "95051", "email": "service@gstire.com", "phone": "408-555-3400", "variation_type": "no_match"},
            {"business_name": "Redwood Family Dentistry", "address_line_one": "1500 Main Street", "city": "Redwood City", "state": "CA", "zip_code": "94063", "email": "smile@redwooddental.com", "phone": "650-555-1500", "variation_type": "no_match"},
        ],
    },
    {
        "scenario_id": 10,
        "demo_purpose": "Near-exact business name with address-only variations (Street/St, Suite, ZIP+4)",
        "lead_business_name": "Evergreen Supply Warehouse",
        "lead_address": "6000 Depot Street",
        "lead_city": "Minneapolis",
        "lead_state": "MN",
        "lead_zip": "55401",
        "lead_email": "orders@evergreensupply.com",
        "lead_phone": "612-555-6000",
        "pos_variants": [
            {"business_name": "Evergreen Supply Warehouse", "address_line_one": "6000 Depot St", "city": "Minneapolis", "state": "MN", "zip_code": "55401", "email": "orders@evergreensupply.com", "phone": "612-555-6000", "variation_type": "exact_name_address_abbrev"},
            {"business_name": "Evergreen Supply Whse", "address_line_one": "6000 Depot Street STE 10", "city": "Minneapolis", "state": "MN", "zip_code": "55401-7700", "email": "shipping@evergreen.com", "phone": "612-555-8888", "variation_type": "warehouse_abbreviated"},
            {"business_name": "Evergreen Supplies Warehouse", "address_line_one": "6000 Depot ST", "city": "Minneapolis", "state": "MN", "zip_code": "55401", "email": "orders@evergreensupply.com", "phone": "612-555-6000", "variation_type": "plural_variation"},
            {"business_name": "Evergreen Supply Warehouse", "address_line_one": "6000 Depot Street", "city": "Mpls", "state": "MN", "zip_code": "55401-7700", "email": "warehouse@egsupply.com", "phone": "612-555-5555", "variation_type": "exact_name_city_abbrev"},
        ],
    },
]

BACKGROUND_BUSINESSES = [
    ("Lakeside Pet Grooming", "700 Lakeshore Drive"),
    ("Central Valley Plumbing", "1100 Main Street"),
    ("Rapid Transit Courier", "950 Logistics Avenue"),
    ("Diamond Auto Glass", "320 Windshield Lane"),
    ("Coastal Surf Shop", "55 Beach Boulevard"),
    ("Pioneer Feed & Grain", "4200 Rural Route"),
    ("Metro Dry Cleaners", "880 Garment Way"),
    ("Summit IT Consulting", "2300 Tech Center Drive"),
    ("Valley View Dental", "1650 Smile Street"),
    ("Harbor Marine Supply", "100 Dock Road"),
    ("Crosstown Electric", "3100 Wire Avenue"),
    ("Prairie Wind Farm Supply", "7700 Harvest Lane"),
    ("Alpine Ski Rentals", "500 Mountain Pass Road"),
    ("Bayshore Coffee Roasters", "240 Espresso Court"),
    ("Ironwork Fabrication", "1900 Steel Boulevard"),
]


def call_gemini_for_scenarios() -> list[dict]:
    """Call Gemini 3.5 Flash to generate business name scenarios."""
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        logger.warning("google-genai not installed; using built-in fallback scenarios")
        return FALLBACK_SCENARIOS

    project = (
        os.getenv("VERTEX_PROJECT_ID")
        or os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("PROJECT_ID")
    )
    location = os.getenv("VERTEX_LOCATION", _RULES["environment"]["vertex_ai"]["location"])
    model_name = os.getenv("GEMINI_MODEL", _RULES["environment"]["models"]["gemini_flash"])

    if not project:
        logger.warning("No VERTEX_PROJECT_ID set; using built-in fallback scenarios")
        return FALLBACK_SCENARIOS

    logger.info("Calling Gemini %s for scenario generation...", model_name)
    try:
        client = genai.Client(
            vertexai=True,
            project=project,
            location=location,
            http_options=types.HttpOptions(api_version="v1"),
        )
        response = client.models.generate_content(model=model_name, contents=GEMINI_PROMPT)
        text = getattr(response, "text", "") or str(response)
        text = text.strip()
        if text.startswith("```"):
            text = text.split("```", 1)[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.rsplit("```", 1)[0].strip()
        scenarios = json.loads(text)
        if isinstance(scenarios, list) and len(scenarios) >= 8:
            logger.info("Gemini returned %d scenarios", len(scenarios))
            return scenarios
        logger.warning("Gemini returned unexpected format; using fallback")
        return FALLBACK_SCENARIOS
    except Exception as e:
        logger.warning("Gemini call failed (%s); using built-in fallback scenarios", e)
        return FALLBACK_SCENARIOS


def period_to_timestamp(fiscal_year: int, fiscal_period: int, week: int, rng: random.Random) -> datetime:
    anchor = datetime(fiscal_year, 1, 1)
    days = (fiscal_period - 1) * 28 + (week - 1) * 7 + rng.randint(0, 3)
    hours = rng.randint(8, 18)
    minutes = rng.randint(0, 59)
    seconds = rng.randint(0, 59)
    return anchor + timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)


def phone_digits(rng: random.Random) -> str:
    area = rng.randint(200, 989)
    prefix = rng.randint(200, 989)
    line = rng.randint(1000, 9999)
    return f"{area:03d}-{prefix:03d}-{line:04d}"


def generate_leads(scenarios: list[dict], warehouse: int, rng: random.Random) -> list[dict]:
    """Generate 50 lead rows (5 per scenario) from the scenario definitions."""
    leads = []
    acct_counter = 20000001
    for scenario in scenarios[:NUM_SCENARIOS]:
        sid = scenario["scenario_id"]
        for j in range(LEADS_PER_SCENARIO):
            lead_idx = (sid - 1) * LEADS_PER_SCENARIO + j + 1
            lead_id = f"LEAD-SAMPLE-{warehouse}-{lead_idx:04d}"
            fp = rng.randint(1, 10)
            wk = rng.randint(1, 4)
            ts = period_to_timestamp(FISCAL_YEAR, fp, wk, rng)
            industry, industry_desc = INDUSTRIES[rng.randrange(len(INDUSTRIES))]
            addr = scenario.get("lead_address", "100 Main Street")
            city = scenario.get("lead_city", "Anytown")
            state = scenario.get("lead_state", "WA")
            zipcode = scenario.get("lead_zip", "98101")
            leads.append({
                "lead_id": lead_id,
                "warehouse_number": warehouse,
                "fiscal_year_lead": FISCAL_YEAR,
                "fiscal_period_lead": fp,
                "week": wk,
                "updated_date": ts.isoformat(),
                "lead_source": rng.choice(LEAD_SOURCES),
                "account_number": str(acct_counter),
                "membership_number": f"MEM{rng.randint(100000, 999999)}",
                "lead_status": "Open",
                "confidence_level": "",
                "match_result": "",
                "type": rng.choice(LEAD_TYPES),
                "business_name": scenario["lead_business_name"],
                "address_line_one": addr,
                "address_line_two": "",
                "city": city,
                "state": state,
                "zip_code": zipcode,
                "email": scenario.get("lead_email", ""),
                "phone": scenario.get("lead_phone", ""),
                "bd_industry": industry,
                "industry_description": industry_desc,
                "customer_name": "",
                "closed_fiscal_period": "",
                "closed_fiscal_year": "",
                "batch_id": f"sample-{warehouse}",
                "load_date": datetime.now(UTC).isoformat(),
                "updated_by": "generate_sample_parallel_data",
            })
            acct_counter += 1
    return leads


def generate_pos_for_scenario(
    scenario: dict,
    warehouse: int,
    rng: random.Random,
    pos_counter: int,
) -> tuple[list[dict], int]:
    """Generate POS rows for one scenario's variants, linked to its leads."""
    rows = []
    sid = scenario["scenario_id"]
    variants = scenario.get("pos_variants", [])

    for lead_j in range(LEADS_PER_SCENARIO):
        lead_id = f"LEAD-SAMPLE-{warehouse}-{(sid - 1) * LEADS_PER_SCENARIO + lead_j + 1:04d}"
        for var in variants:
            pos_counter += 1
            fp = rng.randint(1, 13)
            wk = rng.randint(1, 4)
            ts = period_to_timestamp(FISCAL_YEAR, fp, wk, rng)
            relation = "no_match" if scenario["scenario_id"] == 9 else "partial_single"
            rows.append({
                "pos_id": f"POS-SAMPLE-{warehouse}-{pos_counter:05d}",
                "warehouse_number": warehouse,
                "fiscal_year_transaction": FISCAL_YEAR,
                "fiscal_period_transaction": fp,
                "week": wk,
                "account_number": str(rng.randint(30000001, 39999999)),
                "membership_number": f"MEM{rng.randint(100000, 999999)}",
                "sales_reference_id": f"SRF{rng.randint(100000000, 999999999)}",
                "business_name": var["business_name"],
                "first_name": "",
                "last_name": "",
                "address_line_one": var.get("address_line_one", ""),
                "address_line_two": "",
                "city": var.get("city", ""),
                "state": var.get("state", ""),
                "zip_code": var.get("zip_code", ""),
                "email": var.get("email", ""),
                "phone": var.get("phone", ""),
                "order_amount": round(rng.uniform(50, 5000), 2),
                "shop_type": rng.choice(SHOP_TYPES),
                "bd_industry": "",
                "industry_description": "",
                "load_date": datetime.now(UTC).isoformat(),
                "updated_date": ts.isoformat(),
                "expected_relation": relation,
                "expected_lead_id": lead_id,
            })
    return rows, pos_counter


def generate_background_pos(
    warehouse: int,
    rng: random.Random,
    pos_counter: int,
    count: int = BACKGROUND_POS_COUNT,
) -> tuple[list[dict], int]:
    """Generate unrelated background POS rows (no matching lead)."""
    rows = []
    for _ in range(count):
        pos_counter += 1
        biz_name, addr = rng.choice(BACKGROUND_BUSINESSES)
        fp = rng.randint(1, 13)
        wk = rng.randint(1, 4)
        ts = period_to_timestamp(FISCAL_YEAR, fp, wk, rng)
        city = rng.choice(["Springfield", "Riverside", "Fairview", "Madison", "Franklin"])
        state = rng.choice(["WA", "OR", "CA", "TX", "IL", "MN", "TN", "AZ", "CO"])
        rows.append({
            "pos_id": f"POS-SAMPLE-{warehouse}-{pos_counter:05d}",
            "warehouse_number": warehouse,
            "fiscal_year_transaction": FISCAL_YEAR,
            "fiscal_period_transaction": fp,
            "week": wk,
            "account_number": str(rng.randint(40000001, 49999999)),
            "membership_number": f"MEM{rng.randint(100000, 999999)}",
            "sales_reference_id": f"SRF{rng.randint(100000000, 999999999)}",
            "business_name": biz_name,
            "first_name": "",
            "last_name": "",
            "address_line_one": addr,
            "address_line_two": "",
            "city": city,
            "state": state,
            "zip_code": f"{rng.randint(10000, 99999)}",
            "email": "",
            "phone": phone_digits(rng),
            "order_amount": round(rng.uniform(25, 3000), 2),
            "shop_type": rng.choice(SHOP_TYPES),
            "bd_industry": "",
            "industry_description": "",
            "load_date": datetime.now(UTC).isoformat(),
            "updated_date": ts.isoformat(),
            "expected_relation": "unmatched",
            "expected_lead_id": "",
        })
    return rows, pos_counter


def generate_warehouse(
    scenarios: list[dict],
    warehouse: int,
    output_dir: Path,
    seed: int,
) -> None:
    """Generate leads_corrected.xlsx and pos_corrected.xlsx for one warehouse."""
    rng = random.Random(seed + warehouse)
    wh_dir = output_dir / str(warehouse)
    wh_dir.mkdir(parents=True, exist_ok=True)

    leads = generate_leads(scenarios, warehouse, rng)

    all_pos = []
    pos_counter = 0
    for scenario in scenarios[:NUM_SCENARIOS]:
        rows, pos_counter = generate_pos_for_scenario(scenario, warehouse, rng, pos_counter)
        all_pos.extend(rows)

    bg_rows, pos_counter = generate_background_pos(warehouse, rng, pos_counter)
    all_pos.extend(bg_rows)

    leads_df = pd.DataFrame(leads)
    pos_df = pd.DataFrame(all_pos)

    leads_path = wh_dir / "leads_corrected.xlsx"
    pos_path = wh_dir / "pos_corrected.xlsx"

    leads_df.to_excel(leads_path, index=False)
    pos_df.to_excel(pos_path, index=False)

    logger.info(
        "Warehouse %s: %d leads, %d POS rows → %s",
        warehouse, len(leads_df), len(pos_df), wh_dir,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate curated sample mock data for parallel analysis demo",
    )
    parser.add_argument(
        "--warehouses",
        default="115,569,947",
        help="Comma-separated warehouse numbers (default: 115,569,947)",
    )
    parser.add_argument(
        "--output-dir",
        default=str(Path(__file__).resolve().parents[1] / "mock_data"),
        help="Output directory (default: mock_data/)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducibility (default: 42)",
    )
    parser.add_argument(
        "--use-fallback",
        action="store_true",
        help="Skip Gemini and use built-in fallback scenarios",
    )
    args = parser.parse_args()

    warehouses = [int(w.strip()) for w in args.warehouses.split(",")]
    output_dir = Path(args.output_dir)

    if args.use_fallback:
        scenarios = FALLBACK_SCENARIOS
        logger.info("Using built-in fallback scenarios (--use-fallback)")
    else:
        scenarios = call_gemini_for_scenarios()

    scenarios_path = output_dir / "sample_scenarios.json"
    scenarios_path.parent.mkdir(parents=True, exist_ok=True)
    with open(scenarios_path, "w") as f:
        json.dump(scenarios, f, indent=2)
    logger.info("Saved %d scenarios to %s", len(scenarios), scenarios_path)

    for wh in warehouses:
        generate_warehouse(scenarios, wh, output_dir, args.seed)

    logger.info("Done. Load with: python mock_data/load_mock_data.py --warehouse <N>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
