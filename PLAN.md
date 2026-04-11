# TG-Bot для заявок (математика и точные науки)

## Context
Проект реализован. Телеграм-бот для приёма заявок от клиентов на решение задач по математике и точным наукам. Единый бот работает в трёх контекстах: личка клиента, группа операторов, личка администратора. Оплата через Robokassa. Аукцион между операторами за 120 минут.

---

## Tech Stack
- **Python 3.11+**
- **aiogram 3.x** — Telegram-бот (async, FSM из коробки)
- **PostgreSQL** — основная БД
- **SQLAlchemy 2.x async** + **Alembic** — ORM + миграции
- **FastAPI + uvicorn** — HTTP-сервер (TG webhook + Robokassa callback)
- **APScheduler** (AsyncIOScheduler + SQLAlchemy jobstore) — таймер аукциона
- **Docker + docker-compose** — деплой
- **RedisStorage** (aiogram FSM) — сохранение FSM-состояний между рестартами
- **pydantic-settings** — конфиг из `.env`

---

## Структура проекта

```
tg-bot_for_avito/
├── app/
│   ├── main.py                    # FastAPI app factory + lifespan
│   ├── config.py                  # Settings (pydantic-settings)
│   ├── bot/
│   │   ├── instance.py            # Bot singleton
│   │   ├── dispatcher.py          # Dispatcher, middleware chain, router includes
│   │   ├── routers/
│   │   │   ├── client/
│   │   │   │   ├── menu.py        # /start, главное меню
│   │   │   │   ├── order_create.py # FSM: создание заявки (4 шага)
│   │   │   │   ├── order_list.py  # Текущие / история + все card actions
│   │   │   │   ├── reviews.py     # Отзывы + FSM рейтинг→текст
│   │   │   │   └── messaging.py   # Прокси клиент→оператор
│   │   │   ├── operator/
│   │   │   │   ├── menu.py        # Группа: "Перейти к заявке", reply-кнопки → DM
│   │   │   │   ├── order_list.py  # Файлы (edit) / ← Назад
│   │   │   │   ├── bid.py         # FSM: ставка "Могу взять"
│   │   │   │   ├── messaging.py   # Прокси оператор→клиент + переговоры по цене
│   │   │   │   └── notes.py       # FSM: заметка + загрузка решения с комментарием
│   │   │   └── admin/
│   │   │       ├── commands.py    # /addoperator /deleteoperator /operators /admins
│   │   │       │                  # /stats /endauction /confirmpayment /completeorder /commands
│   │   │       └── reviews.py     # Модерация отзывов (одобрить/отклонить)
│   │   │   └── errors.py          # Aiogram error handler → алерт в личку админа
│   │   ├── filters/
│   │   │   ├── is_admin.py        # telegram_id == settings.admin_telegram_id
│   │   │   ├── is_operator.py     # user.role in (operator, admin)
│   │   │   ├── is_client.py       # user.role in (client, operator, admin) — все роли
│   │   │   └── is_group.py        # chat.id == operator_group_id
│   │   ├── keyboards/
│   │   │   ├── client_reply.py    # 4 reply-кнопки клиента
│   │   │   ├── operator_reply.py  # 3 reply-кнопки (в группе)
│   │   │   ├── order_inline.py    # Все inline-клавиатуры для карточек заявок
│   │   │   ├── admin_inline.py    # Одобрить/Отклонить отзыв, rating_kb (звёзды)
│   │   │   └── callbacks.py       # OrderCB, NegotCB, ReviewCB, ReviewListCB, RatingCB
│   │   ├── states/
│   │   │   ├── order_create.py    # waiting_files → comment → deadline → budget
│   │   │   ├── bid.py             # waiting_price
│   │   │   └── note.py            # NoteStates, SolutionStates, MessagingStates,
│   │   │                          # ClientMessagingStates, OrderEditStates,
│   │   │                          # NegotiationStates, CounterOfferStates,
│   │   │                          # RequisitesStates, ReviewStates
│   │   ├── formatters.py          # format_order_card(is_admin), format_client_card,
│   │   │                          # format_client_history_card, _money(), _history_lines()
│   │   └── middlewares/
│   │       ├── db_session.py      # AsyncSession per update (commit/rollback)
│   │       └── user_register.py   # Авторегистрация, inject user в data[], auto-promote admin
│   ├── db/
│   │   ├── base.py                # DeclarativeBase + TimestampMixin
│   │   ├── engine.py              # create_async_engine + async_sessionmaker
│   │   └── models/
│   │       ├── user.py            # UserRole: client / operator / admin
│   │       ├── order.py           # OrderStatus enum + доп. поля жизненного цикла
│   │       ├── order_file.py      # telegram_file_id, file_type
│   │       ├── solution_file.py   # telegram_file_id, file_type
│   │       ├── order_log.py       # OrderLogAction enum, actor_id, detail
│   │       ├── bid.py             # unique(order_id, operator_id) — upsert через INSERT ON CONFLICT
│   │       ├── review.py          # rating INT, status ENUM(pending/approved/rejected)
│   │       ├── message.py         # MessageDirection: client_to_operator / operator_to_client
│   │       └── operator_note.py
│   ├── repositories/
│   │   ├── user_repo.py           # get_by_role() для /operators /admins
│   │   ├── order_repo.py          # get_by_id_for_update, assign_operator, update_agreed_price
│   │   ├── bid_repo.py            # upsert, get_min_bid (ORDER BY amount, created_at), get_losers
│   │   ├── review_repo.py         # create(rating=), get_approved, set_status
│   │   └── message_repo.py
│   ├── utils/
│   │   └── telegram_log_handler.py  # Logging handler → ERROR+ записи в личку админа
│   ├── services/
│   │   ├── auction_service.py     # start_auction, place_bid, close_auction, recover_overdue
│   │   └── payment_service.py     # Robokassa: generate_link + verify_callback
│   ├── scheduler/
│   │   └── setup.py               # AsyncIOScheduler + SQLAlchemy jobstore (sync URL!)
│   └── api/
│       ├── webhook.py             # POST /telegram/webhook
│       └── payment.py             # POST /payment/robokassa
├── migrations/
│   ├── env.py                     # Alembic async env
│   └── versions/
│       ├── 0001_initial.py        # Все таблицы (создана вручную)
│       ├── 0002_fix_columns.py    # file_id → telegram_file_id, drop stale columns
│       ├── 0003_add_review_rating.py # rating column в reviews
│       ├── 0004_refactor.py       # order_logs, review status enum, bid unique,
│       │                          # solution_files.file_type, order lifecycle fields
│       ├── 0005_schema_fixes.py   # bids.amount precision Numeric(12,2),
│       │                          # messages.text NOT NULL
│       └── 0006_orderlogaction_new_values.py  # ALTER TYPE orderlogaction ADD VALUE
│                                              # requisites_sent, dispute_opened
├── backup/
│   ├── Dockerfile                 # python:3.11-slim + postgresql-client (apt)
│   └── backup.py                  # pg_dump → gzip → Telegram, цикл раз в сутки в 03:00 UTC
├── .env.example
├── alembic.ini
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

---

## База данных

### Статусы заявки
SQLAlchemy хранит **Python-имена enum** (не `.value`). В БД: `pending`, `awaiting_payment`, `in_progress`, `completed`, `cancelled`. В UI отображается `.value` (русские строки).

| Python enum | Русское value | Когда |
|-------------|--------------|-------|
| `pending` | `На рассмотрении` | Аукцион идёт |
| `awaiting_payment` | `Ожидает оплаты` | Оператор назначен, ждём оплаты |
| `in_progress` | `В работе` | Оплата подтверждена, оператор работает |
| `completed` | `Выполнено` | Оператор загрузил решение (авто-завершение) |
| `cancelled` | `Отменено` | Нет ставок / отменил клиент/оператор/система |

Дополнительные поля жизненного цикла в `orders` (не новые статусы):
- `payment_received_at` — момент, когда пришёл Robokassa callback или клиент нажал «Я оплатил»
- `payment_confirmed_at` — момент, когда админ нажал `/confirmpayment`
- `solution_uploaded_at` — момент загрузки решения оператором (авто-завершение после этого)
- `payment_revision` — счётчик, инкрементируется при изменении цены (старые Robokassa-ссылки инвалидируются)
- `cancelled_by` — `"client"` / `"operator"` / `"admin"` / `"system"`

### Ключевые модели

**users**: `id, telegram_id BIGINT, username, full_name, role ENUM(client/operator/admin), created_at`

**orders**: `id, client_id FK, operator_id FK nullable, status ENUM, comment TEXT, deadline DATE, budget NUMERIC(12,2), payment_amount NUMERIC(12,2), payment_invoice_id VARCHAR unique, auction_end_at TIMESTAMP, group_message_id BIGINT, payment_received_at, payment_confirmed_at, solution_uploaded_at, payment_revision INT DEFAULT 0, cancelled_by VARCHAR, created_at, updated_at`

> `comment` хранит записи в формате `"ISO_TS|текст"`, разделённые `"\n---\n"`. Позволяет хранить несколько комментариев клиента с точным временем каждого.

**order_files**: `id, order_id FK, telegram_file_id, file_type, created_at`

**solution_files**: `id, order_id FK, telegram_file_id, file_type, created_at`

**bids**: `id, order_id FK, operator_id FK, amount NUMERIC(12,2), created_at` + `UNIQUE(order_id, operator_id)` → upsert через `INSERT … ON CONFLICT DO UPDATE`

**reviews**: `id, order_id FK, client_id FK, rating INT DEFAULT 5, text TEXT NOT NULL, status ENUM(pending/approved/rejected) DEFAULT pending, moderated_by FK nullable, moderated_at nullable, created_at` + `UNIQUE(order_id, client_id)`

**messages**: `id, order_id FK, sender_id FK, text TEXT NOT NULL, direction ENUM(client_to_operator/operator_to_client), created_at`

> **Важно:** Python-имена MessageDirection: `client_to_operator` и `operator_to_client`. НЕ использовать старые алиасы `client_to_op`/`op_to_client`.

**operator_notes**: `id, order_id FK, operator_id FK, text TEXT, created_at`

**order_logs**: `id, order_id FK, actor_id FK nullable, action ENUM(created/bid_placed/auction_closed/operator_assigned/payment_received/payment_confirmed/price_updated/requisites_sent/solution_uploaded/completed/cancelled/comment_added/files_added/dispute_opened), detail VARCHAR nullable, created_at`

---

## Бизнес-логика

### Создание заявки (FSM: 4 шага)
```
"Создать заявку"
  → waiting_files  (до 10 файлов, /done для завершения — файлы опциональны)
  → waiting_comment ("-" для пропуска; иначе сохраняется с UTC timestamp)
  → waiting_deadline (ДД.ММ.ГГГГ, не раньше сегодня по МСК UTC+3)
  → waiting_budget (число > 0, лимит 5 активных заявок на клиента — двойная проверка)
  → create order in DB
  → AuctionService.start_auction(order)
     → post в группу: "🆕 Новая заявка №{id} создана" + кнопка "Перейти к заявке"
     → post в группу: "📋 Выберите действие:" + reply keyboard (3 кнопки)
     → APScheduler: job на auction_end_at = now() + 120 min
  → клиенту: "🎉 Ваша заявка создана! Ожидайте, пока операторы возьмут её в работу
              📋 Статус заявки вы можете посмотреть в разделе «Текущие заявки»"
