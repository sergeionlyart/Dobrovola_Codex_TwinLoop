Source: docs/TECH_SPEC_JurisParse_UN_V_1.1.md
Canonical path: docs/TECH_SPEC.md
Do not edit requirements casually; prefer PRs

# TECH_SPEC_JurisParse_UN

Created: February 23, 2026 1:13 PM

# 

## **Введение и контекст проекта**

Данный парсер разрабатывается как **фундамент** для будущей RAG‑системы юридического копилота, предназначенного для подготовки **проектов индивидуальных жалоб (individual communications)** в договорные органы ООН (например CCPR, CAT, CEDAW, CRPD). Система должна помогать юристу быстро находить и корректно цитировать релевантные нормы и практику: тексты конвенций и протоколов, замечания общего порядка/общие рекомендации, процессуальные правила, а также решения (Views/Decisions) по индивидуальным обращениям и связанные follow‑up материалы.

Ключевая особенность будущего RAG‑подхода — опора на концепцию **PageIndex**: мы не хотим строить систему исключительно на “семантических чанках” и топ‑K по эмбеддингам. Вместо этого мы планируем организовать корпус как **набор документов, разложенных на воспроизводимые “страницы/сегменты”**, которые можно:

1. надёжно извлекать и индексировать (page‑level/segment‑level),
2. связывать с исходными артефактами (PDF/DOCX/HTML) и метаданными,
3. использовать для объяснимого “tree search” внутри документов и для формирования **юридически корректных цитат** (в идеале: doc symbol + page/paragraph).

Отсюда следует, что парсер — это не “разовая выгрузка PDF в базу”, а **производственный контур накопления и нормализации корпуса**, который должен обеспечивать три критически важные свойства для будущего копилота:

1. **Трассируемость и доказуемость источника**
    
    Любой фрагмент текста, который попадёт в ответ/черновик жалобы, должен быть связан с:
    
    - конкретным официальным документом (желательно UN symbol),
    - конкретной страницей/сегментом,
    - конкретным артефактом (файл, версия, хэш).
        
        В юридическом сценарии это не “nice‑to‑have”, а базовое требование: юрист должен проверять и воспроизводить выводы модели, а система не должна подсовывать “похожие” формулировки без проверяемой ссылки.
        
2. **Воспроизводимость и контроль версий**
    
    Корпус документов ООН обновляется: появляются новые решения, публикуются уточнённые версии, меняются форматы на источниках. Парсер должен строиться так, чтобы можно было:
    
    - повторить прогон и получить тот же результат на тех же входных файлах,
    - обнаружить, что источник изменился (по хэшу/версии),
    - корректно обновить данные в Mongo без дублирования и потери истории.
        
        Это важно для будущих индексов (в т.ч. PageIndex‑деревьев) и для регрессионного тестирования RAG‑логики.
        
3. **Структура данных “под retrieval”, а не “под хранение”**
    
    Мы заранее проектируем Mongo‑схему так, чтобы она помогала retrieval‑модулю:
    
    - выбирать документы по метаданным (комитет, тип, статьи, страна, исход, даты),
    - быстро доставать нужные страницы/сегменты для контекста,
    - в перспективе — строить деревья/узлы (PageIndex tree) поверх тех же сегментов, не переделывая базу.
        
        Иными словами, оптимизация здесь не только про скорость парсинга, но и про то, насколько легко потом реализовать: document routing → tree/node selection → генерацию текста жалобы с обязательными цитатами.
        

### **Что должен “понимать” разработчик при принятии решений**

При реализации парсера важно держать в голове, что конечный пользователь — юрист, а конечный артефакт — **проект жалобы**, где:

- каждый юридически значимый тезис должен быть подкреплён источником,
- цитирование должно быть максимально “официальным” и устойчивым (doc symbol, page/para),
- ошибки “не нашёл документ / перепутал версию / сослался не туда” критичнее, чем пропуск второстепенного материала.

Поэтому допускается и приветствуется “творческий” подход к инженерным решениям, если он усиливает следующие приоритеты:

- **устойчивость к изменениям источников** (не хардкодить ID и структуру там, где можно извлечь справочники динамически),
- **качество и повторяемость сегментации** (страницы/сегменты как атомы контекста),
- **идемпотентность** (повторный прогон не плодит сущности),
- **богатые метаданные** (чтобы RAG мог маршрутизировать и объяснять).

Итоговая роль парсера в проекте — превратить разрозненные официальные публикации ООН в **локальный, контролируемый, цитируемый корпус**, который станет базой для следующего слоя: построения PageIndex‑подобных деревьев, агентного поиска по структуре документов и генерации юридических текстов с проверяемыми ссылками.

# 

---

# **TechSpec v1.1: JurisParse UN**

## **Пайплайн парсинга документов Treaty Bodies ООН → MongoDB Atlas + артефакты в Google Cloud Storage (GCS)**

### **Stage 1 (Parser/Ingest), с совместимостью с концепцией PageIndex**

**Дата:** 2026‑02‑23

**Статус:** готово к реализации (Ready for implementation)

**Основной принцип:** документ → **версии документа** → **страницы/сегменты** (page_index 1‑based) → трассируемость до артефакта/хэша → подготовка к будущей PageIndex‑RAG.

---

## **0) Контекст и цель**

Мы строим фундамент для будущей RAG‑системы юридического копилота (draft индивидуальных жалоб / individual communications) на базе корпуса официальных документов ООН (treaty bodies). На этапе 1 (этот ТЗ) мы реализуем **производственный ingest‑контур**, который:

1. находит (discovery) документы из официальных источников,
2. скачивает и хранит бинарные артефакты в GCS,
3. извлекает текст и разбивает на воспроизводимые “страницы/сегменты”,
4. сохраняет метаданные + сегменты в MongoDB Atlas,
5. гарантирует идемпотентность, трассируемость и воспроизводимость результата.

**Ключевая совместимость:** будущая PageIndex‑RAG ожидает дерево/узлы, привязанные к страницам (page_index), и режим цитирования по страницам. В PageIndex API page_index — **1‑based**.

---

## **1) Границы работ (Scope)**

### **1.1 In‑scope (MVP‑1)**

- Комитеты: **CCPR, CAT, CEDAW, CRPD**
- Языки: **EN** (архитектурно поддержать массив языков)
- Типы документов (doc_type, внутренний enum):
    - **jurisprudence_view** (Jurisprudence — Views/Decisions; admissibility/merits)
    - **jurisprudence_followup** (опционально в конце MVP‑1)
    - **general_comment** (для CCPR/CAT/CRPD/др.)
    - **general_recommendation** (CEDAW)
    - **rules_of_procedure**
    - **working_methods**
    - **guidelines / note**
    - **treaty_text**
    - **optional_protocol**
    - **procedure_guidance** (OHCHR процедура/гайд)

