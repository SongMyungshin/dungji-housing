import json
from urllib import error, request

from config.settings import GEMINI_API_KEY, GEMINI_MODEL


class GeminiClient:
    def __init__(self, api_key=None, model=None):
        self.api_key = api_key if api_key is not None else GEMINI_API_KEY
        self.model = model or GEMINI_MODEL
        self.base_url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        )

    @property
    def enabled(self):
        return bool(self.api_key)

    def generate_json(self, prompt, temperature=0.2):
        if not self.enabled:
            raise RuntimeError("GEMINI_API_KEY environment variable is not set.")

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": temperature,
                "responseMimeType": "application/json",
            },
        }
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(
            f"{self.base_url}?key={self.api_key}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with request.urlopen(req, timeout=60) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"Gemini API error: {exc.code} {detail}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"Gemini network error: {exc}") from exc

        candidates = body.get("candidates", [])
        if not candidates:
            raise RuntimeError(f"Gemini returned no candidates: {body}")

        text = candidates[0]["content"]["parts"][0]["text"]
        return json.loads(text)
