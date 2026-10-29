# duedillforge-ua — передача розробнику

Дата: 2026-10-29. Репозиторій: `https://github.com/arefievarefiev-glitch/duedillforge-ua` (локальна копія `/home/claude/duedillforge-ua`, **нічого не закомічено**: `LICENSE` змінено, решта файлів нових). Замовник: практикуючий юрист в Україні (судове представництво, договірне право, M&A, DD), програмує слабо, потрібні готові робочі рішення.

**Стан.** Код і тести готові, плагін **жодного разу не запускався на живому NotebookLM**. П'ять файлів тестів проходять (близько 44 тестів, усі з імітованими агентами). Пріоритет №1 — пробний запуск на малому наборі документів із закладеними дефектами.

## 1. Що це

Плагін для Claude (Cowork і Claude Code): юридична перевірка (DD) українського товариства на стороні **покупця корпоративних прав**. Джерела: документи й законодавство в блокнотах NotebookLM плюс документи проєкту Claude. Вихід українською: звіт DOCX, реєстр ризиків XLSX, запит додаткових документів DOCX. Висновку «укладати чи ні» немає, є перелік питань до вирішення перед підписанням.

Команди: `/duedillforge:dd` (нова перевірка), `:continue` (відповідь на шлюз, відновлення після протермінованого входу), `:status`.

## 2. Ухвалені рішення

| Тема | Рішення |
|---|---|
| Архітектура | Нова легка машина станів, не форк memoforge-ua; запускачі `scripts/ddf`, `ddf.cmd` та частина структури з memoforge (MIT) |
| «Не виявлено» | Присвоює лише програма, на одній із трьох підстав: повнотекстове читання (одиниці через `evidence cut`); суцільний ланцюг дослівних цитат без розривів; повнотекстовий пошук, усі збіги переглянуто. Тексти статусів друкує програма за шаблонами |
| Рівні читання | Ключові документи (статут, рішення органів, витяги, правовстановлювальні, дозволи, фінансові, судові) і документи проєкту читаються повністю, цитати звіряє код. Масив договорів скринінгується вузькими запитами до NotebookLM |
| Паралелізм | 2 запити NotebookLM одночасно за замовчуванням, налаштовується |
| Реєстри | Плагін публічні реєстри не опитує; працює з витягами, відсутні йдуть до запиту документів |
| Матеріальність | Грошового порогу немає |
| Шкала ризику | критичний / суттєвий / до відома |
| Режими | Глибина `full` / `redflag`; модулі `general` / `agro` |
| Блоки й блокноти | Визначаються під кожне завдання; блокнот законодавства один спільний; один проєкт Claude на об'єкт |
| Протокол NLM | `lib/agent-core/nlm_protocol.md` дослівно + `nlm-dd-addendum.md`; `notebook_get` замість `source_list_drive`; `source_add` плагін не використовує |
| Протермінований вхід | Збій ставить завдання на паузу; після `nlm login` команда `:continue` викликає `refresh_auth` |
| Законодавство | Текст у блокноті = чинна редакція (відповідальність користувача, у звіті є рядок про це). Legal Data Hunter лише коли норми немає в блокнотах, з позначкою `[Перевірити чинну редакцію]`. Примітка в звіті: «Оцінено за чинною редакцією; редакцію на дату укладення документа не перевірено.» |
| Мова | Звіт українською, стиль у `lib/prose-style-uk.md` |

## 3. Конвеєр

Цикл маршрутизатора: `ddf next` → дія (`dispatch` / `gate` / `pause` / `done` / `failed` / `cancelled`) → виконати → `ddf report --slot` → `ddf next`. Глобальна опція `--task ID` ставиться перед підкомандою.

Фази: `preflight_scout` → `preflight_gate` (proceed / rescan / cancel) → `block_review` (хвилі) → `legal_basis` → `calibration` → `coverage_audit` (один раз, може перевідкрити позиції) → `findings_gate` (автоматично при критичних знахідках) → `drafting` → `review` (fidelity + form, до `review_iterations` раундів) → `export`.

- Агентів у хвилі = min(`max_parallel_queries`, `max_agents_per_wave`, пакети); запитів на агента = `max_parallel_queries // n_agents`.
- У режимі `full` після скринінгу ключові позиції зі статусом `screened` (до `upgrade_max_docs`=25 документів) перечитуються повністю.
- Слот: до `max_slot_attempts` спроб, далі деградація залежно від виду слота (звіт видається завжди). Пауза при `blocker: auth_expired` лишає слот відкритим.
- Промпти пишуться у `prompts/<slot>.md`, результати читаються з `slots/<slot>.json`.
- Текст автора звіту лінтується: заборонені фрази про відсутність ризиків (`OUTCOME_PHRASES`), невідомі id знахідок, критичні знахідки без пункту в питаннях до підписання.

## 4. Реєстр перевірок (`ledger.py`)

Статуси: `pending`, `risk`, `clear`, `screened`, `unverified` (з кодом причини), `missing`, `na`. Агент **заявляє**, `ledger._evaluate` / `_check_clear` **присвоює**; невдалий `clear` знижується з причиною. Покриття (`N з M одиниць`, `збігів переглянуто: N`) показується у звіті.

Якість тексту: `ok` / `degraded` / `none` (менше 200 знаків = `none`, причина `too_short`). `degraded` і `none` ніколи не дають `clear`. Стан завантаження: `text` | `not_fetched` (лише в списку, береться на вимогу) | `failed`.

План читання (`dispatch.read_plan`): ключові види, документи проєкту й обсяги до `small_scope_docs`=5 читаються повністю.

## 5. Карта репозиторію

