# Analytics & Funnel

## Архитектура: почему не отдельная таблица событий

Вместо отдельной `analytics_events` таблицы используются **существующие источники данных**:

| Что считаем | Откуда |
|------------|--------|
| Зарегистрированные пользователи | `users.created_at` |
| Заявок создано | `order_logs WHERE action='created'` |
| Заявок с хоть одной ставкой | `DISTINCT order_id WHERE action='bid_placed'` |
| Назначен оператор | `DISTINCT order_id WHERE action='operator_assigned'` |
| Оплата подтверждена | `DISTINCT order_id WHERE action='payment_confirmed'` |
| Завершено | `DISTINCT order_id WHERE action='completed'` |
| Отменено | `DISTINCT order_id WHERE action='cancelled'` |
| Спор открыт | `DISTINCT order_id WHERE action='dispute_opened'` |
| Отзывов оставлено | `reviews.created_at` |
| Денежные метрики | `operator_earnings` |
| Источники пользователей | `users.source` |

Почему **не** `analytics_events`:
- `order_logs` уже содержит все нужные события lifecycle
- Дополнительная таблица = дополнительный write на каждое событие без новых данных
- "Начал создание заявки" (FSM entry) не имеет `order_id` в момент клика → требует отдельной events-таблицы, но эта метрика добавляется позже по необходимости

---

## Воронка

Этапы (в порядке пути клиента):

```
👤 Зарегистрировались          ← users.created_at
  ↓
📋 Создали заявку              ← order_logs action='created'
  ↓ (конверсия из созданных)
🤝 Заявка получила ставку      ← order_logs action='bid_placed' (DISTINCT order_id)
  ↓
👷 Назначен оператор           ← order_logs action='operator_assigned'
  ↓
💳 Оплата подтверждена         ← order_logs action='payment_confirmed'
  ↓
✅ Заявка завершена            ← order_logs action='completed'

Потери:
❌ Заявка отменена             ← order_logs action='cancelled'
⚖️ Открыт спор                ← order_logs action='dispute_opened'
⭐ Отзыв оставлен             ← reviews.created_at
```

**Completion rate** = завершено / создано × 100%

---

## Атрибуция источников (Attribution)

### Как работает

Источник записывается **один раз при регистрации** пользователя из payload deeplink `/start`.

| Deeplink | Записывается в `users.source` |
|---------|-------------------------------|
| `t.me/YourBot?start=avito` | `avito` |
| `t.me/YourBot?start=tg_channel` | `tg_channel` |
| `t.me/YourBot?start=abc123` (неизвестный) | `direct` |
| `/start` без payload | `unknown` |

### Известные источники (`KNOWN_SOURCES`)

```python
KNOWN_SOURCES = frozenset({"avito", "tg_channel", "direct"})
```

Добавить новый источник — добавить строку в `KNOWN_SOURCES` и обновить deeplink в рекламных материалах.

### Пример deeplink для Авито

```
https://t.me/YourBot?start=avito
```

Поместите эту ссылку в профиль Авито — все пользователи, пришедшие оттуда, получат `source='avito'`.

---

## Команды администратора

### `/stats` — бизнес-сводка

```
/stats
```

Показывает:
- Количество заявок по статусам
- Число пользователей + заявок + завершённых + отменённых
- Completion rate (завершено / создано)
- Финансовая картина: gross, к выплате, выплачено
- Предупреждение если есть замороженные выплаты (on_hold)
- Разбивка отмен по инициатору (клиент / оператор / администратор)

### `/funnelstats [период]` — воронка с конверсиями

```
/funnelstats            — всё время
/funnelstats 7d         — последние 7 дней
/funnelstats 30d        — последние 30 дней
/funnelstats today      — сегодня
```

Показывает каждый этап воронки с:
- Абсолютным числом
- Конверсией из предыдущего этапа (в %)

### `/sourcestats [период]` — источники пользователей

```
/sourcestats
/sourcestats 7d
/sourcestats 30d
```

Показывает:
- Число пользователей по каждому источнику
- ASCII-бар для визуализации доли
- Подсказку как настроить deeplinks

