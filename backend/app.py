# -*- coding: utf-8 -*-
"""
ProcessAI — Flask backend / API proxy.

Призначення:
  • Тримати ВСІ секрети (ключі Azure OpenAI та Azure Speech) на сервері —
    вони ніколи не потрапляють у браузер.
  • Тримати ВСІ промпти на сервері (див. prompts.py).
  • Проксіювати виклики до Azure (chat, STT, TTS), щоб фронтенд звертався
    лише до власного бекенду.
  • Віддавати сам HTML-додаток (same-origin, без CORS-проблем).

Запуск:
  cd backend
  cp .env.example .env   # та заповнити ключі
  pip install -r requirements.txt
  python app.py
  # відкрити http://localhost:8000/
"""

import os
import json

import requests
from flask import Flask, request, jsonify, send_from_directory, Response

import prompts

# ── .env (необов'язково) ───────────────────────────────────────────────
try:
    from dotenv import load_dotenv  # python-dotenv
    load_dotenv()
except Exception:
    pass


# ════════════════════════════════════════════════════════════════════════
#  КОНФІГУРАЦІЯ (лише з оточення — нічого не хардкодимо)
# ════════════════════════════════════════════════════════════════════════
def env(name, default=""):
    return (os.environ.get(name, default) or "").strip()


AZURE_OPENAI_ENDPOINT    = env("AZURE_OPENAI_ENDPOINT").rstrip("/")
AZURE_OPENAI_KEY         = env("AZURE_OPENAI_KEY")
AZURE_OPENAI_API_VERSION = env("AZURE_OPENAI_API_VERSION", "2024-02-01")
DEPLOYMENT_PRIMARY       = env("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
DEPLOYMENT_SECONDARY     = env("AZURE_OPENAI_DEPLOYMENT2", "") or DEPLOYMENT_PRIMARY

AZURE_SPEECH_KEY    = env("AZURE_SPEECH_KEY")
AZURE_SPEECH_REGION = env("AZURE_SPEECH_REGION")
TTS_VOICE           = env("AZURE_TTS_VOICE", "uk-UA-PolinaNeural")
TTS_RATE            = env("AZURE_TTS_RATE", "1.0")
STT_LANG            = env("AZURE_STT_LANG", "uk-UA")

# Дружні підписи моделей для бейджів (назва деплойменту — не секрет)
MODEL_LABEL  = env("MODEL_LABEL",  DEPLOYMENT_PRIMARY)
MODEL2_LABEL = env("MODEL2_LABEL", DEPLOYMENT_SECONDARY)

PORT = int(env("PORT", "8000") or "8000")

# Папка з HTML-додатком (на рівень вище за backend/)
APP_DIR  = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(APP_DIR)
HTML_FILE = "processai_farmak.html"

REASONING_MODELS = [
    "gpt-5.1", "gpt-5.1-chat", "gpt-5.1-chat-latest",
    "gpt-5.1-codex", "gpt-5.1-codex-max",
    "o1", "o1-mini", "o1-preview",
    "o3", "o3-mini", "o4-mini",
]


def is_reasoning_model(name: str) -> bool:
    if not name:
        return False
    n = name.lower()
    return any(r in n for r in REASONING_MODELS)


app = Flask(__name__, static_folder=None)


# Дозволяємо звертатися і з file:// (на випадок, якщо HTML відкрито не з бекенду)
@app.after_request
def add_cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


class ApiError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.message = message
        self.status = status


def _require_openai():
    if not AZURE_OPENAI_ENDPOINT or not AZURE_OPENAI_KEY:
        raise ApiError("Бекенд не налаштовано: відсутні AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_KEY", 500)


def call_openai(deployment, messages, max_tokens, temperature, timeout=90):
    """Єдина точка виклику Azure OpenAI Chat Completions. Ключ додається тут."""
    _require_openai()
    url = (f"{AZURE_OPENAI_ENDPOINT}/openai/deployments/{deployment}"
           f"/chat/completions?api-version={AZURE_OPENAI_API_VERSION}")
    token_key = "max_completion_tokens" if is_reasoning_model(deployment) else "max_tokens"
    body = {"messages": messages, token_key: max_tokens}
    if not is_reasoning_model(deployment):
        body["temperature"] = temperature

    try:
        r = requests.post(
            url,
            headers={"Content-Type": "application/json", "api-key": AZURE_OPENAI_KEY},
            data=json.dumps(body),
            timeout=timeout,
        )
    except requests.exceptions.Timeout:
        raise ApiError("Модель не відповіла вчасно (timeout)", 504)
    except requests.exceptions.RequestException as e:
        raise ApiError(f"Помилка з'єднання з Azure OpenAI: {e}", 502)

    if not r.ok:
        msg = f"HTTP {r.status_code}"
        try:
            msg = r.json().get("error", {}).get("message", msg)
        except Exception:
            body_txt = (r.text or "").strip()
            if body_txt:
                msg = f"{msg}: {body_txt[:300]}"
        raise ApiError(f"Azure OpenAI: {msg}", r.status_code)

    try:
        data = r.json()
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError, TypeError):
        # Несподівана відповідь (напр., фільтр контенту або інша схема)
        raise ApiError("Azure OpenAI повернув несподівану відповідь: "
                       + (r.text or "")[:300], 502)


