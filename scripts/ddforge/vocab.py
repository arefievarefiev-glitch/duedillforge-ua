"""Controlled vocabularies and the fixed Ukrainian wording of statuses.

Everything the report says about the outcome of a check comes from this file, not from a model:
the writer agent never phrases «не виявлено» itself (see lint in `machine.py`).
"""

from __future__ import annotations

# --- statuses of a checklist item --------------------------------------------------------

PENDING = "pending"
RISK = "risk"
CLEAR = "clear"
SCREENED = "screened"
UNVERIFIED = "unverified"
MISSING = "missing"
NA = "na"

STATUSES = (PENDING, RISK, CLEAR, SCREENED, UNVERIFIED, MISSING, NA)
CLAIMABLE = (RISK, CLEAR, SCREENED, UNVERIFIED, MISSING, NA)

STATUS_UK = {
    PENDING: "Не перевірено",
    RISK: "Виявлено",
    CLEAR: "Не виявлено",
    SCREENED: "Скринінг без ознак",
    UNVERIFIED: "Не підтверджено",
    MISSING: "Документ відсутній",
    NA: "Не застосовується",
}

STATUS_LEGEND_UK = {
    RISK: "Знайдено положення документа, що створює ризик; цитату звірено з текстом джерела.",
    CLEAR: "Ризику не виявлено, і програма підтвердила повноту перевірки: прочитано повний текст "
           "або суцільний ланцюг цитат, або повнотекстовий пошук без непереглянутих збігів.",
    SCREENED: "Ознак не виявлено при скринінгу; повний перегляд тексту не проводився. "
              "Це не підтвердження відсутності ризику.",
    UNVERIFIED: "Перевірку не завершено або її результат не підтверджено (причину наведено в кожному рядку).",
    MISSING: "Потрібного документа немає в блокноті чи документах проєкту; його включено до запиту додаткових документів.",
    NA: "Позиція не стосується об'єкта; причину наведено в кожному рядку.",
}

# --- reasons attached to `unverified` ------------------------------------------------------

REASONS_UK = {
    "scan_unreadable": "скан нечитабельний",
    "text_degraded": "текст розпізнано з помилками",
    "chain_gap": "розрив у ланцюгу цитат",
    "query_failed": "запит блокнота не дав результату",
    "no_full_text": "повний текст документа недоступний",
    "no_structure": "у документа не вдалося виділити структурні одиниці",
    "quote_not_found": "цитату не знайдено в тексті джерела",
    "clear_not_supported": "умови для статусу «Не виявлено» не виконано",
    "too_many_hits": "забагато збігів для повного перегляду",
    "not_reached": "позицію не було перевірено в межах завдання",
    "auth_expired": "протермінований вхід у NotebookLM",
    "agent_failed": "агент не повернув чинний результат",
}

# --- basis of a `clear` -------------------------------------------------------------------

BASIS_UK = {
    "scan": "повнотекстовий пошук за ключовими словами позиції; усі збіги переглянуто",
    "units": "прочитано структурні одиниці документа",
    "chain": "суцільний ланцюг дослівних цитат без розривів",
}

# --- severities and recommendations ---------------------------------------------------------

CRITICAL = "critical"
SIGNIFICANT = "significant"
INFO = "info"
SEVERITIES = (CRITICAL, SIGNIFICANT, INFO)
SEVERITY_ORDER = {CRITICAL: 0, SIGNIFICANT: 1, INFO: 2}
SEVERITY_UK = {CRITICAL: "Критичний", SIGNIFICANT: "Суттєвий", INFO: "До відома"}
SEVERITY_DEF_UK = {
    CRITICAL: "Наслідок ставить під сумнів право на предмет угоди, повноваження або закриття, чи створює "
              "невизначене велике зобов'язання. Рішення потрібне до підписання.",
    SIGNIFICANT: "Потребує договірного захисту або усунення до закриття (передумова закриття, запевнення та "
                 "гарантії, спеціальне відшкодування, коригування ціни). Угоду не блокує.",
    INFO: "Окремого захисту не потребує; дія після закриття або інформація для моніторингу.",
}