### **1.2 Out‑of‑scope (в MVP‑1 не делаем)**

- OCR как полноценный слой (только флаг needs_ocr и сохранение артефакта)
- State party reports, NGO submissions, session/agenda docs
- Concluding observations (можно добавить позже)
- Полноценный entity extraction / цитирование по параграфам (можно оставить “крючки”)

---

## **2) Термины и инварианты (PageIndex‑aligned)**

### **2.1 Термины**

- **Document** — логическая сущность “документ по UN symbol + язык + провайдер”.
- **Document Version** — конкретная версия контента (привязка к content_sha256 канонического артефакта).
- **Artifact** — любой бинарный/сырой/производный файл (PDF/DOCX/HTML/страницы поиска/скриншоты страниц/таблицы и т.д.), хранимый в GCS.
- **Segment** — атом retrieval (обычно 1 PDF‑страница), имеющий page_index (1‑based) и текст.
- **PageIndex Tree/Node** — будущее дерево узлов, где узлы привязаны к страницам через page_index (в API) или диапазоны start_index/end_index (в open‑source генераторе).

### **2.2 Инварианты (обязательные требования)**

1. page_index **всегда 1‑based** (первая страница = 1), как в PageIndex API.
2. Любой сегмент должен быть трассируем до:
    - doc_symbol, language, provider,
    - конкретной doc_version_id,
    - конкретного артефакта (artifact_id, sha256, gcs_uri).
3. Идентификаторы должны быть:
    - детерминированными,
    - устойчивыми при повторном прогоне,
    - не создающими смысловых коллизий с PageIndex node_id (например "0007").
4. Повторный прогон ingest на тех же входах **не создаёт дубликатов** сущностей (кроме ingest_runs).

---

## **3) Источники данных и стратегии извлечения**

### **3.1 Обязательные источники (MVP‑1)**

**Источник №1 (основной):** UN Treaty Body Database (OHCHR)

- Search/Catalog: TBSearch.aspx
- Download resolver: Download.aspx?Lang=...&symbolno=...

**Источник №2 (канонический резолвер по UN symbol):** docs.un.org

**Источник №3 (рамка процедуры):** OHCHR pages + guidance note

**Источник №4 (тексты договоров/протоколов):** OHCHR/un.org (как артефакты treaty/protocol)

> Все URL‑шаблоны должны быть в конфиге. В ответе — только в code‑блоках (чтобы не “захардкодить” в коде).
> 

Пример шаблонов (в config):

```
sources:
  tbinternet:
    tbsearch_url: "https://tbinternet.ohchr.org/_layouts/15/treatybodyexternal/TBSearch.aspx"
    download_url_template: "https://tbinternet.ohchr.org/_layouts/15/treatybodyexternal/Download.aspx?Lang={lang}&symbolno={symbol}"
  docs_un:
    canonical_url_template: "https://docs.un.org/en/{symbol}"
```

### **3.2 Требование: динамическая синхронизация справочников TBSearch (lookup_sync)**

ID внутри TBSearch (TreatyID/DocTypeID) **нельзя** хардкодить в коде. Нужно:

- скачивать TBSearch HTML,
- извлекать значения <select><option> (и/или ссылок),
- сохранять в Mongo коллекцию tb_lookups,
- резолвить нужные ID по лейблам (CCPR/CAT/…, “Jurisprudence”, “Rules of procedure”…).

Это критично для устойчивости при изменениях источника (верстка/ID). (См. Приложение C.)

---

## **4) Архитектура пайплайна (ETL)**

### **4.1 Стадии**

1. **lookup_sync**
    - вход: TBSearch URL
    - выход: запись в tb_lookups (справочники treaty/doc types/countries + sha256 html)
2. **crawl (discovery)**
    - найти документы по комитетам/типам/языкам в scope
    - сохранить сырой HTML выдачи как artifact(kind=crawl_html) в GCS
    - создать/обновить source_items
3. **resolve**
    - для каждого source_item открыть Download.aspx
    - собрать доступные форматы/языки/ссылки
    - выбрать “предпочтительный формат” (PDF > DOCX > HTML)
    - сохранить решение в source_items.selected_download
4. **download**
    - скачать файл, посчитать sha256
    - записать artifact (в GCS + метаданные в Mongo)
    - определить canonical_artifact_id (обычно PDF; если DOCX/HTML и включена конверсия — derived converted_pdf)
5. **extract**
    - извлечь текст:
        - PDF: строго page‑level extraction (1 сегмент = 1 страница)
        - DOCX/HTML: либо конверсия в PDF → page extraction, либо pseudo pages
    - сформировать document_versions + метрики качества
6. **segment**
    - создать segments с устойчивыми segment_id, page_index, text_sha256
7. **load**
    - upsert в Mongo: documents, document_versions, artifacts, segments, source_items, ingest_runs, errors
8. **validate**
    - выполнить валидаторы схемы и инвариантов (см. раздел 10)

---

## **5) Режимы запуска (CLI)**

Рекомендуемый CLI: jurisparse (Typer/Click — на выбор), команды:

- lookup-sync
- crawl
- download --from-manifest <path>|--from-db
- extract --from-manifest <path>|--from-db
- ingest (end-to-end: lookup_sync → crawl → resolve → download → extract → segment → load)
- reprocess --from-manifest <path> (без сети; только GCS → extract/segment/load)
- validate --run-id <id>|--doc-id <id>|--doc-version-id <id>
- export-manifest --run-id <id>

Общие опции:

- -config config.yaml
- -run-id (если не задан — генерировать)
- -dry-run (без записи в Mongo, только отчёт + скачивание/кэш опционально)
- -max-docs, --max-per-committee, --rate-limit-rps, --retries
- -log-json (структурные логи)

---

## **6) Хранение артефактов (GCS) и стратегия путей**

### **6.1 Источник истины для файлов**

- Все бинарные/сырьевые/производные файлы хранятся **в GCS**.
- В Mongo — только метаданные, sha256, размер, gcs_uri, source_url, связи.

### **6.2 Нейминг объектов в GCS**

Требование: путь должен быть:

- детерминированным,
- удобным для человека,
- не зависеть от локального пути.

Рекомендуемый формат:

```
gs://<bucket>/<prefix>/<provider>/<doc_symbol_sanitized>/<language>/<kind>/<sha256>.<ext>
```

Пример:

