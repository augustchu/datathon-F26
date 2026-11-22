"""Find reviewable field claims in saved passages with exact source offsets."""

from functools import lru_cache
import re

from .data import EQUIPMENT_CONTEXT, WEAK_CONTEXT, _parse_date


SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|[;\r\n]+")
NUMBER = r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]{1,2}(?:,[0-9]{2})+,[0-9]{3}|[0-9]{1,9})"
NUMBER_START = r"(?<![\w.,])"
NUMBER_END = r"(?![\w]|[.,][0-9])"
FACILITY = r"(?:hospital|facility|nursing home|medical cent(?:re|er))"
STAFF_BASIS = r"(?:full[- ]time|employed|visiting|resident|permanent|on[- ]staff)"
BED_BASIS = r"(?:staffed|licensed|operating|operational)"
BED_LABEL = r"(?:bed capacity|bed count|bed strength|beds)"
DOCTOR_LABEL = r"(?:doctors|physicians)"
DATED = re.compile(r"\bas of\s+([0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{1,2}[./-][0-9]{1,2}[./-][0-9]{4})\b", re.I)
UNCERTAIN_LEFT = re.compile(
    r"\b(?:over|under|around|about|approximately|approx\.?|nearly|up to|at least|at most|"
    r"more than|less than|no more than|no|not|only|(?:between|from)\s+[0-9,.]+\s+(?:and|to))\s*$"
    r"|(?:[0-9]\s*(?:[-–—/]|to|or)|[+<>~≤≥])\s*$", re.I,
)
UNCERTAIN_RIGHT = re.compile(r"^\s*(?:[+<>~≤≥]|(?:[-–—/]|to|or)\s*[0-9])", re.I)
PAST_OR_PLANNED = re.compile(
    r"\b(?:formerly|previously|used to|initially|originally|began with|started (?:with|as)|"
    r"opened (?:as|with)|(?:expanded|increased|grew|rose) from|capable of expanding|expansion|"
    r"will (?:have|offer|operate|provide|install|add|be)|planned|plans to|proposed|under construction|"
    r"expected to|projected|procurement|tender|orders? for|being installed|upcoming)\b", re.I,
)
OUTSIDE_BRANCH = re.compile(
    r"\b(?:(?:other|another|nearby|adjacent|partner|affiliated|parent|sister|referral)\s+"
    r"(?:[\w-]+\s+){0,2}(?:branch(?:es)?|hospitals?|facilit(?:y|ies)|clinics?|cent(?:re|er)s?)|"
    r"across (?:its|our|all|the|multiple|several|two|three|four|five|[0-9]+)|"
    r"(?:both|all) (?:branches|locations|facilities|hospitals)|network[- ]wide|chain[- ]wide|"
    r"group[- ]wide|group of hospitals|network total|combined|aggregate|operated by a third party)\b", re.I,
)
UNIT = r"(?:icu|nicu|picu|sicu|ccu|hdu|intensive care|critical care|dialysis|neonatal|maternity|"
UNIT += r"emergency|trauma|isolation|wards?|rooms?|floors?|departments?|wings?|units?)"
UNIT_BEFORE = re.compile(rf"\b{UNIT}\b", re.I)
UNIT_AFTER = re.compile(
    rf"^\s+(?:(?:surgical|medical|adult|paediatric|pediatric)\s+){{0,2}}{UNIT}\b"
    rf"|^\s+(?:(?:only|dedicated|assigned|used)\s+){{0,2}}(?:in|at|for|within)\s+"
    rf"(?:[\w-]+\s+){{0,4}}{UNIT}\b", re.I,
)
SUBJECT = re.compile(
    rf"\b(?:{FACILITY}|branch|site|it|we)\s+(?:currently\s+|now\s+)?"
    r"(?:has|have|had|houses|maintains|accommodates|employs|employed|staffs|staffed|provides|offers)\s+"
    r"(?:(?:a total|a capacity|a team|a staff|a headcount)\s+of\s+)?$", re.I,
)
FACILITY_LABEL = re.compile(rf"\b(?:{FACILITY}|branch|site)(?:'s|’s)?\s*$|\btotal\s*$", re.I)
FACILITY_FEATURES = re.compile(rf"\b{FACILITY}\s+(?:currently\s+)?features\s+(?:a\s+)?$", re.I)
BEDDED_CAPACITY = re.compile(r"^\s+capacity\b", re.I)
CAPACITY_TAIL = re.compile(r"^\s*(?:[.!?]\s*$|$|,?\s*(?:including|comprising)\b)", re.I)
DESCRIPTOR = re.compile(
    r"^\s+(?:(?:multi[- ]?special(?:ty|ity)|super[- ]?special(?:ty|ity)|district|rural|"
    r"general|teaching|private|government|tertiary|secondary|charitable|modern|regional|"
    rf"speciali[sz]ed|referral|specialty|speciality)\s+){{0,5}}{FACILITY}\b", re.I,
)
PAST_SUBJECT = re.compile(r"\b(?:had|employed|staffed|was|were)\b", re.I)
COUNTS = {
    'capacity': re.compile(
        rf"{NUMBER_START}(?P<first>{NUMBER})\s*[-–—]?\s*(?:(?P<basis>{BED_BASIS})\s+)?"
        r"(?:(?:total|inpatient|hospital)\s+)?beds?(?:ded)?\b"
        rf"|\b(?P<label>(?:total\s+)?(?:{BED_BASIS}\s+)?{BED_LABEL})"
        rf"\s*(?:of\s+|is\s+|was\s+|[:=]\s*)?(?P<last>{NUMBER}){NUMBER_END}", re.I,
    ),
    'numberDoctors': re.compile(
        rf"{NUMBER_START}(?P<first>{NUMBER})\s+(?:(?P<basis>{STAFF_BASIS})\s+)?{DOCTOR_LABEL}\b"
        rf"|\b(?P<label>(?:total\s+)?(?:{STAFF_BASIS}\s+)?(?:doctor count|physician count|{DOCTOR_LABEL}))"
        rf"\s*(?:of\s+|is\s+|was\s+|[:=]\s*)?(?P<last>{NUMBER}){NUMBER_END}", re.I,
    ),
}
DOCTOR_UNITS = {
    'full time': 'full_time_doctors', 'employed': 'employed_doctors', 'visiting': 'visiting_doctors',
    'permanent': 'permanent_doctors', 'resident': 'resident_doctors', 'on staff': 'on_staff_doctors',
}
BED_UNITS = {'licensed': 'licensed_beds', 'staffed': 'staffed_beds', 'operating': 'operating_beds', 'operational': 'operating_beds'}
EQUIPMENT = {
    'mri_scanner': r"(?:mri|magnetic resonance imaging)\s+(?:scanners?|machines?|systems?)",
    'ct_scanner': r"(?:ct|computed tomography)\s+(?:scanners?|machines?|systems?)",
    'dialysis_machine': r"(?:ha?emodialysis|dialysis)\s+machines?",
    'ventilator': r"ventilators?",
    'digital_xray_system': r"digital\s+x[- ]?ray\s+(?:machines?|systems?|equipment)",
    'xray_system': r"x[- ]?ray\s+(?:machines?|systems?|equipment)",
    'ultrasound_machine': r"(?:ultrasound|ultrasonography)\s+(?:machines?|systems?|equipment)",
    'defibrillator': r"defibrillators?",
    'oxygen_concentrator': r"oxygen\s+concentrators?",
    'mammography_machine': r"mammography\s+(?:machines?|systems?|equipment)",
}
EQUIPMENT_PATTERNS = {key: re.compile(rf"\b(?:{pattern})\b", re.I) for key, pattern in EQUIPMENT.items()}
EQUIPMENT_SUBTYPE = re.compile(r"(?P<subtype>[0-9]+(?:\.[0-9]+)?[ -]?(?:tesla|slice|t))\s*$", re.I)
EQUIPMENT_LEFT = re.compile(
    rf"\b(?:{FACILITY}|branch|site|we|it)\s+(?:currently\s+|now\s+)?"
    r"(?:has|have|houses|operates|maintains|(?:is|are)\s+(?:currently\s+)?equipped with|has installed|installed)\s+"
    r"(?:(?:an?|[0-9]+)\s+)?$|\b(?:installed|on[- ]site)\s+(?:(?:an?|[0-9]+)\s+)?$", re.I,
)
EQUIPMENT_RIGHT = re.compile(r"^\s+(?:(?:is|are)\s+)?(?:installed|in use|in operation|available on[- ]site)\b", re.I)
EQUIPMENT_NEGATIVE = re.compile(r"\b(?:no|lacks?|does not have|does not operate|without)\s+(?:an?\s+)?$", re.I)
SERVICE_LEFT = re.compile(
    r"\b(?:offers?|provides?|performs?|conducts?|operates?|runs?)\s*[:–—-]?\s+"
    r"(?:(?:our|an?|the|onsite|on[- ]site|inpatient|outpatient|advanced|comprehensive|24/?7|24[- ]hour)\s+){0,5}$"
    r"|\b(?:services offered|services provided)\s*[:–—-]\s*$|\bservices include\s+$", re.I,
)
SERVICE_RIGHT = re.compile(
    r"^\s*(?:(?:services?|scans?|procedures?)\s+)?(?:is|are)\s+(?:currently\s+)?(?:offered|provided|performed|available)\b",
    re.I,
)
OWNED_UNIT = re.compile(r"\b(?:nicu|unit|ward|cent(?:re|er))\b", re.I)
OWNED_UNIT_LEFT = re.compile(r"^our\s+$", re.I)
OWNED_UNIT_RIGHT = re.compile(
    r"^(?:\s*\((?-i:[A-Z][A-Z0-9-]{1,7})\))?\s+is dedicated to providing\s+speciali[sz]ed care for\b", re.I,
)
OWNED_UNIT_PLANNING = re.compile(r"\b(?:future|planning|intends? to|aims? to|hopes? to)\b", re.I)
SERVICE_NEGATIVE_LEFT = re.compile(
    r"\b(?:no|no longer|without|does not|do not|doesn't|don't|cannot|can't|never)\s+"
    r"(?:(?:currently\s+)?(?:offer|provide|perform|conduct|operate|run)\s+)?(?:an?\s+)?$", re.I,
)
SERVICE_NEGATIVE_RIGHT = re.compile(
    r"^\s+(?:(?:services?|scans?|procedures?)\s+)?(?:(?:is|are)\s+)?(?:currently\s+)?"
    r"(?:unavailable|discontinued|not\s+(?:currently\s+)?(?:offered|provided|performed|available))\b", re.I,
)
NON_SERVICE_OBJECT = re.compile(
    r"^\s+(?:patients?|equipment|machines?|scanners?|devices?|apparatus|records?|fee|fees|"
    r"bookings?|appointments?|consultants?|schedules?|training|referrals?|statistic\w*)\b", re.I,
)
NEGATED_VERB = re.compile(r"\b(?:not|never|without|no longer)\s+(?:currently\s+)?(?:has|have|operates?|installed|"
                         r"offers?|provides?|performs?|conducts?|runs?)\b", re.I)
