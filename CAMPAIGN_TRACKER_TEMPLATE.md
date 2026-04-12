# Campaign Tracker

Перенеси эту таблицу в Google Sheets или Notion.
Каждая строка = одна кампания. Не меняй campaign_id после публикации.

**Бот:** `t.me/studario1_bot`

---

## Таблица кампаний

| # | date_started | platform | source | campaign_id | link | ad_text_version | status | budget | notes | day3_result | day7_result | day14_result | decision |
|---|-------------|----------|--------|-------------|------|-----------------|--------|--------|-------|-------------|-------------|--------------|----------|
| 1 | | Авито | avito | avito_ad1 | `t.me/studario1_bot?start=avito_ad1` | Вариант 1 — Короткий оффер | draft | | | | | | |
| 2 | | Авито | avito | avito_ad2 | `t.me/studario1_bot?start=avito_ad2` | Вариант 2 — С примерами предметов | draft | | | | | | |
| 3 | | Авито | avito | avito_ad3 | `t.me/studario1_bot?start=avito_ad3` | Вариант 3 — С ценой | draft | | | | | | |
| 4 | | Авито | avito | avito_ad4 | `t.me/studario1_bot?start=avito_ad4` | Вариант 4 — Дедлайн | draft | | | | | | |
| 5 | | Авито | avito | avito_ad5 | `t.me/studario1_bot?start=avito_ad5` | Вариант 5 — Соцдоказательство | draft | | | | | | |
| 6 | | Telegram-канал | tg_channel | tg_channel_post1 | `t.me/studario1_bot?start=tg_channel_post1` | Пост 1 — Знакомство | draft | | | | | | |
| 7 | | Telegram-канал | tg_channel | tg_channel_post2 | `t.me/studario1_bot?start=tg_channel_post2` | Пост 2 — Кейс | draft | | | | | | |
| 8 | | Telegram-канал | tg_channel | tg_channel_story1 | `t.me/studario1_bot?start=tg_channel_story1` | Пост 3 — Закреплённый | draft | | | | | | |
| 9 | | Ручная рассылка | direct | direct_manual1 | `t.me/studario1_bot?start=direct_manual1` | — | draft | | | | | | |
| 10 | | Чаты / группы | direct | direct_chat1 | `t.me/studario1_bot?start=direct_chat1` | — | draft | | | | | | |

---

## Описание колонок

| Колонка | Что заполнять |
|---------|--------------|
| `date_started` | Дата публикации (например `2026-04-15`) |
| `platform` | Авито / Telegram-канал / VK / Ручная рассылка |
| `source` | Всегда одно из: `avito`, `tg_channel`, `direct` |
| `campaign_id` | Точно как в ссылке — не менять после старта |
| `link` | Полная ссылка для копипасты |
| `ad_text_version` | Какой вариант текста использовал |
| `status` | `draft` → `live` → `paused` / `stopped` |
| `budget` | Сколько потрачено на размещение (если платно) |
| `notes` | Любые заметки: "поднял в топ", "поменял фото" |
| `day3_result` | Число регистраций к дню 3 (`/sourcestats 7d`) |
| `day7_result` | Completion rate к дню 7 (`/campaignstats avito 7d`) |
| `day14_result` | Gross revenue к дню 14 (`/sourcefunnel 14d`) |
| `decision` | `scale` / `pause` / `rewrite` / `stop` |

---

## Статусы кампании

- `draft` — создана, но ещё не размещена
- `live` — активна, трафик идёт
- `paused` — временно приостановлена (например, нет операторов)
- `stopped` — остановлена насовсем (плохие результаты или неактуальна)

---

## Правила работы с таблицей

1. **Один campaign_id = одна строка навсегда.** Не редактируй campaign_id после публикации.
2. **Обновляй status сразу** при публикации: `draft` → `live`.
3. **Заполняй day3/day7/day14 по расписанию** — не откладывай, иначе забудешь.
4. **decision заполняй на день 7** — это момент принятия решения: масштабировать или остановить.
5. **Не удаляй строки** — даже остановленные кампании нужны для истории.

---

## Быстрая команда для сравнения

```
/campaignstats avito 7d
```

Скопируй campaign_id из вывода команды → найди строку в таблице → заполни day7_result.