```

### Аукцион
- Оператор нажимает "Могу взять" в **личке с ботом** → FSM `BidStates.waiting_price` → вводит сумму
- `AuctionService.place_bid(order_id, operator_id, amount)`:
  - `operator_id` — DB id (FK в bids), **не** telegram_id. Telegram_id берётся через `UserRepo.get_by_id()`
  - Если `bid.amount == order.budget` → **авто-назначение** (`close_auction` сразу)
  - Иначе → оператору отправляется обновлённая карточка с его ставкой в списке
  - `session.expire_all()` перед перезагрузкой заявки — иначе новая ставка не видна
- При равных ставках — побеждает тот, кто поставил **раньше** (`ORDER BY amount ASC, created_at ASC`)
- По истечении 120 мин или `/endauction {id}` от админа:
  - Нет ставок → заявка отменяется (`cancelled_by="system"`), клиент уведомлён, admin уведомлён
  - Есть ставки → `min(bids, key=(amount, created_at))` → назначить оператора
- `close_auction` — идемпотентна, работает под `SELECT FOR UPDATE`

### Оплата
```
Если ROBOKASSA_LOGIN настроен:
  generate_link → клиенту ссылка → Robokassa POST /payment/robokassa
  → verify sig (MD5(OutSum:InvId:pass2).upper()) → payment_received_at = now()
  → статус остаётся awaiting_payment → уведомить admin для подтверждения
  Response: f"OK{InvId}"  ← точная строка, иначе Robokassa будет повторять

