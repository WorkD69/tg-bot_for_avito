# Testing Guide

## Стек

| Библиотека | Версия | Роль |
|-----------|--------|------|
| pytest | ≥ 8.0 | фреймворк |
| pytest-asyncio | ≥ 0.23 | поддержка async/await |
| pytest-mock | ≥ 3.12 | MockFixture (не используется напрямую, но полезен) |

Установка:
```bash
pip install -r requirements-dev.txt
```

---

## Структура

```
tests/
  conftest.py (root)           # устанавливает env vars до любых импортов app
  unit/
    test_payment_service.py    # PaymentService: verify_callback, generate_link
    test_formatters.py         # _money(), _parse_comment_ts()
  integration/
    conftest.py                # async DB engine + savepoint isolation + mock_bot
    test_auction.py            # AuctionService: bid selection, idempotency, auto-assign
    test_order_transitions.py  # OrderRepo: state machine + price negotiation
```

---

## Запуск

### Только юнит-тесты (без БД, без Docker)

```bash
# из корня проекта
pytest tests/unit/ -v
```

Работают без `.env`, без запущенного Docker, без сети. ~1 секунда.

### Все тесты (включая интеграционные)

В `docker-compose.yml` порт 5432 проброшен на `127.0.0.1:5432` — DB доступна
с хоста без дополнительных шагов, пока Docker запущен.

**Шаг 1** — убедиться что DB запущена:
```bash
docker-compose up -d db
```

**Шаг 2** — создать тестовую БД (один раз):
```bash
docker exec tg-bot_for_avito-db-1 psql -U botuser -d botdb -c "CREATE DATABASE tg_bot_test;"
```

**Шаг 3** — запустить все тесты:
```bash
TEST_DATABASE_URL="postgresql+asyncpg://botuser:botpass@localhost:5432/tg_bot_test" pytest tests/ -v
```

Если тестовая БД недоступна, интеграционные тесты **автоматически пропускаются** (skip), юнит-тесты работают всегда.

### Запуск конкретного файла

```bash
pytest tests/unit/test_payment_service.py -v
TEST_DATABASE_URL="postgresql+asyncpg://botuser:botpass@localhost:5432/tg_bot_test" pytest tests/integration/test_auction.py -v
```

### С другими кредами БД

```bash
TEST_DATABASE_URL=postgresql+asyncpg://myuser:mypass@localhost:5432/tg_bot_test pytest tests/ -v
```

### Заметка про event loop (pytest-asyncio ≥ 0.23)

Integration tests используют module-scoped DB engine. Тесты помечены
`pytestmark = pytest.mark.asyncio(loop_scope="module")` — это обязательно,
иначе asyncpg падает с `RuntimeError: Future attached to a different loop`.

---

## Что покрыто

### Блок A — Аукцион / выбор оператора (`test_auction.py`)

| Сценарий | Тест |
|---------|------|
| Нет ставок → заявка отменена системой | `TestCloseAuctionNoBids` |
| `cancelled_by == "system"` | `test_cancelled_by_is_system` |
| Минимальная ставка побеждает | `test_lowest_bid_wins` |
| При равных ставках — более ранняя побеждает | `test_tie_break_earliest_bid_wins` |
| Ставка == budget → авто-назначение | `test_bid_equal_to_budget_triggers_assign` |
| Ставка < budget → аукцион продолжается | `test_bid_below_budget_does_not_auto_assign` |
| Закрытие уже закрытой заявки → `already_closed` (no-op) | `TestCloseAuctionIdempotency` |
| Повторное закрытие не меняет оператора | `test_already_closed_does_not_change_operator` |
| Несуществующая заявка → `not_found` | `test_not_found_returns_not_found` |
| Нельзя ставить на свою заявку | `test_operator_cannot_bid_on_own_order` |
| `payment_revision` инкрементируется при назначении | `test_payment_revision_incremented` |
| `payment_invoice_id` версионирован | `test_payment_invoice_id_versioned` |

### Блок B — Оплата / Robokassa (`test_payment_service.py`, `test_order_transitions.py`)

