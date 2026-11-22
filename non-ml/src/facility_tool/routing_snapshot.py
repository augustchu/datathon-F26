"""Prepare the baseline blood-bank snapshot for the live routing check."""

import json
import os
from pathlib import Path

import pandas as pd

from .data import extract_claims, link_entities, load_records
from .geo_audit import attach_routing_review, audit_locations
from .geography import add_postal_context, build_postal_areas
from .patient_experience import prepare_patient_experience
from .quality import build_quality_profiles
from .query_assets import prepare_lookup_assets
from .reviewed_claims import attach_reviewed_claims
from .updates import contact_evidence
from .verification import verify_services
from .workspace import load_inputs


def prepare_blood_bank_snapshot(root, store, config, *, mode='databricks', spark=None):
    """Build the test cohort in a fresh store without recovery, model or load overlays."""
    if mode not in {'databricks', 'export', 'public'}:
        raise ValueError('The live routing baseline requires databricks, export or public inputs.')
    root = Path(root)
    as_of = config['as_of']
    config_dir = root / 'non-ml' / 'config'
    inputs = load_inputs(mode, config, spark)
    facilities = inputs['facilities']
    records = load_records(root, facilities if len(facilities.columns) else None)
    entities, links = link_entities(records)
    postal_areas = build_postal_areas(inputs['pins'])
    entities = add_postal_context(entities, postal_areas)
    reviewed_holds = json.loads((config_dir / 'reviewed_location_holds.json').read_text(encoding='utf-8'))
    _, review, _ = audit_locations(
        records, entities, postal_areas, links=links, postal_offset_km=50, reviewed_holds=reviewed_holds,
    )
    entities = attach_routing_review(entities, review)

    aliases = config['service_aliases']
    blood_bank_aliases = {'blood_bank': aliases['blood_bank']}
    claims = extract_claims(records, links, blood_bank_aliases, as_of)
    reviewed_entries = json.loads((config_dir / 'reviewed_service_evidence.json').read_text(encoding='utf-8'))
    reviewed, _ = attach_reviewed_claims(reviewed_entries, entities, aliases, as_of)
    reviewed_blood_bank = reviewed.loc[reviewed['service'].eq('blood_bank')]
    if not reviewed_blood_bank.empty:
        claims = pd.concat([claims, reviewed_blood_bank], ignore_index=True)
    verification = verify_services(claims, as_of)
    _, quality = build_quality_profiles(records, links, claims, verification, as_of)
    rating_path = os.environ.get('DATATHON_PATIENT_RATINGS')
    ratings = pd.read_csv(rating_path, dtype={'facility_id': str}) if rating_path else pd.DataFrame()

    frames = {
        'entities': entities,
        'postal_areas': postal_areas,
        'verified_claims': claims,
        'service_verification': verification,
        'quality_profiles': quality,
        'patient_experience': prepare_patient_experience(entities, ratings, as_of),
        'contact_evidence': contact_evidence(records, links),
    }
    for name, frame in frames.items():
        store.write(name, frame)
    return prepare_lookup_assets(store, {**config, 'service_aliases': blood_bank_aliases})
