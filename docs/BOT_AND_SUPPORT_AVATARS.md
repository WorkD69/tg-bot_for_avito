# Аватарки бота и поддержки — Техническое задание

## Готовые SVG файлы

- `assets/branding/bot_avatar.svg` — аватар Telegram-бота
- `assets/branding/support_avatar.svg` — аватар @studario_support

Оба файла 512×512px, открываются в любом браузере.

---

## Аватар бота — описание дизайна

**Концепция:** Монограмма S на глубоком синем фоне с оранжевым акцентом.

**Элементы:**
- Круг 512×512, радиальный градиент от #2D5499 (светлее) до #0F2040 (темнее)
- Тонкое белое кольцо (opacity 8%) — добавляет глубину
- Белая буква "S" — Arial Black, 280px, жирная, по центру
- Оранжевый кружок (#FF6B35) в правом верхнем квадранте — акцент и фирменный элемент
- Полупрозрачная белая точка внутри оранжевого кружка — блик

**Читаемость в миниатюре:**
- При 30px: виден синий круг с белой S — узнаваемо
- При 50px: виден оранжевый акцент
- При 512px: полный эффект

---

## Аватар поддержки — описание дизайна

**Концепция:** Тот же стиль, но тёплый оранжевый фон + иконка диалога = "человек здесь".

**Элементы:**
- Круг 512×512, градиент от #FF8A5B до #D94F1A (тёплый оранжевый)
- Тонкое белое кольцо (opacity 12%)
- Белый speech bubble (прямоугольник 292×196 с закруглёнными углами + хвостик)
- Оранжевая буква "S" внутри пузырька — бренд-якорь
- Маленький синий кружок (#1B3A6B) как counter-акцент — зеркалит оранжевую точку на боте

**Логика:**
- Бот = синий фон → официальный сервис
- Support = оранжевый фон → живой человек, помощь, тепло
- Оба узнаются как часть одного бренда

---

## Экспорт в PNG для установки аватара

**В браузере:**
1. Открыть SVG файл в Chrome
2. Правая кнопка → "Сохранить изображение" (некоторые браузеры сохраняют как PNG)
3. Если нет → F12, вкладка Elements, найти svg → Screenshot элемента

**Через Inkscape (самый простой путь):**
```bash
# Установить Inkscape с inkscape.org, затем:
inkscape bot_avatar.svg --export-type=png -o bot_avatar.png
inkscape support_avatar.svg --export-type=png -o support_avatar.png
```

**Через Python (если есть cairosvg):**
```bash
pip install cairosvg
python -c "import cairosvg; cairosvg.svg2png(url='bot_avatar.svg', write_to='bot_avatar.png', output_width=512, output_height=512)"
```

**Онлайн конвертер:** svgtopng.com, cloudconvert.com

---

## Как установить аватар боту

1. Открыть @BotFather в Telegram
2. /mybots → выбрать бота → Edit Bot → Edit Botpic
3. Загрузить bot_avatar.png (512×512 или любой квадрат)

## Как установить аватар @studario_support

1. Зайти в аккаунт @studario_support
2. Настройки → Изменить профиль → Фото
3. Загрузить support_avatar.png

---

## Промты для AI-генерации (если нужна альтернатива SVG)

**Для аватара бота (Midjourney / DALL-E / Stable Diffusion):**
```
Minimalist app icon for educational service "Studario". 
Deep navy blue circular background (#1B3A6B). 
White bold letter "S" centered, clean sans-serif font.
Small bright orange circle accent in upper right area.
No gradients except subtle radial on background.
Clean, professional, trustworthy. 512x512px. Vector style.
No text other than the letter S.
```

**Для аватара поддержки:**
```
Minimalist Telegram avatar for customer support account.
Warm orange circular background.
White speech bubble icon in center.
Small letter "S" inside the speech bubble in orange.
Small navy blue dot accent.
Same visual family as the bot avatar but warmer, more human.
512x512px. Clean vector style. Professional but approachable.
```
