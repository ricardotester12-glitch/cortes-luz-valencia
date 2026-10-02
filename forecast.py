"""Calcula la probabilidad de corte por hora para cada circuito y la publica en docs/data.json.

Modelo: para cada circuito y hora del día, promedio ponderado (los días recientes pesan más)
de si hubo corte en esa hora, solo contando los días/horas que el canal realmente cubrió.
Se suaviza hacia el promedio de toda la zona para circuitos con pocos datos.
"""
import json
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from collect import DATA, FMT, leer

RAIZ = os.path.dirname(os.path.abspath(__file__))
CONFIG = json.load(open(os.path.join(RAIZ, "config.json"), encoding="utf-8"))
VE = timezone(timedelta(hours=-4))
VIDA_MEDIA = 4.0       # días: un dato de hace 4 días pesa la mitad
FUERZA_PRIOR = 1.5     # cuántos "días" vale el promedio de la zona
DUR_TIPICA = timedelta(minutes=190)
MIN_SOLAPE = 20        # minutos de corte para contar la hora como "sin luz"
UMBRAL = 0.5


def p(s):
    return datetime.strptime(s, FMT)


def solape(a0, a1, b0, b1):
    return max(timedelta(0), min(a1, b1) - max(a0, b0))


def cargar(ahora):
    reportes = leer(os.path.join(DATA, "reportes.csv"))
    ultimo = defaultdict(str)
    cobertura = defaultdict(list)  # zona -> [(desde, hasta)]
    for r in reportes:
        t = p(r["hora"])
        ultimo[r["zona"]] = max(ultimo[r["zona"]], r["hora"])
        ventana = timedelta(hours=6) if int(r["resueltos"]) else timedelta(minutes=30)
        cobertura[r["zona"]].append((t - ventana, t))

    cortes, urbs, en_curso = defaultdict(list), {}, {}
    for c in leer(os.path.join(DATA, "cortes.csv")):
        k = (c["zona"], c["circuito"])
        urbs[k] = c["urbanizaciones"]
        ini = p(c["inicio"])
        if c["fin"]:
            fin = p(c["fin"])
        elif c["ultimo_visto_off"] == ultimo[c["zona"]] and ahora - p(c["ultimo_visto_off"]) < timedelta(hours=2):
            fin = ahora
            en_curso[k] = c["inicio"]
        else:
            fin = max(p(c["ultimo_visto_off"]), ini + DUR_TIPICA)
        cortes[k].append((ini, fin))
    for m in leer(os.path.join(DATA, "manual.csv")):
        k = (m["zona"], m["circuito"])
        cortes[k].append((p(m["inicio"]), p(m["fin"])))
        cobertura[m["zona"]].append((p(m["inicio"]), p(m["fin"])))
    return cortes, cobertura, urbs, en_curso


def matriz(cortes, cobertura, zona):
    """{fecha: [None|0|1] * 24} para un circuito: None = el canal no cubrió esa hora."""
    dias = sorted({a.date() for a, _ in cobertura[zona]} | {b.date() for _, b in cobertura[zona]})
    out = {}
    for d in dias:
        fila = []
        for h in range(24):
            h0 = datetime(d.year, d.month, d.day, h)
            h1 = h0 + timedelta(hours=1)
            off = any(solape(a, b, h0, h1) >= timedelta(minutes=MIN_SOLAPE) for a, b in cortes)
            cubierta = off or any(solape(a, b, h0, h1) > timedelta(0) for a, b in cobertura[zona])
            fila.append((1 if off else 0) if cubierta else None)
        out[d] = fila
    return out


def perfil(mats, hasta, prior=None):
    """Probabilidad por hora usando solo los días anteriores a `hasta`."""
    num, den = [0.0] * 24, [0.0] * 24
    for d, fila in mats.items():
        if d >= hasta:
            continue
        w = 0.5 ** ((hasta - d).days / VIDA_MEDIA)
        for h, v in enumerate(fila):
            if v is not None:
                num[h] += w * v
                den[h] += w
    if prior is None:
        return [num[h] / den[h] if den[h] else None for h in range(24)]
    return [(num[h] + FUERZA_PRIOR * (prior[h] or 0)) / (den[h] + FUERZA_PRIOR) for h in range(24)]