Если ROBOKASSA_LOGIN пуст (ручной режим):
  → клиент видит "Я оплатил" кнопку → нажимает → payment_received_at = now()
  → admin получает: "💳 Клиент сообщил об оплате. Сумма: X ₽. /confirmpayment {id}"

В обоих случаях переход в in_progress только через:
  /confirmpayment {id} → payment_confirmed_at = now() → статус = in_progress
  → клиент и оператор уведомлены
```

### Загрузка решения (FSM оператора)
```
"Отправить решение"
  → SolutionStates.waiting_files, state: {order_id, files=[], comment=""}
  → Оператор отправляет файлы (фото/документы) — добавляются в files[]
  → Оператор отправляет текст — сохраняется как comment (последний перезаписывает)
  → /done
     → если нет файлов → ошибка
     → сохранить SolutionFile записи в БД
     → если есть comment → сохранить как Message(direction=operator_to_client) в БД
     → solution_uploaded_at = now()
     → update_status(completed)  ← авто-завершение, без шага admin
     → уведомить клиента: "🎉 Заявка №{id} выполнена!\n💬 Комментарий оператора: {comment}\n📂 Посмотрите в разделе «История заявок»"
```
Комментарий виден клиенту: в уведомлении + в карточке «История взаимодействия» (🔧 Оператор).

### Переговоры по цене (awaiting_payment)
```
Клиент: "Обсудить цену" → NegotiationStates.waiting_text → пишет текст или сумму
  → если число → counter_amount передаётся в NegotCB.proposed_amount
  → оператор получает: клиентское сообщение + negot_operator_kb

Оператор отвечает:
  ✅ Принять предложение → negot_accept
     → payment_amount = proposed_amount (если было число)
     → payment_revision++ → старые Robokassa-ссылки инвалидированы
     → клиент уведомлён о новой цене + send_requisites_kb для оператора

  💬 Встречная сумма → negot_counter → CounterOfferStates.waiting_amount
     → формат: "3700" или "3700 Минимальная стоимость работы"
     → payment_amount = amount, payment_revision++
     → клиент уведомлён о новой сумме (+ комментарий если есть)
     → в Robokassa-режиме: новая ссылка для оплаты
     → send_requisites_kb для оператора

  ❌ Отменить заявку → negot_cancel_order
     → order.cancelled_by = "operator", статус = cancelled
     → клиент уведомлён
     → группа операторов уведомлена ("❌ Заявка №{id} отменена оператором")
```

### Уведомления операторской группы
При любом изменении заявки:
- Новая заявка: `"🆕 Новая заявка №{id} создана"` + кнопка "Перейти к заявке"
- Клиент добавил комментарий: `"✏️ К заявке №{id} добавлен комментарий клиентом"` + кнопка
- Клиент добавил файлы: `"📎 К заявке №{id} добавлены файлы клиентом"` + кнопка
- Клиент отменил: `"❌ Заявка №{id} отменена клиентом"` + кнопка
- Оператор отменил: `"❌ Заявка №{id} отменена оператором"` + кнопка

### Отзывы (FSM: 2 шага)
```
Клиент нажимает "⭐ Оставить отзыв" (только на карточке выполненной заявки)
  → bot показывает inline rating_kb (1–5 звёзд, RatingCB)
  → Клиент выбирает оценку → ReviewStates.waiting_text
  → Клиент пишет текст → Review сохраняется (status=pending, rating, text)
  → "🙏 Спасибо за обратную связь!"
     → клиент: client_main_kb() только если user.role == UserRole.client
        (оператор/админ тестирующий клиентский флоу — клавиатура не меняется)
  → Администратор получает уведомление + Одобрить/Отклонить
