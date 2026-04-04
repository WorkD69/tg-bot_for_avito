# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Telegram bot for accepting math/exact science task orders from clients. A **single bot** operates in three contexts simultaneously: client DMs, operator supergroup, and admin DM. Payments via Robokassa. Auction system assigns orders to operators.

## Commands

```bash
# Start all services (first run)
docker-compose up -d --build

# Full reset — wipe DB and restart from scratch (order IDs reset to 1)
docker-compose down -v && docker-compose up -d --build

# Apply DB migrations on first run / after model changes
docker-compose exec bot alembic upgrade head

# Create a new migration after changing models
docker-compose exec bot alembic revision --autogenerate -m "description"

# View logs
docker-compose logs -f bot

# Check Python syntax (use full Python path on Windows)
"C:\Program Files\Python312\python.exe" -m py_compile app/path/to/file.py

# Restart bot only (after code changes, no DB changes)
docker-compose restart bot
```

## Architecture

### Operator Isolation (Critical Pattern)
The operator supergroup is **notifications-only**. All operator interactions happen in their **private DM** with the bot:
- Reply button pressed in group → bot sends response to operator's DM via `bot.send_message(operator.telegram_id, ...)`, does NOT reply in the group
- "Перейти к заявке" inline button in group → sends full order card to operator's DM
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
      → If ROBOKASSA_LOGIN is empty → operator sends requisites manually, client presses "Я оплатил"
  → Robokassa POST /payment/robokassa → sets payment_received_at → notifies admin
  → Admin /confirmpayment {id} → moves to in_progress → notify both parties
  → Operator uploads solution files + optional text comment → order auto-completes → client notified
```

### Session-per-update Middleware
`app/bot/middlewares/db_session.py` opens an `AsyncSession` for every Telegram update, commits on success, rolls back on exception. Handlers receive `session: AsyncSession` via aiogram DI. **Never** share sessions across async boundaries.

### post_commit Pattern
Handlers receive `post_commit: list` via DI. Append notification coroutines there — they fire **after** the session commits. This guarantees notifications are never sent for rolled-back transactions:
```python
post_commit.append(bot.send_message(chat_id, text))
```

### APScheduler and Sessions
`_auto_close_auction` in `auction_service.py` is called by APScheduler — it **must open its own** `AsyncSessionFactory()` context. Never pass a session object to a scheduled job. APScheduler jobstore uses a **sync** connection string (`postgresql://...`, not `postgresql+asyncpg://`).

### Router Priority
`dispatcher.py` includes routers in this order: `admin_router` → `operator_router` → `client_router`. Most specific role first.

**Critical routing rule**: `action="view"` callback is handled by `IsOperatorGroup()` in `operator/menu.py` (sends card to DM). In `client/order_list.py` the list uses `action="client_view"` — separate action to avoid operator handler intercepting client taps. Never merge these two actions.

### Robokassa Callback
The `/payment/robokassa` endpoint receives **Form POST** (not JSON). Response **must** be the exact string `f"OK{InvId}"`. Signature verification: `MD5(f"{OutSum}:{InvId}:{pass2}").upper()`.

### "Файлы" View (Inline Edit Pattern)
When operator clicks "Файлы": bot **edits the same message** (`edit_message_text`) to show files header + "← Назад" button, then sends files as replies. "← Назад" edits back to the card. Never send files as a new standalone message.

### place_bid — operator_id vs telegram_id
`place_bid(operator_id)` receives a **DB user id** (FK in bids table), NOT telegram_id. Always resolve: `operator_user = await UserRepo(session).get_by_id(operator_id)` then use `operator_user.telegram_id`.

### Session Cache After Mutations
After `bid_repo.upsert()`, call `session.expire_all()` before reloading the order with relations — otherwise SQLAlchemy returns stale cached data.

### format_order_card — is_admin flag
`format_order_card(order, is_admin=False)` — operators see `client.full_name` only; admins see `@username`. Always pass `is_admin=(user.role == UserRole.admin)` when calling from operator handlers.