```
gs://un-corpus/raw/tbinternet/CRPD_C_33_D_92_2021/EN/source_pdf/9f3a...c1.pdf
gs://un-corpus/derived/tbinternet/CRPD_C_33_D_92_2021/EN/page_image/9f3a...c1/p0007.png
```

### **6.3 Локальный кэш**

Допустим как рабочая директория:

- ./data_cache/...
- не является источником истины.

---

## **7) Извлечение текста и сегментация**

### **7.1 PDF: page‑level extraction (обязательный путь)**

**Цель:** сегмент = 1 PDF‑страница, page_index 1‑based.

Рекомендованный стек:

- Primary: PyMuPDF (pymupdf)
- Fallback: pdfplumber
- Дополнительно: pypdf для метаданных

Алгоритм:

1. открыть PDF
2. page_count = len(pages)
3. для каждой страницы i:
    - page_index = i+1
    - raw_text = extract(page)
    - text = normalize_text_v1(raw_text)
    - text_sha256 = sha256(text)
4. записать сегменты

**Quality checks:**

- doc_total_chars = sum(char_count)
- если doc_total_chars < min_total_chars или слишком много пустых страниц:
    - document_versions.extraction_quality.needs_ocr = true
    - сегменты **не создаём** (или создаём пустые, но помечаем — решение ниже)
    - документ не участвует в retrieval в MVP‑1, но артефакт и версия фиксируются

Рекомендуемое решение MVP‑1:

- если needs_ocr=true → **не создавать segments**, чтобы retrieval не получал “пустой контекст”; вместо этого документ отмечен как “требует OCR”.

### **7.2 DOCX/HTML: стратегия “совместимость с PageIndex”**

PageIndex tree generation “официально” ориентирован на PDF.

Поэтому для DOCX/HTML (если PDF не доступен) выбираем одно из двух:

**Вариант A (предпочтительный):** конверсия в PDF (LibreOffice headless)

- создаём derived artifact kind=converted_pdf
- далее используем PDF page extraction
- is_official_pagination = false (это не официальная пагинация источника)

**Вариант B:** pseudo pages (детерминированная сегментация текста)

- segment_type = pseudo_page
- page_index = 1..N
- is_official_pagination = false
- границы должны быть детерминированными (см. 7.3)

### **7.3 Детерминированная pseudo‑сегментация (если не PDF)**

Нужна строгая спецификация, чтобы повторные прогоны давали те же границы.

**Нормализация текста (normalize_text_v1):**

- заменить \r\n → \n
- заменить \u00A0 (nbsp) → пробел
- схлопнуть последовательности пробелов/табов до 1 пробела
- удалить пробелы в конце строк
- схлопнуть 3+ пустых строк до 2
- trim всего текста

**Правила разрезания:**

- сначала разбить на блоки по заголовкам (HTML: h1/h2/h3; DOCX: стили Heading 1/2/3)
- затем внутри блока собирать чанки по target_chars с ограничениями:
    - min_chars (например 1200)
    - target_chars (например 2000)
    - max_chars (например 2800)
- добавление следующего параграфа:
    - если превысили max_chars → закрыть сегмент (если текущий >= min_chars) и начать новый
    - если текущий < min_chars, допускается превышать max_chars до hard_max_chars (например 3800), чтобы не делать слишком мелкие сегменты

**ID pseudo pages:**

- segment_id = "{doc_version_id}:s{page_index:04d}" (префикс s = synthetic)

---

## **8) Модель данных MongoDB Atlas (PageIndex‑aligned)**

### **8.1 Коллекции**

**Обязательные:**

1. documents — логический документ
2. document_versions — версии документа (по контенту)
3. artifacts — все артефакты (GCS)
4. segments — страницы/сегменты текста
5. source_items — факты discovery
6. tb_lookups — справочники TBSearch (lookup_sync)
7. ingest_runs — информация о прогоне
8. errors — структурные ошибки

**Опциональные (заложить “место”):**

- pageindex_trees — хранение дерева узлов (из PageIndex API или open‑source), для Stage 2/3.

---

### **8.2 ID и ключи (общий принцип)**

- Все “главные” сущности используют **детерминированные строковые IDs** (удобно для upsert и ссылок):
    - doc_id — UUIDv5 от (provider|doc_symbol_norm|language)
    - doc_version_id — UUIDv5 от (doc_id|content_sha256)
    - artifact_id — UUIDv5 от (sha256)
    - segment_id — строка вида {doc_version_id}:p0007 или {doc_version_id}:s0007
    - source_item_id — sha1 от (provider|doc_symbol_norm|language|download_page_url)
    - run_id — UUIDv4

> doc_type
> 
> 
> **не участвует**
> 

---

### **8.3 documents**

_id = doc_id

```
{
  "_id": "doc_id",
  "doc_id": "uuidv5",
  "doc_key": "tbinternet|CRPD/C/33/D/92/2021|EN",

  "doc_symbol": "CRPD/C/33/D/92/2021",
  "language": "EN",

  "title": "string|null",
  "doc_type": "jurisprudence_view|general_comment|...",

  "committee": "CCPR|CAT|CEDAW|CRPD|null",
  "treaty": "ICCPR|CAT|CEDAW|CRPD|null",

  "dates": {
    "published": "YYYY-MM-DD|null",
    "adopted": "YYYY-MM-DD|null",
    "decision": "YYYY-MM-DD|null"
  },

  "case_meta": {
    "state_party": "string|null",
    "communication_no": "string|null"
  },

  "source": {
    "provider": "tbinternet|ohchr|docs.un.org|un.org",
    "canonical_url": "string|null",
    "search_url": "string|null"
  },

  "current_version_id": "doc_version_id|null",

  "created_at": "ISO",
  "updated_at": "ISO"
}
```

**Индексы:**

- уникальный doc_key
- индекс doc_symbol
- индекс committee, doc_type, language

---

### **8.4 document_versions**

_id = doc_version_id

```
{
  "_id": "doc_version_id",
  "doc_version_id": "uuidv5(doc_id|content_sha256)",

  "doc_id": "doc_id",
  "content_sha256": "hex",
  "retrieved_at": "ISO",
  "ingested_at": "ISO",

  "canonical_artifact_id": "artifact_id",
  "artifact_ids": ["artifact_id"],

  "page_count": 42,
  "segment_count": 42,

  "parser": {
    "name": "un_ingest",
    "version": "0.1.0",
    "git_commit": "string",
    "config_hash": "sha256(config_snapshot)"
  },

  "extraction_quality": {
    "needs_ocr": false,
    "total_chars": 12345,
    "empty_pages": 0,
    "warnings": []
  },

  "pageindex": {
    "pageindex_doc_id": "pi-...|null",
    "tree_id": "tree_id|null",
    "uploaded_at": "ISO|null",
    "mode": "api|mcp|null"
  }
}
```

