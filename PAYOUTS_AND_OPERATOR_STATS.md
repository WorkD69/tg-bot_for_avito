# Operator Stats & Payouts

## Как устроена модель

### Таблица `operator_earnings`

Одна строка на каждую завершённую заявку. Создаётся автоматически при переходе заявки в статус `completed`.

| Поле | Тип | Описание |
|------|-----|---------|
| `order_id` | FK (unique) | Связь с заявкой — один earning на заявку |
| `operator_id` | FK | Оператор, выполнивший заявку |
| `gross_amount` | Decimal | Что заплатил клиент (= `order.payment_amount` в момент завершения) |
| `operator_share` | Decimal | Что получает оператор (`gross * payout_percent / 100`) |
| `payout_percent` | Decimal | Процент выплаты на момент расчёта (хранится для аудита) |
| `status` | Enum | `pending` / `on_hold` / `adjusted` / `paid` / `excluded` |
| `paid_at` | DateTime | Когда отмечено как выплачено |
| `paid_by_id` | FK | Кто отметил выплату |
| `frozen_at` | DateTime | Когда заморожено (on_hold) |
| `frozen_by_id` | FK | Кто заморозил |
| `note` | Text | Причина заморозки / корректировки / примечание к выплате |

---

## Статусы выплат и безопасность

| Статус | Иконка | Что означает | В `/payouts`? | В `/markpaid`? |
|--------|--------|-------------|:-------------:|:--------------:|
| `pending` | ⏳ | Ожидает выплаты (дефолт) | ✅ Да | ✅ Да |
| `on_hold` | 🔒 | Заморожено — требует ручного решения | ❌ **Нет** | ❌ **Нет** |
| `adjusted` | ✏️ | Сумма скорректирована вручную — к выплате | ✅ Да | ✅ Да |
| `paid` | ✅ | Выплачено оператору | ❌ Нет | ❌ Нет |
| `excluded` | 🚫 | Навсегда исключено из выплат | ❌ Нет | ❌ Нет |

### Константа `PAYABLE_STATUSES`

```python
PAYABLE_STATUSES = (EarningStatus.pending, EarningStatus.adjusted)
```

Это ворота безопасности системы. Только эти два статуса считаются безопасными к выплате.
`on_hold` **не входит** — намеренно. Тест `TestPayableStatusesSafetyContract` в `tests/unit/test_earning_repo.py` защищает этот инвариант.

---

## Как считаются выплаты

```
operator_share = gross_amount × OPERATOR_PAYOUT_PERCENT / 100
```

Округление — ROUND_HALF_UP до 2 знаков. В `.env`:
```env
OPERATOR_PAYOUT_PERCENT=80.0   # дефолт: 80%
```

Процент хранится в каждой строке earning — смена конфига не пересчитывает старые записи.

---

## Когда создаётся earning

1. **Оператор загрузил решение `/done`** → автоматическое завершение (`notes.py`)
2. **Админ принудительно завершил `/completeorder {id}`** → (`commands.py`)

`EarningRepo.create_for_order()` — идемпотентно, двойное создание невозможно.
Earning создаётся только если у заявки есть `payment_amount` и `operator_id`.

---

## Команды администратора

### `/operatorstats` — статистика

```
/operatorstats                    — сводка по всем операторам
/operatorstats @vasya             — детали одного оператора (всё время)
/operatorstats @vasya 7d          — последние 7 дней
/operatorstats @vasya 30d         — последние 30 дней
/operatorstats 123456789          — по telegram_id
```

Показывает: активных заявок, завершённых, средний чек, gross, pending, paid.
⚠️ Если у оператора есть `on_hold` записи — сразу видно в сводке.

### `/payouts` — выплаты к обработке

```
/payouts                — все операторы с payable выплатами
/payouts @vasya         — payable выплаты конкретного оператора
```

**Включает:** `pending` + `adjusted` (иконка ✏️).
**Не включает:** `on_hold` — они показываются отдельно ниже с предупреждением.

### `/markpaid` — отметить выплату

```
/markpaid @vasya
/markpaid @vasya Сбер *1234, 05.04.2026
/markpaid 123456789 USDT tx:abc123
```

Платит **все payable** (pending + adjusted) для оператора.
`on_hold` записи **никогда не затрагиваются**.
После выплаты, если у оператора остались `on_hold` записи — показывается предупреждение.

### `/disputes` — замороженные выплаты

```
/disputes
```

