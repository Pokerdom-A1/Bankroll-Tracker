# Банкролл-трекер для Pokerdom

Однофайловый консольный банкролл-трекер для Pokerdom: импорт кэш-сессий и турниров (CSV/XLSX), хранение в SQLite, расчёт метрик (ROI, ITM, winrate bb/100), учёт депозитов/выводов, кэшбек по уровням, графики, экспорт отчётов.

---

## ⚡️ Возможности

* 📥 Импорт **cash** и **MTT** из CSV / XLSX (с авто-маппингом колонок и `--dry-run`).
* 💾 Локальное хранилище **SQLite** (один файл в корне).
* 📊 Метрики:

  * Cash: **профит**, **winrate (bb/100)**, рейк, руки.
  * MTT: **ROI**, **ITM%**, профит, призы/инвестиции.
* 🧮 Учёт банкролла: депозиты, выводы, бонусы, корректировки.
* 💸 Конфигурируемый **кэшбек** по уровням.
* 📈 Графики (PNG): кумулятивный профит, кривая банкролла, winrate по периодам, ROI по месяцам.
* 📤 Экспорт: CSV/JSON + `summary.json`.
* 🧰 Гибкий `--map` для нестандартных выгрузок, локализация RU/EN, подробные логи.

---

## 🧱 Архитектура

* Один файл: `pokerdom.py`.
* База данных: `.pokerdom_tracker.sqlite` (в корне репозитория).
* Таблицы: `cash_sessions`, `tournaments`, `bankroll_tx`, `imports`, `config`.

---

## 🔧 Установка

1. Убедитесь, что установлен **Python 3.10+**.
2. Клонируйте репозиторий:

```bash
git clone https://github.com/Pokerdom-A1/Bankroll-Tracker.git
cd Bankroll-Tracker
```

3. (Опционально) создайте виртуальное окружение:

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate
```

4. Установите опциональные пакеты, если нужны графики/чтение XLSX:

```bash
pip install matplotlib openpyxl
```

> Скрипт работает **без** этих пакетов; просто будут недоступны соответствующие фичи.

---

## 🚀 Быстрый старт

Инициализируйте БД и дефолтный конфиг:

```bash
python pokerdom.py init
```

Импортируйте кэш-сессии:

```bash
python pokerdom.py import-cash --file data/cash.csv --room Pokerdom --currency RUB
```

Импортируйте турниры:

```bash
python pokerdom.py import-mtt --file data/mtt.csv --room Pokerdom --currency RUB
```

Посмотрите сводку:

```bash
python pokerdom.py stats --room Pokerdom --currency RUB
```

Сделайте график кумулятивного профита:

```bash
python pokerdom.py graph --type profit_cum --out out/
```

---

## 📚 Форматы данных

### Cash — CSV (минимум)

```csv
date,stakes_bb,hands,buyin,cashout,currency,rake,notes
2025-09-01,5,860,3000,3250,RUB,120,"NL10 Fast"
```

### MTT — CSV (минимум)

```csv
date,buyin,fee,prize,position,field_size,currency,rake,notes
2025-09-01,900,100,0,312,1200,RUB,100,"PKO Turbo"
2025-09-02,1800,200,10000,1,950,RUB,200,"Sunday Main"
```

> Если `buyin` уже включает `fee`, поле `fee` можно оставить пустым — скрипт определит сам.

### XLSX

* Поддерживается при наличии `openpyxl`.
* Читается **активный лист**, первая строка — заголовки.

---

## 🔀 Маппинг колонок (`--map`)

При импорте можно переименовать колонки через JSON:

```bash
python pokerdom.py import-cash --file cash_ru.csv \
  --map '{"date":"Дата","stakes_bb":"Блайнд","hands":"Руки","buyin":"Вход","cashout":"Выход","rake":"Рейк","currency":"Валюта"}'