SCOPE_EXCEPTION = re.compile(
    r"^\s+(?:(?:services?|scans?|procedures?)\s+)?(?:(?:is|are)\s+)?(?:currently\s+)?"
    r"(?:(?:offered|provided|performed|available|installed)\s+)?(?:only\s+)?(?:at|from|via|through)\s+"
    r"(?!this\b|the hospital\b|the facility\b|the site\b)(?:[\w-]+\s+){0,4}"
    r"(?:branch|hospital|facility|site|vendor|partner|affiliate|provider|contractor|third[- ]party)\b", re.I,
)


def _spans(text):
    cursor = 0
    for boundary in SENTENCE_BREAK.finditer(text):
        yield from _trimmed_span(text, cursor, boundary.start())
        cursor = boundary.end()
    yield from _trimmed_span(text, cursor, len(text))


def _trimmed_span(text, start, end):
    fragment = text[start:end]
    if fragment.strip():
        yield start + len(fragment) - len(fragment.lstrip()), start + len(fragment.rstrip())


def _date(sentence):
    dates = {_parse_date(token) for token in DATED.findall(sentence)}
    if not dates:
        return ''
    return next(iter(dates)).isoformat() if len(dates) == 1 and None not in dates else None


def _scope_at(left, right):
    last_clause = re.split(r"[,;:]|\b(?:and|but)\b", left, flags=re.I)[-1]
    return not UNIT_BEFORE.search(last_clause[-65:]) and not UNIT_AFTER.match(right)