Список всех `on_hold` earnings по всем операторам. Показывает: order_id, оператора, сумму, кто и когда заморозил, причину. Для каждой — quick-actions:
```
→ /unfreezeearning {order_id}
→ /excludeearning {order_id} причина
```

### `/freezeearning` — заморозить выплату

```
/freezeearning 42
/freezeearning 42 Клиент открыл спор
```

Переводит earning заявки №42 в `on_hold`. После этого он НЕ попадёт в `/payouts` и `/markpaid`.

### `/unfreezeearning` — разморозить

```
/unfreezeearning 42
/unfreezeearning 42 Спор решён в пользу оператора
```

Переводит `on_hold` → `pending`. Earning снова становится payable.

### `/excludeearning` — исключить навсегда

```
/excludeearning 42
/excludeearning 42 Клиент получил возврат
```

Переводит в `excluded`. Навсегда. Нельзя отменить (только вручную через DB).
Нельзя применить к уже `paid` earning.

### `/adjustearning` — скорректировать сумму

```
/adjustearning 42 1200
/adjustearning 42 1200 Штраф за нарушение дедлайна
```

Меняет `operator_share` на 1200. `gross_amount` не меняется (для аудита).
Если earning был `on_hold` — остаётся `on_hold`. Разморозить отдельно.
После корректировки earning попадает в `/payouts` как `adjusted` (✏️).

---

## Спорные заявки — правильный процесс

### Спор открыт, заявка ещё в работе

Не завершайте заявку до урегулирования — earning не создастся.

### Спор открыт после завершения (earning уже создан)

```
# 1. Заморозить немедленно
/freezeearning 42 Клиент подал спор — ждём разбирательства

# Затем:
# Вариант А — спор решён в пользу оператора
/unfreezeearning 42 Спор закрыт — оператор прав
# → earning снова pending, попадёт в следующую выплату

# Вариант Б — спор решён против оператора
/excludeearning 42 Клиент прав, возврат
# → earning excluded навсегда

# Вариант В — частичная выплата
/adjustearning 42 500 Компромисс
/unfreezeearning 42 Скорректировано
# → earning pending с суммой 500
```

### Почему спорная заявка не может случайно попасть в выплату

1. `on_hold` ∉ `PAYABLE_STATUSES` — константа в коде
2. `EarningRepo.get_all_payable()` фильтрует по `PAYABLE_STATUSES` — SQL-уровень
3. `EarningRepo.mark_paid()` бросает `ValueError` если статус `on_hold` — Python-уровень
4. `mark_paid_all_payable()` вызывает `get_payable()` который не вернёт `on_hold`
5. Тесты `TestMarkPaidSafety` верифицируют это поведение против реальной БД

---

## Ежемесячный процесс выплат

```
1. Посмотреть замороженные:
   /disputes                         (если пусто — хорошо)

2. Посмотреть все к выплате:
   /payouts

3. Детали по каждому оператору:
   /payouts @vasya

4. Перевести деньги (банк / USDT / другой способ)

5. Отметить выплату:
   /markpaid @vasya Сбер *1234, 2000 ₽, 01.05.2026

6. Проверить что pending = 0:
   /operatorstats @vasya
```

---

## Экспорт данных (SQL)

```sql
SELECT
    oe.id,
    u.full_name AS operator,
    u.telegram_id,
    oe.order_id,
    oe.gross_amount,
    oe.operator_share,
    oe.payout_percent,
    oe.status,
    oe.paid_at,
    oe.frozen_at,
    oe.note,
    oe.created_at
FROM operator_earnings oe
JOIN users u ON u.id = oe.operator_id
ORDER BY oe.created_at DESC;
```

---

## Миграции

| Миграция | Что добавляет |
|----------|--------------|
| `0007_operator_earnings` | Таблица `operator_earnings`, enum `earningstatus` |
| `0008_earningstatus_on_hold` | Значение `on_hold` + колонки `frozen_at`, `frozen_by_id` |

Применить:
```bash
docker-compose exec bot alembic upgrade head
```

---

## Что намеренно НЕ реализовано

| Возможность | Почему |
|------------|--------|
| Авто-заморозка при `dispute_opened` лог-событии | `dispute_opened` лог сейчас нигде не пишется; замораживать нужно вручную |
| Батчи выплат | Избыточно — `/markpaid` достаточно для текущего масштаба |
| Уведомление оператора о выплате | Добавить при необходимости |
| Мульти-валюта / налоги | Не входит в MVP |