```
- В "Отзывы о нас" видны только approved. Показывается `full_name` (не @username).
- Повторная отправка отзыва по той же заявке → идемпотентна.

---

## Интерфейсы

### Клиент (DM)

**Reply-кнопки**: `Создать заявку` | `Текущие заявки` | `История заявок` | `Отзывы`

**Текущие заявки** → статусы pending/awaiting_payment/in_progress → `client_orders_list_kb` (`action="client_view"`)

Нажатие на заявку → `format_client_card(order)` или специальная карточка по статусу:

| Статус | Карточка | Клавиатура |
|--------|----------|------------|
| `pending` / `in_progress` | `format_client_card` | Добавить комментарий \| Добавить файлы \| (Отменить если pending) \| 🔄 Обновить \| ← Назад |
| `awaiting_payment` | `format_client_card` | (Я оплатил — только в ручном режиме) \| Обсудить цену \| Отменить \| 🔄 Обновить \| ← Назад |
| `completed` | `format_client_history_card` | 📂 Решение \| 💬 Задать вопрос \| ⭐ Оставить отзыв \| 🔄 Обновить \| ← Назад |
| `cancelled` | `format_client_history_card` | 🔄 Обновить \| ← Назад |

- "← Назад" — **удаляет** сообщение-карточку (try/except на случай старого сообщения) и показывает список
- "Отменить заявку" → подтверждение → "Да/Нет". "Нет" удаляет подтверждение (try/except)
- "📂 Решение" → бот отправляет файлы + после последнего файла кнопка "← История заявок"
- "💬 Задать вопрос" (action="client_msg") — доступен для completed заявок; заблокирован только для cancelled
- "↩️ Ответить оператору" — кнопка в сообщении от оператора; заблокирована если заявка cancelled

**История заявок** → статусы completed/cancelled → тот же `client_orders_list_kb`

### Оператор

**В группе**: `Свободные заявки` | `Мои заявки` | `История выполненных заявок`
→ ответ отправляется в **личку** оператора (не в группу)

**"Перейти к заявке"** (inline в группе) → карточка в личке, или алерт "напишите /start"

**Карточка оператора** — `format_order_card(order, is_admin=False)`:
- **Обычный оператор**: видит `full_name` клиента (без @username)
- **Администратор**: видит `@username` клиента (передаётся `is_admin=True`)
- Содержит: ставки всех операторов, история взаимодействия (до 10 записей), заметки оператора

| Статус заявки | Кнопки |
|--------------|--------|
| `pending` (свободная) | Могу взять \| Файлы \| 🔄 Обновить |
| `in_progress` (моя) | Файлы \| Написать клиенту \| Добавить заметку \| Отправить решение \| 🔄 Обновить |
| `awaiting_payment` (моя) | 📤 Отправить реквизиты \| Файлы \| Написать клиенту \| 🔄 Обновить |
| `completed` / `cancelled` | 🔄 Обновить |

Кнопка `🔄 Обновить` (action=`"refresh"` для операторов, `"client_refresh"` для клиентов) — делает `edit_message_text` на месте. При `MessageNotModified` → алерт "✅ Уже актуально".

**FSM-защиты (SolutionStates.waiting_files)**:
Пока идёт загрузка решения — кнопки "Написать клиенту", "Добавить заметку", "Могу взять" возвращают алерт вместо сброса FSM.

**Написать клиенту** → оператор вводит текст → клиент получает сообщение + кнопка "↩️ Ответить оператору"

**"Файлы"** — edit pattern:
```
→ edit_message_text → "📎 Файлы по заявке №{id}:" + кнопка "← Назад"
→ send files as reply to that message
← Назад → edit_message_text обратно в карточку
```

### Администратор (DM)

Команды (все только в личке, IsAdmin):
- `/addoperator @username` или `/addoperator {telegram_id}` — назначить оператора
- `/deleteoperator @username` — снять оператора (с гардом на активные заявки)
- `/addadmin @username` или `/addadmin {telegram_id}` — назначить администратора
- `/removeadmin @username` — снять администратора (гарды: нельзя снять ADMIN_TELEGRAM_ID, нельзя снять себя)
- `/operators` — список всех операторов с telegram_id
- `/admins` — список всех администраторов
- `/stats` — сводка по статусам заявок
- `/endauction {order_id}` — досрочно завершить аукцион
- `/confirmpayment {order_id}` — вручную подтвердить оплату -> статус `in_progress`
- `/completeorder {order_id}` — принудительно завершить заявку (только in_progress)
- `/cancelorder {order_id}` — принудительно отменить заявку в любом не-финальном статусе
- `/commands` — список всех команд

---

## Форматирование

### Бюджет
`_money(amount)` — целое число без копеек (`1500 ₽`), дробное с копейками (`1500.50 ₽`)

### Комментарии клиента
Хранятся в `order.comment` как `"ISO_TS|текст\n---\nISO_TS|текст"`.
Парсится через `_parse_comment_ts(part)` → timestamp в МСК + текст.

### Карточки
| Функция | Для кого | Что показывает |
|---------|---------|----------------|
| `format_order_card(order, is_admin=False)` | Оператор/Админ | Ставки, клиент (full_name или @username), история, заметки |
| `format_client_card(order)` | Клиент (активная) | Без ставок и имён операторов, кол-во файлов, история |
| `format_client_history_card(order)` | Клиент (история) | Дата создания + дата выполнения/отмены, итоговая сумма |

`_history_lines(order)` — объединяет `order.comment` и `order.messages`, последние 10 записей по времени.

---

## Конфиг (`.env`)
```
BOT_TOKEN=
DOMAIN=yourdomain.com           # без https://, используется Caddy для SSL
WEBHOOK_BASE_URL=https://yourdomain.com
WEBHOOK_PATH=/telegram/webhook
WEBHOOK_SECRET=random_secret_string
DATABASE_URL=postgresql+asyncpg://user:pass@db/dbname
POSTGRES_USER=
POSTGRES_PASSWORD=
POSTGRES_DB=
OPERATOR_GROUP_ID=-XXXXXXX      # обычная группа — без префикса -100
ADMIN_TELEGRAM_ID=
ROBOKASSA_LOGIN=                 # если пусто — активируется ручной режим оплаты
ROBOKASSA_PASS1=
ROBOKASSA_PASS2=
ROBOKASSA_IS_TEST=true
REDIS_URL=redis://redis:6379/0
BACKUP_CHAT_ID=                  # telegram chat_id группы для ежедневных бэкапов БД
```

---

## Docker

```yaml
services:
  db:       postgres:16-alpine
  redis:    redis:7-alpine
  bot:
    build: .
    env_file: .env
    depends_on: [db, redis]
    # DEV: ports: ["8000:8000"] — для ngrok. PROD: порт закрыт через docker-compose.prod.yml
    command: sh -c "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"
    healthcheck: python3 -c "urllib.request.urlopen('http://localhost:8000/health')" (interval 30s, retries 3, start_period 30s)
  caddy:
    image: caddy:2-alpine
    ports: ["80:80", "443:443", "443:443/udp"]
    volumes: [./Caddyfile:/etc/caddy/Caddyfile, caddy_data:/data, caddy_config:/config]
    # Caddyfile: одна строка — "{env.DOMAIN} { reverse_proxy bot:8000 }"
    # SSL-сертификат получается автоматически от Let's Encrypt
    # DOMAIN= обязательна в .env (без https://)
  backup:
    build: ./backup
    env_file: .env
    environment: [BACKUP_CHAT_ID]
    depends_on: [db]
    volumes: [backup_data:/backups]
    # Запускается раз в сутки в 03:00 UTC — pg_dump -> gzip -> Telegram-группа
    # Хранит последние 30 дней локально, Telegram хранит вечно
    # Бот должен быть участником BACKUP_CHAT_ID