# ════════════════════════════════════════════════════════════════════════
#  СТАТИКА — віддаємо HTML-додаток та супутні файли
# ════════════════════════════════════════════════════════════════════════
@app.route("/")
def index():
    return send_from_directory(ROOT_DIR, HTML_FILE)


@app.route("/<path:filename>")
def static_files(filename):
    # Дозволяємо лише ті типи, що потрібні додатку
    if filename.endswith((".html", ".js", ".css", ".png", ".svg", ".ico", ".woff", ".woff2")):
        return send_from_directory(ROOT_DIR, filename)
    return ("Not found", 404)


# ════════════════════════════════════════════════════════════════════════
#  API
# ════════════════════════════════════════════════════════════════════════
@app.route("/api/config", methods=["GET", "OPTIONS"])
def api_config():
    """Несекретна конфігурація для фронтенду (без жодних ключів)."""
    if request.method == "OPTIONS":
        return ("", 204)
    return jsonify({
        "openaiReady": bool(AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_KEY),
        "speechEnabled": bool(AZURE_SPEECH_KEY and AZURE_SPEECH_REGION),
        "modelLabel": MODEL_LABEL,
        "model2Label": MODEL2_LABEL,
        "lang": STT_LANG,
        "voice": TTS_VOICE,
        "ttsRate": TTS_RATE,
    })


@app.route("/api/interview", methods=["POST", "OPTIONS"])
def api_interview():
    """Хід інтерв'ю. Системний промпт будується на сервері з метаданих сесії."""
    if request.method == "OPTIONS":
        return ("", 204)
    payload = request.get_json(force=True, silent=True) or {}
    meta = payload.get("meta") or {}
    turns = payload.get("messages") or []
    # Лишаємо тільки рольові репліки користувача/асистента
    turns = [{"role": m.get("role"), "content": m.get("content", "")}
             for m in turns if m.get("role") in ("user", "assistant")]

    system_msg = {"role": "system", "content": prompts.build_interview_system(meta)}
    messages = [system_msg] + turns
    content = call_openai(DEPLOYMENT_PRIMARY, messages, 700, 0.35, timeout=60)
    return jsonify({"reply": content})


@app.route("/api/structure", methods=["POST", "OPTIONS"])
def api_structure():
    if request.method == "OPTIONS":
        return ("", 204)
    payload = request.get_json(force=True, silent=True) or {}
    prompt = prompts.build_struct_prompt(payload.get("transcript", ""))
    content = call_openai(DEPLOYMENT_SECONDARY, [{"role": "user", "content": prompt}], 2500, 0.1)
    return jsonify({"content": content})


@app.route("/api/document", methods=["POST", "OPTIONS"])
def api_document():
    if request.method == "OPTIONS":
        return ("", 204)
    payload = request.get_json(force=True, silent=True) or {}
    prompt = prompts.build_doc_prompt(
        payload.get("transcript", ""),
        payload.get("docLabel", "Документ"),
        payload.get("name", ""),
    )
    content = call_openai(DEPLOYMENT_SECONDARY, [{"role": "user", "content": prompt}], 3000, 0.2)
    return jsonify({"content": content})


@app.route("/api/farmak-procedure", methods=["POST", "OPTIONS"])
def api_farmak_procedure():
    if request.method == "OPTIONS":
        return ("", 204)
    payload = request.get_json(force=True, silent=True) or {}
    prompt = prompts.build_gen_prompt(payload.get("transcript", ""), payload.get("name", ""))
    content = call_openai(DEPLOYMENT_SECONDARY, [{"role": "user", "content": prompt}], 4000, 0.15, timeout=120)
    return jsonify({"content": content})


