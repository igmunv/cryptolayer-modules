# Telegram

Модуль для Telegram, работающий через **личный аккаунт** (протокол MTProto, библиотека [`Telethon`](https://github.com/LonamiWebs/Telethon)) - сообщения и файлы отправляются напрямую в личных сообщениях, **без использования бота**. Поддерживает передачу как текстовых сообщений, так и файлов (`supports_files = True`).

## Данные для авторизации

- **API ID** и **API Hash** - получаются на [my.telegram.org](https://my.telegram.org) в разделе **API development tools** после входа под своим номером телефона. Нужно один раз создать там приложение.
- **Phone Number** - номер телефона аккаунта, от имени которого будет идти переписка, в международном формате (например `+79991234567`).

> **Важно:** `API ID`/`API Hash` идентифицируют не аккаунт, а "приложение". Их **не нужно** получать отдельно для каждого собеседника - оба тестировщика могут использовать **одну и ту же** пару `API ID`/`API Hash`, у каждого свой только `Phone Number` (свой аккаунт).

### Как получить API ID / API Hash

1. Зайдите на [my.telegram.org](https://my.telegram.org) и войдите по номеру телефона (придёт код в Telegram).
2. Откройте **API development tools**.
3. Заполните форму: `App title` и `Short name` - любые, `Platform` - любую (например Desktop), остальные поля можно оставить пустыми.
4. После отправки формы получите `api_id` (число) и `api_hash` (строка).

#### Если не получается зайти или создать приложение

- **my.telegram.org не открывается / регион заблокирован** - попробуйте зеркало `https://my.telegram.org/auth` или зайти через VPN.
- **Номер не зарегистрирован в Telegram** - сначала зарегистрируйте аккаунт в самом приложении Telegram, потом уже на my.telegram.org.
- **"Too many attempts" / flood-ошибка** - Telegram временно ограничивает попытки входа, нужно подождать (обычно от нескольких минут до часа).

#### Публичные (общие) API ID / Hash

Если совсем не получается создать своё приложение, для **тестирования** можно использовать публично известную пару, которую годами используют в туториалах и open-source Telegram-клиентах (например библиотека Pyrogram официально публикует её в своей документации как тестовую):

```
API ID:   2040
API Hash: b18441a1ff607e10a989891a5462e627
```

Эта пара **не принадлежит CryptoLayer** и не контролируется нами - это общеизвестные "публичные" данные, которыми пользуются тысячи сторонних приложений. Формально это нарушает условия использования Telegram API (каждое приложение должно регистрировать свои данные), поэтому:

- используйте это **только для быстрого локального теста**, не для постоянного/продакшн использования;
- из-за огромного количества чужих приложений с теми же данными возможны более частые ограничения скорости (`FloodWait`) или блокировки Telegram по этой паре без предупреждения;
- для реального использования всё же настоятельно рекомендуется получить свои `API ID`/`API Hash` - это бесплатно и занимает пару минут.

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

Module for Telegram that works through a **personal account** (MTProto protocol, using the [`Telethon`](https://github.com/LonamiWebs/Telethon) library) - messages and files are sent directly in private messages, **without using a bot**. Supports both text messages and file transfer (`supports_files = True`).

## Credentials

- **API ID** and **API Hash** - obtained at [my.telegram.org](https://my.telegram.org) under **API development tools** after logging in with your phone number. You need to create an application there once.
- **Phone Number** - the phone number of the account that will be used for the conversation, in international format (e.g. `+79991234567`).

> **Note:** `API ID`/`API Hash` identify the "application", not the account. You **don't** need a separate pair per companion - both testers can use the **same** `API ID`/`API Hash` pair, each only needs their own `Phone Number` (own account).

### How to get API ID / API Hash

1. Go to [my.telegram.org](https://my.telegram.org) and log in with your phone number (a code will arrive in Telegram).
2. Open **API development tools**.
3. Fill in the form: `App title` and `Short name` - anything, `Platform` - any (e.g. Desktop), other fields can be left empty.
4. After submitting, you get an `api_id` (number) and `api_hash` (string).

#### If you can't log in or create an application

- **my.telegram.org doesn't open / region is blocked** - try the mirror `https://my.telegram.org/auth` or use a VPN.
- **Phone number not registered on Telegram** - register an account in the Telegram app itself first, then on my.telegram.org.
- **"Too many attempts" / flood error** - Telegram temporarily rate-limits login attempts, wait a while (usually a few minutes to an hour).

#### Public (shared) API ID / Hash

If you really can't create your own application, for **testing** you can use a publicly known pair that has been used for years in tutorials and open-source Telegram clients (e.g. the Pyrogram library officially publishes it in its docs as a test credential):

```
API ID:   2040
API Hash: b18441a1ff607e10a989891a5462e627
```

This pair **does not belong to CryptoLayer** and is not controlled by us - it's widely known "public" data used by thousands of third-party applications. Technically this violates Telegram API's terms of use (every application is supposed to register its own credentials), so:

- use it **only for a quick local test**, not for permanent/production use;
- because of the huge number of unrelated apps sharing the same credentials, expect more frequent rate limits (`FloodWait`) or Telegram blocking this pair without warning;
- for real usage, it's still strongly recommended to get your own `API ID`/`API Hash` - it's free and takes a couple of minutes.

### Companion

The companion identifier (`user_id`) can be either:

- a username, e.g. `username` or `@username`;
- or a numeric Telegram user ID.

## First run

On the first run, Telethon will ask directly in the console for the confirmation code sent via Telegram (and the two-factor password, if enabled). After a successful login, a session file `cryptolayer_telegram_<phone number>.session` is created next to the module - on subsequent runs, re-entering the code won't be required as long as the session file is kept.

## How it works

- **Sending** - text is sent via `client.send_message`, files via `client.send_file`, directly to the private chat with the companion.
- **Receiving** - the module subscribes to the `NewMessage` event in the dialog with the specified companion. Incoming documents are downloaded to a temporary directory and forwarded to CryptoLayer, text messages are forwarded directly.

## Limitations

- Both companions must know each other's username or user ID in advance - Telethon cannot "discover" a user without it.
- The module requires a real Telegram account (phone number), not a bot - this is intentional, so the conversation happens in a regular private chat.
- Maximum file size is limited by Telegram itself (2 GB for regular accounts, 4 GB for Telegram Premium).

## Testing together via CryptoLayer CLI

You need two different Telegram accounts (two phone numbers) - one per tester. You can test on two different machines or on one (the session file is named `cryptolayer_telegram_<phone number>.session`, so different numbers don't conflict even if both testers run the CLI from the same directory).

1. Both sides exchange their username (`@username`) or numeric user ID beforehand - needed as the "companion" during chat setup in the CLI.
2. Each person downloads [CryptoLayer CLI](https://github.com/igmunv/cryptolayer-cli) and puts this module's folder (`Telegram/`) into `src/modules/`, as described in the [CryptoLayer docs](https://github.com/igmunv/cryptolayer/blob/main/docs/README.md#53-тестирование-собственного-модуля).
3. Each person runs the CLI, sets a password for local encryption, selects the **Telegram** module, and enters their `API ID`, `API Hash` (the same pair can be shared by both, see above) and their own `Phone Number`.
4. On first login, the console will ask for a confirmation code - enter the code received in Telegram (and the 2FA password, if enabled).
5. The CLI will then ask for the companion identifier - enter the other tester's username/ID from step 1.
6. Once both sides have started the CLI and pointed at each other, CryptoLayer establishes a secure tunnel - you can then chat with plain text, and send a file with `/file <path_to_file>` (e.g. `/file C:\Users\User\test.txt`).
7. Files received from the companion are saved by the CLI under `data/received_files/`, and the chat prints the saved path and original file name.

> The `/file` command was added to the CLI together with this module - if you have an older `cryptolayer-cli` checkout, update it (`git pull`), otherwise sending files from the chat won't be available.