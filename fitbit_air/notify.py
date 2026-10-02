"""Envía el resumen diario y las alertas al móvil (ntfy o Telegram)."""

import requests


def send(settings: dict, title: str, body: str, urgent: bool = False) -> list[str]:
    sent = []
    topic = settings.get("ntfy_topic")
    if topic:
        server = settings.get("ntfy_server", "https://ntfy.sh").rstrip("/")
        requests.post(f"{server}/{topic}", data=body.encode("utf-8"), timeout=20, headers={
            "Title": title.encode("utf-8"),
            "Priority": "high" if urgent else "default",
            "Tags": "warning" if urgent else "heart",
        }).raise_for_status()
        sent.append("ntfy")
    token, chat = settings.get("telegram_token"), settings.get("telegram_chat_id")
    if token and chat:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=20,
                      json={"chat_id": chat, "text": f"{title}\n\n{body}"}).raise_for_status()
        sent.append("telegram")
    return sent