def prior_zona(mats_zona, hasta):
    num, den = [0.0] * 24, [0.0] * 24
    for mats in mats_zona:
        for h, v in enumerate(perfil(mats, hasta)):
            if v is not None:
                num[h] += v
                den[h] += 1
    return [num[h] / den[h] if den[h] else 0.0 for h in range(24)]


def backtest(mats, mats_zona):
    """Predice cada día con los anteriores y lo compara con lo que pasó."""
    acierto = base = n = alto = alto_ok = 0
    dias = sorted(mats)
    for d in dias[1:]:
        pr = perfil(mats, d, prior_zona(mats_zona, d))
        for h, v in enumerate(mats[d]):
            if v is None:
                continue
            n += 1
            acierto += (pr[h] >= UMBRAL) == bool(v)
            base += v == 0  # modelo ingenuo: "nunca se va"
            if pr[h] >= UMBRAL:
                alto += 1
                alto_ok += v
    return {"horas": n, "acierto": acierto / n if n else None, "ingenuo": base / n if n else None,
            "horas_alta": alto, "alta_cumplidas": alto_ok / alto if alto else None}


def companeros(k, mats_todos):
    """Circuitos que se apagan en las mismas horas (Jaccard sobre horas sin luz)."""
    propio = {(d, h) for d, f in mats_todos[k].items() for h, v in enumerate(f) if v == 1}
    res = []
    for k2, m2 in mats_todos.items():
        if k2 == k or k2[0] != k[0]:
            continue
        otro = {(d, h) for d, f in m2.items() for h, v in enumerate(f) if v == 1}
        if propio and otro:
            res.append((len(propio & otro) / len(propio | otro), k2[1]))
    return [{"circuito": c, "coincidencia": round(j, 2)} for j, c in sorted(res, reverse=True)[:4] if j >= 0.3]


def main():
    ahora = datetime.now(VE).replace(tzinfo=None, second=0, microsecond=0)
    cortes, cobertura, urbs, en_curso = cargar(ahora)
    mats = {k: matriz(v, cobertura, k[0]) for k, v in cortes.items()}
    manana = ahora.date() + timedelta(days=1)
    salida = {"actualizado": ahora.strftime(FMT), "principal": CONFIG["circuito"], "zona": CONFIG["zona"],
              "circuitos": {}}
    for zona in {k[0] for k in mats}:
        mz = [m for k, m in mats.items() if k[0] == zona]
        prior = prior_zona(mz, manana)
        for k, m in mats.items():
            if k[0] != zona:
                continue
            prob = perfil(m, manana, prior)
            salida["circuitos"][f"{zona}/{k[1]}"] = {
                "zona": zona, "circuito": k[1], "urbanizaciones": urbs.get(k, ""),
                "perfil": [round(x, 3) for x in prob],
                "en_curso_desde": en_curso.get(k),
                "historial": [{"fecha": d.isoformat(), "horas": f} for d, f in sorted(m.items())][-14:],
                "backtest": backtest(m, mz),
                "companeros": companeros(k, {kk: mm for kk, mm in mats.items()}),
                "dias_con_datos": len(m),
            }
    os.makedirs(os.path.join(RAIZ, "docs"), exist_ok=True)
    with open(os.path.join(RAIZ, "docs", "data.json"), "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, separators=(",", ":"))
    c = salida["circuitos"].get(f'{CONFIG["zona"]}/{CONFIG["circuito"]}')
    if c:
        print(CONFIG["circuito"], "perfil:", " ".join(f"{h}h:{round(x*100)}" for h, x in enumerate(c["perfil"])))
        print("backtest:", c["backtest"], "| compañeros:", c["companeros"])


if __name__ == "__main__":
    main()