```

**Dev vs Production:**
- `docker-compose.yml` — базовый (dev): порт 8000 экспонируется наружу (для ngrok)
- `docker-compose.prod.yml` — production override: порт 8000 закрывается, Caddy проксирует через внутреннюю сеть
- Запуск production: `docker-compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build`

**Healthcheck бота:** использует `python3 -c "import urllib.request; ..."` — curl не установлен в python:3.11-slim.

Полный сброс БД (заявки с №1):
```bash
docker-compose down -v
docker-compose up -d --build
```

---

## Критические файлы

| Файл | Роль |
|------|------|
| `app/main.py` | FastAPI + lifespan: webhook setup, scheduler start, router mounting |
| `app/bot/dispatcher.py` | Middleware chain + все роутеры (порядок: admin → operator → client) |
| `app/services/auction_service.py` | Полный жизненный цикл аукциона + recover_overdue_auctions |
| `app/bot/middlewares/db_session.py` | Session-per-update: commit/rollback, inject в handlers |
| `app/bot/middlewares/user_register.py` | Авторегистрация + auto-promote admin |
| `app/api/payment.py` | Robokassa callback: Form params, verify, `return f"OK{InvId}"` |
| `app/bot/formatters.py` | Три форматтера карточек + _money() + _history_lines() |
| `app/bot/routers/operator/menu.py` | group_view_order (IsOperatorGroup + try/except) |
| `app/bot/routers/client/order_list.py` | client_view action + все card actions клиента |
| `app/bot/routers/operator/notes.py` | FSM загрузки решения: файлы + текстовый комментарий |
| `app/bot/routers/operator/messaging.py` | Переговоры по цене (negot_*), реквизиты |
| `app/bot/routers/errors.py` | Aiogram error handler: трейсбек → личка ADMIN_TELEGRAM_ID |
| `app/utils/telegram_log_handler.py` | Logging handler: logger.error() → Telegram |
| `backup/backup.py` | Скрипт бэкапа: pg_dump + gzip + send to Telegram + retention 30d |

---

## Верификация (end-to-end)

- [x] `/start` в DM → 4 reply-кнопки
- [x] FSM создания заявки: все 4 шага, заявка в БД со статусом "На рассмотрении"
- [x] В группе появилось уведомление "🆕 Новая заявка №{id}" + reply keyboard (3 кнопки)
- [x] "Свободные заявки" в группе → список приходит в личку оператора, не в группу
- [x] "Перейти к заявке" в группе → карточка в личке оператора (или алерт "напишите /start")
- [x] Кнопки "№{id}" у клиента используют `action="client_view"` — оператор не перехватывает
- [x] Клиент видит `full_name` оператора (без @username); оператор видит `full_name` клиента
- [x] Администратор видит `@username` клиента в карточке
- [x] "← Назад" в карточке удаляет её и показывает список (try/except на старых сообщениях)
- [x] Отмена заявки: подтверждение → "Да" отменяет, "Нет" удаляет подтверждение
- [x] Оператор вводит ставку → получает обновлённую карточку со ставкой
- [x] Оператор вводит ставку = бюджет → авто-назначение
- [x] `/endauction {id}` → досрочное завершение, min bid побеждает (при равных — раньше)
- [x] Без Robokassa → клиент видит "Я оплатил", при нажатии → admin /confirmpayment
- [x] `/confirmpayment {id}` → статус "В работе", оба уведомлены
- [x] Robokassa callback → payment_received_at → admin подтверждает → статус "В работе"
- [x] Переговоры по цене: клиент "Обсудить цену" → оператор: принять/встречная/отменить
- [x] Встречная сумма с комментарием ("3700 Причина") — комментарий доходит до клиента
- [x] При отмене оператором — группа операторов уведомлена
- [x] Клиент добавляет комментарий/файлы → в группу уведомление + кнопка "Перейти к заявке"
- [x] Оператор загружает решение: файлы + текстовый комментарий → /done
- [x] Клиент видит комментарий к решению в уведомлении и в «История взаимодействия»
- [x] После просмотра решения — кнопка "← История заявок"
- [x] "💬 Задать вопрос" работает на выполненных заявках; заблокирован на отменённых
- [x] FSM загрузки решения: "Написать клиенту" / "Добавить заметку" / "Могу взять" → алерт
- [x] Отзыв: выбор рейтинга → текст → admin модерация → "Отзывы о нас" без @username
- [x] После отзыва: клиент получает client_main_kb, оператор/админ — без смены клавиатуры
- [x] Рестарт контейнера → FSM-состояния сохранены (Redis), аукционный таймер восстановлен
- [x] Max 5 активных заявок на клиента — при превышении отказ (двойная проверка)
- [x] `/operators`, `/admins`, `/stats`, `/commands`, `/completeorder` — работают
- [ ] Robokassa в боевом режиме (нужна регистрация)
- [ ] End-to-end тест с двумя реальными операторами

---

## Известные решённые проблемы

### Деплой и инфраструктура
- **python-multipart** — обязателен для FastAPI Form POST (Robokassa callback)
- **Offline wheels** — пакеты скачиваются через `pip download` с флагами платформы
- **Scheduler sync URL** — APScheduler jobstore требует `postgresql://` (не `+asyncpg`)

