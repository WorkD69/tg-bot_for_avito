# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Telegram bot for accepting math/exact science task orders from clients. A **single bot** operates in three contexts simultaneously: client DMs, operator supergroup, and admin DM. Payments via Robokassa. Auction system assigns orders to operators.

## Commands

```bash
# Start all services (first run)
docker-compose up -d --build

# Apply DB migrations on first run / after model changes
docker-compose exec bot alembic upgrade head

# Create a new migration after changing models
docker-compose exec bot alembic revision --autogenerate -m "description"

# View logs
docker-compose logs -f bot

# Run locally without Docker (requires local PostgreSQL + Redis)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Check Python syntax (use full Python path on Windows)
"C:\Program Files\Python312\python.exe" -m py_compile app/path/to/file.py
```

## Architecture

### Operator Isolation (Critical Pattern)
The operator supergroup is **notifications-only**. All operator interactions happen in their **private DM** with the bot:
- Reply button pressed in group → bot sends response to operator's DM via `bot.send_message(operator.telegram_id, ...)`, does NOT reply in the group
- "Перейти к заявке" inline button in group → sends full order card to operator's DM
- This is the only correct way to isolate UX between operators in a Telegram group
- `IsOperatorGroup()` filter handles group callbacks **before** DM handlers — critical for routing

### Request Flow: New Order → Auction → Payment
```
Client FSM (4 steps: files → comment → deadline → budget)
  → AuctionService.start_auction()
      → posts "🆕 Новая заявка №{id}" + "Перейти к заявке" button to operator group
      → posts reply keyboard (Свободные заявки / Мои заявки / История) to operator group
      → APScheduler job scheduled at auction_end_at (now + 120 min)
  → Operator bids via "Могу взять" in their DM
      → bid == budget → immediate auto-assign
      → bid != budget → wait for 120 min or admin /endauction
  → Operator assigned → PaymentService.generate_link() → client receives Robokassa URL
      → If ROBOKASSA_LOGIN is empty → admin gets manual /confirmpayment {id} prompt
  → Robokassa POST /payment/robokassa → order status "В работе" → notify both parties
  → Admin can also /confirmpayment {id} to manually confirm payment
```

### Session-per-update Middleware
`app/bot/middlewares/db_session.py` opens an `AsyncSession` for every Telegram update, commits on success, rolls back on exception. Handlers receive `session: AsyncSession` via aiogram's dependency injection (it's in `data[]`). **Never** share sessions across async boundaries.

### APScheduler and Sessions
`_auto_close_auction` in `auction_service.py` is called by APScheduler — it **must open its own** `AsyncSessionFactory()` context. Never pass a session object to a scheduled job; sessions are not serializable and the job fires in a different async context.

### Router Priority
`dispatcher.py` includes routers in this order: `admin_router` → `operator_router` → `client_router`. Most specific role first. Handlers use `IsAdmin`, `IsOperator`, `IsClient`, `IsOperatorGroup` filters to gate access.

**Critical routing rule**: `action="view"` callback is handled by `IsOperatorGroup()` in `operator/menu.py` (sends card to DM). In `client/order_list.py` the list uses `action="client_view"` — separate action to avoid operator handler intercepting client taps.

### Robokassa Callback
The `/payment/robokassa` endpoint receives **Form POST** (not JSON). Response **must** be the exact string `f"OK{InvId}"`. Signature verification: `MD5(f"{OutSum}:{InvId}:{pass2}").upper()`.

### "Файлы" View (Inline Edit Pattern)
When operator clicks "Файлы" on an order card in their DM: the bot **edits the same message** (`edit_message_text`) to show the files header + "← Назад" button, then sends the actual files as a reply to that message. "← Назад" edits back to the card. Never send files as a new standalone message.

### place_bid — operator_id vs telegram_id
`place_bid(operator_id)` receives a **DB user id** (foreign key in bids table), NOT telegram_id. Before calling `bot.send_message()` always resolve: `operator_user = await UserRepo(session).get_by_id(operator_id)` and use `operator_user.telegram_id`. These are different numbers.

### Session Cache After Mutations
After creating/updating a bid, call `session.expire_all()` before reloading the order with relations — otherwise SQLAlchemy returns stale cached data and the new bid won't appear in the card.

## Key Env Variables

| Variable | Purpose |
|----------|---------|
| `OPERATOR_GROUP_ID` | Negative chat_id of the operator group (regular group: `-XXXXXXX`, NOT `-100XXXXXXX`) |
| `ADMIN_TELEGRAM_ID` | Single admin's Telegram user_id |
| `ROBOKASSA_IS_TEST` | Set `true` during development |
| `REDIS_URL` | FSM state storage — required for persistence across restarts |
| `ROBOKASSA_LOGIN` | If empty — payment flow uses manual /confirmpayment bypass |

APScheduler uses a **sync** PostgreSQL connection string (`postgresql://...`, not `postgresql+asyncpg://`) for its jobstore — separate from the async SQLAlchemy engine.

## Order Statuses (Python enum names stored in DB)

SQLAlchemy stores **Python enum names** (not `.value`). DB values: `pending`, `awaiting_payment`, `in_progress`, `completed`, `cancelled`. Display `.value` in UI (Russian strings). Never hardcode Russian status strings in queries — always use `OrderStatus.pending` etc.