**Индексы:**

- уникальный (doc_id, content_sha256)
- индекс doc_id
- индекс content_sha256

---

### **8.5 artifacts**

_id = artifact_id (UUIDv5 от sha256), уникальность по sha256

```
{
  "_id": "artifact_id",
  "artifact_id": "uuidv5(sha256)",
  "doc_id": "doc_id|null",
  "doc_version_id": "doc_version_id|null",

  "kind": "source_pdf|source_docx|source_html|crawl_html|converted_pdf|page_image|table_csv|extracted_text_jsonl",
  "format": "pdf|docx|html|png|csv|jsonl",
  "language": "EN|null",

  "sha256": "hex",
  "bytes": 123456,
  "gcs_uri": "gs://...",

  "source_url": "string|null",
  "created_at": "ISO",

  "parser": { "name": "un_ingest", "version": "0.1.0" },

  "storage": {
    "provider": "gcs",
    "bucket": "string",
    "object_name": "string",
    "generation": "string|null",
    "etag": "string|null"
  },

  "local_cache_path": "string|null"
}
```

**Индексы:**

- уникальный sha256
- индекс doc_id, doc_version_id
- индекс kind

---

### **8.6 segments**

_id = segment_id (детерминированный), уникальность по (doc_version_id, page_index)

```
{
  "_id": "segment_id",

  "segment_id": "{doc_version_id}:p{page_index:04d}",
  "segment_type": "page|pseudo_page",

  "doc_id": "doc_id",
  "doc_version_id": "doc_version_id",
  "language": "EN",

  "page_index": 7,

  "page_label": "7",
  "is_official_pagination": false,
  "pagination_method": "index_fallback|footer_regex|source_metadata",
  "pagination_confidence": 0.2,

  "text": "....",
  "text_sha256": "hex",
  "text_norm_version": "v1",

  "char_count": 2450,
  "token_estimate": 610,

  "source_artifact_id": "artifact_id",
  "page_image_artifact_id": "artifact_id|null",

  "heading_path": ["string"],

  "created_at": "ISO",
  "extraction": { "method": "pymupdf", "method_version": "1.23.x" },

  "citation": {
    "doc_symbol": "CRPD/C/33/D/92/2021",
    "language": "EN",
    "page_index": 7,
    "page_label": "7"
  }
}
```

**Индексы:**

- уникальный (doc_version_id, page_index)
- индекс doc_id
- индекс doc_version_id
- опционально: text full‑text index (временная мера до отдельного поискового слоя)

> Примечание: префиксы p/s в segment_id специально выбраны, чтобы не путать с PageIndex node_id вида "0007". https://docs.pageindex.ai/endpoints
> 

---

### **8.7 source_items**

_id = source_item_id = sha1(provider|doc_symbol|language|download_page_url)

(дата **не** участвует в ID)

```
{
  "_id": "source_item_id",
  "source_item_id": "sha1",
  "provider": "tbinternet",
  "doc_symbol": "CRPD/C/33/D/92/2021",
  "language": "EN",

  "title": "string|null",
  "committee": "CRPD|null",
  "doc_type_hint": "string|null",
  "state_party": "string|null",
  "date_listed": "YYYY-MM-DD|null",

  "search_url": "string|null",
  "download_page_url": "string",
  "raw_html_artifact_id": "artifact_id|null",

  "resolved_downloads": [
    { "format": "pdf|docx|html", "lang": "EN", "url": "string" }
  ],
  "selected_download": { "format": "pdf", "url": "string" },

  "first_seen_at": "ISO",
  "last_seen_at": "ISO"
}
```

**Индексы:**

- индекс doc_symbol, committee, provider
- индекс last_seen_at

---

### **8.8 tb_lookups**

Используется для lookup_sync (TreatyID/DocTypeID/countries), чтобы **не хардкодить**.

```
{
  "_id": "uuid",
  "provider": "tbinternet",
  "language": "EN",
  "fetched_at": "ISO",
  "source_url": "string",
  "html_sha256": "hex",
  "lookups": {
    "treaties": { "8": "CCPR", "1": "CAT" },
    "doc_type_categories": { "6": "Jurisprudence" },
    "doc_types": { "17": "Jurisprudence", "65": "Rules of procedure" },
    "countries": { "123": "Poland" }
  }
}
```

---

### **8.9 ingest_runs**

_id = run_id

```
{
  "_id": "run_id",
  "run_id": "uuid",
  "started_at": "ISO",
  "finished_at": "ISO",

  "parser": { "name": "un_ingest", "version": "0.1.0", "git_commit": "string" },
  "config_snapshot": { "...": "..." },
  "config_hash": "sha256(config_snapshot)",

  "stats": {
    "source_items_found": 0,
    "artifacts_downloaded": 0,
    "documents_upserted": 0,
    "doc_versions_upserted": 0,
    "segments_created": 0,
    "docs_needs_ocr": 0,
    "errors": 0
  }
}
```

---

### **8.10 errors**

_id можно ObjectId (допустимо), важно — structured payload:

```
{
  "_id": "ObjectId",
  "run_id": "uuid",
  "stage": "lookup_sync|crawl|resolve|download|extract|segment|mongo|validate",

  "doc_symbol": "string|null",
  "doc_id": "doc_id|null",
  "doc_version_id": "doc_version_id|null",

  "url": "string|null",
  "error_type": "HTTP_404|PARSE_HTML|DOWNLOAD|PARSE_PDF|EXTRACT_TEXT|MONGO_WRITE|VALIDATION",
  "message": "string",
  "traceback": "string|null",
  "retryable": true,
  "created_at": "ISO"
}
```

---

### **8.11 pageindex_trees (опционально, Stage 2/3)**

Если позже начнёте хранить дерево, структура должна поддерживать как:

- API‑дерево (узел имеет node_id, page_index, text, nodes) https://docs.pageindex.ai/endpoints 
- open‑source дерево (узел имеет start_index, end_index, node_id, summary, nodes) https://raw.githubusercontent.com/VectifyAI/PageIndex/main/README.md 

```
{
  "_id": "tree_id",
  "tree_id": "uuid",
  "doc_version_id": "doc_version_id",
  "provider": "pageindex_api|pageindex_oss|internal",
  "created_at": "ISO",
  "tree_format": "api_page_index|oss_start_end",
  "tree": [ { "node_id": "0006", "page_index": 21, "title": "...", "text": "...", "nodes": [] } ],
  "tree_hash": "sha256(canonical_json)"
}
```

