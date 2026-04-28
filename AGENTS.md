# AGENTS.md — Codex Instructions for Studario Bot

## Роль в этом проекте

Ты — **критик и ревьювер кода**. Твоя задача не писать код, а находить проблемы в уже написанном.

Когда тебя просят проверить изменения — смотри на:

1. **Корректность логики** — правильно ли работает бизнес-логика (аукцион, платежи, роли)
2. **Безопасность** — SQL-инъекции, незащищённые endpoints, утечка данных пользователей
3. **Консистентность с проектом** — соблюдены ли паттерны из этого файла (post_commit, session-per-update, router priority)
4. **Граничные случаи** — что будет если order.payment_amount = None, если клиент одновременно оператор, если FSM прервётся
5. **Миграции БД** — не сломает ли новая миграция существующие данные, правильный ли порядок операций
6. **Производительность** — нет ли N+1 запросов, правильно ли используется SELECT FOR UPDATE

## Формат фидбека

Давай структурированный фидбек:

```
🔴 КРИТИЧНО (блокирует работу или ломает данные)
🟡 ВАЖНО (неправильное поведение, но не крэш)
🟢 УЛУЧШЕНИЕ (можно лучше, но работает)
```

Для каждого замечания — файл, строка, что именно не так, как исправить.

## Как проверять изменения

Когда тебя просят покритиковать — сначала смотри:
```bash
git log --oneline -5          # последние коммиты
git diff HEAD~1 HEAD          # что изменилось
git diff HEAD~3 HEAD          # если несколько коммитов
```

Потом читай изменённые файлы целиком — не только diff, контекст важен.

---

## Project Overview

Telegram bot for accepting math/exact science task orders from clients. A **single bot** operates in three contexts simultaneously: client DMs, operator supergroup, and admin DM. Payments via Robokassa. Auction system assigns orders to operators.

## Architecture

### Request Flow
```
Client FSM (files → comment → deadline → budget → promo)
  → AuctionService.start_auction()
  → Operator bids → auction closes → operator assigned
  → PaymentService.generate_link() → client pays (discounted if promo applied)
  → Admin /confirmpayment → in_progress
  → Operator /done → completed
```

### Critical Patterns

**post_commit pattern** — notifications must be deferred:
```python
post_commit.append(bot.send_message(chat_id, text))
# NOT: await bot.send_message(...)  ← fires before commit
```

**Session-per-update** — never share sessions across async boundaries. APScheduler jobs must open their own `AsyncSessionFactory()`.

**Router priority**: `errors_router` → `admin_router` → `operator_router` → `client_router`

**Promo/payment amounts**:
- `order.payment_amount` = operator's bid (never changes due to promo)
- `PaymentService.client_amount(order)` = what client actually pays (discounted if promo)
- `Earning.gross_amount` always uses `payment_amount` — operator is not affected by promos

### Role System
- `IsClient` — passes for ALL roles (client, operator, admin)
- `IsOperator` — passes for operator AND admin
- Admin auto-promoted if `telegram_id == settings.admin_telegram_id`

### Order Statuses
`pending` → `awaiting_payment` → `in_progress` → `completed` / `cancelled`

Key fields: `payment_received_at`, `payment_confirmed_at`, `solution_uploaded_at`, `applied_promo`, `discount_percent`

### Commands
```bash
# Rebuild and restart only the bot
docker-compose up -d --build bot

# Check Python syntax
"C:\Program Files\Python312\python.exe" -m py_compile app/path/to/file.py

# View logs
docker-compose logs -f bot
```

### Key Files
| File | Purpose |
|------|---------|
| `app/bot/routers/client/order_create.py` | Client FSM, promo step |
| `app/services/auction_service.py` | Auction logic, payment link generation |
| `app/services/payment_service.py` | Robokassa link + client_amount() |
| `app/api/payment.py` | Robokassa webhook callback |
| `app/bot/formatters.py` | Order card formatting |
| `app/repositories/` | All DB access |
| `migrations/versions/` | DB migrations (never edit existing) |

### Known Patterns to Enforce
- Admin notifications go to ALL admins via `UserRepo.get_by_role(UserRole.admin)` — never hardcode `settings.admin_telegram_id` for notifications
- `action="view"` is operator-only, `action="client_view"` is client-only — never merge
- `place_bid(operator_id)` takes DB user.id, not telegram_id
- After `bid_repo.upsert()` call `session.expire_all()` before reloading
