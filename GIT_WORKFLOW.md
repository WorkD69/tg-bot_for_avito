# Git Workflow

Правила работы с репозиторием `tg-bot_for_avito`.

---

## Ветки

Работаем в одной ветке: `main`.

Для крупных экспериментальных изменений:
```bash
git checkout -b feature/название
# работа...
git checkout main
git merge feature/название
git branch -d feature/название
```

---

## Когда делать commit

**Делай commit после каждого завершённого блока работы:**
- Добавлен новый роутер или хэндлер
- Исправлена ошибка (bug fix)
- Добавлена миграция
- Обновлена конфигурация/инфраструктура
- Написана документация
- Завершён рефакторинг

**НЕ делай commit:**
- В середине незаконченной фичи
- Если тест или запуск падает
- Если файлы в нестабильном состоянии

---

## Когда делать push

Push только если:
1. Изменения завершены и проверены
2. Локальный запуск работает (docker-compose up -d --build)
3. Нет явных ошибок в логах
4. Ты не в середине большого блока работы

```bash
git push origin main
```

---

## Что НЕЛЬЗЯ коммитить

| Файл/папка | Почему |
|-----------|--------|
| `.env` | Реальные секреты — токены, пароли |
| `.env.prod` | То же самое |
| `wheels/` | Бинарные файлы, большой размер |
| `__pycache__/`, `*.pyc` | Генерируется автоматически |
| `tmp/` | Временные файлы |
| `*.log` | Логи |
| `*.db` | Локальные базы |

---

## Перед крупными изменениями

```bash
# 1. Убедиться, что всё закоммичено
git status

# 2. Сделать снапшот текущего состояния (если боишься)
git stash   # или просто commit "WIP: ..."

# 3. Приступать к работе
```

---

## Self-check перед commit

```bash
# Что изменилось?
git diff

# Что попадёт в commit?
git status

# Нет ли секретов?
git diff --staged | grep -i "token\|password\|secret\|pass1\|pass2"

# Синтаксис Python (для изменённых .py файлов)
"C:\Program Files\Python312\python.exe" -m py_compile app/path/to/file.py
```

---

## Формат commit message

```
<тип>: <краткое описание>

[опционально: детали]
```

**Типы:**
- `feat:` — новая функциональность
- `fix:` — исправление ошибки
- `refactor:` — рефакторинг без изменения поведения
- `docs:` — документация
- `infra:` — Docker, конфиги, CI/CD
- `migration:` — изменение схемы БД
- `chore:` — мелкие задачи (gitignore, cleanup)

**Примеры:**
```
feat: добавлен /cancelorder для принудительной отмены заявок
fix: исправлена гонка в close_auction под нагрузкой
migration: 0007_add_dispute_table
infra: docker-compose.prod.yml для production деплоя
docs: E2E_TEST_PLAN.md и LOCAL_QA_CHECKLIST.md
```

---

## Стандартный рабочий цикл

```bash
# 1. Начало работы — проверить статус
git status
git log --oneline -5

# 2. Сделать изменения...

# 3. Self-check
git diff
git status

# 4. Staged нужные файлы
git add app/bot/routers/new_handler.py
git add migrations/versions/0007_new_migration.py
# НЕ делай git add . без проверки!

# 5. Commit
git commit -m "feat: добавлен хэндлер для X"

# 6. Push (когда блок завершён и проверен)
git push origin main
```

---

## После деплоя на VPS

На сервере всегда только `git pull`, никогда не редактировать файлы напрямую:

```bash
cd /opt/tg-bot
git pull origin main
docker-compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build bot
```