### БД и ORM
- **Enum хранит Python-имена** (`pending`, не `На рассмотрении`) — в миграции английские имена
- **OPERATOR_GROUP_ID** — без `-100` для обычной группы (только суперgroup использует `-100`)
- **file_id vs telegram_file_id** — миграция 0001 создала `file_id`, модели ожидали `telegram_file_id`. Исправлено в 0002
- **messagedirection enum** — имена `client_to_operator` / `operator_to_client` (не `client_to_op`)
- **0004 migration enum duplication** — `orderlogaction` нельзя создавать через `SQLAlchemy Enum.create()` + `create_type=False` в asyncpg. Решение: raw `op.execute("CREATE TYPE ... AS ENUM (...)")` + `op.execute("CREATE TABLE ...")`
- **`session.expire_all()`** — обязателен перед перезагрузкой объекта после mutation
- **bids upsert** — `INSERT … ON CONFLICT DO UPDATE` (BidRepo.upsert) вместо insert+select

### Бот и роли
- **IsClient пропускает все роли** — оператор/админ может создавать заявки и использовать клиентский UI
- **Авто-промоушн admin** — при каждом апдейте если telegram_id совпадает с ADMIN_TELEGRAM_ID
- **operator_id ≠ telegram_id** — в `place_bid` параметр — DB id. Нужен `UserRepo.get_by_id()` для telegram_id
- **action="view" конфликт** — клиентский список использует `action="client_view"`, иначе операторский хэндлер перехватывает
- **"Перейти к заявке" не работает** — оператор не написал /start боту. Добавлен try/except + алерт
- **UnboundLocalError в solution_done** — дублирующий `from app.db.models.order import OrderStatus` внутри функции делал имя локальным → обращение до import → ошибка. Удалён дублирующий import.

### Инфраструктурные улучшения (сессия 2)

**Мониторинг ошибок (замена Sentry — своя реализация):**
- `app/bot/routers/errors.py` — aiogram `@router.errors()`: ловит необработанные исключения → трейсбек → личка ADMIN_TELEGRAM_ID
- `app/utils/telegram_log_handler.py` — `logging.Handler` уровня ERROR: ловит `logger.error()` / `logger.exception()` → Telegram через `loop.create_task`
- Инициализируется в `app/main.py` lifespan startup

**Логирование (замена except: pass):**
- В 5 файлах заменены `except Exception: pass` на `logger.warning("...", exc_info=True)` для всех `bot.send_message` уведомлений
- `message.delete()` / `edit_message_text(MessageNotModified)` — оставлены как `pass` (ожидаемые ошибки)

**Кнопка 🔄 Обновить на карточках заявок:**
- Операторские: `free_order_card_kb`, `my_order_card_kb`, `awaiting_payment_operator_kb` + `operator_refresh_only_kb` (completed/cancelled)
- Клиентские: все четыре клавиатуры (`active`, `awaiting_payment`, `completed`, `cancelled`)
- `action="refresh"` → хендлер в `operator/menu.py`, `edit_message_text` на месте
- `action="client_refresh"` → хендлер в `client/order_list.py`, аналогично
- При `MessageNotModified` → "✅ Уже актуально" (два action разделены чтобы роутеры не конфликтовали)

**Управление администраторами:**
- `/addadmin @username|{id}` — назначить администратора (только IsAdmin)
- `/removeadmin @username|{id}` — снять (гварды: нельзя снять ADMIN_TELEGRAM_ID, нельзя снять себя)
- Обновлён `/commands`

**Резервное копирование БД:**
- Новый Docker-сервис `backup` (`backup/Dockerfile` + `backup/backup.py`)
- `python:3.11-slim` + `postgresql-client` через apt
- Ежедневно в 03:00 UTC: `pg_dump` → gzip → `sendDocument` в Telegram-группу `BACKUP_CHAT_ID`
- Локальное хранение: 30 дней; Telegram хранит вечно
- `BACKUP_CHAT_ID` в `.env` — бот должен быть участником группы

---