@app.route("/api/diagrams", methods=["POST", "OPTIONS"])
def api_diagrams():
    if request.method == "OPTIONS":
        return ("", 204)
    payload = request.get_json(force=True, silent=True) or {}
    prompt = prompts.build_diag_prompt(payload.get("transcript", ""), payload.get("name", ""))
    content = call_openai(DEPLOYMENT_SECONDARY, [{"role": "user", "content": prompt}], 3000, 0.1)
    return jsonify({"content": content})


# ── Azure Speech: STT ───────────────────────────────────────────────────
@app.route("/api/stt", methods=["POST", "OPTIONS"])
def api_stt():
    if request.method == "OPTIONS":
        return ("", 204)
    if not (AZURE_SPEECH_KEY and AZURE_SPEECH_REGION):
        raise ApiError("Speech не налаштовано на бекенді", 503)

    audio = request.get_data()
    lang = request.args.get("lang", STT_LANG)
    url = (f"https://{AZURE_SPEECH_REGION}.stt.speech.microsoft.com/speech/recognition/"
           f"conversation/cognitiveservices/v1?language={lang}&format=detailed")
    try:
        r = requests.post(
            url,
            headers={
                "Ocp-Apim-Subscription-Key": AZURE_SPEECH_KEY,
                "Content-Type": "audio/wav; codecs=audio/pcm; samplerate=16000",
            },
            data=audio,
            timeout=30,
        )
    except requests.exceptions.RequestException as e:
        raise ApiError(f"STT помилка з'єднання: {e}", 502)

    if not r.ok:
        raise ApiError(f"STT HTTP {r.status_code}: {r.text[:160]}", r.status_code)

    d = r.json()
    status = d.get("RecognitionStatus")
    if status in ("NoMatch", "InitialSilenceTimeout"):
        return jsonify({"text": ""})
    if status and status != "Success":
        raise ApiError(f"STT: {status}", 422)
    text = d.get("DisplayText") or (d.get("NBest") or [{}])[0].get("Display", "")
    return jsonify({"text": text})


# ── Azure Speech: TTS ───────────────────────────────────────────────────
def _xml_escape(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


@app.route("/api/tts", methods=["POST", "OPTIONS"])
def api_tts():
    if request.method == "OPTIONS":
        return ("", 204)
    if not (AZURE_SPEECH_KEY and AZURE_SPEECH_REGION):
        raise ApiError("Speech не налаштовано на бекенді", 503)

    payload = request.get_json(force=True, silent=True) or {}
    text = payload.get("text", "")
    voice = TTS_VOICE
    rate = str(payload.get("rate") or TTS_RATE)
    lang = voice[:5]

    ssml = (f"<speak version='1.0' xml:lang='{lang}'>"
            f"<voice name='{voice}'><prosody rate='{rate}'>{_xml_escape(text)}</prosody></voice></speak>")

    url = f"https://{AZURE_SPEECH_REGION}.tts.speech.microsoft.com/cognitiveservices/v1"
    try:
        r = requests.post(
            url,
            headers={
                "Ocp-Apim-Subscription-Key": AZURE_SPEECH_KEY,
                "Content-Type": "application/ssml+xml",
                "X-Microsoft-OutputFormat": "audio-16khz-128kbitrate-mono-mp3",
            },
            data=ssml.encode("utf-8"),
            timeout=30,
        )
    except requests.exceptions.RequestException as e:
        raise ApiError(f"TTS помилка з'єднання: {e}", 502)

    if not r.ok:
        raise ApiError(f"TTS HTTP {r.status_code}", r.status_code)

    return Response(r.content, mimetype="audio/mpeg")


@app.errorhandler(ApiError)
def handle_api_error(e):
    return jsonify({"error": e.message}), e.status


@app.errorhandler(Exception)
def handle_unexpected(e):
    import traceback
    traceback.print_exc()
    return jsonify({"error": f"{type(e).__name__}: {e}"}), 500


if __name__ == "__main__":
    print(f"ProcessAI backend → http://localhost:{PORT}/")
    print(f"  OpenAI ready : {bool(AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_KEY)}")
    print(f"  Speech ready : {bool(AZURE_SPEECH_KEY and AZURE_SPEECH_REGION)}")
    app.run(host="0.0.0.0", port=PORT, debug=False)
