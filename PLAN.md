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
│   │   │   │   ├── messaging.py   # Прокси оператор→клиент
│   │   │   │   └── notes.py       # FSM: заметка к заявке
│   │   │   └── admin/
│   │   │       ├── commands.py    # /addoperator /deleteoperator /operators /admins
│   │   │       │                  # /stats /endauction /confirmpayment /commands
│   │   │       └── reviews.py     # Модерация отзывов (одобрить/отклонить)
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
│   │   │   └── callbacks.py       # OrderCB, ReviewCB, ReviewListCB, RatingCB
│   │   ├── states/
│   │   │   ├── order_create.py    # waiting_files → comment → deadline → budget
│   │   │   ├── bid.py             # waiting_price
│   │   │   └── note.py            # OrderEditStates, NoteStates, MessagingStates,
│   │   │                          # SolutionStates, ReviewStates (waiting_rating, waiting_text)
│   │   ├── formatters.py          # format_order_card, format_client_card,
│   │   │                          # format_client_history_card, _money(), _history_lines()
│   │   └── middlewares/
│   │       ├── db_session.py      # AsyncSession per update (commit/rollback)
│   │       └── user_register.py   # Авторегистрация, inject user в data[], auto-promote admin
│   ├── db/
│   │   ├── base.py                # DeclarativeBase + TimestampMixin
│   │   ├── engine.py              # create_async_engine + async_sessionmaker
│   │   └── models/
│   │       ├── user.py
│   │       ├── order.py
│   │       ├── order_file.py      # telegram_file_id (не file_id!)
│   │       ├── solution_file.py   # telegram_file_id (не file_id!)
│   │       ├── bid.py
│   │       ├── review.py          # rating INT (добавлен миграцией 0003)
│   │       ├── message.py
│   │       └── operator_note.py
│   ├── repositories/
│   │   ├── user_repo.py           # get_by_role() для /operators /admins
│   │   ├── order_repo.py
│   │   ├── bid_repo.py            # get_min_bid: ORDER BY amount, created_at
│   │   ├── review_repo.py         # create(rating=) параметр
│   │   └── message_repo.py
│   ├── services/
│   │   ├── auction_service.py     # start_auction, place_bid, close_auction
│   │   └── payment_service.py     # Robokassa: generate_link + verify_callback
│   ├── scheduler/
│   │   └── setup.py               # AsyncIOScheduler + SQLAlchemy jobstore (sync URL)
│   └── api/
│       ├── webhook.py             # POST /telegram/webhook
│       └── payment.py             # POST /payment/robokassa
├── migrations/
│   ├── env.py                     # Alembic async env
│   └── versions/
│       ├── 0001_initial.py        # Все таблицы (создана вручную)
│       ├── 0002_fix_columns.py    # file_id → telegram_file_id, drop stale columns
│       └── 0003_add_review_rating.py # rating column в reviews
├── .env.example
├── alembic.ini
├── docker-compose.yml
├── Dockerfile
└── requirements.txt               # + python-multipart (FastAPI Form POST)
```

---

## База данных

### Статусы заявки
SQLAlchemy хранит **Python-имена enum** (не `.value`). В БД: `pending`, `awaiting_payment`, `in_progress`, `completed`, `cancelled`. В UI отображается `.value` (русские строки).

| Python enum | Русское value | Когда |
|-------------|--------------|-------|
| `pending` | `На рассмотрении` | Аукцион идёт |
| `awaiting_payment` | `Ожидает оплаты` | Оператор назначен, ждём оплаты |
| `in_progress` | `В работе` | Оплачено, оператор работает |
| `completed` | `Выполнено` | Оператор сдал решение |
| `cancelled` | `Отменено` | Нет ставок / клиент отменил |

### Ключевые модели

**users**: `id, telegram_id BIGINT, username, full_name, role ENUM(client/operator/admin), created_at`

**orders**: `id, client_id FK, operator_id FK nullable, status ENUM, comment TEXT, deadline DATE, budget NUMERIC, auction_end_at TIMESTAMP, updated_at, payment_amount NUMERIC, payment_invoice_id VARCHAR unique, group_message_id BIGINT, created_at`

> `comment` хранит записи в формате `"ISO_TS|текст"`, разделённые `"\n---\n"`. Позволяет хранить несколько комментариев с точным временем каждого.

**order_files**: `id, order_id FK, telegram_file_id, file_type, created_at`

**solution_files**: `id, order_id FK, telegram_file_id, created_at`

**bids**: `id, order_id FK, operator_id FK, amount NUMERIC, created_at`

**reviews**: `id, order_id FK, client_id FK, rating INT DEFAULT 5, text TEXT, is_approved BOOL DEFAULT false, created_at`

**messages**: `id, order_id FK, sender_id FK, text TEXT, direction ENUM(client_to_op/op_to_client), created_at`

**operator_notes**: `id, order_id FK, operator_id FK, text TEXT, created_at`

---

## Бизнес-логика

### Создание заявки (FSM: 4 шага)
```
"Создать заявку"
  → waiting_files  (до 10 файлов, /done для завершения — файлы опциональны)
  → waiting_comment ("-" для пропуска; иначе сохраняется с UTC timestamp)
  → waiting_deadline (ДД.ММ.ГГГГ, не раньше сегодня по МСК UTC+3)
  → waiting_budget (число, лимит 5 активных заявок на клиента)
  → create order in DB
  → AuctionService.start_auction(order)
     → post в группу: "🆕 Новая заявка №{id} создана" + кнопка "Перейти к заявке"
     → post в группу: "📋 Выберите действие:" + reply keyboard (3 кнопки)
     → APScheduler: job на auction_end_at = now() + 120 min
  → "🎉 Ваша заявка создана! Ожидайте, пока операторы возьмут её в работу
     📋 Статус заявки вы можете посмотреть в разделе «Текущие заявки»"
