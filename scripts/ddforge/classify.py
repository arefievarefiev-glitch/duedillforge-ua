"""Heuristic classification of a document by title (and, if needed, the first lines of its text).

A wrong guess narrows the scope of a checklist item, so the rule is conservative: when the title does not
decide the kind, the document stays `undetermined` and is in the scope of every item.
"""

from __future__ import annotations

import re

from .vocab import UNDETERMINED

# (kind, pattern) — the first kind whose pattern matches the TITLE wins; order matters.
TITLE_RULES = [
    ("statut", r"\bстатут\b|установч|засновницьк"),
    ("registry_extract", r"витяг\s+з\s+(?:державного\s+)?(?:єдиного\s+)?(?:реєстр|єдр|кадастр)|єдр\b|кадастров(?:ий|а)\s+(?:план|номер)|єдрср"),
    ("title_doc", r"свідоцтво\s+про\s+право|державн(?:ий|ого)\s+акт|правовстановлю|технічний\s+паспорт|договір\s+купівлі-продажу\s+(?:нерухом|будівл|земельн)"),
    ("contract_financing", r"кредитн|позик|застав[аиіу]|іпотек|порук|гарант(?:ія|ійн)|факторинг|овердрафт"),
    ("contract_lease", r"оренд[иіау]|найм[уі]|суборенд|емфітевзис|лізинг"),
    ("corp_decision", r"протокол\s+(?:загальн|зборів|засідан)|рішення\s+(?:єдиного\s+)?(?:учасник|засновник|загальн|наглядов|правлінн)|наказ\s+про\s+призначення\s+директор"),
    ("corp_register", r"список\s+учасник|реєстр\s+акціонер|структур[аи]\s+власност|бенефіціар"),
    ("permit", r"ліцензі|дозвіл|дозвільн|сертифікат|реєстраційне\s+посвідчення|спеціальн\w+\s+дозвіл"),
    ("hr_doc", r"трудов(?:ий|ого)\s+договір|штатн|колективн(?:ий|ого)\s+договір|наказ\s+про\s+(?:прийняття|звільнення|відпустк)|правила\s+внутрішнього"),
    ("tax_doc", r"податков|декларац|довідка\s+про\s+відсутність\s+заборгован|єдин(?:ий|ого)\s+(?:податок|внесок)|пдв\b"),
    ("court_doc", r"рішення\s+суду|ухвала|постанова\s+(?:суду|апеляц|верховн)|позовна\s+заява|виконавч(?:ий|е)\s+(?:лист|провадження|напис)|судов"),
    ("fin_statement", r"баланс\b|звіт\s+про\s+фінансов|фінансов(?:а|ої)\s+звітність|аудиторськ|оборотно-сальдов"),
    ("ip_it_doc", r"авторськ|патент|торгов(?:а|ої)\s+марк|свідоцтво\s+на\s+знак|ліцензійний\s+договір|персональн\w+\s+дан|програмн\w+\s+забезпеч|домен"),
    ("insurance", r"страхов|поліс"),
    ("correspondence", r"\bлист\b|претензі|акт\s+перевірки|припис|повідомлення|вимога\s+(?:про|щодо)"),
    ("contract_commercial", r"договір|контракт|угода|специфікац|додаткова\s+угода|акт\s+(?:прийому|виконаних)"),
]
_COMPILED = [(kind, re.compile(pattern, re.I | re.U)) for kind, pattern in TITLE_RULES]


def classify_title(title: str) -> str:
    """Return a document kind code or `undetermined`."""
    text = (title or "").strip()
    if not text:
        return UNDETERMINED
    for kind, regex in _COMPILED:
        if regex.search(text):
            return kind
    return UNDETERMINED


def classify_origin(origin: str, title: str) -> str:
    if origin == "legislation":
        return "law"
    return classify_title(title)
