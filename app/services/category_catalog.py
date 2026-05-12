from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CategoryCatalogEntry:
    key: str
    fr: str
    en: str
    ar: str


INTERNAL_INCOME_CATEGORY_KEY = "income_general"


EXPENSE_CATEGORY_CATALOG: tuple[CategoryCatalogEntry, ...] = (
    CategoryCatalogEntry("rent", "Loyer", "Rent", "الكراء"),
    CategoryCatalogEntry("housing_generic", "Charges logement", "Housing costs", "مصاريف السكن"),
    CategoryCatalogEntry("home_maintenance", "Entretien maison", "Home maintenance", "صيانة الدار"),
    CategoryCatalogEntry("electricity", "Électricité", "Electricity", "الكهرباء"),
    CategoryCatalogEntry("water", "Eau", "Water", "الماء"),
    CategoryCatalogEntry("internet", "Internet", "Internet", "الإنترنت"),
    CategoryCatalogEntry("phone", "Téléphone", "Phone", "التلفون"),
    CategoryCatalogEntry("gas", "Gaz", "Gas", "الغاز"),
    CategoryCatalogEntry("home_insurance", "Assurance habitation", "Home insurance", "تأمين الدار"),
    CategoryCatalogEntry("admin_fees", "Frais administratifs", "Admin fees", "مصاريف إدارية"),
    CategoryCatalogEntry("bills_generic", "Factures", "Bills", "الفواتير"),
    CategoryCatalogEntry("groceries", "Courses", "Groceries", "المأكولات"),
    CategoryCatalogEntry("house_supplies", "Produits maison", "House supplies", "لوازم الدار"),
    CategoryCatalogEntry("restaurants", "Restaurants", "Restaurants", "المطاعم"),
    CategoryCatalogEntry("health_pharmacy", "Pharmacie", "Pharmacy", "الصيدلية"),
    CategoryCatalogEntry("health_consultation", "Consultation médicale", "Medical consultation", "استشارة طبية"),
    CategoryCatalogEntry("health_generic", "Santé", "Health", "الصحة"),
    CategoryCatalogEntry("personal_care", "Soins personnels", "Personal care", "العناية الشخصية"),
    CategoryCatalogEntry("transport_public", "Transport public", "Public transport", "النقل العمومي"),
    CategoryCatalogEntry("transport_taxi", "Taxi / VTC", "Taxi / Ride-hailing", "الطاكسي"),
    CategoryCatalogEntry("transport_fuel", "Carburant", "Fuel", "الوقود"),
    CategoryCatalogEntry("transport_generic", "Transport", "Transport", "النقل"),
    CategoryCatalogEntry("transport_parking", "Parking", "Parking", "الباركينغ"),
    CategoryCatalogEntry("transport_maintenance", "Entretien véhicule", "Vehicle maintenance", "صيانة الطوموبيل"),
    CategoryCatalogEntry("car_insurance", "Assurance auto", "Car insurance", "تأمين الطوموبيل"),
    CategoryCatalogEntry("family_support", "Aide famille", "Family support", "مصروف العائلة"),
    CategoryCatalogEntry("children_school", "Frais scolaires enfants", "Kids school fees", "قراية الدراري"),
    CategoryCatalogEntry("children_activities", "Activités enfants", "Kids activities", "أنشطة الدراري"),
    CategoryCatalogEntry("childcare", "Garde d'enfants", "Childcare", "حضانة الدراري"),
    CategoryCatalogEntry("debt_payment", "Paiement dette", "Debt payment", "خلّاص الدين"),
    CategoryCatalogEntry(
        "debt_extra_payment",
        "Paiement dette (supplément)",
        "Extra debt payment",
        "زيادة فخلصان الدين",
    ),
    CategoryCatalogEntry("taxes", "Taxes", "Taxes", "الضرايب"),
    CategoryCatalogEntry("insurance_other", "Autres assurances", "Other insurance", "تأمينات أخرى"),
    CategoryCatalogEntry("shopping", "Shopping", "Shopping", "الشوبينغ"),
    CategoryCatalogEntry("entertainment", "Loisirs", "Entertainment", "الترفيه"),
    CategoryCatalogEntry("miscellaneous", "Divers", "Miscellaneous", "مصاريف متنوعة"),
    CategoryCatalogEntry("subscriptions", "Abonnements", "Subscriptions", "لا بونومون"),
    CategoryCatalogEntry("savings_contribution", "Épargne", "Savings", "الادخار"),
    CategoryCatalogEntry("investment_contribution", "Investissement", "Investment", "الاستثمار"),
    CategoryCatalogEntry("gifts_charity", "Cadeaux & dons", "Gifts & charity", "الهدايا والتبرعات"),
    CategoryCatalogEntry("travel", "Voyage", "Travel", "السفر"),
    CategoryCatalogEntry("business_tools", "Outils de travail", "Work tools", "أدوات الخدمة"),
    CategoryCatalogEntry("business_travel", "Déplacements pro", "Business travel", "تنقلات الخدمة"),
    CategoryCatalogEntry("freelance_expenses", "Frais freelance", "Freelance expenses", "مصاريف الفريلانس"),
)


INTERNAL_INCOME_CATEGORY_KEYS = {
    INTERNAL_INCOME_CATEGORY_KEY,
    "income_salary",
    "income_freelance",
    "income_bonus",
    "income_commission",
    "income_refund",
    "income_other",
}
INTERNAL_INCOME_CATEGORY_KEYS_SQL = tuple(sorted(INTERNAL_INCOME_CATEGORY_KEYS))


def _norm(value: str) -> str:
    return " ".join(value.strip().lower().split())