---

## Метрики бизнеса

| Метрика | Команда | Откуда |
|---------|---------|--------|
| Всего пользователей | `/stats`, `/funnelstats` | `users` |
| Заявок всего / по статусу | `/stats` | `orders` |
| Conversion rate | `/funnelstats` | `order_logs` |
| Completion rate | `/stats`, `/funnelstats` | `order_logs` |
| Отмены по инициатору | `/stats` | `orders.cancelled_by` |
| Споры | `/stats`, `/funnelstats` | `order_logs` |
| Gross выручка | `/stats` | `operator_earnings` |
| Pending к выплате | `/stats` | `operator_earnings` |
| Выплачено | `/stats` | `operator_earnings` |
| Замороженные выплаты | `/stats` → `/disputes` | `operator_earnings` |
| Источники пользователей | `/sourcestats` | `users.source` |
| Отзывы | `/funnelstats` | `reviews` |

---

## Периоды и фильтры

Все аналитические команды поддерживают период:

| Токен | Смысл |
|-------|-------|
| `today` | С полуночи UTC сегодня |
| `7d` | Последние 7 дней |
| `30d` | Последние 30 дней |
| *(не указан)* | Всё время |

---

## Пример выходных данных

### `/funnelstats 30d`

```
📈 Воронка (30 дней)

👤 Зарегистрировались: 47
📋 Создали заявку: 31  ↳ 66.0% от пред.
🤝 Получили ставку: 28  ↳ 90.3% от пред.
👷 Назначен оператор: 26  ↳ 92.9% от пред.
💳 Оплата подтверждена: 24  ↳ 92.3% от пред.
✅ Завершено: 22  ↳ 91.7% от пред.

— Потери / выходы —
❌ Отменено заявок: 5
   (16.1% от созданных)
⚖️ Открыто споров: 1
⭐ Оставлено отзывов: 14
   (63.6% от завершённых)

🎯 Completion rate (создана → завершена): 71.0%
```

### `/sourcestats`

```
🔗 Источники пользователей (всё время) — всего: 89

████████░░ Авито: 52 (58.4%)
███░░░░░░░ Telegram-канал: 24 (27.0%)
█░░░░░░░░░ Прямой (deeplink): 7 (7.9%)
░░░░░░░░░░ Неизвестно: 6 (6.7%)

Как настроить атрибуцию:
  Deeplink: t.me/YourBot?start=avito
  Доступные источники: avito, tg_channel, direct
```

---

## Миграция

| Файл | Что добавляет |
|------|--------------|
| `0009_user_source.py` | Колонка `source VARCHAR(32) DEFAULT 'unknown'` в таблице `users` |

Применить:
```bash
docker-compose exec bot alembic upgrade head
```

---

## Тесты

| Файл | Что тестирует |
|------|--------------|
| `tests/unit/test_analytics.py` | Парсинг source из deeplink, KNOWN_SOURCES константа, _parse_period, _conv |
| `tests/integration/test_analytics_repo.py` | Funnel counts, period filter, source breakdown, business summary против реальной БД |

Запустить:
```bash
# Unit (без БД)
python3 -m pytest tests/unit/test_analytics.py -v

# Integration (нужна БД)
TEST_DATABASE_URL="postgresql+asyncpg://botuser:botpass@localhost:5432/tg_bot_test" \
  python3 -m pytest tests/integration/test_analytics_repo.py -v
```

---

## Что намеренно НЕ реализовано

| Возможность | Почему |
|------------|--------|
| "Начал создание заявки" (FSM entry) | Нет order_id в этой точке; write на каждый клик избыточен при текущем масштабе |
| Когортный анализ | Избыточно для MVP |
| Автообновление / дашборд | Команды достаточны — дашборд добавить при реальной потребности |
| Мульти-источники на заявку | Источник на уровне пользователя достаточен |
| BI-экспорт / CSV | Используйте SQL-запрос из `PAYOUTS_AND_OPERATOR_STATS.md` как шаблон |