### Исправленные баги (аудит, сессия 1)
1. **reviews.py** — `got_review_text` возвращал `client_main_kb()` для всех ролей → добавлена проверка `user.role == UserRole.client`
2. **client/messaging.py** — `start_client_message` не блокировал cancelled-заявки → добавлена проверка `order.status == OrderStatus.cancelled`
3. **operator/messaging.py** — `extra_comment` терялся в Robokassa-ветке `counter_offer_done` → добавлен `comment_line` в оба варианта сообщения
4. **operator/messaging.py** — `negot_cancel_order` не уведомлял группу операторов → добавлено `bot.send_message(operator_group_id, ...)`
5. **client/reviews.py** — `all_reviews` показывал `@username` публично → заменён на `r.client.full_name`
6. **client/order_list.py** — `back_to_orders_list`, `back_to_history_list`, `cancel_order_no` — `delete()` без try/except → добавлены try/except
7. **operator/notes.py** — `solution_done` использовал `get_by_id` вместо `get_by_id_for_update` → исправлено
8. **operator/notes.py**, **operator/messaging.py**, **operator/bid.py** — кнопки карточки во время `SolutionStates.waiting_files` молча сбрасывали FSM → добавлен гард с алертом
9. **operator/menu.py** — мёртвый обработчик `complete_order` (action="complete") удалён (авто-завершение теперь в `solution_done`)

### Исправленные баги (аудит, сессия 3)
10. **operator/notes.py** — `solution_comment` матчил `/cancel` как текст (F.text ловит команды) → добавлен фильтр `~F.text.startswith("/")`, команды пролетают в `cmd_cancel`
11. **operator/messaging.py** — `negot_cancel_order` читал заявку без `SELECT FOR UPDATE` → заменён `get_by_id` на `get_by_id_for_update`; устранён race condition при одновременной отмене клиентом и оператором
12. **operator/messaging.py** — 5 хендлеров (`send_operator_message`, `send_requisites_done`, `negot_accept`, `counter_offer_done`, `negot_cancel_order`) отправляли уведомления ДО `session.commit()` → добавлен `post_commit: list` в сигнатуры, все `bot.send_message` к третьим лицам перенесены в `post_commit.append(...)`
13. **admin/commands.py** — отсутствовала команда `/cancelorder {id}` → добавлена; отменяет заявку в любом нефинальном статусе, уведомляет клиента и оператора, пишет лог `"Admin forced cancel"`; добавлена в `/commands`

### Исправленные баги (аудит, сессия 4)
14. **services/notification_service.py** — удалён мёртвый `NotificationService` (нигде не вызывался, содержал устаревшие тексты без `_money()`)
15. **client/order_list.py** — `view_solution` не показывал текстовый комментарий оператора → перед файлами отображается последний `Message(direction=operator_to_client)` по заявке
16. **auction_service.py / order_repo.py** — `group_message_id` сохранялся в БД, но нигде не читался → убраны `set_group_message_id` вызов и метод; поле в модели осталось (без миграции)
17. **client/order_list.py** — `add_comment_start` и `add_files_start` не проверяли активный FSM → добавлен guard: если уже в `OrderEditStates.waiting_*`, показывается алерт «завершите текущее действие»
18. **order_inline.py / operator/notes.py** — не было механизма для оператора сообщить о невозможности выполнить заявку → добавлена кнопка «⚠️ Не могу выполнить» в `my_order_card_kb` и обработчик `cant_do_order`: сохраняет `OperatorNote`, уведомляет админа с подсказкой `/cancelorder {id}` и клиента

### Исправленные баги (аудит, сессия 5)
19. **client/reviews.py** — `start_review` загружал все отзывы клиента для проверки дубля → заменён `get_by_client(user.id)` + Python-фильтр на точечный `get_by_order_client(order.id, user.id)` (один SELECT по PK unique constraint)
20. **operator/messaging.py** — `start_operator_message` не проверял статус заявки → оператор мог написать клиенту по завершённой/отменённой заявке через устаревшую кнопку; добавлена проверка `status in (completed, cancelled)`
21. **backup/backup.py** — Telegram-отправка без ретраев; при сбое никто не узнал → добавлены 3 попытки с паузой 30 сек; при полном провале — alert-сообщение через `sendMessage` к `ADMIN_TELEGRAM_ID` (новый env var в backup-контейнере)
22. **order_repo.py / scheduler/jobs.py / main.py** — уведомление adminа о Robokassa-платеже не было гарантировано → добавлен daily cron-job `remind_unconfirmed_payments` (10:00 UTC): находит заявки с `payment_received_at IS NOT NULL AND payment_confirmed_at IS NULL`, отправляет список с командами `/confirmpayment {id}`

### Исправленные баги (аудит, сессия 6)
23. **formatters.py** — `format_order_card` показывал ставки конкурентов всем операторам → добавлен параметр `viewer_id: int | None`; оператор видит только свою ставку + счётчик чужих; admin видит все ставки; все call sites в `operator/menu.py` и `auction_service.py` обновлены
24. **order_inline.py / client/order_list.py** — не было механизма спора → добавлена кнопка «⚠️ Оспорить» в `client_completed_order_kb`; обработчик `client_dispute`: пишет лог `comment_added + detail="Client opened dispute"`, уведомляет admin через `post_commit`
25. **client/menu.py** — `/start` молча сбрасывал FSM без уведомления → если было активное состояние, показывается «ℹ️ Незавершённое действие отменено» с подсказкой про `/cancel`
26. **api/payment.py** — stale Robokassa callback (старый InvId после смены цены) возвращал 404, Robokassa бесконечно ретраила → парсим `{order_id}_{revision}` из InvId; если заявка существует с более высоким `payment_revision` — возвращаем `OK{InvId}` чтобы остановить ретраи
27. **operator/messaging.py** — оператор в `CounterOfferStates.waiting_amount` не знал как выйти → добавлена подсказка «Для отмены — /cancel» в prompt