`pending` → `awaiting_payment` → `in_progress` → `completed` / `cancelled`

## Role System

- `IsClient` filter passes for **all** roles (client, operator, admin) — operators and admins can create orders and use client UI simultaneously
- `IsOperator` filter passes for operator **and** admin
- `UserRegisterMiddleware` auto-promotes user to `admin` role if their `telegram_id == settings.admin_telegram_id`
- This means the admin can test the full client flow without a separate account

## Order Creation Rules

- Files are **optional** — client can send `/done` without any files
- Deadline is validated against **Moscow time** (UTC+3) — cannot be in the past
- Error message for invalid/past deadline: `"❌ Некорректный формат даты\nВведите дату в формате ДД.ММ.ГГГГ, не предшествующую текущей"`
- After budget is entered, client receives: `"🎉 Ваша заявка создана! Ожидайте, пока операторы возьмут её в работу\n\n📋 Статус заявки вы можете посмотреть в разделе «Текущие заявки»"`
- Max 5 active orders per client (all statuses except completed/cancelled)
- Comment stored with UTC timestamp: `"{ISO_TS}|{text}"`, multiple comments separated by `"\n---\n"`

## Client Order Card Rules

- Client sees **different card** than operator — no bids, no operator names: `format_client_card(order)`
- Active orders: 4 buttons — "Добавить комментарий", "Добавить файлы", "Отменить заявку" (only if pending), "← Назад"
- "← Назад" deletes the card message and shows the orders list again
- Cancelling shows confirmation keyboard; "← Нет, назад" deletes the confirmation message
- History (completed/cancelled): `format_client_history_card(order)` — shows created + updated dates
- Completed: buttons "📂 Решение", "💬 Задать вопрос", "⭐ Оставить отзыв", "← Назад"
- Cancelled: only "← Назад" button

## Client Notifications to Operator Group

When client modifies an active order, operator group receives notification + "Перейти к заявке" inline button:
- Added comment: `"✏️ К заявке №{id} добавлен комментарий клиентом"`
- Added files: `"📎 К заявке №{id} добавлены файлы клиентом"`
- Cancelled: `"❌ Заявка №{id} отменена клиентом"`

## Review Flow

1. Client taps "⭐ Оставить отзыв" → bot shows star rating keyboard (1–5 stars, `RatingCB`)
2. Client picks rating → bot asks for text
3. Text submitted → admin DM receives review + Одобрить/Отклонить keyboard
4. Rating stored in `reviews.rating` column (migration 0003)

## Budget Display

Always show budget as integer when no fractional part: `1500 ₽` not `1500.00 ₽`. Use `_money()` helper in `formatters.py`.

## Message Style Rules

All bot messages must follow these rules:
- **No trailing period** on the last sentence of any message
- **Relevant emoji** at the start of each message or sentence
- Example one-liner: `"✅ Ставка принята"`
- Example multi-line: `"❌ Некорректный формат даты\nВведите дату в формате ДД.ММ.ГГГГ, не предшествующую текущей"`

## Migrations

| File | Contents |
|------|----------|
| `0001_initial.py` | All tables, created manually. Enum values are Python names (`pending`, not Russian). |
| `0002_fix_columns.py` | Renames `file_id` → `telegram_file_id` in order_files/solution_files; drops stale `file_type` from solution_files and `rating` from reviews |
| `0003_add_review_rating.py` | Adds `rating INTEGER NOT NULL DEFAULT 5` to reviews |

**Do not delete migration files** — they are the source of truth for DB schema. Always create a new revision on top, never modify existing ones.

## Admin Commands (private DM only)

- `/addoperator @username` or `/addoperator {telegram_id}` — sets `user.role = operator`
- `/deleteoperator @username` — reverts `user.role` back to `client`
- `/operators` — list all operators with telegram_id
- `/admins` — list all admins with telegram_id
- `/endauction {order_id}` — forces auction close, assigns lowest bidder (ties broken by earliest bid)
- `/confirmpayment {order_id}` — manually confirm payment, moves order to `in_progress`
- `/stats` — order counts by status
- `/commands` — shows all available admin commands

## Auction Tie-Breaking

`BidRepo.get_min_bid()` orders by `(amount ASC, created_at ASC)` — if two operators bid same amount, earliest bid wins.

## Known Solved Issues

- **`column solution_files.telegram_file_id does not exist`** — migration 0001 used `file_id`, models expected `telegram_file_id`. Fixed by migration 0002.
- **Operator "Перейти к заявке" did nothing** — operator must write `/start` to bot in DM first; otherwise Telegram blocks bot from initiating chat. Handler now catches exception and shows alert.
- **Client list buttons intercepted by operator handler** — client order list uses `action="client_view"`, operator uses `action="view"`. Never merge these.
- **`place_bid` sent to wrong ID** — was passing DB user.id to `bot.send_message` instead of `telegram_id`.
- **First bid not showing** — SQLAlchemy session cache not invalidated. Fixed with `session.expire_all()` before reload.
- **OPERATOR_GROUP_ID format** — regular groups use `-XXXXXXX` (no `-100` prefix). Only supergroups use `-100XXXXXXX`.