---

## **9) Идемпотентность и версионирование**

### **9.1 Upsert‑ключи (строго)**

- artifacts: upsert по sha256 (и _id=artifact_id=uuidv5(sha256))
- documents: upsert по doc_key / _id=doc_id
    - **doc_type не участвует в ключе**
- document_versions: upsert по (doc_id, content_sha256) / _id=doc_version_id
- segments: upsert по (doc_version_id, page_index) / _id=segment_id
- source_items: upsert по _id=source_item_id

### **9.2 Что считается “новой версией документа”**

- если изменился content_sha256 канонического артефакта (PDF/converted_pdf) → новая document_version
- старые версии **не удаляем**
- documents.current_version_id обновляем на последнюю обработанную версию

---

## **10) Manifest и воспроизводимость**

### **10.1 Manifest JSONL (обязательный артефакт)**

Каждая строка manifest — “решение пайплайна” для одной версии документа.

```
{
  "provider": "tbinternet",
  "doc_symbol": "CRPD/C/33/D/92/2021",
  "language": "EN",

  "doc_id": "uuidv5",
  "doc_version_id": "uuidv5",

  "download_page_url": "https://...Download.aspx?...",
  "selected_format": "pdf",
  "download_file_url": "https://...file.pdf",

  "canonical_artifact_id": "uuidv5(sha256)",
  "sha256": "hex",
  "gcs_uri": "gs://.../source_pdf/<sha256>.pdf",

  "retrieved_at": "ISO"
}
```

### **10.2 reprocess**

Команда reprocess --from-manifest manifest.jsonl обязана:

- НЕ обращаться к сети
- подтянуть файлы из GCS (или локального кэша при наличии)
- пересобрать segments
- обновить Mongo идемпотентно

---

## **11) Валидация качества и целостности (validate)**

Команда validate должна включать:

### **11.1 Schema‑валидация**

- Pydantic/JSONSchema валидирует структуру документов для коллекций:
    - documents
    - document_versions
    - artifacts
    - segments
    - source_items

### **11.2 Инварианты (обязательные проверки)**

- segments.page_index:
    - начинается с 1
    - уникален в рамках (doc_version_id)
- Для PDF‑версий:
    - document_versions.page_count == count(segments where segment_type=page)
- Каждый segment:
    - имеет source_artifact_id, который существует в artifacts
    - text_sha256 == sha256(normalize_text_v1(text))
- Для needs_ocr=true:
    - segments отсутствуют (или помечены и исключаются из retrieval; выбрать 1 стратегию и зафиксировать)

### **11.3 Идемпотентность (автотест/проверка)**

- повторный запуск ingest на том же test_manifest **не создаёт** новых:
    - documents/document_versions/artifacts/segments
        
        (кроме ingest_runs)
        

### **11.4 (Опционально) Smoke‑тест PageIndex совместимости**

Если в окружении есть PageIndex API key:

- загрузить 1 PDF в PageIndex API
- получить tree (где узлы имеют page_index и node_id) https://docs.pageindex.ai/endpoints 
- проверить, что page_index узлов попадает в ваш диапазон segments.page_index той же версии
- этот тест подтверждает корректность page_index контракта

---

## **12) Логирование и сетевой этикет**

- Rate limit + retry/backoff для HTTP ошибок
- JSON‑логи: каждый лог включает:
    - run_id, stage, doc_symbol/doc_id/doc_version_id, url, duration_ms
- Ошибки не “роняют” весь прогон: пишутся в errors, пайплайн продолжает.

---

## **13) Deliverables / Definition of Done (DoD)**

### **13.1 Deliverables**

1. Python package jurisparse_un + CLI jurisparse
2. Реализация стадий: lookup_sync, crawl, resolve, download, extract, segment, load, validate, reprocess
3. Подключение к:
    - MongoDB Atlas (URI/config)
    - GCS bucket (service account)
4. Тестовый набор (tests/data/test_manifest.jsonl) ~20 документов:
    - 5 CCPR jurisprudence
    - 5 CAT jurisprudence
    - 5 CEDAW jurisprudence
    - 5 CRPD jurisprudence
    - +2–4 стандарта/процедуры/договор
5. README “как запустить” + пример отчёта validate

### **13.2 Критерии приёмки**

- ≥95% документов из test_manifest ingested (остальные — с понятной ошибкой в errors)
- Повторный запуск ingest на test_manifest:
    - не создаёт дубликаты сущностей (кроме ingest_runs)
- Для каждого ingested PDF:
    - segments по страницам существуют
    - page_index корректный (1‑based)
- Все artifacts имеют sha256 и gcs_uri
- Есть manifest и reprocess --from-manifest
- validate формирует отчёт: counts, needs_ocr, empty_pages, warnings

---

## **14) Пример config.yaml (скелет)**

```
mongo:
  uri: "mongodb+srv://<user>:<password>@<cluster-host>/"
  db: "un_corpus"

storage:
  provider: "gcs"
  bucket: "un-corpus-bucket"
  prefix_raw: "raw"
  prefix_derived: "derived"
  local_cache_dir: "./data_cache"

sources:
  tbinternet:
    tbsearch_url: "https://tbinternet.ohchr.org/_layouts/15/treatybodyexternal/TBSearch.aspx"
    download_url_template: "https://tbinternet.ohchr.org/_layouts/15/treatybodyexternal/Download.aspx?Lang={lang}&symbolno={symbol}"
    rate_limit_rps: 1.0
    timeout_sec: 30
    retries: 4

scope:
  committees: ["CCPR", "CAT", "CEDAW", "CRPD"]
  languages: ["EN"]
  doc_types:
    - jurisprudence_view
    - general_comment
    - general_recommendation
    - rules_of_procedure
    - working_methods
    - guidelines

extraction:
  pdf:
    primary: "pymupdf"
    fallback: "pdfplumber"
    min_total_chars: 1000

segmentation:
  pseudo_pages:
    min_chars: 1200
    target_chars: 2000
    max_chars: 2800
    hard_max_chars: 3800
```

---

# **Приложение A) TBSearch doc_type mapping (через лейблы)**

Рекомендуется отдельный конфиг tbsearch_doc_type_map.yaml:

```
rules:
  - tb_label: "Jurisprudence"
    internal_doc_type: "jurisprudence_view"

  - tb_label: "Follow-up on Jurisprudence"
    internal_doc_type: "jurisprudence_followup"

  - tb_label: "Follow-up to Views"
    internal_doc_type: "jurisprudence_followup"

  - tb_label: "Interim follow-up report on jurisprudence"
    internal_doc_type: "jurisprudence_followup"

  - tb_label: "General Comment/recommendation"
    internal_doc_type: "general_comment"
    overrides:
      CEDAW: "general_recommendation"

  - tb_label: "Rules of procedure"
    internal_doc_type: "rules_of_procedure"

  - tb_label: "Working methods"
    internal_doc_type: "working_methods"
```