### Исправленные баги (аудит, сессия 7)
28. **requirements.txt** — `aiohttp` не был явно указан (неявная зависимость от aiogram) → добавлен `aiohttp>=3.9.0,<4.0.0`
29. **client/messaging.py** — `send_client_message` использовал прямой `await bot.send_message` до commit → переведён на `post_commit.append()`; добавлен `post_commit: list` в сигнатуру
30. **client/order_list.py** — `client_negotiate_done` использовал прямой `await bot.send_message` до commit → переведён на `post_commit.append()`; убран try/except-заглушитель
31. **client/order_create.py** — `start_order` не проверял активный FSM → нажатие «Создать заявку» посреди другого действия молча перебивало состояние; добавлен guard с сообщением
32. **operator/notes.py** — `solution_done` использовал прямой `await bot.send_message` до commit → переведён на `post_commit.append()`; убран try/except-заглушитель
33. **admin/commands.py** — `cmd_confirm_payment`, `cmd_complete_order`, `cmd_cancel_order` использовали прямые `await bot.send_message` до commit → все три переведены на `post_commit: list`; убраны try/except-заглушители

### Исправленные баги (аудит, сессия 8)
34. **client/reviews.py** — `start_review` не проверял статус заявки → клиент мог нажать старую кнопку «⭐ Оставить отзыв» и оставить отзыв на незавершённую заявку; добавлена проверка `order.status == OrderStatus.completed` с алертом
35. **docker-compose.yml / Caddyfile** — отсутствовал SSL/reverse-proxy → webhook Telegram не мог работать в production; добавлен сервис `caddy` (ports 80/443), `Caddyfile` с одной строкой конфига (автоматический SSL Let's Encrypt); порт `8000:8000` у `bot` закомментирован — доступ только через Caddy; добавлена переменная `DOMAIN` в `.env.example`; добавлен `healthcheck` для сервиса `bot` (curl `/health`)
36. **operator/messaging.py / keyboards/callbacks.py / keyboards/order_inline.py / client/order_list.py** — race condition в переговорах по цене: клиент отправлял два встречных предложения, оператор мог принять оба, цена менялась дважды; добавлено поле `revision: int = 0` в `NegotCB`; `negot_operator_kb()` принимает `revision` и кодирует его в кнопки Accept/Counter; `client_negotiate_done` передаёт `revision=order.payment_revision`; `negot_accept` и `negot_counter` проверяют `callback_data.revision != order.payment_revision` — устаревший callback инвалидируется с алертом «Это предложение устарело»
37. **client/order_list.py** — при отмене заявки `awaiting_payment` клиентом оператор получал уведомление только в группу → добавлено прямое DM оператору если `order.operator_id is not None`
38. **operator/messaging.py / db/models/order_log.py / migrations/0006** — `send_requisites_done` не оставлял записи в журнале заявки → добавлен `OrderLogAction.requisites_sent`; вызов `add_log` перед отправкой реквизитов; новая миграция `0006_orderlogaction_new_values` добавляет `requisites_sent` и `dispute_opened` через `ALTER TYPE ... ADD VALUE IF NOT EXISTS`
39. **client/order_list.py / db/models/order_log.py** — `client_dispute` логировал действие как `comment_added` → добавлен `OrderLogAction.dispute_opened`; теперь споры можно фильтровать отдельно от комментариев
40. **bot/routers/errors.py** — при необработанном исключении пользователь не получал никакого ответа и думал что бот завис → добавлен ответ пользователю: `message.answer(...)` или `callback_query.answer(..., show_alert=True)` после уведомления администратора
41. **client/reviews.py / keyboards/callbacks.py / keyboards/admin_inline.py** — `all_reviews` выводил все отзывы в одно сообщение, при росте числа отзывов упирался в лимит Telegram 4096 символов → добавлена пагинация: `REVIEWS_PAGE_SIZE=5`, поле `page: int = 0` в `ReviewListCB`, хелпер `reviews_pagination_kb()`, заголовок `"⭐ Отзывы (1/3):"`
42. **client/order_list.py** — `client_negotiate` не проверял активный FSM перед входом в `NegotiationStates.waiting_text` → нажатие кнопки из старого сообщения молча перебивало активный стейт (создание заявки, редактирование); добавлен FSM guard; добавлена подсказка «Для отмены — /cancel» в prompt
43. **client/messaging.py** — `start_client_message` не проверял активный FSM → аналогично п.42; добавлен FSM guard
44. **operator/menu.py** — `_pick_kb` возвращал `None` для чужой активной заявки (admin/operator смотрит заявку другого оператора) → заменено на `operator_refresh_only_kb(order.id)`; теперь всегда есть кнопка «🔄 Обновить»
45. **client/order_list.py** — при добавлении клиентом комментария или файлов уведомлялась только группа операторов → добавлено прямое DM назначенному оператору (если `order.operator_id is not None`) в `add_comment_done` и `add_files_done`
46. **db/models/order.py** — комментарий к полю `cancelled_by` не включал значение `"operator"` → исправлен; поле `group_message_id` помечено как deprecated (не записывается и не читается приложением)
47. **api/payment.py** — проверка stale InvId использовала `payment_revision > 0` вместо точного сравнения → исправлено на `cb_revision < stale_order.payment_revision`; добавлена проверка `parts[1].isdigit()`; лог стал информативнее
48. **services/auction_service.py** — при повторной ставке оператора текст подтверждения всегда был «✅ Ставка принята» → перед upsert проверяется `get_operator_bid`; если ставка уже была — текст «🔄 Ставка обновлена: X ₽»