```

**Файлы опциональны** — клиент может отправить `/done` без файлов.
**Дедлайн** — проверяется по московскому времени.
**Лимит** — не более 5 активных заявок на клиента одновременно.

### Аукцион
- Оператор нажимает "Могу взять" в **личке с ботом** → FSM waiting_price → вводит сумму
- `AuctionService.place_bid(order_id, operator_id, amount)`:
  - `operator_id` — DB id, **не** telegram_id. Telegram_id берётся через `UserRepo.get_by_id()`
  - Если `bid.amount == order.budget` → **авто-назначение**
  - Иначе → оператору отправляется обновлённая карточка заявки с актуальными ставками
  - `session.expire_all()` перед перезагрузкой заявки — иначе новая ставка не видна
- При равных ставках — побеждает тот, кто поставил **раньше** (`ORDER BY amount, created_at`)
- По истечении 120 мин или `/endauction {id}` от админа:
  - Нет ставок → клиенту: "К сожалению, из-за большой нагруженности у операторов нет возможности выполнить вашу работу" → статус `Отменено`
  - Есть ставки → `min(bids, key=(amount, created_at))` → назначить оператора

### Оплата
```
Если ROBOKASSA_LOGIN настроен:
  generate_link → клиенту ссылка → Robokassa callback → /payment/robokassa
  → verify sig → order.status = in_progress → уведомить обе стороны
  Response: f"OK{InvId}"

Если ROBOKASSA_LOGIN пуст (режим разработки):
  → клиенту: "Реквизиты пришлёт администратор"
  → админу: "💳 Заявка №{id} ожидает ручного подтверждения. /confirmpayment {id}"
  → /confirmpayment {id} → order.status = in_progress → уведомить обе стороны