def _numeric_claim(field, hit, sentence, claim_date):
    left, right = sentence[:hit.start()], sentence[hit.end():]
    capacity_noun = BEDDED_CAPACITY.match(right) if field == 'capacity' and hit['first'] else None
    scoped_right = re.sub(r"^\s*,", ' ', right[capacity_noun.end():]) if capacity_noun else right
    if (UNCERTAIN_LEFT.search(left) or UNCERTAIN_RIGHT.match(scoped_right) or not _scope_at(left, scoped_right)
            or SCOPE_EXCEPTION.match(scoped_right) or NEGATED_VERB.search(left)):
        return None
    subject = SUBJECT.search(left)
    explicit_subject = subject is not None and (claim_date or not PAST_SUBJECT.search(subject[0]))
    described_capacity = capacity_noun and FACILITY_FEATURES.search(left) and CAPACITY_TAIL.match(scoped_right)
    anchored = explicit_subject or FACILITY_LABEL.search(left) or described_capacity
    label = hit['label'] or ''
    if not (anchored or label.lower().startswith('total ') or field == 'capacity' and DESCRIPTOR.match(right)):
        return None
    # A past copula can otherwise be accepted through the text label or descriptor.
    if not claim_date and PAST_SUBJECT.search(left[-25:] + label + hit[0]):
        return None
    raw_value = hit['first'] or hit['last']
    number = int(raw_value.replace(',', ''))
    basis_pattern = BED_BASIS if field == 'capacity' else STAFF_BASIS
    basis = hit['basis'] or next(iter(re.findall(rf"\b{basis_pattern}\b", label, re.I)), '')
    basis = basis.lower().replace('-', ' ')
    if field == 'capacity':
        return number, 'beds', BED_UNITS.get(basis, 'whole_facility_beds'), raw_value
    if re.match(r"^\s+and\s+(?:the\s+)?(?:nurses|staff|employees|paramedics)\b", right, re.I):
        return None
    if not basis:
        if re.match(r"^\s+(?:on[- ]staff|on the payroll)\b", right, re.I):
            basis = 'on staff'
        elif subject and re.search(r"\bemploy(?:s|ed)\b", subject[0], re.I):
            basis = 'employed'
    explicit_total = 'total' in label.lower() or subject and 'total' in subject[0].lower()
    implicit_scope = 'whole_facility_doctors' if explicit_total else 'doctors_unspecified'
    return number, 'doctors', DOCTOR_UNITS.get(basis, implicit_scope), raw_value


