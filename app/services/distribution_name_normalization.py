from __future__ import annotations

import re
import unicodedata


_DISTRIBUTION_EQUIVALENT_BY_KEY: dict[str, str] = {
    "nourriture": "food",
    "courses": "food",
    "food": "food",
    "الماكلة": "food",
    "sante": "health",
    "santé": "health",
    "pharmacie": "health",
    "health": "health",
    "الصحة": "health",
    "factures": "bills",
    "لفواتير": "bills",
    "الفواتير": "bills",
    "لفواتير الثابتة": "bills",
    "الفواتير الثابتة": "bills",
    "charges": "housing_charges",
    "housing charges": "housing_charges",
    "مصاريف السكن": "housing_charges",
    "famille - aide": "family_aid",
    "famille — aide": "family_aid",
    "famille – aide": "family_aid",
    "famille aide": "family_aid",
    "aide famille": "family_aid",
    "aide_famille": "family_aid",
    "famille_aide": "family_aid",
    "مساعدة العائلة": "family_aid",
    "مساعدة العايلة": "family_aid",
    "عاونة العائلة": "family_aid",
    "عاونة العايلة": "family_aid",
    "loyer": "rent",
    "الكراء": "rent",
    "transport public": "public_transport",
    "النقل العمومي": "public_transport",
    "taxi vtc": "taxi_private",
    "taxi indrive": "taxi_private",
    "تاكسي نقل خاص": "taxi_private",
    "طاكسي اندرايف": "taxi_private",
    "imprevus طوارئ": "emergency_buffer",
    "imprevus": "emergency_buffer",
    "imprevu": "emergency_buffer",
    "urgences": "emergency_buffer",
    "urgence": "emergency_buffer",
    "emergency": "emergency_buffer",
    "emergencies": "emergency_buffer",
    "الطوارئ": "emergency_buffer",
    "طوارئ": "emergency_buffer",
    "equilibre": "balance_buffer",
    "balance": "balance_buffer",
    "التوازن": "balance_buffer",
    "loisirs": "entertainment",
    "entertainment": "entertainment",
    "الترفيه": "entertainment",
    "restaurants": "restaurants",
    "restaurant": "restaurants",
    "المطاعم": "restaurants",
    "مطاعم": "restaurants",
    "shopping": "shopping",
    "shoping": "shopping",
    "الشوبينغ": "shopping",
    "التسوق": "shopping",
    "التسوقات": "shopping",
    "المرونة": "flexibility",
    "flexibilite": "flexibility",
    "flexibility": "flexibility",
    "flex": "flexibility",
}


def _strip_accents(value: str) -> str:
    return "".join(
        ch
        for ch in unicodedata.normalize("NFD", value)
        if unicodedata.category(ch) != "Mn"
    )


def normalize_distribution_name_key(value: str) -> str:
    compact = re.sub(r"[\u2010-\u2015/_-]+", " ", value)
    compact = re.sub(r"\s+", " ", compact).strip()
    if not compact:
        return ""
    return _strip_accents(compact).casefold()


def distribution_name_equivalent_key(value: str) -> str:
    normalized = normalize_distribution_name_key(value)
    if not normalized:
        return ""
    return _DISTRIBUTION_EQUIVALENT_BY_KEY.get(normalized, normalized)
