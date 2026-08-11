# Локальный AI-советник для Telegram Desktop

Windows-приложение на Python/PySide6, которое анализирует открытый чат в Telegram Desktop и показывает стратегию переговоров с черновиками ответа. Приложение не логинится в Telegram, не использует Bot API и не отправляет сообщения автоматически.

## Возможности MVP

- Импорт Telegram export в `.json`, частичная поддержка `.html`.
- Импорт переписки напрямую через Telegram MTProto при наличии `api_id`/`api_hash` и локальной session.
- Для HTML export из нескольких частей (`messages.html`, `messages2.html`, ...) достаточно выбрать любой файл серии: приложение импортирует все части из папки по порядку.
- При HTML/JSON импорте создается очищенный `.txt` без HTML-разметки в `data/cleaned-exports`.
- Имя профиля берется локально без модели: для JSON из `name/title`; для HTML сначала из имени папки, если папка похожа на имя контакта, иначе из заголовка страницы чата.
- Создание локального профиля заказчика в SQLite.
- Захват активного окна Telegram Desktop по кнопке или `Ctrl+Shift+S`.
- Анализ диалога через локальный Codex Gateway `/v1/responses` с JSON-ответом.
- Комментарий/цель пользователя к текущей ситуации: можно указать, чего вы хотите добиться, и получить переписанный ответ под эту цель.
- Постоянная память стиля: при импорте приложение один раз строит короткую локальную инструкцию по вашему стилю письма и сохраняет ее в SQLite.
- Инструкцию стиля можно посмотреть и вручную поправить в поле `Мой стиль`, например добавить сухой юмор, сарказм или самоиронию.
- В overlay есть тон `мой стиль`: он использует сохраненную инструкцию, включая юмор/сарказм, если вы это указали.
- В overlay есть тон `стратегичный`: он делает ответ более расчетливым, с фокусом на мягкую силу, управление эмоциями заказчика, выгоду, рычаги, условные уступки и пространство для маневра.
- Deprecated fallback через OpenAI API или OCR, если явно выбран другой backend.
- Always-on-top overlay справа с резюме, рисками, стратегией, вариантами ответа и кнопкой `Copy`.

## Установка

```powershell
cd C:\work\pain
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
cd codex-gateway
.\install-gateway.ps1
cd ..
```

Codex Gateway запускается на этом компьютере и использует локальную авторизацию Codex CLI из `%USERPROFILE%\.codex`.

Пример `.env`:

```env
ANALYSIS_BACKEND=codex
OPENCLAW_BASE_URL=http://127.0.0.1:18790
OPENCLAW_TOKEN=
OPENCLAW_MODEL=
OPENCLAW_SESSION_KEY=pain-advisor
STUB_RESPONSE_PATH=

OPENCLAW_SSH_TUNNEL_ENABLED=false
OPENCLAW_SSH_TUNNEL_TARGET=openclaw-server
OPENCLAW_SSH_TUNNEL_LOCAL_PORT=18790
OPENCLAW_SSH_TUNNEL_REMOTE_HOST=127.0.0.1
OPENCLAW_SSH_TUNNEL_REMOTE_PORT=18790

APP_DB_PATH=data/advisor.sqlite3
SAVE_SCREENSHOTS=false
SCREENSHOT_DIR=data/screenshots
TELEGRAM_WINDOW_TITLE=Telegram
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
TELEGRAM_PHONE=
TELEGRAM_SESSION_PATH=data/telegram.session
TELEGRAM_HISTORY_LIMIT=500

# Deprecated fallback only
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4.1-mini
```

## Запуск

```powershell
.\start_pain_advisor.cmd
```

Этот файл сначала проверяет/запускает локальный Codex Gateway, затем открывает desktop-приложение.

Для ручного запуска компонентов:

```powershell
.\codex-gateway\start-gateway.ps1
$env:PYTHONPATH = "$PWD\src"
.\.venv\Scripts\python.exe -m pain_assistant
```

