"""The delegation block of `nlm_protocol.md` §8, generated per notebook.

An agent receives neither the protocol nor the project instructions, so every prompt for an agent that works
with a notebook carries this block. The five numbered points follow §8; points 6-8 are the DD addendum
(`lib/agent-core/nlm-dd-addendum.md`).
"""

from __future__ import annotations

UNIT_HINTS = {
    "legislation": "Стаття N.",
    "documents": "Стаття N. / Розділ N. / Додаток N / нумерований пункт (залежно від документа)",
}


def delegation_block(notebook: dict, role: str, ddf_cmd: str = "ddf") -> str:
    """`notebook` = {"ref", "title"}; `role` = "documents" | "legislation"."""
    title = notebook.get("title") or notebook.get("ref")
    unit = UNIT_HINTS.get(role, UNIT_HINTS["documents"])
    lines = [
        f"Робота з блокнотом NotebookLM «{title}» (ідентифікатор: {notebook.get('ref')}):",
        "1. Перелік джерел — через notebook_get (не source_list_drive). Текст джерела — через "
        "source_get_content. Одиницю тексту вирізай командою "
        f"`{ddf_cmd} evidence cut`, а не відтворюй з пам'яті: після реєстрації тексту команда повертає "
        f"одиниці за заголовком «{unit}». notebook_query — лише для пошуку, де положення міститься: "
        "одне вузьке питання на виклик, source_ids, new_conversation: true.",
        "2. Якщо результат source_get_content збережено у файл, не читай його повністю і не переписуй: передай "
        f"шлях до файлу команді `{ddf_cmd} evidence register-text --file <шлях>`. Якщо результат прийшов у "
        "відповіді, запиши його у файл дослівно, без змін і скорочень, і зареєструй так само.",
    ]
    if role == "legislation":
        lines.append(
            "3. Текст блокнота є чинною редакцією (відповідальність користувача). Не звіряй його із зовнішніми "
            "джерелами і не перевіряй його чинність.")
        lines.append(
            "4. Норма, якої немає в блокноті, — прогалина блокнота законодавства. Назви її в результаті "
            "(акт і одиниця). Запасне джерело дозволено одне: Legal Data Hunter, лише для такої норми; "
            "результат познач як source: ldh.")
    else:
        lines.append(
            "3. Текст джерел цього блокнота — копія документа від продавця. Редакцію і чинність документа не "
            "перевіряй: оцінюй його зміст.")
        lines.append(
            "4. Документ чи одиниця, якої немає в блокноті, — прогалина блокнота. Назви її в результаті "
            "(джерело і одиниця). Запасні джерела заборонені: не шукай документ у вебі й не відновлюй його "
            "з пам'яті.")
    lines += [
        "5. Помилка автентифікації (stale, «Authentication expired») — зупини звернення до блокнота і поверни "
        "блокер: у JSON-результаті поле \"blocker\": \"auth_expired\". Не замінюй блокнот вебом.",
        "6. Не додавай джерела до блокнота (source_add заборонено) і не змінюй блокнот.",
        "7. Твердження про відсутність («немає», «не передбачено») ґрунтуються лише на прочитаному повному "
        "тексті, суцільному ланцюгу цитат або повнотекстовому пошуку командою "
        f"`{ddf_cmd} evidence scan`. Відповідь notebook_query такою підставою не є.",
        "8. Цитату для знахідки бери лише з тексту, виданого `evidence cut`, або з поля cited_text відповіді "
        "notebook_query. Переказ моделі цитатою не вважається.",
    ]
    return "\n".join(lines)
