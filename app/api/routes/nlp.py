from __future__ import annotations

import re
import datetime
from uuid import UUID
from typing import List, Optional
from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models import User, MerchantCategoryMapping, UserCategoryPreference, Category
from app.services.ai_gateway_client import chat_completion_via_gateway
from app.services.category_catalog import category_key_from_name

router = APIRouter(prefix="/nlp")

_GLOBAL_MAPPINGS_CACHE = None


class NLPPredictRequest(BaseModel):
    text: str


class NLPPredictResponse(BaseModel):
    amount: Optional[float] = None
    date: Optional[str] = None
    description: str
    category: Optional[str] = None
    needs_disambiguation: bool = False
    suggested_categories: List[str] = []


class NLPFeedbackRequest(BaseModel):
    keyword: str
    category_name: str


class NLPFeedbackResponse(BaseModel):
    status: str
    message: str


def parse_date_from_text(text: str) -> tuple[Optional[datetime.date], list[str]]:
    """
    Parses date keywords or patterns and returns the date and matched patterns/words to clean.
    Supports French, English, and Darija.
    """
    text_lower = text.lower()
    today = datetime.date.today()

    # 1. Look for explicit date formats first: DD/MM/YYYY, YYYY-MM-DD, etc.
    # regex for DD/MM/YYYY or DD-MM-YYYY
    date_pattern_1 = re.search(r'\b(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})\b', text)
    if date_pattern_1:
        day, month, year = map(int, date_pattern_1.groups())
        try:
            return datetime.date(year, month, day), [date_pattern_1.group(0)]
        except ValueError:
            pass

    # regex for YYYY-MM-DD
    date_pattern_2 = re.search(r'\b(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})\b', text)
    if date_pattern_2:
        year, month, day = map(int, date_pattern_2.groups())
        try:
            return datetime.date(year, month, day), [date_pattern_2.group(0)]
        except ValueError:
            pass

    # regex for DD/MM or DD-MM (assumes current year)
    date_pattern_3 = re.search(r'\b(\d{1,2})[/\-](\d{1,2})\b', text)
    if date_pattern_3:
        day, month = map(int, date_pattern_3.groups())
        try:
            return datetime.date(today.year, month, day), [date_pattern_3.group(0)]
        except ValueError:
            pass

    # 2. Key word matches
    # Today
    today_words = ["today", "aujourd'hui", "aujourdhui", "lyoum", "lyouma"]
    for w in today_words:
        pattern = r'\b' + re.escape(w) + r'\b'
        match = re.search(pattern, text_lower)
        if match:
            return today, [text[match.start():match.end()]]

    # Yesterday
    yesterday_words = ["yesterday", "hier", "barah", "lbarah", "lbareh", "bareh"]
    for w in yesterday_words:
        pattern = r'\b' + re.escape(w) + r'\b'
        match = re.search(pattern, text_lower)
        if match:
            return today - datetime.timedelta(days=1), [text[match.start():match.end()]]

    # Day before yesterday
    before_yesterday_words = ["day before yesterday", "avant-hier", "avanthier", "olbarah", "walbarah"]
    for w in before_yesterday_words:
        pattern = r'\b' + re.escape(w) + r'\b'
        match = re.search(pattern, text_lower)
        if match:
            return today - datetime.timedelta(days=2), [text[match.start():match.end()]]

    return None, []


def parse_amount_from_text(text: str) -> tuple[Optional[float], Optional[str]]:
    """
    Parses amount and returns (amount, exact_amount_substring_to_remove).
    First checks for numbers explicitly followed by a currency like dh, mad, $, dirhams.
    Then falls back to the first numerical value that is not part of a date.
    """
    # 1. Match numeric values explicitly associated with currency suffixes/prefixes
    currency_suffix_pattern = r'\b(\d+(?:[.,]\d+)?)\s*(?:dh|mad|dirham|dirhams|dhs|usd|\$|€|eur)\b'
    currency_prefix_pattern = r'\b(?:dh|mad|dirham|dirhams|dhs|usd|\$|€|eur)\s*(\d+(?:[.,]\d+)?)\b'

    match_suffix = re.search(currency_suffix_pattern, text, re.IGNORECASE)
    if match_suffix:
        val_str = match_suffix.group(1).replace(',', '.')
        try:
            return float(val_str), match_suffix.group(0)
        except ValueError:
            pass

    match_prefix = re.search(currency_prefix_pattern, text, re.IGNORECASE)
    if match_prefix:
        val_str = match_prefix.group(1).replace(',', '.')
        try:
            return float(val_str), match_prefix.group(0)
        except ValueError:
            pass

    # 2. Fallback to any number that doesn't look like a year
    number_pattern = r'\b\d+(?:[.,]\d+)?\b'
    for match in re.finditer(number_pattern, text):
        val_str = match.group(0).replace(',', '.')
        try:
            val = float(val_str)
            if val in [2024, 2025, 2026, 2027]:
                continue
            return val, match.group(0)
        except ValueError:
            pass

    return None, None


