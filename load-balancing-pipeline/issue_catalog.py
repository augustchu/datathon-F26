"""Editable medical-issue to care-capability map for the future patient UI.

These entries are examples only. Replace/extend them with the team's reviewed
dropdown vocabulary and clinical service map before using recommendations.
"""

ISSUE_CATALOG = {
    "general_care": {
        "label": "General / not yet specified",
        "service_key": "general care",
        "provider_scope": "general_or_multispecialty",
        "nfhs_profile": "general care",
        "draft": True,
    },
    "dialysis_need": {
        "label": "Dialysis need (example)",
        "service_key": "dialysis",
        "provider_scope": "any_matching_provider",
        "nfhs_profile": "dialysis",
        "draft": True,
    },
    "emergency_c_section": {
        "label": "Emergency C-section (example)",
        "service_key": "emergency c-section",
        "provider_scope": "any_matching_provider",
        "nfhs_profile": "emergency c-section",
        "draft": True,
    },
    "newborn_icu": {
        "label": "Newborn intensive care (example)",
        "service_key": "nicu",
        "provider_scope": "any_matching_provider",
        "nfhs_profile": "nicu",
        "draft": True,
    },
    "cardiac_surgery": {
        "label": "Cardiac surgery (example)",
        "service_key": "cardiac surgery",
        "provider_scope": "any_matching_provider",
        "nfhs_profile": "cardiac surgery",
        "draft": True,
    },
    "mri_need": {
        "label": "MRI scan (example)",
        "service_key": "mri",
        "provider_scope": "any_matching_provider",
        "nfhs_profile": "mri",
        "draft": True,
    },
    "burn_injury": {
        "label": "Burn injury (example)",
        "service_key": "burn care",
        "provider_scope": "any_matching_provider",
        "nfhs_profile": "burn care",
        "draft": True,
    },
    "trauma_injury": {
        "label": "Trauma / serious injury (example)",
        "service_key": "trauma",
        "provider_scope": "any_matching_provider",
        "nfhs_profile": "trauma",
        "draft": True,
    },
    "pediatric_illness": {
        "label": "Child illness (example)",
        "service_key": "pediatric illness",
        "provider_scope": "any_matching_provider",
        "nfhs_profile": "pediatric illness",
        "draft": True,
    },
    "maternal_care": {
        "label": "Maternal care (example)",
        "service_key": "maternal care",
        "provider_scope": "any_matching_provider",
        "nfhs_profile": "maternal care",
        "draft": True,
    },
}
