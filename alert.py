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


DIAG = {}  # último resultado de cada canal, sin datos sensibles (docs/diagnostico.json)


def secreto(nombre):
    """Lee un secreto sin comillas, saltos de línea ni espacios (incluidos los invisibles al copiar)."""
    v = (os.environ.get(nombre) or "").replace(" ", " ").strip().strip('"').strip("'")
    return "".join(v.split())


def telegram(texto):
    token, chat = secreto("TELEGRAM_TOKEN"), secreto("TELEGRAM_CHAT_ID")
    if not (token and chat):
        DIAG["telegram"] = "sin configurar: falta " + ("TELEGRAM_TOKEN" if not token else "TELEGRAM_CHAT_ID")
        return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat, "text": texto}, timeout=20)
        DIAG["telegram"] = "ok" if r.ok else f"Telegram respondió {r.status_code}: {r.json().get('description', '')}"
        return r.ok
    except Exception as e:  # un fallo de envío nunca debe tumbar la tarea
        DIAG["telegram"] = f"error {type(e).__name__}"
        return False


def correo(asunto, texto):
    user, clave, para = secreto("EMAIL_USER"), secreto("EMAIL_APP_PASSWORD"), secreto("EMAIL_TO")
    if not (user and clave and para):
        faltan = [n for n, v in (("EMAIL_USER", user), ("EMAIL_APP_PASSWORD", clave), ("EMAIL_TO", para)) if not v]
        DIAG["correo"] = "sin configurar: falta " + ", ".join(faltan)
        return False
    msg = MIMEText(f"{texto}\n\nDashboard: {PAGINA}", "plain", "utf-8")
    msg["Subject"], msg["From"], msg["To"] = asunto, user, para
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
            s.login(user, clave)
            s.send_message(msg)
        DIAG["correo"] = "ok"
        return True
    except Exception as e:  # un fallo de envío nunca debe tumbar la tarea
        detalle = e.smtp_error.decode(errors="ignore")[:160] if hasattr(e, "smtp_error") else ""
        DIAG["correo"] = f"error {type(e).__name__} {detalle}".strip()
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
    if DIAG:
        DIAG["fecha"] = ahora.strftime(FMT)
        with open(os.path.join(RAIZ, "docs", "diagnostico.json"), "w", encoding="utf-8") as f:
            json.dump(DIAG, f, ensure_ascii=False, indent=1)
        print("Diagnóstico:", DIAG)


def prueba():
    """Envía un mensaje de prueba por cada canal y deja el resultado en docs/diagnostico.json."""
    texto = "🧪 PRUEBA de configuración: si te llega este mensaje, las alertas de cortes de luz funcionan."
    print("Telegram:", telegram(texto), "| Correo:", correo("🧪 Prueba de alertas de cortes de luz", texto))
    DIAG["fecha"] = datetime.now(VE).strftime(FMT) + " (prueba)"
    with open(os.path.join(RAIZ, "docs", "diagnostico.json"), "w", encoding="utf-8") as f:
        json.dump(DIAG, f, ensure_ascii=False, indent=1)
    print("Diagnóstico:", DIAG)


if __name__ == "__main__":
    import sys
    prueba() if "--prueba" in sys.argv else main()
