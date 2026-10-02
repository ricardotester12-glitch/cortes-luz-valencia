"""Avisa por Telegram cuando se va la luz en los circuitos de config.json["alertar"].

Necesita los secretos TELEGRAM_TOKEN y TELEGRAM_CHAT_ID; sin ellos no hace nada.
"""
import json
import os

import requests

from forecast import CONFIG, RAIZ

F_ENVIADAS = os.path.join(RAIZ, "data", "alertas_enviadas.json")


def main():
    token, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        print("Sin TELEGRAM_TOKEN/TELEGRAM_CHAT_ID: no se envían alertas")
        return
    data = json.load(open(os.path.join(RAIZ, "docs", "data.json"), encoding="utf-8"))
    enviadas = set(json.load(open(F_ENVIADAS))) if os.path.exists(F_ENVIADAS) else set()
    propio = CONFIG["circuito"]
    for nombre in CONFIG["alertar"]:
        c = data["circuitos"].get(f'{CONFIG["zona"]}/{nombre}')
        if not (c and c["en_curso_desde"]):
            continue
        clave = f"{nombre}|{c['en_curso_desde']}"
        if clave in enviadas:
            continue
        hora = c["en_curso_desde"][11:]
        texto = (f"⚡ Se fue la luz en tu circuito ({propio}) a las {hora}." if nombre == propio
                 else f"⚠️ Se fue la luz en {nombre} a las {hora}. Suele apagarse junto con {propio}: prepárate.")
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat, "text": texto}, timeout=20)
        enviadas.add(clave)
    json.dump(sorted(enviadas)[-200:], open(F_ENVIADAS, "w"))


if __name__ == "__main__":
    main()
