# VK Group

Модуль для российскиой социальной сети ВКонтакте. Авторизация происходит от имени группы с помощью идентификатора группы и токена, который можно получить в настройках группы во ВКонтакте.

Чтобы использовать этот модуль, собеседникам необходимо:

- Обеим создать группу во ВКонтакте
- Получить токен группы
- Включить LongPoll с разрешением на приём события о новом сообщении
- Включить возможности бота для группы и разрешить добавлять группу в беседы
- Создать общую беседу
- Добавить в беседу группы собеседников
- Выдать группам права администратора в этой беседе
- Получить ID беседы для групп (если это новые группы и они не разу не были добавлены в беседы, то ID всегда равен `2000000001`)

Далее при запуске приложения на базе CryptoLayer, необходимо:
- В поле `Group ID` указать идентификатор группы (без минуса)
- В поле `Token` указать токен группы
- При запросе `User ID` указать ID беседы для этой группы

Данный модуль позволяет избегать капчи и блокировку аккаунта, так как используется API групп, а не аккаунта пользователя.

---

Module for the Russian social network VKontakte. Authorization is performed on behalf of a group using a Group ID and a token, which can be obtained in the group settings on VKontakte.

To use this module, both participants need to:

- Each create a VKontakte group
- Obtain a group token
- Enable LongPoll API with permissions for "New message" events
- Enable Bot features for the group and allow adding the group to chats
- Create a shared group chat
- Add each participant's group to the chat
- Grant admin privileges to the groups in this chat
- Get the Chat ID for the groups (if these are new groups that have never been added to chats before, the ID is always `2000000001`)

Then, when launching the CryptoLayer-based application:
- Enter the group identifier (without the minus sign) in the `Group ID` field
- Enter the group token in the `Token` field
- When prompted for `User ID`, enter the Chat ID for this group

This module helps avoid captchas and account bans, as it uses the Group API instead of a personal user account.