```

### Уведомления оператора о действиях клиента
При любом изменении заявки клиентом в операторский чат приходит уведомление + кнопка "Перейти к заявке":
- Добавлен комментарий: `"✏️ К заявке №{id} добавлен комментарий клиентом"`
- Добавлены файлы: `"📎 К заявке №{id} добавлены файлы клиентом"`
- Заявка отменена: `"❌ Заявка №{id} отменена клиентом"`

### Отзывы (FSM: 2 шага)
```
Клиент нажимает "Оставить отзыв"
  → bot показывает inline-клавиатуру со звёздами (1–5, RatingCB)
  → Клиент выбирает оценку → waiting_text
  → Клиент пишет текст
  → Review сохраняется в БД (rating + text)
  → Администратор получает уведомление + Одобрить/Отклонить
```

### Бот-прокси (переписка)
- **Клиент пишет** через "Написать оператору" → бот шлёт в личку оператора + логирует в `messages`
- **Оператор пишет** через "Написать клиенту" → FSM → `bot.send_message(client.telegram_id, ...)` + лог
- История сообщений отображается в карточке заявки

---

## Интерфейсы

### Клиент (DM)

**Reply-кнопки**: `Создать заявку` | `Текущие заявки` | `История заявок` | `Отзывы`

**Создание заявки**: сообщение при старте FSM — `"📎 Отправьте файлы с заданием (до 10 штук)\nКогда закончите — отправьте /done"`

**Текущие заявки** → статус не в {Выполнено, Отменено} → `client_orders_list_kb` (`action="client_view"`)

Нажатие на заявку → `format_client_card(order)` (без ставок и имён операторов):
```
📌 Заявка №{id}
Статус: {status}
Дата создания: {dd.mm.yyyy hh:mm} МСК
Дедлайн: {dd.mm.yyyy}
Желаемый бюджет: {budget} ₽
Прикреплённых файлов: {N}
История взаимодействия:
[{dd.mm.yyyy hh:mm}] 👤 Клиент (комментарий): {text}
[{dd.mm hh:mm}] 🔧 Оператор: {text}
```
Кнопки: `✏️ Добавить комментарий` | `📎 Добавить файлы` | `❌ Отменить заявку` (если pending) | `← Назад`
- "← Назад" **удаляет** карточку и показывает список заново

**История заявок** → статус в {Выполнено, Отменено} → `client_orders_list_kb` (`action="client_view"`)

Нажатие на заявку → `format_client_history_card(order)`:
```
📌 Заявка №{id}
Статус: {status}
Дата создания: {dd.mm.yyyy hh:mm} МСК
Дата выполнения/отмены: {dd.mm.yyyy hh:mm} МСК
Дедлайн: {dd.mm.yyyy}
Желаемый бюджет: {budget} ₽
Прикреплённых файлов: {N}
История взаимодействия: ...
```
- **Выполнено**: кнопки `📂 Решение` | `💬 Задать вопрос` | `⭐ Оставить отзыв` | `← Назад`
- **Отменено**: только `← Назад`
- "← Назад" **удаляет** карточку и показывает историю

**Отмена заявки**: сначала подтверждение с кнопками `✅ Да, отменить` и `← Нет, назад`
- "← Нет, назад" **удаляет** сообщение-подтверждение

### Операторская группа + личка оператора

#### Принцип изоляции
> **Группа — только уведомления. Все действия — в личке с ботом.**

**Reply-кнопки в группе**: `Свободные заявки` | `Мои заявки` | `История выполненных заявок`
Каждая → список отправляется **в личку** оператора, в группе ничего не отвечаем.

**"Перейти к заявке"** (inline в группе):
```
→ try: bot.send_message(operator.telegram_id, card, kb)
  except: callback.answer("Напишите боту /start в личных сообщениях", show_alert=True)
```
Оператор **обязан** написать боту /start в личке хотя бы раз — иначе бот не может инициировать диалог.

**Карточка заявки (в DM оператора)**:
```
📌 Заявка №{id}
Статус: {status}
Клиент: @{username}
Дата создания: {dd.mm.yyyy hh:mm} МСК
Дедлайн: {dd.mm.yyyy}
Желаемый бюджет: {budget} ₽
Сбор цен до: {auction_end} МСК
Ставки операторов:
  • {op1_name}: {amount} ₽
  • {op2_name}: {amount} ₽
  (или: "Ставок пока нет")
