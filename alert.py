"""Alertas de cortes: registro en docs/alertas.json, Telegram y correo.

- "se fue": solo se registra (no se envía); "volvió": se envía. Solo tu circuito.
- "aviso": ~30 min antes de cada bloque de la zona, con la probabilidad para tu circuito
- "resumen": pronóstico del día a las 6 a. m.

Telegram: secretos TELEGRAM_TOKEN y TELEGRAM_CHAT_ID.
Correo (Gmail): secretos EMAIL_USER, EMAIL_APP_PASSWORD y EMAIL_TO.
Sin secretos, solo se llena el registro.
"""
import json
import os
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText

import requests

from collect import FMT
from forecast import CONFIG, RAIZ, VE, prob_bloque

F_ALERTAS = os.path.join(RAIZ, "docs", "alertas.json")
PAGINA = "https://ricardotester12-glitch.github.io/cortes-luz-valencia/"
AVISO_MIN = CONFIG.get("aviso_previo_min", 30)
UMBRAL_AVISO = CONFIG.get("umbral_aviso", 0.35)
ENVIAR = CONFIG.get("enviar", ["aviso", "resumen", "volvió"])  # "se fue" queda solo en el registro
ASUNTOS = {"aviso": "⏰ Posible corte en {m} min", "resumen": "Pronóstico de cortes de hoy",
           "se fue": "⚡ Se fue la luz", "volvió": "✅ Volvió la luz"}


def telegram(texto):
    token, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return False
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat, "text": texto}, timeout=20)
    return r.ok


def correo(asunto, texto):
    user, clave, para = (os.environ.get(k) for k in ("EMAIL_USER", "EMAIL_APP_PASSWORD", "EMAIL_TO"))
    if not (user and clave and para):
        return False
    msg = MIMEText(f"{texto}\n\nDashboard: {PAGINA}", "plain", "utf-8")
    msg["Subject"], msg["From"], msg["To"] = asunto, user, para
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
            s.login(user, clave)
            s.send_message(msg)
        return True
    except smtplib.SMTPException as e:
        print("Error de correo:", e)
        return False


def main():
    data = json.load(open(os.path.join(RAIZ, "docs", "data.json"), encoding="utf-8"))
    alertas = json.load(open(F_ALERTAS, encoding="utf-8")) if os.path.exists(F_ALERTAS) else []
    claves = {a["clave"] for a in alertas}
    ahora = datetime.now(VE).replace(tzinfo=None, second=0, microsecond=0)
    hoy = ahora.strftime("%Y-%m-%d")
    zona, propio = CONFIG["zona"], CONFIG["circuito"]
    zona_txt = {"ValenciaNorte": "Valencia Norte", "SanDiego": "San Diego"}.get(zona, zona)
    mio = data["circuitos"].get(f"{zona}/{propio}")
    bloques = data.get("bloques", {}).get(zona, [])
    nuevas = []

    def nueva(clave, tipo, circuito, hora_evento, texto):
        if clave not in claves:
            nuevas.append({"clave": clave, "tipo": tipo, "circuito": circuito, "hora_evento": hora_evento, "texto": texto})
            claves.add(clave)

    # Resumen del día (una vez, entre 5:30 y 8:00)
    if mio and bloques and (5, 30) <= (ahora.hour, ahora.minute) < (8, 0):
        lineas = [f"• {b}  {round(prob_bloque(mio['perfil'], b) * 100)}%" for b in bloques]
        nueva(f"resumen|{hoy}", "resumen", propio, f"{hoy} {ahora:%H:%M}",
              f"📋 Pronóstico de hoy para {propio} ({zona_txt}).\nProbabilidad de corte en cada bloque:\n" + "\n".join(lineas))

    # Aviso previo: el bloque arranca dentro de los próximos ~AVISO_MIN minutos
    if mio and not mio["en_curso_desde"]:
        for b in bloques:
            inicio = datetime.strptime(f"{hoy} {b}", FMT)
            if inicio < ahora:
                inicio += timedelta(days=1)
            falta = (inicio - ahora).total_seconds() / 60
            p = prob_bloque(mio["perfil"], b)
            if 0 < falta <= AVISO_MIN + 15 and p >= UMBRAL_AVISO:
                nueva(f"aviso|{inicio:%Y-%m-%d %H:%M}", "aviso", propio, inicio.strftime(FMT),
                      f"⏰ En ~{round(falta)} min (a las {b}) empieza un bloque de cortes en {zona_txt}.\n"
                      f"Probabilidad de que le toque a {propio}: {round(p * 100)}%.")

    # Se fue / volvió
    if mio and mio["en_curso_desde"]:
        nueva(f'off|{propio}|{mio["en_curso_desde"]}', "se fue", propio, mio["en_curso_desde"],
              f"⚡ Se fue la luz en {propio} a las {mio['en_curso_desde'][11:]}.")
    for a in list(alertas) if mio else []:
        if a["tipo"] != "se fue" or a["circuito"] != propio:
            continue
        corte = next((r for r in mio["registro"] if r["inicio"] == a["hora_evento"] and r["estado"] == "confirmado"), None)
        if corte:
            m = corte["minutos"]
            nueva(f'on|{propio}|{a["hora_evento"]}', "volvió", propio, corte["fin"],
                  f"✅ Volvió la luz en {propio} a las {corte['fin'][11:]} (duró {m // 60}h {m % 60}m).")

    for a in nuevas:
        a["detectada"] = ahora.strftime(FMT)
        if a["tipo"] in ENVIAR:
            a["enviada"] = telegram(a["texto"])
            m = round((datetime.strptime(a["hora_evento"], FMT) - ahora).total_seconds() / 60)
            a["correo"] = correo(ASUNTOS[a["tipo"]].format(m=max(m, 0)) + f" · {propio}", a["texto"])
        print(a["texto"].replace("\n", " | "), "| Telegram:", a["enviada"], "| Correo:", a.get("correo"))
    if nuevas:
        alertas = (nuevas[::-1] + alertas)[:300]
    with open(F_ALERTAS, "w", encoding="utf-8") as f:
        json.dump(alertas, f, ensure_ascii=False, indent=0)


if __name__ == "__main__":
    main()