Правило применения:

- сначала точное совпадение tb_label
- если не найдено — либо skip, либо unknown (в зависимости от режима), но **обязательно** писать warning/error.

---

# **Приложение B) lookup_sync требования (TBSearch справочники)**

lookup_sync обязан:

1. скачать HTML TBSearch (по языку)
2. извлечь:
    - treaties (committee)
    - doc_type_categories
    - doc_types
    - countries
3. сохранить в tb_lookups + html_sha256
4. использовать в последующих стадиях резолв ID по лейблам

Мини‑набор pytest‑тестов:

- после lookup_sync присутствуют ключевые labels (“Jurisprudence”, “General Comment/recommendation”, “Rules of procedure”, “Working methods”, CCPR/CAT/CEDAW/CRPD)
- если отсутствуют → fail‑fast (значит источник изменился)

---

# **Приложение C) Структура PageIndex дерева и цитирования (для ориентации Stage 2)**

- В PageIndex API tree‑результат содержит узлы с node_id, page_index, text, nodes и прямо отмечает, что page_index 1‑based. https://docs.pageindex.ai/endpoints 
- В open‑source PageIndex repo пример дерева использует start_index/end_index (диапазоны страниц) и node_id. https://raw.githubusercontent.com/VectifyAI/PageIndex/main/README.md 
- В Chat API/SDK есть режим inline‑цитат вида <doc=file.pdf;page=1> (по параметру enable_citations). https://docs.pageindex.ai/sdk/chat 










## Definition of Done (DoD) / Критерии готовности

## Ниже — чек‑лист “100% готовности” (Definition of Done) для Stage 1 (Parser/Ingest) по вашему TechSpec v1.1. Я не меняю требования, а раскладываю их на конкретные задачи/артефакты и на проверки, которые должны пройти, чтобы можно было честно сказать: TechSpec выполнен.

⸻

1) “Blocking” критерии готовности

Считать TechSpec выполненным нельзя, если хоть одно из ниже не выполнено:
	•	Трассируемость: любой созданный segment трассируется до doc_symbol + language + provider + doc_version_id + artifact_id + sha256 + gcs_uri.
	•	page_index строго 1-based везде (первая страница = 1), и это подтверждено в validate.
	•	Идемпотентность: повторный прогон на тех же входах не плодит documents/document_versions/artifacts/segments/source_items (кроме ingest_runs).
	•	Версионирование по контенту: новая document_version появляется только при изменении content_sha256 канонического артефакта.
	•	Никаких захардкоженных TreatyID/DocTypeID: lookup_sync реально парсит TBSearch и кладёт справочники в tb_lookups.
	•	reprocess не ходит в сеть, работает только по manifest + GCS/кэш.
	•	validate существует и реально валидирует схемы + инварианты (а не “заглушка”).

⸻

2) Артефакты, которые должны быть “готовы” как deliverables

2.1 Репозиторий/пакет/CLI
	•	Python package jurisparse_un (или эквивалентное имя пакета, но CLI — jurisparse).
	•	CLI jurisparse установлен как entrypoint (pip install → команда доступна).
	•	Команды CLI реализованы (минимум):
	•	lookup-sync
	•	crawl
	•	download --from-manifest <path>|--from-db
	•	extract --from-manifest <path>|--from-db
	•	ingest (end-to-end: lookup → crawl → resolve → download → extract → segment → load → validate)
	•	reprocess --from-manifest <path> (строго без сети)
	•	validate --run-id <id>|--doc-id <id>|--doc-version-id <id>
	•	export-manifest --run-id <id>
	•	Общие опции CLI реализованы и работают:
	•	-config config.yaml
	•	-run-id (если не задан — генерируется UUIDv4)
	•	-dry-run (без записи в Mongo; отчёт, скачивание/кэш — допустимо)
	•	-max-docs, --max-per-committee, --rate-limit-rps, --retries
	•	-log-json (структурные JSON-логи)

2.2 Конфиги/справочники
	•	config.yaml поддерживает структуру из TechSpec (mongo/storage/sources/scope/extraction/segmentation).
	•	Все URL-шаблоны источников берутся только из конфига (не из кода).
	•	Отдельный конфиг маппинга TBSearch → internal doc_type:
	•	tbsearch_doc_type_map.yaml (как в Appendix A) или эквивалент,
	•	правила “точное совпадение → overrides → иначе warning/error”.

2.3 Тестовые данные/документация
	•	tests/data/test_manifest.jsonl (обязательный артефакт): ~20 документов + 2–4 стандарта/процедуры/договор (как в Deliverables).
	•	README “как запустить” (локально/CI), включая:
	•	пример config.yaml,
	•	типовой прогон jurisparse ingest ...,
	•	прогон validate и пример отчёта,
	•	прогон reprocess --from-manifest ... и гарантия “no network”.
	•	Пример отчёта validate (как файл в репо или в README).

⸻

3) Реализация пайплайна по стадиям: задачи и ожидаемые выходы

Ниже — DoD на уровне “есть код + правильные side effects в Mongo/GCS”.

3.1 lookup_sync

Задачи:
	•	Скачать HTML TBSearch (по sources.tbinternet.tbsearch_url, язык из scope).
	•	Извлечь из HTML значения <select><option> (или эквивалентные структуры), минимум:
	•	treaties (комитеты/договора),
	•	doc_type_categories,
	•	doc_types,
	•	countries.
	•	Посчитать html_sha256.
	•	Записать в коллекцию tb_lookups документ со структурой из TechSpec (provider/language/fetched_at/source_url/html_sha256/lookups).
	•	Внутренние ID (TreatyID/DocTypeID) не фиксируются в коде как константы.

Проверки:
	•	Автотест/валидация: после lookup-sync в tb_lookups.lookups присутствуют ключевые labels:
	•	“Jurisprudence”
	•	“General Comment/recommendation”
	•	“Rules of procedure”
	•	“Working methods”
	•	“CCPR”, “CAT”, “CEDAW”, “CRPD”
	•	Если чего-то нет → fail-fast (как требует Appendix B).

⸻

3.2 crawl (discovery)

Задачи:
	•	По комитетам/типам/языкам из scope выполнить поиск в TBSearch.
	•	Сохранить сырой HTML выдачи как artifact в GCS (kind=crawl_html) и метаданные в artifacts.
	•	Создать/обновить source_items:
	•	doc_symbol, language, committee, doc_type_hint, download_page_url, search_url,
	•	raw_html_artifact_id,
	•	first_seen_at, last_seen_at.