```
.claude-plugin/plugin.json   маніфест, userConfig
.mcp.json                    Legal Data Hunter (https://legaldatahunter.com/mcp, http)
agents/                      scout, block-reviewer, legal-basis-researcher, severity-calibrator,
                             coverage-auditor, report-writer, fidelity-reviewer, form-reviewer
skills/                      dd (+ references/router.md), continue, status
lib/agent-core/              nlm_protocol.md, nlm-dd-addendum.md, untrusted-content.md,
                             output-contract.md, tooling.md
lib/checklists/*.json        переліки позицій (corporate 14, contracts 12, real-estate 9, land 9, labor 8,
                             finance 8, litigation 7, tax 6, permits 5, ip-it 6, agro + 3 окремі agro-*)
lib/risk-scale.md, lib/prose-style-uk.md
scripts/ddf, ddf.cmd         запускач (додає <plugin_data>/site-packages до PYTHONPATH)
scripts/ddforge/             store, config, vocab, quality, indexer, evidence, ledger, register, docs,
                             checklists, classify, nlm_block, preflight, dispatch, machine, render, cli
scripts/tests/               test_evidence, test_ledger, test_machine, test_checklists, test_launcher
docs/verification.md         що перевірено, а що ні
README.md
```

CLI `ddf`: `task new|list|status|cancel|resolve`, `next`, `report --slot`, `gate --decision [--text]`, `resume`, `status`, `export`, `deps [check|install]`, `nlm-block`, `evidence register-text|units|cut|scan|review|locate`, `docs ingest-inbox|set-kind|list`, `finding override`, `state doc|ledger-row`.

Налаштування в `plugin.json` userConfig: `output_folder`, `publish_folder`, `max_parallel_queries`, `items_per_batch`, `findings_gate`, `review_iterations`, `writer_model`. Читаються також з env `CLAUDE_PLUGIN_OPTION_<KEY>` і `options.json`; у `config.py` є ще `max_agents_per_wave`=4, `upgrade_max_docs`=25, `small_scope_docs`=5, `findings_per_batch`=10, `scan_hit_cap`=50 (у userConfig не винесені).

## 6. Не перевірено (головні ризики)

1. **Живий NotebookLM**: `source_get_content` на великих джерелах, `notebook_get`, паралелізм вище 2, поведінка зі сканами.
2. **Ризик копіювання.** Для малих джерел `source_get_content` повертає текст у відповіді; щоб зареєструвати його, агент мусить дослівно записати його у файл. Це витрата вихідних токенів і можливе спотворення. Звірка цитат ловить розбіжність із зареєстрованим текстом, але не з оригіналом. Варіант рішення: скрипт, що забирає текст напряму, якщо MCP це дозволить.
3. **Завантаження в Cowork** `agents/*.md` і `skills/*/SKILL.md` (поля заголовка); у скілі припущено доступ до документів проєкту через інструмент `Projects`.
4. **Legal Data Hunter**: форма відповідей і покриття українського права.
5. **Якість промптів** агентів і висновків; реальних документів у тестах немає.
6. **OCR** немає: плагін лише класифікує якість тексту.
7. **Оцінка часу** (~72 запити/год при 100 с на запит і 2 паралельних) — розрахунок, не вимір.
8. Хуків і шлюзу дозволів немає: Cowork може запитувати підтвердження викликів.
9. Дуже короткий документ (<200 знаків) вважається нечитабельним і йде до запиту документів.
10. Переліки позицій — чернетки; норми названо на рівні акта, без статей. Потрібне затвердження юристом.

## 7. Наступні кроки

1. Пробний запуск: 1 блокнот із 10–15 документів із закладеними дефектами (відсутня згода учасників, прострочений договір, розбіжність у даних статуту й витягу), 1 блокнот законодавства. Перевірити пункти 1–3 розділу 6.
2. Закомітити й запушити в нову гілку (за замовчуванням `main`; краще гілка). Ще не зроблено.
3. Видалити невикористану змінну `heading_end` в `evidence.chain_coverage`.
4. Винести нові параметри `config.py` у `plugin.json`, якщо потрібні користувачу.
5. Виправити `source_list_drive` у memoforge-ua (потрібен доступ на запис, не робилося).
6. Затвердження переліків юристом; прогін на реальному об'єкті.

## 8. Ідеї, що не реалізовані (з огляду `strigov/superpowers-strigov-ver`)

Перегляд зроблено лише за головною сторінкою репозиторію, `SKILL.md` не читалися. Придатне для перенесення:

1. **Незалежний рецензент на іншій родині моделей.** `fidelity-reviewer` і `form-reviewer` зараз працюють на тій самій родині, що й автор звіту; тести цього не закривають. Потрібен зовнішній виклик (наприклад Codex CLI чи інша модель) або принаймні окрема модель у `agents/*.md`.
2. **Дешева перевірка перед дорогою** уже є (`ddf` звіряє цитати й статуси до рецензентів).
3. **Журнал авторизацій і дій** над документами та безпечні правила Git: кожна дія лишає слід у задачі.
4. **Обмежені цикли рев'ю** уже є (`review_iterations`).
5. **Пам'ять про архітектуру** (компактна карта в `docs/`) — для плагіна корисна як опис фаз і слотів; поточний `docs/handoff.md` виконує цю роль.
6. Застереження: репозиторій має 4 зірки й одного автора, залежить від зовнішнього Codex CLI і названих моделей, які швидко старіють; брати ідеї, а не код.

## 9. Як відтворити тести

```
cd scripts
python tests/test_evidence.py && python tests/test_ledger.py && python tests/test_machine.py \
  && python tests/test_checklists.py && python tests/test_launcher.py
```

Тести перевіряють логіку на імітаціях і не доводять роботу в бойових умовах.
