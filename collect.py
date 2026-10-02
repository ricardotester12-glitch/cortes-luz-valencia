"""Recolecta reportes del canal Monitor Vecinal y actualiza el historial.

  python collect.py                      # lee la vista web pública (lo que corre GitHub Actions)
  python collect.py --export result.json # importa un export de Telegram Desktop
"""
import argparse
import csv
import html
import json
import os
import re
from datetime import datetime, timedelta, timezone

import requests

from parser import parse_reporte, zona_de

CANAL = "https://t.me/s/monitorvecinalcanalvalnagsd"
VE = timezone(timedelta(hours=-4))
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
F_CORTES = os.path.join(DATA, "cortes.csv")
F_REPORTES = os.path.join(DATA, "reportes.csv")
C_CORTES = ["zona", "circuito", "inicio", "fin", "ultimo_visto_off", "urbanizaciones"]
C_REPORTES = ["post", "zona", "hora", "resueltos"]
FMT = "%Y-%m-%d %H:%M"


def leer(ruta):
    if not os.path.exists(ruta):
        return []
    with open(ruta, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def escribir(ruta, columnas, filas):
    os.makedirs(DATA, exist_ok=True)
    with open(ruta, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, columnas)
        w.writeheader()
        w.writerows(filas)


def posts_web(paginas=4):
    """Devuelve [(post_id, datetime_local, texto)] de las últimas páginas del canal."""
    posts, antes = [], None
    for _ in range(paginas):
        url = CANAL + (f"?before={antes}" if antes else "")
        s = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"}).text
        bloques = re.split(r'data-post="[^/"]+/', s)[1:]
        if not bloques:
            break
        for b in bloques:
            pid = int(b.split('"')[0])
            m = re.search(r'tgme_widget_message_text[^>]*>(.*?)</div>', b, re.S)
            d = re.search(r'datetime="([^"]+)"', b)
            if not (m and d):
                continue
            t = html.unescape(re.sub(r"<[^>]+>", "", re.sub(r"<br\s*/?>", "\n", m.group(1))))
            posts.append((pid, datetime.fromisoformat(d.group(1)).astimezone(VE).replace(tzinfo=None), t))
        antes = min(int(b.split('"')[0]) for b in bloques)
    return sorted(posts)


def posts_export(ruta):
    data = json.load(open(ruta, encoding="utf-8"))
    out = []
    for x in data["messages"]:
        if x["type"] != "message":
            continue
        tx = x["text"]
        t = tx if isinstance(tx, str) else "".join(p if isinstance(p, str) else p["text"] for p in tx)
        out.append((x["id"], datetime.fromisoformat(x["date"]), t))  # el export ya viene en hora local
    return sorted(out)


def integrar(posts):
    cortes = {(c["zona"], c["circuito"], c["inicio"]): c for c in leer(F_CORTES)}
    reportes = {r["post"]: r for r in leer(F_REPORTES)}
    zona_previa = None
    for pid, hora, texto in posts:
        if "Cto:" not in texto:
            continue
        zona = zona_de(texto) or zona_previa  # "Parte 2/3" no trae cabecera: hereda la zona
        zona_previa = zona
        if not zona:
            continue
        resueltos, lista = parse_reporte(texto, hora.year)
        r = reportes.setdefault(str(pid), {"post": pid, "zona": zona, "hora": hora.strftime(FMT), "resueltos": 0})
        r["resueltos"] = int(r["resueltos"] or 0) or int(resueltos)
        for c in lista:
            k = (zona, c["circuito"], c["inicio"].strftime(FMT))
            fila = cortes.setdefault(k, {"zona": zona, "circuito": c["circuito"], "inicio": k[2], "fin": "",
                                         "ultimo_visto_off": "", "urbanizaciones": c["urbanizaciones"]})
            if c["fin"]:
                fila["fin"] = c["fin"].strftime(FMT)
            elif hora.strftime(FMT) > fila["ultimo_visto_off"]:
                fila["ultimo_visto_off"] = hora.strftime(FMT)
    escribir(F_CORTES, C_CORTES, sorted(cortes.values(), key=lambda c: (c["inicio"], c["zona"], c["circuito"])))
    escribir(F_REPORTES, C_REPORTES, sorted(reportes.values(), key=lambda r: r["hora"]))
    return len(cortes), len(reportes)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--export")
    a = ap.parse_args()
    posts = posts_export(a.export) if a.export else posts_web()
    print("posts leídos:", len(posts))
    print("cortes / reportes en historial:", *integrar(posts))