```

Скрипт пытается сопоставить поля **автоматически** (RU/EN синонимы). `--map` имеет приоритет.

---

## 🧾 Команды CLI

```
python pokerdom.py <command> [OPTIONS]
```

### База

* `init` — создать БД и конфиг.

### Импорт

* `import-cash --file PATH [--map JSON] [--room Pokerdom] [--currency RUB] [--dry-run]`
* `import-mtt  --file PATH [--map JSON] [--room Pokerdom] [--currency RUB] [--dry-run]`

### Ручной ввод

* `add-cash --date YYYY-MM-DD --stakes-bb F --hands N --buyin F --cashout F [--currency RUB] [--rake F] [--notes ""] [--room Pokerdom]`
* `add-mtt  --date YYYY-MM-DD --buyin F --prize F [--fee F] [--position N] [--field-size N] [--currency RUB] [--rake F] [--notes ""] [--room Pokerdom]`
* `add-tx   --date YYYY-MM-DD --amount F --type {deposit,withdraw,bonus,adjustment} [--currency RUB] [--notes ""]`

### Статистика и графики

* `stats [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--room Pokerdom] [--currency RUB]`
* `graph --type {profit_cum,bankroll,cash_winrate,mtt_roi} --out DIR [--group {day,month}]`

### Экспорт

* `export --what {cash,mtt,tx,all} --format {csv,json} --out DIR`

### Прогноз

* `forecast --target 100000 [--from YYYY-MM-DD] [--to YYYY-MM-DD]`

### Кэшбек

* `rakeback [--from YYYY-MM-DD] [--to YYYY-MM-DD]`

### Конфиг

* `config [--set key=value]... [--get key]`

Глобальные флаги:

* `--db PATH` — путь к базе (по умолчанию `.pokerdom_tracker.sqlite`)
* `-v/--verbose` — подробные логи

---

## 📈 Метрики и формулы

**Cash**

* Профит: `cashout - buyin`
* Winrate (bb/100): агрегируется по всем сессиям с учётом рук и блайнда.
* Рейк/100 рук: `(sum(rake) / sum(hands)) * 100`

**MTT**

* ROI: `(sum(prize) - sum(buyin + fee)) / sum(buyin + fee)`
* ITM%: `count(prize>0)/count(all)*100`

**Банкролл**

* `депозиты/выводы/бонусы/коррекции` + профит из Cash + MTT (с учётом фильтров).

---

## 💸 Кэшбек (конфиг)

Пример уровней (по умолчанию):

```json
{
  "rakeback": {
    "currency": "RUB",
    "tiers": [
      {"min_rake": 0,     "rate": 0.02},
      {"min_rake": 5000,  "rate": 0.04},
      {"min_rake": 20000, "rate": 0.06},
      {"min_rake": 50000, "rate": 0.08}
    ],
    "reset_period": "weekly"
  }
}
```

Изменение через CLI:

```bash
python pokerdom.py config --set rakeback='{"currency":"RUB","tiers":[{"min_rake":0,"rate":0.03}]}'
```

---

## 🌍 Валюты

* У каждой записи есть поле `currency`.
* По умолчанию считаются записи **одной** валюты (`--currency`).
* Конвертация через `config --set rates='{"USD->RUB":98.5,"EUR->RUB":105}'` (опционально).

---

## 🧪 Примеры сценариев

```bash
# 1) Инициализация
python pokerdom.py init

# 2) Импорт кэша (прогон без записи)
python pokerdom.py import-cash --file samples/cash.csv --dry-run

# 3) Импорт турниров
python pokerdom.py import-mtt --file samples/mtt.xlsx

# 4) Депозит + сводка
python pokerdom.py add-tx --date 2025-09-01 --amount 10000 --type deposit
python pokerdom.py stats --currency RUB

# 5) Графики
python pokerdom.py graph --type bankroll --out out/
python pokerdom.py graph --type cash_winrate --group month --out out/

# 6) Кэшбек за неделю
python pokerdom.py rakeback --from 2025-09-01 --to 2025-09-07

# 7) Экспорт всего
python pokerdom.py export --what all --format csv --out export/
```

---

## 🧩 Пример структуры репозитория

```
Bankroll-Tracker/
├─ pokerdom.py
├─ README.md
├─ samples/
│  ├─ cash.csv
│  └─ mtt.xlsx
├─ out/                # графики (gitignore)
├─ export/             # экспорт (gitignore)
└─ .gitignore
```

Рекомендуемый `.gitignore`:

```
.pokerdom_tracker.sqlite
out/
export/
*.png
*.log
.venv/
__pycache__/
```

---

## 🔐 Приватность и легальность

* Скрипт **не** взаимодействует с клиентом или процессом Pokerdom, не кликает и не играет за вас.
* Работает **только** с вашими файлами выгрузок/CSV/XLSX или ручным вводом.
* Соблюдайте правила и условия использования вашего покер-рума.

---

## 🧰 Отладка и частые вопросы

* **`openpyxl` не установлен** — сохраните Excel как CSV или установите пакет: `pip install openpyxl`.
* **Нет графиков** — установите `matplotlib`: `pip install matplotlib`.
* **Проблемы с кодировкой CSV** — используйте UTF-8/UTF-8-SIG.
* **Дубликаты при импорте** — задействована базовая дедупликация; при сомнении используйте `--dry-run`.
* **Логи в файл** — `python pokerdom.py config --set log_file=".pokerdom_tracker.log"`

---

## 🤝 Вклад

PR и issue приветствуются.
Пожалуйста, соблюдайте стиль кода (PEP 8) и добавляйте примеры данных в `samples/`.