### FSM Guard Pattern
Before setting a new FSM state in operator callback handlers, check if `SolutionStates.waiting_files` is active to prevent silently cancelling an in-progress solution upload:
```python
current_state = await state.get_state()
if current_state == SolutionStates.waiting_files:
    await callback.answer("⚠️ Загрузка решения в процессе — сначала завершите /done", show_alert=True)
    return
```
Applied in: `start_operator_message`, `start_note`, `start_bid`.

## Key Env Variables

| Variable | Purpose |
|----------|---------|
| `OPERATOR_GROUP_ID` | Negative chat_id of the operator group (regular group: `-XXXXXXX`, NOT `-100XXXXXXX`) |
| `ADMIN_TELEGRAM_ID` | Single admin's Telegram user_id |
| `ROBOKASSA_IS_TEST` | Set `true` during development |
| `REDIS_URL` | FSM state storage — required for persistence across restarts |
| `ROBOKASSA_LOGIN` | If empty — payment flow uses manual requisites + "Я оплатил" button |

## Order Statuses (Python enum names stored in DB)

SQLAlchemy stores **Python enum names** (not `.value`). DB values: `pending`, `awaiting_payment`, `in_progress`, `completed`, `cancelled`. Display `.value` in UI (Russian strings).

`pending` → `awaiting_payment` → `in_progress` → `completed` / `cancelled`

Extra state tracked via fields (not new statuses):
- `payment_received_at` — Robokassa callback or client pressed "Я оплатил"
- `payment_confirmed_at` — admin ran `/confirmpayment`
- `solution_uploaded_at` — operator uploaded solution files; **triggers auto-complete**
- `payment_revision` — incremented on price change, invalidates old Robokassa links
- `cancelled_by` — `"client"` | `"operator"` | `"system"` | `"admin"`

**Order auto-completes** when operator sends `/done` in solution upload FSM — no manual "Завершить заявку" step needed. The old `action="complete"` handler has been removed.

## Role System

- `IsClient` filter passes for **all** roles (client, operator, admin) — operators and admins can create orders and use client UI simultaneously
- `IsOperator` filter passes for operator **and** admin
- `UserRegisterMiddleware` auto-promotes user to `admin` role if their `telegram_id == settings.admin_telegram_id`

## Solution Upload FSM

Operator flow: "Отправить решение" → `SolutionStates.waiting_files`
- Photo/document messages → added to `files[]` in FSM state
- Text messages (not `/done`) → saved as `comment` in FSM state (last one wins)
- `/done` → saves `SolutionFile` rows + saves comment as `Message(direction=operator_to_client)` if present → auto-completes order → notifies client with comment

The comment appears in: client's notification message AND in `format_client_history_card` history section (as `🔧 Оператор: {text}`).

## Client Order Card Rules

- Client sees `format_client_card(order)` — no bids, no operator names
- `"← Назад"` buttons use `try/except` around `message.delete()` — messages older than 48h cannot be deleted
- `"💬 Задать вопрос"` (action=`client_msg`) — allowed on `completed` orders, blocked only on `cancelled`
- `"↩️ Ответить оператору"` button appears in operator-to-client messages; blocked if order is `cancelled`
- After viewing solution files, a `"← История заявок"` button is sent as a final navigation message

## Client Notifications to Operator Group

All events send the group a notification + "Перейти к заявке" inline button:
- New order, client added comment, client added files, client cancelled, operator cancelled

## Review Flow

1. "⭐ Оставить отзыв" → `rating_kb` (1–5 stars, `RatingCB`) → text input
2. Saved with `status=pending`; admin DM receives + Одобрить/Отклонить keyboard
3. After submitting: `client_main_kb()` sent only if `user.role == UserRole.client` — operators/admins testing as client do not get their keyboard replaced
4. "Отзывы о нас" shows `client.full_name` (never `@username`) for approved reviews

## Price Negotiation (awaiting_payment)

