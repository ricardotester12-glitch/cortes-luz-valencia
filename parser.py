"""Convierte el texto de un reporte de Monitor Vecinal en cortes por circuito."""
import re
from datetime import datetime

ZONAS = [("ValenciaNorte", "ValenciaNorte"), ("Naguanagua", "Naguanagua"), ("SAN DIEGO", "SanDiego"), ("San Diego", "SanDiego")]
FECHA_HORA = re.compile(r"(\d\d)/(\d\d)\D+?(\d\d):(\d\d)")


def zona_de(texto):
    cabecera = texto[:300]
    for marca, zona in ZONAS:
        if marca in cabecera:
            return zona
    return None


def _dt(linea, anio):
    m = FECHA_HORA.search(linea)
    if not m:
        return None
    d, mes, h, mi = map(int, m.groups())
    return datetime(anio, mes, d, h, mi)


def parse_reporte(texto, anio):
    """Devuelve (cubre_resueltos, [cortes]); cada corte: circuito, urbanizaciones, inicio, fin|None."""
    cortes, actual = [], None
    for linea in texto.splitlines():
        m = re.search(r"Cto:\s*#(\S+)", linea)
        if m:
            actual = {"circuito": m.group(1), "urbanizaciones": "", "inicio": None, "fin": None, "fase": False}
            cortes.append(actual)
        elif actual is None:
            continue
        elif linea.strip().startswith("🏘"):
            actual["urbanizaciones"] = linea.strip().lstrip("🏘").strip()
        elif "Fase" in linea:
            actual["fase"] = True
        elif "OFF" in linea:
            actual["inicio"] = _dt(linea, anio)
        elif re.search(r"\bON\b", linea):
            actual["fin"] = _dt(linea, anio)
    validos = [c for c in cortes if c["inicio"] and not c["fase"]]
    return "RESUELTOS" in texto, validos