История взаимодействия:
[{dd.mm.yyyy hh:mm}] 👤 Клиент (комментарий): {text}
[{dd.mm hh:mm}] 👤 Клиент: {text}
[{dd.mm hh:mm}] 🔧 Оператор: {text}
  (или: "Сообщений пока нет")
```
Кнопки (свободная заявка): `Могу взять` | `Файлы`
Кнопки (моя заявка): `Файлы` | `Написать клиенту` | `Добавить заметку` | `Отправить решение`

**Кнопка "Файлы"** — edit pattern:
```
→ edit_message_text → "📎 Файлы по заявке №{id}:" + кнопка "← Назад"
→ send files as reply
← Назад → edit_message_text обратно в карточку
```

**После ставки** — оператор получает обновлённую карточку с его ставкой в списке.

### Администратор (DM)

Команды (все только в личке, IsAdmin):
- `/addoperator @username` или `/addoperator {telegram_id}` — назначить оператора
- `/deleteoperator @username` — снять оператора
- `/operators` — список всех операторов
- `/admins` — список всех администраторов
- `/stats` — сводка по статусам заявок
- `/endauction {order_id}` — досрочно завершить аукцион
- `/confirmpayment {order_id}` — вручную подтвердить оплату → статус `В работе`
- `/commands` — список всех команд

Уведомления:
- Новый отзыв → inline `Одобрить` / `Отклонить`
- Нет ставок за 120 мин → уведомление
- Если ROBOKASSA не настроен → запрос на ручное подтверждение оплаты

---

## Форматирование

### Бюджет
`_money(amount)` — целое число без копеек (`1500 ₽`), дробное с копейками (`1500.50 ₽`)

### Комментарии
Хранятся в `order.comment` как `"ISO_TS|текст\n---\nISO_TS|текст"`.
Парсится через `_parse_comment_ts(part)` → timestamp в МСК + текст.
Старые записи без TS показываются с `[—]`.

### Карточки
| Функция | Для кого | Что показывает |
|---------|---------|----------------|
| `format_order_card(order)` | Оператор | Полная: ставки, клиент, история |
| `format_client_card(order)` | Клиент (активная) | Без ставок и оператора, кол-во файлов |
| `format_client_history_card(order)` | Клиент (история) | Дата создания + дата выполнения/отмены |

---

## Конфиг (`.env`)
```
BOT_TOKEN=
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
    ports: ["8000:8000"]
    command: sh -c "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"
