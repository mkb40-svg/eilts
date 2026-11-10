# IELTS Self-Prep

Подготовка к IELTS в своём GitHub-репозитории: без регистрации, без сервера, без базы данных.
Вы выполняете задания, ИИ-агент проверяет работы, ставит ориентировочный band, разбирает ошибки,
помнит повторяющиеся проблемы и каждый день выдаёт следующее задание под ваши слабые места.

Ваш прогресс → [PROGRESS.md](PROGRESS.md) · план → [plan/PLAN.md](plan/PLAN.md) · формат материалов → [content/FORMAT.md](content/FORMAT.md)

## Что уже работает (MVP)

| Возможность | Без ключа ИИ | С ключом ИИ |
| --- | --- | --- |
| Анкета «Старт», профиль, план до экзамена | да | да |
| Reading: проверка по ключу, band, точность по типам вопросов | да | + разбор каждой ошибки с цитатой из текста |
| Writing Task 1 и 2 | подсчёт слов, сохранение работы | + band по 4 критериям, ошибки с исправлениями, приоритеты |
| Задание на день под слабые места, итоги недели | да | + совет наставника в итогах |
| Вопросы наставнику (`@claude` в комментарии) | — | да |
| Сайт на GitHub Pages с таймером | да | да |

Listening, Speaking и пробные экзамены — следующий этап (v1).

## Как начать (около 15 минут)

1. Нажмите **Use this template → Create a new repository**. Выберите **Public**, если нужен бесплатный сайт
   (работы и прогресс тогда видны всем под вашим ником), или **Private** (сайт на Pages — только на платном тарифе).
2. **Settings → Pages → Source: GitHub Actions** — включает сайт. Затем **Actions → «Сайт на GitHub Pages» → Run workflow**.
   Сайт откроется по адресу `https://<ваш-логин>.github.io/<имя-репозитория>/`.
3. *(Можно позже)* **Settings → Secrets and variables → Actions → New repository secret** — ключ ИИ (таблица ниже).
4. **Issues → New issue → «Старт: анкета ученика»**. Через минуту агент ответит: профиль, план и две Issue диагностики.
5. Выполните диагностику и отвечайте **комментарием прямо в Issue задания**. Дальше задания приходят каждый день.

Время ежедневного задания задаётся одной строкой `cron` (в UTC) в
[.github/workflows/daily-task.yml](.github/workflows/daily-task.yml).

## Подключение ИИ

Ключи хранятся только в **Secrets** репозитория — в файлах их нет. Каждую роль агента можно отдать своему
провайдеру в [config.yml](config.yml), раздел `agent.roles`:

| Провайдер | `provider` в config.yml | Секрет | Заметки |
| --- | --- | --- | --- |
| Claude (Anthropic) | `anthropic` | `ANTHROPIC_API_KEY` | По умолчанию; строгий JSON, кэш промпта |
| Claude в Azure (Microsoft Foundry) | `azure_claude` | `ANTHROPIC_FOUNDRY_API_KEY` + переменная `ANTHROPIC_FOUNDRY_RESOURCE` | Оплата через подписку Azure |
| Azure OpenAI | `azure_openai` | `AZURE_OPENAI_API_KEY` | `model` = имя развёртывания; укажите свой `base_url` |
| Grok (xAI) | `xai` | `XAI_API_KEY` | `model` — имя модели из консоли xAI |
| DeepSeek | `deepseek` | `DEEPSEEK_API_KEY` | Данные обрабатываются на серверах DeepSeek |

Пример: оценка эссе на Claude, разборы и вопросы — на DeepSeek:

```yaml
roles:
  examiner: {provider: anthropic, model: claude-opus-5-5, effort: high}
  explainer: {provider: deepseek, model: <имя модели DeepSeek>}
  coach: {provider: deepseek, model: <имя модели DeepSeek>}
```

Какая модель оценивает эссе точнее, покажет калибровка: положите в `agent/calibration/writing/` работы,
оценённые преподавателем, и запустите **Actions → «Калибровка экзаменатора»** (подробнее —
[agent/calibration/README.md](agent/calibration/README.md)).

**Расходы.** Лимит на месяц — `budget_usd_month` в `config.yml`; при превышении ИИ отключается до нового месяца,
проверки по ключу продолжают работать. Цены моделей — в [agent/rubrics/pricing.yml](agent/rubrics/pricing.yml):
для Grok, DeepSeek и Azure OpenAI впишите их сами, иначе расход по ним считается как 0.
Ориентир на Claude: проверка эссе ≈ $0.11 на Opus 5.5, ≈ $0.06 на Sonnet 5.5, меньше цента на Haiku 5.5.

## Как это устроено

```
Ученик → Issue (форма или комментарий) → GitHub Actions → проверка кодом + ИИ → комментарий-разбор
                                                         ↘ progress/, plan/, PROGRESS.md (коммит)
```

- `content/` — задания и ключи ответов (только оригинальные материалы или с открытой лицензией).
- `submissions/` — ваши работы; `progress/` — профиль, результаты, банк ошибок, расходы, разборы.
- `agent/` — код агента (Python), промпты ролей, рубрики, калибровка.
- `.github/` — формы Issue и сценарии Actions.

Агент реагирует только на владельца репозитория: чужие Issues в публичном репозитории не тратят ваш бюджет.
Текст работ передаётся ИИ как данные — попытки «попросить» высокий балл внутри эссе игнорируются.

## Честно о границах

- Оценки ориентировочные и не являются официальным результатом IELTS.
- Перевод сырого балла Reading в band — по общедоступным приблизительным таблицам; по одному тексту оценка грубая.
- В публичном репозитории ключи ответов видны любому, кто специально их откроет. Не подсматривайте — иначе прогресс будет ненастоящим.
- Расписание GitHub (`cron`) может запаздывать на несколько минут.

## Для разработки

```bash
pip install -r agent/requirements.txt pytest
python -m pytest -q
```

```bash
PYTHONPATH=agent/src python -m ielts_agent validate
```

```bash
PYTHONPATH=agent/src python -m ielts_agent --dry-run daily
```

`--dry-run` ничего не отправляет в GitHub, а печатает действия агента.

## Лицензия

Код — MIT. Учебные материалы в `content/` — CC BY 4.0, если в файле не указано иное.