@lru_cache(maxsize=16)
def _service_patterns(alias_items):
    patterns = {}
    for service, aliases in alias_items:
        expressions = {
            r"[\s\-–—/]+".join(re.escape(part) for part in re.split(r"[\s_\-–—/]+", alias.strip()) if part)
            for alias in (service, *aliases)
        }
        expressions.discard('')
        if expressions:
            alternatives = '|'.join(sorted(expressions, key=lambda value: (-len(value), value)))
            patterns[service] = re.compile(rf"\b(?:{alternatives})\b", re.I)
    return patterns


def _specific_matches(patterns, sentence):
    """Prefer a fully named subtype over a general overlapping mention."""
    hits = [(key, match) for key, pattern in patterns.items() for match in pattern.finditer(sentence)]
    for key, match in hits:
        if not any(other.start() <= match.start() and other.end() >= match.end()
                   and other.end() - other.start() > match.end() - match.start() for _, other in hits):
            yield key, match


def _claim(field, value, unit, scope, stance, start, end, rule, claim_date, **detail):
    return dict(field=field, value=value, unit=unit, scope=scope, stance=stance, start=start, end=end,
                rule=rule, claim_date=claim_date, **detail)


def extract_candidates(text, aliases):
    """Propose narrow English-language claims. An exact span still requires attribution and review."""
    patterns = _service_patterns(tuple((key, tuple(values)) for key, values in sorted(aliases.items())))
    found = []
    for start, end in _spans(text):
        sentence = text[start:end]
        if PAST_OR_PLANNED.search(sentence) or OUTSIDE_BRANCH.search(sentence):
            continue
        claim_date = _date(sentence)
        if claim_date is None:
            continue
        for field, regex in COUNTS.items():
            for hit in regex.finditer(sentence):
                parsed = _numeric_claim(field, hit, sentence, claim_date)
                if parsed:
                    value, unit, scope, raw_value = parsed
                    found.append(_claim(field, value, unit, scope, 'supports', start, end,
                                        'exact_scoped_count', claim_date, raw_value=raw_value))
        for key, match in _specific_matches(EQUIPMENT_PATTERNS, sentence):
            left, right = sentence[:match.start()], sentence[match.end():]
            if SCOPE_EXCEPTION.match(right):
                continue
            subtype = EQUIPMENT_SUBTYPE.search(left)
            cue = left[:subtype.start()] if subtype else left
            negative = EQUIPMENT_NEGATIVE.search(cue)
            if negative or (not NEGATED_VERB.search(left) and (EQUIPMENT_LEFT.search(cue) or EQUIPMENT_RIGHT.match(right))):
                evidence = {'subtype': subtype['subtype']} if subtype else {}
                found.append(_claim('equipment', key, 'equipment', 'installed_at_branch', 'contradicts' if negative else 'supports',
                                    start, end, 'explicit_equipment', claim_date, raw_value=match[0], **evidence))
        if WEAK_CONTEXT.search(sentence.lower()):
            continue
        for key, match in _specific_matches(patterns, sentence):
            left, right = sentence[:match.start()], sentence[match.end():]
            if (NON_SERVICE_OBJECT.match(right) or EQUIPMENT_CONTEXT.match(right.lstrip())
                    or SCOPE_EXCEPTION.match(right)):
                continue
            explicit_unit = (OWNED_UNIT.search(match[0]) and OWNED_UNIT_LEFT.match(left) and OWNED_UNIT_RIGHT.match(right)
                             and not OWNED_UNIT_PLANNING.search(sentence))
            left = re.split(r"\b(?:but|however|whereas)\b", left, flags=re.I)[-1]
            negative = SERVICE_NEGATIVE_LEFT.search(left) or SERVICE_NEGATIVE_RIGHT.match(right)
            present = SERVICE_LEFT.search(left) or SERVICE_RIGHT.match(right) or explicit_unit
            if negative or (not NEGATED_VERB.search(left) and present):
                rule = 'explicit_owned_unit_service' if explicit_unit and not negative else 'explicit_named_service'
                found.append(_claim('service', key, 'service', 'branch_service', 'contradicts' if negative else 'supports',
                                    start, end, rule, claim_date, raw_value=match[0]))
    # Repeated words inside the same supporting span convey the same atomic claim.
    return list({(entry['field'], entry['value'], entry['scope'], entry['stance'], entry['start'], entry['end'],
                  entry.get('subtype')): entry
                 for entry in found}.values())