```

После любых изменений кода: `docker-compose up -d --build`

---

## Критические файлы

| Файл | Роль |
|------|------|
| `app/main.py` | FastAPI + lifespan: webhook setup, scheduler start, router mounting |
| `app/bot/dispatcher.py` | Middleware chain + все роутеры (порядок: admin → operator → client) |
| `app/services/auction_service.py` | Полный жизненный цикл аукциона |
| `app/bot/middlewares/db_session.py` | Session-per-update: commit/rollback, inject в handlers |
| `app/bot/middlewares/user_register.py` | Авторегистрация + auto-promote admin |
| `app/api/payment.py` | Robokassa callback: Form params, verify, `return f"OK{InvId}"` |
| `app/bot/formatters.py` | Три форматтера карточек + _money() + _history_lines() |
| `app/bot/routers/operator/menu.py` | group_view_order (IsOperatorGroup + try/except) |
| `app/bot/routers/client/order_list.py` | client_view action + все card actions клиента |
| `migrations/versions/0001_initial.py` | Начальная миграция (создана вручную) |
| `migrations/versions/0002_fix_columns.py` | Переименование колонок |
| `migrations/versions/0003_add_review_rating.py` | Рейтинг отзывов |

---

## Верификация (end-to-end)

- [x] `/start` в DM → 4 reply-кнопки
- [x] FSM создания заявки: все 4 шага, заявка в БД со статусом "На рассмотрении"
- [x] В группе появилось уведомление "🆕 Новая заявка №{id}" + reply keyboard (3 кнопки)
- [x] "Свободные заявки" в группе → список приходит в личку оператора, не в группу
- [x] "Перейти к заявке" в группе → карточка в личке оператора (или алерт "напишите /start")
- [x] Кнопки "№{id}" у клиента используют `action="client_view"` — оператор не перехватывает
- [x] Клиент видит свою карточку без ставок операторов
- [x] "← Назад" в карточке удаляет её и показывает список
- [x] Отмена заявки: подтверждение → "Да" отменяет, "Нет" удаляет подтверждение
- [x] Оператор вводит ставку → получает обновлённую карточку со ставкой
- [x] Оператор вводит ставку = бюджет → авто-назначение
- [x] `/endauction {id}` → досрочное завершение, min bid побеждает (при равных — раньше)
- [x] Без Robokassa → клиент получает "реквизиты пришлёт админ", админу `/confirmpayment`
- [x] `/confirmpayment {id}` → статус "В работе", оба уведомлены
- [x] Robokassa callback → статус "В работе", оба уведомлены
- [x] Клиент добавляет комментарий/файлы → в группу уведомление + кнопка "Перейти к заявке"
- [x] Комментарии хранятся с timestamp, показываются в правильное время
- [x] Несколько комментариев к заявке — все сохраняются
- [x] Бюджет показывается без копеек (`1500 ₽`)
- [x] Отзыв: сначала выбор рейтинга (звёзды), потом текст
- [x] Отзыв → admin DM → одобрение → виден в "Отзывы о нас" со звёздами
- [x] История заявок: выполненная → 4 кнопки; отменённая → 1 кнопка "← Назад"
- [x] 120 мин без ставок → клиент уведомлён, статус "Отменено"
- [x] Прокси: сообщение клиента → у оператора; ответ оператора → у клиента
- [x] Рестарт контейнера → FSM-состояния сохранены (Redis), таймер аукциона восстановлен
- [x] Max 5 активных заявок на клиента — при превышении отказ
- [x] `/operators`, `/admins`, `/stats`, `/commands` — работают
- [ ] Robokassa в боевом режиме (нужна регистрация в системе)
- [ ] End-to-end тест с двумя реальными операторами

---

## Известные решённые проблемы

### Деплой и инфраструктура
- **python-multipart** — обязателен для FastAPI Form POST (Robokassa callback)
- **Offline wheels** — пакеты скачиваются через `pip download --platform manylinux_2_17_x86_64 --python-version 311 --only-binary=:all:` из-за VPN
- **Миграции в репозитории** — при `docker-compose down` файлы внутри контейнера теряются

### БД и ORM
- **Enum хранит Python-имена** (`pending`, не `На рассмотрении`) — в миграции указывать английские имена
- **OPERATOR_GROUP_ID** — без `-100` для обычной группы
- **file_id vs telegram_file_id** — миграция 0001 создала `file_id`, модели ожидали `telegram_file_id`. Исправлено в 0002
- **reviews.rating** — в 0001 была колонка `rating` которую удалили в 0002, потом добавили обратно в 0003 (теперь в модели)
- **`session.expire_all()`** — обязателен перед перезагрузкой объекта после mutation, иначе SQLAlchemy отдаёт кэш

### Бот и роли
- **IsClient пропускает все роли** — иначе оператор/админ не может создавать заявки
- **Авто-промоушн admin** — при каждом апдейте если telegram_id совпадает
- **operator_id ≠ telegram_id** — в `place_bid` параметр `operator_id` — DB id (FK в bids). Нужен `UserRepo.get_by_id()` для получения telegram_id
- **action="view" конфликт** — клиентский список использует `action="client_view"`, иначе оператоский хэндлер перехватывает
- **"Перейти к заявке" не работает** — оператор не написал боту /start в личке. Добавлен try/except с алертом
- **Session cache** — после `bid_repo.create()` нужен `session.expire_all()` перед reload