def clean_description(text: str, remove_segments: list[str]) -> str:
    """
    Cleans the description text by removing amounts, dates, and edge prepositions.
    """
    desc = text
    for seg in remove_segments:
        if seg:
            desc = desc.replace(seg, " ")

    # Normalize whitespace
    desc = re.sub(r'\s+', ' ', desc).strip()

    # Clean prepositions and connecting words at the edges (both prefix and suffix)
    edge_prepositions = [
        "pour", "de", "le", "la", "les", "du", "d'", "a", "à", "en", "in", "for", "at", "dial", "dyal", "ta3", "d"
    ]

    edge_pattern_start = re.compile(
        r'^(?:' + '|'.join(re.escape(w) for w in edge_prepositions) + r')$',
        re.IGNORECASE,
    )
    edge_pattern_end = re.compile(
        r'\s*\b(?:' + '|'.join(re.escape(w) for w in edge_prepositions) + r')$',
        re.IGNORECASE,
    )

    old_desc = ""
    while old_desc != desc:
        old_desc = desc
        desc = edge_pattern_start.sub('', desc)
        desc = edge_pattern_end.sub('', desc)
        desc = re.sub(r'^[^a-zA-Z0-9\u0600-\u06FF]+', '', desc)
        desc = re.sub(r'[^a-zA-Z0-9\u0600-\u06FF]+$', '', desc)
        desc = desc.strip()

    return desc


def normalize_text_for_matching(text: str) -> str:
    if not text:
        return ""
    text = text.lower()
    
    # Normalize accents
    import unicodedata
    text = "".join(c for c in unicodedata.normalize('NFD', text) if unicodedata.category(c) != 'Mn')
    
    # Replace common Moroccan Arabic (Darija) chat/spelling variations:
    # 7 -> h, 9 -> q, 3 -> a, 8 -> h
    text = text.replace('7', 'h').replace('9', 'q').replace('3', 'a').replace('8', 'h')
    
    # Remove hyphens, apostrophes, and other internal separators to normalize compound words
    text = re.sub(r"['\-]", "", text)
    
    # Collapse repeating characters (e.g. marjaane -> marjane, taxi -> taxi)
    text = re.sub(r'(.)\1+', r'\1', text)
    
    return text.strip()


def levenshtein_distance(s1: str, s2: str) -> int:
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    
    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
        
    return previous_row[-1]