Если пакет не находится при запуске из исходников:

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m pain_assistant
```

Проверка без старта UI:

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m pain_assistant --check
```

Также на рабочем столе создается ярлык `Telegram Negotiation Advisor`, который запускает `C:\work\pain\start_pain_advisor.cmd`.

## Использование

1. Откройте Telegram Desktop и нужный чат.
2. В приложении нажмите `Импорт export`, чтобы загрузить историю переписки `.json` или `.html`. Если Telegram разбил HTML export на `messages.html`, `messages2.html` и далее, выберите любой из этих файлов. Для удобного имени профиля можно назвать папку экспорта именем человека.
   При импорте также обновляется короткая инструкция вашего стиля письма. Дальше при анализе отправляется только эта выжимка, а не вся история для определения стиля.
   Если этот же чат уже импортировался и файл экспорта не изменился, приложение переиспользует сохраненный профиль и стиль без повторного AI-анализа импорта.
3. Вместо export можно нажать `Импорт Telegram`, указать `@username`, id или точное название чата, и приложение прочитает последние сообщения через MTProto.
4. Оставьте флажок `Скриншот` включенным, если нужно анализировать текущее окно Telegram.
5. При необходимости заполните поле `Мой комментарий к текущему анализу`: например, хотите добиться предоплаты, мягко отказать от новых правок или зафиксировать срок.
6. Нажмите `Предложить стратегию` или используйте `Ctrl+Shift+S`.
7. Если рекомендация в целом подходит, но цель учтена не до конца, в overlay заполните `Моя цель / комментарий` и нажмите `Переписать`.
8. Проверьте overlay справа и скопируйте подходящий вариант ответа.

## Telegram MTProto

Для прямого чтения переписки заполните в `.env`:

```env
TELEGRAM_API_ID=...
TELEGRAM_API_HASH=...
TELEGRAM_PHONE=+79990000000
TELEGRAM_SESSION_PATH=data/telegram.session
TELEGRAM_HISTORY_LIMIT=500
```

Первый вход выполняется один раз из PowerShell:

```powershell
$env:PYTHONPATH = "$PWD\src"
.\.venv\Scripts\python.exe -m pain_assistant --telegram-login
```

После ввода кода Telegram создаст локальную session в `data/telegram.session`. Дальше кнопка `Импорт Telegram` будет читать переписку без повторного кода, пока session действительна.

## Local Codex Gateway

Desktop-клиент отправляет контекст в локальный gateway на `http://127.0.0.1:18790/v1/responses`. Gateway запускает локальный `codex exec` и возвращает Responses-совместимый JSON.

В gateway передаются:

- скриншот окна Telegram как base64 `input_image`;
- профиль заказчика;
- последние импортированные сообщения;
- выбранный тон ответа.
- комментарий/цель пользователя, если поле заполнено.
- сохраненную короткую инструкцию вашего стиля письма, если она уже построена.

При повторном импорте неизменного export приложение сверяет локальный fingerprint очищенных сообщений и не пересчитывает summary/стиль. При изменившемся export существующий профиль обновляется, а не создается заново.

Gateway должен вернуть JSON в формате `AdvisorResult`. Его состояние проверяется командой:

```powershell
Invoke-RestMethod http://127.0.0.1:18790/health
```

Логи находятся в `codex-gateway/gateway.out.log` и `codex-gateway/gateway.err.log`. Остановка:

```powershell
.\codex-gateway\stop-gateway.ps1
```

После переноса gateway на сервер нужно будет снова включить SSH-туннель:

```env
OPENCLAW_SSH_TUNNEL_ENABLED=true
OPENCLAW_SSH_TUNNEL_TARGET=openclaw-server
OPENCLAW_SSH_TUNNEL_LOCAL_PORT=18790
OPENCLAW_SSH_TUNNEL_REMOTE_HOST=127.0.0.1
OPENCLAW_SSH_TUNNEL_REMOTE_PORT=18790
```

