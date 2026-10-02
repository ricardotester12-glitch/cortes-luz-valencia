"""Registra alertas de los circuitos de config.json["alertar"] y las envía por Telegram.

El registro (docs/alertas.json) se llena siempre; el envío por Telegram solo si existen
los secretos TELEGRAM_TOKEN y TELEGRAM_CHAT_ID.
"""
import json
import os
from datetime import datetime

import requests

from collect import FMT
from forecast import CONFIG, RAIZ, VE

F_ALERTAS = os.path.join(RAIZ, "docs", "alertas.json")


def enviar(texto):
    token, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return False
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat, "text": texto}, timeout=20)
    return r.ok


def main():
    data = json.load(open(os.path.join(RAIZ, "docs", "data.json"), encoding="utf-8"))
    alertas = json.load(open(F_ALERTAS, encoding="utf-8")) if os.path.exists(F_ALERTAS) else []
    claves = {a["clave"] for a in alertas}
    ahora = datetime.now(VE).strftime(FMT)
    propio = CONFIG["circuito"]
    nuevas = []

    for nombre in CONFIG["alertar"]:
        c = data["circuitos"].get(f'{CONFIG["zona"]}/{nombre}')
        if not c:
            continue
        # Se fue la luz
        if c["en_curso_desde"] and f'off|{nombre}|{c["en_curso_desde"]}' not in claves:
            hora = c["en_curso_desde"][11:]
            texto = (f"⚡ Se fue la luz en tu circuito ({propio}) a las {hora}." if nombre == propio
                     else f"⚠️ Se fue la luz en {nombre} a las {hora}. Suele apagarse junto con {propio}: prepárate.")
            nuevas.append({"clave": f'off|{nombre}|{c["en_curso_desde"]}', "tipo": "se fue", "circuito": nombre,
                           "hora_evento": c["en_curso_desde"], "texto": texto})
        # Volvió la luz: un corte que avisamos ya tiene hora de regreso
        for a in alertas:
            if a["tipo"] != "se fue" or a["circuito"] != nombre or f'on|{nombre}|{a["hora_evento"]}' in claves:
                continue
            corte = next((r for r in c["registro"] if r["inicio"] == a["hora_evento"] and r["estado"] == "confirmado"), None)
            if corte:
                h = corte["minutos"]
                texto = f"✅ Volvió la luz en {nombre} a las {corte['fin'][11:]} (duró {h // 60}h {h % 60}m)."
                nuevas.append({"clave": f'on|{nombre}|{a["hora_evento"]}', "tipo": "volvió", "circuito": nombre,
                               "hora_evento": corte["fin"], "texto": texto})

    for a in nuevas:
        a["detectada"] = ahora
        # Al circuito propio se le avisa todo; a los vecinos solo cuando se les va la luz
        a["enviada"] = enviar(a["texto"]) if (a["circuito"] == propio or a["tipo"] == "se fue") else False
        print(a["texto"], "| Telegram:", a["enviada"])
    alertas = (nuevas[::-1] + alertas)[:300] if nuevas else alertas
    with open(F_ALERTAS, "w", encoding="utf-8") as f:
        json.dump(alertas, f, ensure_ascii=False, indent=0)


if __name__ == "__main__":
    main()
