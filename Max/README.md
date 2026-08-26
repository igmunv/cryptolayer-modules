# Max — модуль транспорта для CryptoLayer

Модуль обмена текстовыми сообщениями между двумя людьми через **личные аккаунты
MAX** со сквозным шифрованием, которое выполняет само ядро
[CryptoLayer](https://github.com/igmunv/cryptolayer). Модуль реализует контракт
`BaseModule` (пакет `cryptolayer-module-interface`).

Канал связи: **неофициальный пользовательский API** MAX (WebSocket
`wss://ws-api.oneme.ru`, протокол v11) через библиотеку
[vkmax](https://github.com/nsdkinx/vkmax). **Боты и Bot API не используются
вообще.** Полная документация проекта модуля: <https://github.com/Noisy-Auto-Flare/Max-module-cryptolayer>

## ⚠️ Важно: неофициальный API и условия использования

> **Этот модуль работает через неофициальный API MAX.** Официального
> пользовательского API у MAX нет, поэтому vkmax имитирует веб-клиент
> web.max.ru. Такое использование **нарушает условия использования MAX**
> и несёт **риск блокировки вашего аккаунта**. Протокол не документирован
> официально и может измениться в любой момент.
>
> **Используйте модуль на свой риск.** Автор модуля не отвечает за возможную
> блокировку аккаунта, потерю переписки или любые другие последствия.

## Как это работает

- При старте модуль логинится по токену (`login_by_token`) и держит
  WebSocket-соединение; входящие сообщения помечаются **прочитанными** примерно
  через секунду. Попытка показать **«в сети»** делается (`opcode 1
  {"interactive":true}`), но сервер MAX может не поднимать индикатор для
  неофициальных клиентов — это известная лимитация.
- Отправка: `send(text)` кладёт сообщение в очередь (макс. 1000),
  worker-поток отправляет по одному с «человеческой» случайной паузой
  (по умолчанию 2–6 секунд). Сетевые ошибки → повтор с экспоненциальной паузой
  5→120 с; ошибки авторизации/несуществующий чат → терминальная ошибка, worker
  останавливается.
- Приём: входящие push-пакеты (`opcode=128`) фильтруются по вашему диалогу и по
  отправителю (свои эхо-сообщения исключаются), текст передаётся ядру через
  `ingester`. Обрыв WebSocket восстанавливается автоматически: паузы
  5→10→20→40 с (потолок 60 с), после 5 неудачных попыток подряд — поток приёма
  останавливается с ошибкой в логе.
- Шифрование модулем не выполняется: байты шифрует и собирает ядро CryptoLayer
  (AES-256-GCM + подписи ECDSA), модуль гоняет естественный текст.

---

## Установка

### Вариант A — через CryptoLayer CLI (рекомендуется, после мерджа PR)

Модуль уже входит в сборник, отдельного копирования не нужно:

```bash
git clone https://github.com/igmunv/cryptolayer-cli.git
cd cryptolayer-cli
git submodule update --init --recursive
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # Linux/macOS
python src/modules/generate_reqs.py
python src/modules/generate_hidden_imports.py
pip install -r requirements.txt
pip install -r src/modules/common_requirements.txt
python src/cryptolayer_cli.py
```

### Вариант B — ручная установка в существующий CLI

```bash
Copy-Item -Recurse Max C:\путь\cryptolayer-cli\src\modules\Max  # Windows
# cp -r Max <путь>/src/modules/Max  # Linux/macOS
cd <путь>/src/modules
python generate_reqs.py
python generate_hidden_imports.py
pip install -r common_requirements.txt
```

> **Важно:** после копирования НЕ запускайте `./run.sh` и `git submodule update --init --recursive` — они удалят скопированный модуль!

### Шаг 1. Достаньте Token и Device ID (на КАЖДОМ компьютере)

1. Откройте [web.max.ru](https://web.max.ru) и войдите в аккаунт.
2. **F12** → **Application** → **Local Storage** → `https://web.max.ru`.
3. Найдите:
   - **`__oneme_auth`** — внутри JSON `{"token":"...","...":...}`. Скопируйте **только значение поля `token`** (без кавычек и скобок).
   - **`__oneme_device_id`** — копируйте целиком.

### Шаг 2. Узнайте Chat ID

Скрипт `discover_chats.py` лежит рядом с `main.py` (в этой папке). Запустите тем же Python, где стоит `vkmax`:

```bash
# из этой папки Max:
python discover_chats.py --token <TOKEN> --device-id <DEVICE_ID>
# или из корня CLI:
python src/modules/Max/discover_chats.py --token <TOKEN> --device-id <DEVICE_ID>
```

Таблица `chat_id — название/тип — последнее сообщение`, токены маскируются. Возьмите `chat_id` диалога с собеседником (у обоих собеседников ID **скорее всего одинаковый**, но проверьте каждый своим скриптом).

### Шаг 3. Заполните в CLI

1. **Пароль** — для локальных файлов CryptoLayer.
2. **Словарь WordCoder** — **одинаковый** на обеих сторонах.
3. **Модуль** — `MAX`.
4. **Credentials — 4 поля:**

| # | Поле | Что вписать |
|---|---|---|
| 1 | `Token` | `token` из `__oneme_auth` |
| 2 | `Device ID` | `__oneme_device_id` |
| 3 | `Мин. пауза, сек` | пусто = 2 |
| 4 | `Макс. пауза, сек` | пусто = 6 |

5. **Peer ID** — `Chat ID` из шага 2 (обязательно; одни credentials → много пиров).

При первом соединении сверьте подписи по телефону/лично и подтвердите.

## Скорость

Пауза 2–6 с — цена незаметности, сообщение ~1 КБ идёт минуты. Точная семантика потерь — ADR-3 в основном репозитории модуля.

## Устранение неполадок

| Симптом | Что делать |
|---|---|
| `ModuleNotFoundError: No module named 'vkmax'` | Забыли `generate_reqs`/`pip install -r common_requirements.txt` тем же интерпретатором. |
| `Exception: login.token` | Токен протух (ротируется при перелогине). Возьмите свежий `token`. |
| `TERMINAL send failure` | Неверный Chat ID — перепроверьте `discover_chats.py`. |
| `SyntaxError: import vkmax @ git+...` в `hidden_imports.py` | Баг генератора сборника на PEP 508 `pkg @ URL` — пропатчен в этом PR (`generate_hidden_imports.py`). |

## Ограничения

- Только текст; файлов и медиа нет.
- **«В сети» — best-effort**, сервер может не показывать индикатор для неофициальных клиентов.