Не выставляйте endpoint в публичный интернет без авторизации. При переносе оставьте gateway на `127.0.0.1` и подключайтесь через SSH.

## Server Screenshots

Если включено:

```env
SERVER_SCREENSHOT_UPLOAD=true
SERVER_SCREENSHOT_SSH_TARGET=user@192.168.2.22
SERVER_SCREENSHOT_REMOTE_DIR=C:\Users\user\.openclaw\workspace\incoming-screenshots
```

клиент после захвата Telegram загружает PNG на сервер через `scp`, а в запрос AI gateway добавляет путь к файлу. Для запуска через ярлык нужен SSH-доступ без интерактивного пароля, например через SSH key. Если `scp` запросит пароль, GUI-процесс не сможет его ввести.

## Mock/stub проверка без Codex

Для проверки UI без запуска Codex можно использовать встроенный stub backend.

Чтобы проверить клиент без сети:

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m pain_assistant --stub-check
```

Чтобы открыть UI со stub-ответами, временно поставьте:

```env
ANALYSIS_BACKEND=stub
```

После этого кнопка `Предложить стратегию` пройдет тот же UI-путь, но вместо сети вернет тестовый `AdvisorResult`.

Можно подставить свой stub JSON из файла:

```env
ANALYSIS_BACKEND=stub
STUB_RESPONSE_PATH=C:\work\pain\data\stub_response.json
```

Файл должен содержать JSON в формате `AdvisorResult`.

## Приватность и ограничения

- Скриншоты не сохраняются на диск по умолчанию.
- Чтобы разрешить сохранение для отладки, установите `SAVE_SCREENSHOTS=true`.
- Приложение захватывает только окно с заголовком `Telegram` или активное окно Telegram, не нажимает кнопки в Telegram.
- Глобальная горячая клавиша использует пакет `keyboard`; на некоторых системах Windows ему могут понадобиться повышенные права. Кнопка в UI работает без этого.

## Tesseract fallback

Для OCR fallback установите Tesseract OCR:

- Windows installer: https://github.com/UB-Mannheim/tesseract/wiki
- После установки убедитесь, что `tesseract.exe` доступен в `PATH`.

Fallback хуже vision-модели: он извлекает только текст и использует эвристики для оценки тона и рисков.

## Структура

```text
src/pain_assistant/
  __main__.py          # точка входа
  ai_client.py         # deprecated OpenAI/OCR fallback
  analysis_client.py   # выбор backend
  config.py            # env/config
  db.py                # SQLite
  json_utils.py        # разбор JSON-ответов
  models.py            # dataclass-модели
  openclaw_client.py   # клиент AI gateway /v1/responses
  prompts.py           # системные промпты
  screen_capture.py    # поиск Telegram и скриншот
  stub_client.py       # mock/stub backend
  telegram_importer.py # импорт export, локальная очистка HTML и сборка multipart HTML
  ui.py                # PySide6 UI и overlay
data/                  # локальная база и опциональные скриншоты
codex-gateway/         # локальный HTTP gateway вокруг codex exec
```

## Формат ответа агента

```json
{
  "situation_summary": "...",
  "client_intent": "...",
  "risk": "...",
  "recommended_tone": "деловой",
  "tone_reason": "почему этот стиль сейчас лучше",
  "recommended_strategy": "...",
  "do_not_do": ["..."],
  "best_reply": "...",
  "soft_reply": "...",
  "hard_reply": "...",
  "confidence": 0.0
}
```

## Backends

По умолчанию:

```env
ANALYSIS_BACKEND=codex
```

Для отладки можно явно выбрать fallback:

```env
ANALYSIS_BACKEND=ocr
```

Mock/stub без сети:

```env
ANALYSIS_BACKEND=stub
```

или deprecated OpenAI path:

```env
ANALYSIS_BACKEND=openai
OPENAI_API_KEY=sk-...
```