@router.post("/predict", response_model=NLPPredictResponse)
async def predict_nlp(
    request: NLPPredictRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> NLPPredictResponse:
    text = request.text
    if not text.strip():
        return NLPPredictResponse(description="")

    # Parse amount and date
    amount, amount_seg = parse_amount_from_text(text)
    date_val, date_segs = parse_date_from_text(text)

    # Clean description
    remove_segs = []
    if amount_seg:
        remove_segs.append(amount_seg)
    remove_segs.extend(date_segs)

    description = clean_description(text, remove_segs)

    # Category matching
    # 1. Fetch user preferences (targeted by index, very fast)
    pref_stmt = select(UserCategoryPreference).where(
        UserCategoryPreference.user_id == current_user.id
    )
    pref_result = await db.execute(pref_stmt)
    user_prefs = pref_result.scalars().all()

    # 2. Fetch global merchant mappings (cached in memory to avoid redundant DB reads)
    global _GLOBAL_MAPPINGS_CACHE
    if _GLOBAL_MAPPINGS_CACHE is None:
        global_stmt = select(MerchantCategoryMapping)
        global_result = await db.execute(global_stmt)
        _GLOBAL_MAPPINGS_CACHE = global_result.scalars().all()
    global_mappings = _GLOBAL_MAPPINGS_CACHE

    # Search keywords in cleaned description
    # Compute normalized text and tokens for robust prefix & fuzzy matching
    desc_normalized = normalize_text_for_matching(description)
    desc_tokens = [normalize_text_for_matching(t) for t in description.split() if t]

    def is_keyword_matched(keyword_raw: str) -> bool:
        kw_norm = normalize_text_for_matching(keyword_raw)
        if not kw_norm:
            return False
            
        # A. Regex Match (with optional prefixes for Darija/French: l-, d-, b-, f-, el-, al-, la-, le-)
        kw_pattern = r'\b(?:l|d|b|f|el|al|la|le)?' + re.escape(kw_norm) + r'\b'
        if re.search(kw_pattern, desc_normalized):
            return True
            
        # B. Fuzzy matching on token level for spelling corrections / typos
        for t in desc_tokens:
            max_dist = max(1, len(kw_norm) // 4)
            if levenshtein_distance(t, kw_norm) <= max_dist:
                return True
                
        return False

    matched_kws = {}  # keyword -> category_name

    # Apply user-specific preferences (takes precedence)
    for pref in user_prefs:
        if is_keyword_matched(pref.keyword):
            matched_kws[pref.keyword.lower()] = pref.category_name

    # Apply global mappings for any unmatched keywords
    for glob in global_mappings:
        kw_lower = glob.keyword.lower()
        if kw_lower not in matched_kws:
            if is_keyword_matched(glob.keyword):
                matched_kws[kw_lower] = glob.category_name

    # Fallback to general category synonyms if no database matches found
    if not matched_kws:
        FALLBACK_SYNONYMS = {
            "Loisirs": [
                "activite", "activites", "activity", "activities", "loisir", "loisirs", "entertainment", 
                "divertissement", "divertissements", "outing", "outings", "khorja", "khrija", "sortie", 
                "sorties", "cinema", "film", "match", "kora", "sport", "gym", "tserkila", "tsserkila", "tasserkila"
            ],
            "Alimentation": [
                "alimentation", "nourriture", "epicerie", "hanout", "lhanout", "7anout", "l7anout", "makla", 
                "maakla", "marjane", "bim", "carrefour", "supeco", "atacadao", "sucre", "the", "cafe", "restaurant", 
                "restau", "diner", "dejeuner", "ftour", "petitdejeuner", "boulangerie", "patisserie", "courses"
            ],
            "Transport": [
                "transport", "transports", "taxi", "taxis", "طاكسي", "tram", "tramway", "carburant", 
                "essence", "gasoil", "mazot", "autoroute", "peage", "bus", "tobis", "ltaxi", "btaxi"
            ],
            "Abonnements": [
                "abonnement", "abonnements", "subscription", "subscriptions", "netflix", "spotify", "internet", 
                "wifi", "iam", "inwi", "orange", "adsl", "fibro", "fibre", "facture", "factures", "lydec", "radeema", 
                "amendis", "radeef", "eau", "electricite", "lmdw", "kahraba"
            ],
            "Voyage": [
                "voyage", "voyages", "travel", "trip", "trips", "vacances", "hotel", "vol", "avion", "train", 
                "oncf", "billet", "voyager", "safar", "sfar"
            ],
            "Shopping": [
                "shopping", "vetement", "vetements", "fringues", "habits", "chaussures", "achat", "achats", 
                "magasin", "store", "mall"
            ],
            "Santé": [
                "sante", "health", "pharmacie", "pharma", "farmacie", "farmacy", "medecin", "doctor", "tbib", 
                "dwa", "clinique", "hopital", "dentiste"
            ],
            "Loyer": [
                "loyer", "loyers", "rent", "rents", "krae", "kray", "kraya", "appartement", "dar"
            ],
            "Cadeaux & dons": [
                "cadeau", "cadeaux", "don", "dons", "charite", "sadaka", "sadaqa", "gift", "gifts", "present"
            ]
        }
        for category_name, synonyms in FALLBACK_SYNONYMS.items():
            for syn in synonyms:
                if is_keyword_matched(syn):
                    matched_kws[syn.lower()] = category_name
                    break

    unique_categories = sorted(list(set(matched_kws.values())))

    category = None
    needs_disambiguation = False
    suggested_categories = []

    if len(unique_categories) == 1:
        category = unique_categories[0]
    elif len(unique_categories) > 1:
        category = unique_categories[0]  # default to first
        needs_disambiguation = True
        suggested_categories = unique_categories

    date_str = date_val.strftime("%Y-%m-%d") if date_val else None

    return NLPPredictResponse(
        amount=amount,
        date=date_str,
        description=description or text,
        category=category,
        needs_disambiguation=needs_disambiguation,
        suggested_categories=suggested_categories,
    )




@router.post("/feedback", response_model=NLPFeedbackResponse)
async def feedback_nlp(
    request: NLPFeedbackRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> NLPFeedbackResponse:
    keyword = request.keyword.strip()
    category_name = request.category_name.strip()

    if not keyword or not category_name:
        return NLPFeedbackResponse(
            status="error",
            message="Le mot-clé et la catégorie ne peuvent pas être vides.",
        )

    # Upsert logic: search if exists, otherwise create
    stmt = select(UserCategoryPreference).where(
        UserCategoryPreference.user_id == current_user.id,
        UserCategoryPreference.keyword == keyword,
    )
    result = await db.execute(stmt)
    pref = result.scalars().first()

    if pref:
        pref.category_name = category_name
    else:
        pref = UserCategoryPreference(
            user_id=current_user.id,
            keyword=keyword,
            category_name=category_name,
        )
        db.add(pref)

    await db.commit()

    return NLPFeedbackResponse(
        status="success",
        message=f"Préférence enregistrée : {keyword} -> {category_name}",
    )