Client: "Обсудить цену" → `NegotiationStates.waiting_text` → message sent to operator with `negot_operator_kb`

Operator options:
- **Accept** (`negot_accept`) — applies `proposed_amount` from `NegotCB`, increments `payment_revision`
- **Counter** (`negot_counter` → `CounterOfferStates.waiting_amount`) — format: `"3700"` or `"3700 Comment text"`. Updates `payment_amount`, increments `payment_revision`. Comment delivered to client in both Robokassa and manual modes.
- **Cancel** (`negot_cancel_order`) — cancels order, notifies client AND operator group

## Budget Display

Always use `_money()` helper from `formatters.py`: `1500 ₽` not `1500.00 ₽`.

## Message Style Rules

- **No trailing period** on the last sentence
- **Relevant emoji** at the start of each message/sentence

## Migrations

| File | Contents |
|------|----------|
| `0001_initial.py` | All tables. `messagedirection` enum: `client_to_operator`/`operator_to_client` |
| `0002_fix_columns.py` | `file_id` → `telegram_file_id` in order_files/solution_files |
| `0003_add_review_rating.py` | `rating INTEGER NOT NULL DEFAULT 5` in reviews |
| `0004_refactor.py` | `order_logs` table; `reviews.status` enum (replaces `is_approved`); `reviews.moderated_by/at`; unique on `(order_id,client_id)` reviews and `(order_id,operator_id)` bids; `solution_files.file_type`; order lifecycle fields (`payment_received_at`, `payment_confirmed_at`, `solution_uploaded_at`, `payment_revision`, `cancelled_by`) |
| `0005_schema_fixes.py` | `bids.amount` precision `Numeric(12,2)`; `messages.text NOT NULL` |

**0004 migration pitfall**: `orderlogaction` enum must be created via raw `op.execute("CREATE TYPE orderlogaction AS ENUM (...)")` — SQLAlchemy's `Enum.create()` + `create_type=False` does not work reliably with asyncpg dialect.

**messagedirection enum**: Use `MessageDirection.client_to_operator` and `MessageDirection.operator_to_client`. Old aliases `client_to_op`/`op_to_client` do not exist.

**Never modify existing migration files** — always create a new revision on top.

## Server-Side Guards

- Bid: cannot bid on own order (`client_id == operator_id`)
- Messaging: cannot message yourself; client messaging blocked if order is `cancelled`
- `/deleteoperator`: blocked if operator has active assigned orders
- All state-changing actions use `get_by_id_for_update()` (SELECT FOR UPDATE)
- Auction close is idempotent: checks `status == pending` under lock

## Admin Commands (private DM only)

`/addoperator`, `/deleteoperator`, `/operators`, `/admins`, `/endauction {id}`, `/confirmpayment {id}`, `/stats`, `/completeorder {id}`, `/commands`

## Auction Tie-Breaking

`BidRepo.get_min_bid()` orders by `(amount ASC, created_at ASC)` — lowest amount wins; earliest bid breaks ties.

## Known Solved Issues

- **`column solution_files.telegram_file_id does not exist`** — migration 0001 used `file_id`. Fixed by 0002.
- **Operator "Перейти к заявке" did nothing** — operator must write `/start` to bot in DM first. Handler catches exception and shows alert.
- **Client list buttons intercepted by operator handler** — client list uses `action="client_view"`, operator uses `action="view"`. Never merge.
- **`place_bid` sent to wrong ID** — was passing DB `user.id` to `bot.send_message` instead of `telegram_id`.
- **First bid not showing** — SQLAlchemy session cache. Fixed with `session.expire_all()` before reload.
- **OPERATOR_GROUP_ID format** — regular groups: `-XXXXXXX` (no `-100` prefix). Only supergroups use `-100XXXXXXX`.
- **UnboundLocalError in solution_done** — duplicate `from app.db.models.order import OrderStatus` inside function body made Python treat it as local, causing NameError before the import line. Remove any in-function duplicate imports.