Проверки:
	•	source_item_id детерминирован: sha1(provider|doc_symbol_norm|language|download_page_url).
	•	Повторный crawl обновляет last_seen_at, но не создаёт дубликаты source_items.

⸻

3.3 resolve

Задачи:
	•	Для каждого source_item открыть download_page_url.
	•	Собрать resolved_downloads[] (format/lang/url).
	•	Выбрать selected_download по правилу предпочтения: PDF > DOCX > HTML.
	•	Записать выбор в source_items.selected_download.

Проверки:
	•	Логика выбора формата тестируется: если PDF доступен → выбирается PDF.
	•	Если выбран не PDF (DOCX/HTML) — это явно отражено в selected_download.

⸻

3.4 download

Задачи:
	•	Скачать файл по selected_download.url.
	•	Посчитать sha256 файла.
	•	Загрузить в GCS по детерминированному пути:

gs://<bucket>/<prefix>/<provider>/<doc_symbol_sanitized>/<language>/<kind>/<sha256>.<ext>


	•	Создать/апдейтнуть artifacts:
	•	_id=artifact_id=uuidv5(sha256) и upsert по sha256,
	•	gcs_uri, bytes, format, kind, storage.bucket/object_name/generation/etag (если доступно),
	•	связи doc_id/doc_version_id (когда уже известны).
	•	Определить canonical_artifact_id:
	•	Обычно это source_pdf,
	•	Если DOCX/HTML и включена конверсия — создать converted_pdf (derived artifact) и сделать его каноническим.
	•	Локальный кэш поддерживается как не‑истина (рабочий).

Проверки:
	•	Детерминированность путей GCS: один и тот же файл → один и тот же gcs_uri.
	•	Повторная скачка того же файла не создаёт новый artifact (upsert по sha256).
	•	Все artifacts имеют sha256 и gcs_uri.

⸻

3.5 extract

Задачи:
	•	Для PDF: строго page-level extraction: 1 сегмент = 1 страница.
	•	Реализовать стек извлечения:
	•	primary: PyMuPDF,
	•	fallback: pdfplumber,
	•	(опционально) pypdf для метаданных.
	•	Реализовать normalize_text_v1 строго по правилам.
	•	Посчитать метрики качества:
	•	total_chars,
	•	empty_pages,
	•	warnings[].
	•	Реализовать needs_ocr правило:
	•	если total_chars < min_total_chars или слишком много пустых страниц → needs_ocr=true.
	•	Создать/обновить document_versions:
	•	_id=doc_version_id=uuidv5(doc_id|content_sha256),
	•	page_count, segment_count (после сегментации),
	•	canonical_artifact_id, artifact_ids,
	•	parser (name/version/git_commit/config_hash),
	•	retrieved_at, ingested_at.

Проверки:
	•	document_versions.extraction_quality.needs_ocr=true → сегменты не создаются (или другая стратегия, но тогда она должна быть зафиксирована; в TechSpec для MVP‑1 рекомендовано “не создавать”).
	•	Извлечение воспроизводимо: один и тот же PDF → те же page_count, стабильно нормализованный текст.

⸻

3.6 segment

Задачи:
	•	Для PDF: создать segments постранично, segment_type=page.
	•	page_index=i+1 (1‑based).
	•	segment_id строго по формату:
	•	{doc_version_id}:p0007 для PDF page,
	•	{doc_version_id}:s0007 для pseudo pages.
	•	text_sha256 == sha256(normalize_text_v1(text)).
	•	Заполнить обязательные поля segments:
	•	doc_id, doc_version_id, language
	•	page_index, page_label
	•	text, text_sha256, text_norm_version=v1
	•	char_count, token_estimate
	•	source_artifact_id
	•	citation.doc_symbol/language/page_index/page_label
	•	is_official_pagination и поля pagination_* (пусть даже index_fallback + низкая confidence для MVP‑1).
	•	Для DOCX/HTML: реализовать один из путей:
	•	Вариант A (предпочтительный): конверсия в PDF → стандартная page extraction (is_official_pagination=false)
	•	Вариант B: pseudo pages по правилам 7.3 (segment_type=pseudo_page, is_official_pagination=false)

Проверки:
	•	Уникальность page_index внутри doc_version_id.
	•	segments отсутствуют для needs_ocr=true.
	•	Префиксы p/s реально защищают от коллизий с PageIndex node_id вида "0007".

⸻

3.7 load (Mongo upsert + индексы)

Задачи:
	•	Коллекции созданы и используются:
documents, document_versions, artifacts, segments, source_items, tb_lookups, ingest_runs, errors.
	•	Upsert-ключи реализованы строго по TechSpec:
	•	artifacts: upsert по sha256 (_id=uuidv5(sha256))
	•	documents: upsert по doc_key / _id=doc_id (doc_type не участвует в ключе)
	•	document_versions: upsert по (doc_id, content_sha256) / _id=doc_version_id
	•	segments: upsert по (doc_version_id, page_index) / _id=segment_id
	•	source_items: upsert по _id=source_item_id
	•	documents.current_version_id обновляется на последнюю обработанную версию.
	•	Индексы созданы как минимум:
	•	documents: unique doc_key, индексы doc_symbol, (committee, doc_type, language)
	•	document_versions: unique (doc_id, content_sha256), индекс doc_id, индекс content_sha256
	•	artifacts: unique sha256, индексы (doc_id, doc_version_id), индекс kind
	•	segments: unique (doc_version_id, page_index), индексы doc_id, doc_version_id
	•	source_items: индексы (doc_symbol, committee, provider), индекс last_seen_at

Проверки:
	•	Повторный load не увеличивает количество сущностей (кроме ingest_runs).

⸻

3.8 validate

Задачи:
	•	Schema-валидация (Pydantic и/или JSONSchema) для:
	•	documents
	•	document_versions
	•	artifacts
	•	segments
	•	source_items
	•	Инварианты (обязательные):
	•	page_index начинается с 1 и уникален в рамках doc_version_id
	•	Для PDF-версий: document_versions.page_count == count(segments where segment_type=page)
	•	Каждый segment.source_artifact_id существует в artifacts
	•	segment.text_sha256 == sha256(normalize_text_v1(text))
	•	Для needs_ocr=true: сегменты отсутствуют (или выбранная стратегия строго соблюдается)
	•	Отчёт validate включает:
	•	counts,
	•	needs_ocr,
	•	empty_pages,
	•	warnings.

