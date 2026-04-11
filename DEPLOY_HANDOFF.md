# Deploy Handoff

Инструкция для финального деплоя при появлении VPS, домена и боевых ключей.

---

## Шаг 1: Подготовка VPS

```bash
apt update && apt upgrade -y
apt install -y docker.io docker-compose-plugin git
usermod -aG docker $USER
newgrp docker
```

---

## Шаг 2: Клонировать репозиторий

```bash
cd /opt
git clone https://github.com/WorkD69/tg-bot_for_avito.git tg-bot
cd tg-bot
```

---

## Шаг 3: Заполнить .env

```bash
cp .env.prod.example .env
nano .env
```

| Переменная | Откуда |
|-----------|--------|
| `BOT_TOKEN` | @BotFather |
| `DOMAIN` | ваш домен без https:// |
| `WEBHOOK_BASE_URL` | https://yourdomain.com |
| `WEBHOOK_SECRET` | `openssl rand -hex 32` |
| `ADMIN_TELEGRAM_ID` | @userinfobot в Telegram |
| `OPERATOR_GROUP_ID` | @userinfobot в группе |
| `POSTGRES_PASSWORD` | придумать сильный пароль |
| `BACKUP_CHAT_ID` | id приватной группы для бэкапов |

---

## Шаг 4: DNS

A-запись: `yourdomain.com` -> IP VPS. Проверить: `nslookup yourdomain.com`

---

## Шаг 5: Открыть порты

```bash
ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 443/tcp && ufw enable
```

---

## Шаг 6: Telegram

1. Добавить бота в операторскую группу как **администратора**
2. Добавить бота в группу для бэкапов как **участника**

---

## Шаг 7: Запустить production

```bash
docker-compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
docker-compose logs -f bot
docker-compose logs -f caddy
```

---

## Шаг 8: Проверить

```bash
# Webhook
curl -s "https://api.telegram.org/bot<TOKEN>/getWebhookInfo" | python3 -m json.tool

# Health
curl https://yourdomain.com/health
```

Ожидаемый результат: `last_error_message` пустое, `url` правильный.

---

## Шаг 9: Robokassa (если нужна)

В ЛК Robokassa: Result URL = `https://yourdomain.com/payment/robokassa`, Method = POST

---

## Откат

```bash
# Просмотр логов
docker-compose logs --tail=100 bot

# Перезапуск без пересборки
docker-compose restart bot

# Полный пересбор
docker-compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build bot

# АВАРИЙНЫЙ сброс (данные БД потеряны!)
docker-compose down -v
docker-compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```