REC_TYPES = (
    "price", "specific_indemnity", "condition_precedent", "warranty",
    "pre_closing_undertaking", "post_closing_action", "further_review", "none",
)
REC_UK = {
    "price": "коригування ціни",
    "specific_indemnity": "спеціальне відшкодування",
    "condition_precedent": "передумова закриття",
    "warranty": "запевнення та гарантії",
    "pre_closing_undertaking": "зобов'язання до закриття",
    "post_closing_action": "дія після закриття",
    "further_review": "додаткова перевірка",
    "none": "окремого захисту не потребує",
}

CONFIRMATION_UK = {
    "verified": "цитату знайдено в тексті джерела",
    "partial": "цитату знайдено частково або з розбіжностями розпізнавання; звірити з оригіналом",
    "unverified": "цитату не звірено з повним текстом; звірити з оригіналом",
}

# --- document kinds (scout chooses a code; checklists reference codes) ----------------------

UNDETERMINED = "undetermined"
OTHER = "other"

DOC_KINDS_UK = {
    "statut": "Статут і установчі документи",
    "corp_decision": "Рішення органів управління",
    "corp_register": "Корпоративні реєстри та витяги (учасники, бенефіціари)",
    "contract_commercial": "Господарський договір (поставка, послуги, підряд тощо)",
    "contract_financing": "Кредитний договір, застава, порука, іпотека",
    "contract_lease": "Договір оренди (майна, землі)",
    "title_doc": "Правовстановлюючий документ на майно",
    "registry_extract": "Витяг з державного реєстру (речових прав, кадастру, судових рішень)",
    "permit": "Дозвіл, ліцензія, сертифікат",
    "hr_doc": "Кадровий і трудовий документ",
    "tax_doc": "Податковий документ і звітність",
    "court_doc": "Судовий документ, виконавче провадження",
    "fin_statement": "Фінансова звітність і облікові документи",
    "ip_it_doc": "Документ про інтелектуальну власність, ІТ, персональні дані",
    "insurance": "Страхування",
    "correspondence": "Листування, акти перевірок, претензії",
    "law": "Акт законодавства",
    OTHER: "Інше",
    UNDETERMINED: "Невизначено",
}
DOC_KINDS = tuple(DOC_KINDS_UK)

# --- text quality ---------------------------------------------------------------------------

Q_OK, Q_DEGRADED, Q_NONE = "ok", "degraded", "none"
QUALITY_UK = {Q_OK: "читається", Q_DEGRADED: "з помилками розпізнавання", Q_NONE: "нечитабельний"}

# --- depth and modules -----------------------------------------------------------------------

DEPTHS = ("full", "redflag")
DEPTH_UK = {"full": "повний", "redflag": "виявлення критичних ризиків (red-flag)"}
TIERS = ("core", "extended")
SCOPES = ("unit", "whole_doc")
MODULES = ("general", "agro")

# --- fixed sentences ---------------------------------------------------------------------------

REDFLAG_CLOSING_UK = (
    "Скринінг основних позицій не виявив інших ризиків, крім наведених у реєстрі; "
    "це не підтвердження їх відсутності."
)
LEGISLATION_RESPONSIBILITY_UK = (
    "Законодавство взято з блокнота законодавства; актуальність його тексту підтверджена користувачем, "
    "який відповідає за неї."
)
LDH_MARKER_UK = "[Перевірити чинну редакцію]"
INTERTEMPORAL_NOTE_UK = (
    "Оцінено за чинною редакцією; редакцію на дату укладення документа не перевірено."
)
NO_RELIANCE_UK = (
    "Звіт підготовлено для замовника. Покладання на нього третіх осіб потребує окремої письмової згоди."
)

# --- forbidden outcome phrases in free text written by models -----------------------------------

OUTCOME_PHRASES = (
    "не виявлено",
    "не виявили",
    "не виявлена",
    "не виявлені",
    "ризиків немає",
    "ризиків не",
    "відсутні ризики",
    "відсутність ризиків",
    "порушень не",
    "зауважень немає",
)