_CATALOG_BY_KEY = {entry.key: entry for entry in EXPENSE_CATEGORY_CATALOG}
EXPENSE_CATEGORY_KEYS_SQL = tuple(sorted(_CATALOG_BY_KEY.keys()))

_CATEGORY_ALIAS_TO_KEY = {
    "loyer": "rent",
    "rent": "rent",
    "kera": "rent",
    "الكرا": "rent",
    "الكراء": "rent",
    "charges": "housing_generic",
    "charges logement": "housing_generic",
    "housing": "housing_generic",
    "housing costs": "housing_generic",
    "logement": "housing_generic",
    "entretien maison": "home_maintenance",
    "electricite": "electricity",
    "électricité": "electricity",
    "electricity": "electricity",
    "eau": "water",
    "water": "water",
    "internet": "internet",
    "telephone": "phone",
    "téléphone": "phone",
    "phone": "phone",
    "gaz": "gas",
    "gas": "gas",
    "assurance habitation": "home_insurance",
    "frais administratifs": "admin_fees",
    "factures": "bills_generic",
    "facture": "bills_generic",
    "bills": "bills_generic",
    "utilities": "bills_generic",
    "courses": "groceries",
    "food": "groceries",
    "nourriture": "groceries",
    "groceries": "groceries",
    "produits maison": "house_supplies",
    "restaurants": "restaurants",
    "pharmacie": "health_pharmacy",
    "consultation medicale": "health_consultation",
    "consultation médicale": "health_consultation",
    "doctor": "health_consultation",
    "medecin": "health_consultation",
    "médecin": "health_consultation",
    "sante": "health_generic",
    "santé": "health_generic",
    "health": "health_generic",
    "soins personnels": "personal_care",
    "transport public": "transport_public",
    "transport": "transport_public",
    "taxi": "transport_taxi",
    "taxi / vtc": "transport_taxi",
    "carburant": "transport_fuel",
    "fuel": "transport_fuel",
    "essence": "transport_fuel",
    "transport général": "transport_generic",
    "transport general": "transport_generic",
    "parking": "transport_parking",
    "entretien auto": "transport_maintenance",
    "entretien vehicule": "transport_maintenance",
    "entretien véhicule": "transport_maintenance",
    "assurance auto": "car_insurance",
    "credit auto": "debt_payment",
    "crédit auto": "debt_payment",
    "controle technique": "transport_maintenance",
    "contrôle technique": "transport_maintenance",
    "taxe auto": "taxes",
    "carburant 2 roues": "transport_fuel",
    "assurance 2 roues": "car_insurance",
    "entretien 2 roues": "transport_maintenance",
    "aide famille": "family_support",
    "famille — aide": "family_support",
    "children school": "children_school",
    "frais scolaires enfants": "children_school",
    "activites enfants": "children_activities",
    "activités enfants": "children_activities",
    "garde": "childcare",
    "garde d'enfants": "childcare",
    "credit": "debt_payment",
    "crédit": "debt_payment",
    "dettes": "debt_payment",
    "dettes — credit": "debt_payment",
    "dettes - credit": "debt_payment",
    "remboursement": "debt_extra_payment",
    "paiement dette": "debt_payment",
    "taxes": "taxes",
    "impots": "taxes",
    "impôts": "taxes",
    "autres assurances": "insurance_other",
    "shopping": "shopping",
    "loisirs": "entertainment",
    "divers": "miscellaneous",
    "misc": "miscellaneous",
    "miscellaneous": "miscellaneous",
    "abonnements": "subscriptions",
    "epargne": "savings_contribution",
    "épargne": "savings_contribution",
    "savings": "savings_contribution",
    "investissement": "investment_contribution",
    "investment": "investment_contribution",
    "cadeaux": "gifts_charity",
    "voyage": "travel",
    "outils de travail": "business_tools",
    "deplacements pro": "business_travel",
    "déplacements pro": "business_travel",
    "frais freelance": "freelance_expenses",
    "income": INTERNAL_INCOME_CATEGORY_KEY,
    "salary": INTERNAL_INCOME_CATEGORY_KEY,
    "salaire": INTERNAL_INCOME_CATEGORY_KEY,
    "revenu": INTERNAL_INCOME_CATEGORY_KEY,
    "income_general": INTERNAL_INCOME_CATEGORY_KEY,
    "remboursements": INTERNAL_INCOME_CATEGORY_KEY,
}

for _entry in EXPENSE_CATEGORY_CATALOG:
    _CATEGORY_ALIAS_TO_KEY[_norm(_entry.key)] = _entry.key
    _CATEGORY_ALIAS_TO_KEY[_norm(_entry.fr)] = _entry.key
    _CATEGORY_ALIAS_TO_KEY[_norm(_entry.en)] = _entry.key
    _CATEGORY_ALIAS_TO_KEY[_norm(_entry.ar)] = _entry.key


def category_key_from_name(value: str) -> str:
    raw = value.strip()
    if not raw:
        return raw
    normalized = _norm(raw)
    if normalized in _CATALOG_BY_KEY:
        return normalized
    if normalized in INTERNAL_INCOME_CATEGORY_KEYS:
        return INTERNAL_INCOME_CATEGORY_KEY
    alias = _CATEGORY_ALIAS_TO_KEY.get(normalized)
    if alias:
        return alias
    return raw


def is_internal_income_category_key(value: str | None) -> bool:
    if not value:
        return False
    normalized = _norm(value)
    if normalized in INTERNAL_INCOME_CATEGORY_KEYS:
        return True
    mapped = _CATEGORY_ALIAS_TO_KEY.get(normalized)
    return mapped == INTERNAL_INCOME_CATEGORY_KEY