Проверки:
	•	validate падает (non‑zero exit code) при нарушении инвариантов.
	•	validate --run-id и выборочно --doc-id/--doc-version-id работают.

⸻

3.9 manifest + export-manifest + reprocess

Задачи:
	•	Manifest JSONL генерируется и соответствует схеме TechSpec (provider/doc_symbol/lang/doc_id/doc_version_id/download_page_url/selected_format/download_file_url/canonical_artifact_id/sha256/gcs_uri/retrieved_at).
	•	export-manifest --run-id ... создаёт manifest по фактическому прогону.
	•	reprocess --from-manifest manifest.jsonl:
	•	не обращается к сети,
	•	достаёт файлы из GCS (или локального кэша),
	•	пересобирает сегменты,
	•	обновляет Mongo идемпотентно.

Проверки:
	•	В автотесте/CI можно принудительно “запретить сеть” и подтвердить, что reprocess работает.
	•	Результат reprocess совпадает по ключевым свойствам (segment_id/page_index/text_sha256) с исходным прогоном на тех же артефактах.

⸻

4) Логирование, сетевой этикет, устойчивость

4.1 Логи
	•	-log-json включает структурные поля в каждом событии:
	•	run_id, stage, doc_symbol/doc_id/doc_version_id (когда применимо), url (когда применимо), duration_ms.
	•	Логи отражают важные переходы стадий и итоговые метрики.

4.2 Rate limit / retry / ошибки
	•	Rate limit + retry/backoff реализованы для HTTP (конфигурируемые rate_limit_rps, retries, timeout_sec).
	•	Ошибки не валят весь прогон:
	•	записываются в errors,
	•	пайплайн продолжает обработку следующего документа.
	•	errors пишутся структурировано по схеме (stage, ids, url, error_type, message, traceback, retryable, created_at).

⸻

5) Автотесты и проверки: минимум, который должен быть в CI

Ниже — практичный “набор тестов, без которых DoD не закрывается”, даже если часть интеграционных тестов помечена как slow.

5.1 Unit tests
	•	normalize_text_v1:
	•	\r\n → \n,
	•	nbsp → space,
	•	схлопывание пробелов/табов,
	•	trim строк,
	•	схлопывание 3+ пустых строк до 2,
	•	общий trim.
	•	Генерация ID:
	•	doc_id = uuidv5(provider|doc_symbol_norm|language)
	•	doc_version_id = uuidv5(doc_id|content_sha256)
	•	artifact_id = uuidv5(sha256)
	•	segment_id формат p/s и zero-pad
	•	source_item_id = sha1(provider|doc_symbol_norm|language|download_page_url)
	•	Генерация GCS path (детерминированность, sanitization doc_symbol).
	•	Парсер TBSearch HTML (на сохранённом fixture HTML):
	•	корректно вытаскивает treaties/doc_types/countries,
	•	наличие ключевых labels → pass, отсутствие → fail-fast.
	•	Логика выбора формата (PDF > DOCX > HTML).

5.2 Integration tests (можно условно: local docker / staging)
	•	Прогон ingest по tests/data/test_manifest.jsonl в тестовую Mongo + тестовый GCS (или эмулятор) заканчивается успешно.
	•	По итогам:
	•	≥95% документов из manifest ingested (остальные — с понятной записью в errors).
	•	Для каждого ingested PDF:
	•	есть segments по страницам,
	•	page_index 1‑based и непрерывный (если извлечение такое предполагает),
	•	document_versions.page_count == segment_count.
	•	Повторный прогон ingest на том же manifest:
	•	количество documents/document_versions/artifacts/segments/source_items не увеличивается,
	•	увеличивается только ingest_runs (и, возможно, обновляются updated_at/last_seen_at).
	•	reprocess --from-manifest:
	•	без сети,
	•	без дублей,
	•	даёт тот же набор segment_id/text_sha256.

5.3 Validate tests
	•	validate ловит:
	•	page_index=0 или дубликаты page_index,
	•	отсутствующий source_artifact_id,
	•	неверный text_sha256,
	•	несоответствие page_count и фактических сегментов,
	•	нарушение правила needs_ocr → segments отсутствуют.

⸻

6) Приёмочные шаги (ручной сценарий, который должен работать “из коробки”)

Это “ритуал”, после которого можно ставить галочку “готово”:
	1.	Запуск lookup-sync с вашим config.yaml → в Mongo есть tb_lookups, проверки ключевых labels проходят.
	2.	Запуск ingest на tests/data/test_manifest.jsonl (или на выгруженный export-manifest) → получаем run_id.
	3.	Запуск validate --run-id <run_id> → отчёт показывает:
	•	количество документов/версий/сегментов,
	•	needs_ocr список/количество,
	•	empty_pages,
	•	warnings,
	•	0 нарушений инвариантов.
	4.	Повторить ingest на том же manifest → 0 новых сущностей, кроме новой записи ingest_runs.
	5.	export-manifest --run-id <run_id> → manifest.jsonl получен.
	6.	“Сломать сеть” (или запустить в окружении без egress) и выполнить
reprocess --from-manifest manifest.jsonl → успешно, без дублей, validate снова зелёный.

⸻

7) Необязательные пункты (не блокируют “100% Stage 1”, но их стоит явно закрыть решением)

Эти пункты в TechSpec помечены как optional/в конце MVP‑1. Чтобы не было “подвешенного хвоста”, DoD лучше фиксировать так:
	•	jurisprudence_followup: либо
	•	реализовано полностью по тем же правилам (discovery→segments→validate),
	•	или
	•	явно отключено конфигом/скоупом и заведена задача/issue на следующую итерацию (без подмены требований).
	•	(Опционально) Smoke‑тест PageIndex совместимости при наличии ключа:
	•	загрузить 1 PDF,
	•	получить tree,
	•	убедиться, что page_index узлов лежит в диапазоне ваших segments.page_index.
	•	(Опционально) page_image artifacts: если делаете — должны быть детерминированные пути и ссылки из segments.page_image_artifact_id.

⸻

8) Финальная “галочка 100%” — короткая формула

TechSpec Stage 1 считается выполненным на 100%, когда:
	•	Все deliverables из раздела 13.1 реально есть,
	•	Все acceptance criteria из 13.2 выполняются на test_manifest,
	•	validate закрывает схемы + инварианты,
	•	ingest идемпотентен,
	•	manifest + reprocess обеспечивают воспроизводимость без сети,
	•	нет хардкода TBSearch ID (lookup_sync обязателен и протестирован),
	•	соблюдены PageIndex‑aligned инварианты (особенно 1‑based page_index и отсутствие коллизий ID).

