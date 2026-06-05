# ProcessAI — бекенд (Flask)

Захищає ключі API: усі секрети (Azure OpenAI + Azure Speech) і всі промпти
живуть на сервері й **ніколи не потрапляють у браузер**. Фронтенд
(`processai_farmak.html`) звертається лише до цього бекенду.

## Що робить бекенд

- Тримає ключі та ендпоінти Azure (зі змінних оточення / `.env`).
- Тримає всі промпти (`prompts.py`) — їх немає у коді сторінки.
- Проксіює виклики:
  - `POST /api/interview` — хід інтерв'ю (системний промпт будується на сервері);
  - `POST /api/structure` — структурований ARIS JSON;
  - `POST /api/document` — діловий документ;
  - `POST /api/farmak-procedure` — повна процедура Farmak BPM;
  - `POST /api/diagrams` — опис діаграм ARIS;
  - `POST /api/stt` — розпізнавання мовлення (Azure STT);
  - `POST /api/tts` — синтез мовлення (Azure TTS);
  - `GET  /api/config` — несекретна конфігурація (чи увімкнено голос, підписи моделей).
- Віддає сам HTML-додаток за адресою `/`.

## Запуск

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # необов'язково
pip install -r requirements.txt
cp .env.example .env          # відредагуйте: впишіть ключі Azure
python app.py
```

Відкрийте **http://localhost:8000/** — додаток працює через бекенд.

> Важливо: відкривайте додаток саме за адресою бекенду (`http://localhost:8000/`),
> а не як локальний файл, щоб запити `/api/...` йшли на сервер.

## Змінні оточення

Дивіться `.env.example`. Мінімум для роботи тексту:
`AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_KEY`, `AZURE_OPENAI_DEPLOYMENT`.
Для голосу додатково: `AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION`.

## Чому ключі тепер захищені

Раніше ключі вводилися у вкладці «Налаштування» і зберігалися в браузері
(`localStorage`), а запити йшли напряму в Azure — ключ можна було побачити у
DevTools / Network. Тепер ключі є лише на сервері, а браузер бачить тільки
запити до `/api/...` без жодних секретів. Вкладки «Налаштування» та «Промпт»
приховано.
