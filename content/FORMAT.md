# Формат учебных материалов

Все задания лежат в `content/` как Markdown-файлы с YAML-шапкой. Ключи ответов — отдельно в `content/answer-keys/`, чтобы не попадаться на глаза при выполнении задания.

Запрещено добавлять тексты из Cambridge IELTS, British Council, IDP и других изданных пособий. Каждый файл обязан иметь поля `source` и `license` — это проверяет CI (`python -m ielts_agent validate`).

## Общие поля шапки

| Поле | Обязательно | Значение |
| --- | --- | --- |
| `id` | да | Уникальный ID: `R-0001` (Reading), `W1A-0001` (Writing Task 1 Academic), `W1G-0001` (Task 1 General), `W2-0001` (Task 2) |
| `module` | да | `reading` или `writing` |
| `title` | да | Короткое название |
| `time_minutes` | да | Время на выполнение |
| `source` | да | `original` (написано для проекта) или ссылка на источник |
| `license` | да | Например, `CC BY 4.0` |
| `ai_generated` | да | `true`, если текст создан ИИ |
| `reviewed` | да | `true`, если человек проверил текст и ключ |
| `diagnostic` | нет | `true` — задание для стартовой диагностики |

## Reading

Файл: `content/reading/academic/R-0001.md` или `content/reading/general/R-0002.md`.

Дополнительные поля: `variant` (`academic` | `general`), `questions` (число вопросов), `question_types` (список типов из таблицы ниже).

Тело файла:

```markdown
# Заголовок текста

**A** Текст абзаца A...

**B** Текст абзаца B...

## Questions 1–5

Do the following statements agree with the information given in the passage?
Write TRUE, FALSE or NOT GIVEN.

1. Statement...
2. Statement...
```

Нумерация вопросов сквозная, с 1. Инструкции к вопросам — на английском, как на экзамене.

### Типы вопросов

`tfng`, `ynng`, `matching_headings`, `matching_information`, `matching_features`, `matching_sentence_endings`, `multiple_choice`, `summary_completion`, `note_completion`, `table_completion`, `flowchart_completion`, `sentence_completion`, `diagram_labelling`, `short_answer`.

### Ключ ответов

Файл `content/answer-keys/R-0001.yml`:

```yaml
id: R-0001
answers:
  "1": {answer: ["TRUE"], type: tfng}
  "6": {answer: ["iv"], type: matching_headings}
  "11": {answer: ["water vapour", "water vapor"], type: sentence_completion, max_words: 2}
evidence:
  "1": "Точная цитата из текста, которая доказывает ответ."
  "11": "..."
```

Правила ключа:

- `answer` — всегда список допустимых вариантов (регистр не важен).
- Для `tfng` — `TRUE` / `FALSE` / `NOT GIVEN`; для `ynng` — `YES` / `NO` / `NOT GIVEN`.
- Для выбора буквы или римской цифры — сама буква или цифра: `B`, `iv`.
- Для completion-вопросов ответ берётся из текста дословно, `max_words` — лимит из инструкции.
- `evidence` — дословная цитата из текста (без изменений) для каждого вопроса, кроме ответов `NOT GIVEN`.

## Writing

Файлы: `content/writing/task1-academic/W1A-0001.md`, `content/writing/task1-general/W1G-0001.md`, `content/writing/task2/W2-0001.md`.

Дополнительные поля: `task` (`task1-academic` | `task1-general` | `task2`), `essay_type` (для Task 2: `opinion` | `discussion` | `advantages_disadvantages` | `problem_solution` | `two_part`; для писем: `formal` | `semi_formal` | `informal`), `min_words` (150 или 250), `topic`.

Тело файла — текст задания на английском в том виде, как он дан на экзамене. Для Task 1 Academic данные даются таблицей Markdown (или картинкой рядом с файлом).
