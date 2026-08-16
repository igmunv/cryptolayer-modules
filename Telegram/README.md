# Telegram

Модуль для Telegram, работающий через **личный аккаунт** (протокол MTProto, библиотека [`Telethon`](https://github.com/LonamiWebs/Telethon)) - сообщения и файлы отправляются напрямую в личных сообщениях. Поддерживает передачу как текстовых сообщений, так и файлов (`supports_files = True`).

## Данные для авторизации

- **API ID** и **API Hash** - получаются на [my.telegram.org](https://my.telegram.org) в разделе **API development tools** после входа под своим номером телефона. Нужно один раз создать там приложение.
- **Phone Number** - номер телефона аккаунта, от имени которого будет идти переписка, в международном формате (например `+79991234567`).

#### Публичные (общие) API ID / Hash

Если совсем не получается создать своё приложение, можно использовать следующие значения:

```
API ID:   2040
API Hash: b18441a1ff607e10a989891a5462e627
```

### Собеседник

В качестве идентификатора собеседника (`user_id`) можно указать:

- юзернейм, например `username` или `@username`;
- либо числовой Telegram user ID.

## Первый запуск

При первом запуске модуля Telethon запросит прямо в консоли код подтверждения, присланный в Telegram (и пароль двухфакторной аутентификации, если он включён). После успешного входа рядом с модулем создаётся файл сессии `cryptolayer_telegram_<номер телефона>.session` - при последующих запусках повторный ввод кода не потребуется, пока файл сессии не удалён.

## Как работает

- **Отправка** - текст отправляется через `client.send_message`, файлы - через `client.send_file`, напрямую в личный диалог с собеседником.
- **Приём** - модуль подписывается на событие `NewMessage` в диалоге с указанным собеседником. Входящие документы скачиваются во временную директорию и передаются в CryptoLayer, текстовые сообщения передаются напрямую.

## Ограничения

- Оба собеседника должны заранее знать юзернейм или user ID друг друга - Telethon не умеет "находить" пользователя без этого.
- Модуль требует реального аккаунта Telegram (номер телефона), а не бота - это осознанный выбор, чтобы переписка велась в обычных личных сообщениях.
- Максимальный размер файла ограничен возможностями самого Telegram (2 ГБ для обычных аккаунтов, 4 ГБ - для Telegram Premium).

## Тестирование вдвоём через CryptoLayer CLI

Для проверки нужны два разных Telegram-аккаунта (два номера телефона) - по одному на каждого тестировщика. Можно тестировать как на двух разных машинах, так и на одной (файл сессии называется `cryptolayer_telegram_<номер телефона>.session`, поэтому для разных номеров они не конфликтуют, даже если оба тестировщика запускают CLI из одной директории).

1. Оба заранее узнают друг у друга юзернейм (`@username`) или числовой user ID - это понадобится как "собеседник" на шаге настройки чата в CLI.
2. Каждый скачивает [CryptoLayer CLI](https://github.com/igmunv/cryptolayer-cli) и кладёт папку этого модуля (`Telegram/`) в `src/modules/`, как описано в [документации CryptoLayer](https://github.com/igmunv/cryptolayer/blob/main/docs/README.md#53-тестирование-собственного-модуля).
3. Каждый запускает CLI, придумывает пароль для локального шифрования, выбирает модуль **Telegram** и вводит свои `API ID`, `API Hash` (можно использовать одну и ту же пару на двоих, см. выше) и свой `Phone Number`.
4. При первом входе в консоли появится запрос кода подтверждения - вводится код, пришедший в Telegram (и пароль 2FA, если он включён).
5. Далее CLI попросит указать идентификатор собеседника - здесь вводится юзернейм/ID второго тестировщика из шага 1.
6. После того как оба запустили CLI и указали друг друга, CryptoLayer установит защищённый туннель - дальше можно писать обычный текст, а для отправки файла ввести `/file <путь_к_файлу>` (например `/file C:\Users\User\test.txt`).
7. Полученные от собеседника файлы CLI сохраняет в `data/received_files/` и выводит в чат путь к сохранённому файлу и оригинальное имя.

> Команда `/file` появилась в CLI вместе с этим модулем - если у вас более старая версия `cryptolayer-cli`, обновите её (`git pull`), иначе отправка файлов из чата будет недоступна.

---
# Telegram (EN)

Module for running via a **personal Telegram account** (MTProto protocol powered by [`Telethon`](https://github.com/LonamiWebs/Telethon)). Messages and files go straight into private chats without bots. Supports both text and file sharing (`supports_files = True`).

## Credentials

* **API ID** & **API Hash** — grab these at [my.telegram.org](https://my.telegram.org) under **API development tools** after logging in with your phone number. Set up an application there once.
* **Phone Number** — your account's phone number in international format (e.g. `+79991234567`).

#### Public (shared) API ID / Hash

If you can't create your own app right now, you can use these values:

```text
API ID:   2040
API Hash: b18441a1ff607e10a989891a5462e627

```

### Companion

The companion identifier (`user_id`) can be either:

* a username, e.g. `username` or `@username`;
* or a numeric Telegram user ID.

## First run

On the first launch, Telethon will ask directly in the terminal for the verification code sent to your Telegram (plus your 2FA password if enabled). Once logged in, a session file `cryptolayer_telegram_<phone number>.session` is created next to the module. As long as you keep this file, you won't need to enter the code again on future runs.

## How it works

* **Sending** — text goes out via `client.send_message`, files via `client.send_file` directly into the private chat with the peer.
* **Receiving** — the module listens for `NewMessage` events from the target user. Incoming documents land in a temp folder before passing to CryptoLayer; text messages go straight through.

## Limitations

* Both companions must exchange usernames or user IDs beforehand — Telethon can't look up users out of thin air.
* Requires a real phone account, not a bot — this is intentional so conversations happen in regular direct messages.
* File sizes follow standard Telegram limits (2 GB for regular accounts, 4 GB with Telegram Premium).

## Testing together via CryptoLayer CLI

You'll need two separate Telegram accounts (two phone numbers) — one per tester. Testing works fine across two different machines or side-by-side on the same PC (session files are tied to phone numbers, so they won't overwrite each other even if both testers run the CLI from the same folder).

1. Exchange usernames (`@username`) or numeric user IDs beforehand — you'll need this as the "companion" during CLI setup.
2. Each tester downloads [CryptoLayer CLI](https://github.com/igmunv/cryptolayer-cli) and drops this module's folder (`Telegram/`) into `src/modules/`, as described in the [CryptoLayer docs](https://github.com/igmunv/cryptolayer/blob/main/docs/README.md#53-тестирование-собственного-модуля).
3. Each person runs the CLI, sets a local encryption password, selects the **Telegram** module, and enters their `API ID`, `API Hash` (the same pair can be shared by both, see above), and their own `Phone Number`.
4. On the first launch, the terminal will ask for a verification code — enter the code sent to your Telegram (plus 2FA password if prompted).
5. Next, the CLI asks for the companion identifier — enter the other tester's username/ID from step 1.
6. Once both testers start the CLI and point to each other, CryptoLayer establishes a secure tunnel. You can chat with plain text, or send files using `/file <path_to_file>` (e.g. `/file C:\Users\User\test.txt`).
7. Incoming files are saved to `data/received_files/`, and the terminal prints the saved path and original file name.

> The `/file` command was added to the CLI alongside this module — if you're on an older `cryptolayer-cli` checkout, update it (`git pull`), otherwise file transfers won't work in the chat.