# Pre-Deploy Checklist

## Что уже готово (можно проверить локально)

### Инфраструктура
- [x] Docker Compose с db, redis, bot, caddy, backup сервисами
- [x] Healthcheck бота через python3 urllib (не curl — его нет в образе)
- [x] Alembic migrations 0001-0006, применяются автоматически при старте
- [x] Redis для FSM — состояния сохраняются между рестартами контейнера
- [x] APScheduler с SQLAlchemy jobstore — аукционные таймеры переживают рестарт
- [x] Backup-сервис: pg_dump -> Telegram, ежедневно в 03:00 UTC, хранит 30 дней

### Код и логика
- [x] Вся бизнес-логика: создание заявок, аукцион, оплата, решение
- [x] Robokassa callback: верификация подписи, idempotency, stale InvId
- [x] Ручной режим оплаты (ROBOKASSA_LOGIN пуст)
- [x] Переговоры по цене: принять / встречная сумма / отменить + revision guard
- [x] FSM guards: SolutionStates.waiting_files блокирует конкурирующие действия
- [x] post_commit pattern: уведомления только после успешного коммита
- [x] SELECT FOR UPDATE на всех state-changing операциях
- [x] Error monitoring: трейсбеки в личку админа (aiogram errors + logging.ERROR)
- [x] Startup validation: предупреждения о плохом конфиге при старте

### Конфигурация
- [x] `.env.example` с правильным форматом всех переменных
- [x] `.env.prod.example` для production
- [x] `docker-compose.prod.yml` — production override (порт 8000 не экспозируется)
- [x] `Caddyfile` — автоматический Let's Encrypt через `{env.DOMAIN}`

### Документация
- [x] `CLAUDE.md` — архитектура и паттерны для разработки
- [x] `PLAN.md` — полная документация проекта
- [x] `LOCAL_QA_CHECKLIST.md` — ручные тесты локально
- [x] `E2E_TEST_PLAN.md` — полный e2e сценарий
- [x] `DEPLOY_HANDOFF.md` — инструкция для финального деплоя
- [x] `GIT_WORKFLOW.md` — правила работы с git

---

## Что остаётся только на Deploy Stage

### Обязательно перед деплоем
- [ ] VPS с Ubuntu/Debian, Docker и Docker Compose установлены
- [ ] Купить домен и настроить A-запись -> IP VPS
- [ ] Заполнить `.env` на сервере (скопировать из `.env.prod.example`)
  - [ ] `BOT_TOKEN` — боевой токен от @BotFather
  - [ ] `DOMAIN=yourdomain.com` (без https://)
  - [ ] `WEBHOOK_BASE_URL=https://yourdomain.com`
  - [ ] `WEBHOOK_SECRET` — случайная строка (`openssl rand -hex 32`)
  - [ ] `ADMIN_TELEGRAM_ID` — ваш Telegram user_id
  - [ ] `OPERATOR_GROUP_ID` — id операторской группы
  - [ ] `POSTGRES_PASSWORD` — сильный пароль
  - [ ] `BACKUP_CHAT_ID` — id группы для бэкапов
- [ ] Добавить бота в операторскую группу как администратора
- [ ] Добавить бота в группу для бэкапов как члена
- [ ] Открыть порты 80, 443 на VPS (firewall/ufw)

### Robokassa (если нужна)
- [ ] Зарегистрировать магазин в Robokassa
- [ ] Заполнить ROBOKASSA_LOGIN, ROBOKASSA_PASS1, ROBOKASSA_PASS2
- [ ] Установить ROBOKASSA_IS_TEST=false
- [ ] Прописать в Robokassa Result URL: https://yourdomain.com/payment/robokassa

### После запуска на VPS
- [ ] Проверить `docker-compose logs -f bot` — нет ошибок
- [ ] Отправить `/start` боту — приходят 4 reply-кнопки
- [ ] Проверить webhook: `get_webhook_info` должен показывать 0 ошибок
- [ ] Создать тестовую заявку через весь флоу
- [ ] Дождаться 03:00 UTC — проверить, пришёл ли бэкап в группу

---

## Как понять, что проект готов к выкладке

1. Локальный e2e тест (`LOCAL_QA_CHECKLIST.md`) прошёл без ошибок
2. Все пункты "Обязательно перед деплоем" выполнены
3. `.env` на сервере заполнен и проверен
4. Startup validation не выдаёт ERROR-логов при старте