| Сценарий | Тест |
|---------|------|
| Верная подпись verify_callback → True | `test_valid_signature_accepted` |
| Неверная подпись → False | `test_wrong_signature_rejected` |
| Подпись в нижнем регистре → True (case-insensitive) | `test_lowercase_signature_accepted` |
| Другая сумма → False | `test_amount_mismatch_rejects` |
| Другой InvId → False | `test_inv_id_mismatch_rejects` |
| Другой pass2 → False | `test_wrong_pass2_rejects` |
| generate_link содержит правильные параметры | `TestGenerateLink` |
| Подпись в URL корректна | `test_signature_is_correct` |
| IsTest=1 в тестовом режиме | `test_test_mode_adds_is_test` |
| Разные ревизии → разные URL | `test_different_revisions_produce_different_urls` |
| Устаревший InvId не находится в БД после смены revision | `test_stale_inv_id_order_not_found_by_invoice` |
| Логика stale-detection из payment.py | `test_stale_revision_detected_by_comparison` |

### Блок C — Переговоры по цене (`test_order_transitions.py`)

| Сценарий | Тест |
|---------|------|
| `update_agreed_price` меняет `payment_amount` | `test_payment_amount_updated` |
| `payment_revision` инкрементируется | `test_payment_revision_incremented` |
| `payment_invoice_id` обновляется по формуле | `test_invoice_id_reflects_new_revision` |
| `payment_received_at` сбрасывается | `test_payment_received_at_reset` |
| `payment_confirmed_at` сбрасывается | `test_payment_confirmed_at_reset` |
| Два изменения цены → revision вырастает дважды | `test_two_price_changes_revision_increments_twice` |

### Блок D — Статусы и переходы (`test_order_transitions.py`)

| Переход | Тесты |
|---------|-------|
| pending → awaiting_payment | `TestAssignOperator` |
| awaiting_payment → in_progress | `TestConfirmPayment` |
| → cancelled + `cancelled_by` | `TestCancel` |

### Блок E — Форматирование (`test_formatters.py`)

| Функция | Покрыто |
|---------|---------|
| `_money(Decimal)` | целые, дробные, None, 0, без trailing zeros |
| `_parse_comment_ts(str)` | valid ISO, plain text, bad timestamp, whitespace, extra pipes |

---

## Что НЕ покрыто и почему

| Область | Почему нет тестов |
|---------|-----------------|
| Handlers aiogram (роутеры) | Требуют full aiogram dispatcher + FSM — сложный mocking, низкий ROI для pre-deploy |
| Robokassa webhook endpoint (HTTP) | Нужен FastAPI TestClient + полная сессия — интеграция с реальным Robokassa невозможна без боевых ключей |
| APScheduler (`_auto_close_auction`) | Тестируется косвенно через `close_auction` которую он вызывает |
| Redis / FSM-состояния | Требует запущенный Redis; поведение проверяется вручную (LOCAL_QA_CHECKLIST.md Блок 11) |
| `_validate_config()` | Проверяется вручную: `docker-compose logs bot` при старте |
| Backup service | Отдельный Docker-контейнер; проверяется вручную при деплое |
| Файлы / решения оператора | Требует Telegram file_id — только с реальным ботом |

---

## Минимальный набор перед серьёзными изменениями

```bash
# 1. Юнит — всегда, не требует ничего
pytest tests/unit/ -v

# 2. Интеграция — если меняешь auction_service, order_repo, payment_service
docker-compose up -d db
pytest tests/integration/ -v
```

Если все тесты зелёные → можно делать коммит. Если красные — чини до коммита.

---

## Изоляция тестов

Интеграционные тесты используют **savepoint-based isolation**:
- Каждый тест работает внутри транзакции, которая откатывается по завершении
- `session.commit()` внутри теста = release savepoint (данные видны в сессии, но не в других соединениях)
- После теста: outer ROLLBACK — БД чистая
- Эффект: тесты полностью независимы, БД не нужно чистить вручную

---

## Добавление нового теста

**Юнит (нет БД):**
```python
# tests/unit/test_mymodule.py
def test_my_function():
    from app.bot.formatters import _money
    assert _money(42) == "42 ₽"
```

**Интеграционный (нужна БД):**
```python
# tests/integration/test_my_feature.py
from tests.integration.conftest import make_user, make_order

class TestMyFeature:
    async def test_something(self, session, mock_bot):
        user = await make_user(session, 99001, "Test User")
        order = await make_order(session, user.id)
        await session.commit()
        # ... test logic ...
```
