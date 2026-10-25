"""Calcula la probabilidad de corte por hora para cada circuito y la publica en docs/data.json.

Modelo: para cada circuito y hora del día, promedio ponderado (los días recientes pesan más)
de si hubo corte en esa hora, solo contando los días/horas que el canal realmente cubrió.
Se suaviza hacia el promedio de toda la zona para circuitos con pocos datos.
Para el circuito propio se agrega el modelo de riesgo de modelo.py ("riesgo" en data.json).
"""
import json
import math
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import modelo
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

    cortes, urbs, en_curso, registro = defaultdict(list), {}, {}, defaultdict(list)
    for c in leer(os.path.join(DATA, "cortes.csv")):
        k = (c["zona"], c["circuito"])
        urbs[k] = c["urbanizaciones"]
        ini = p(c["inicio"])
        if c["fin"]:
            fin, estado = p(c["fin"]), "confirmado"
        elif c["ultimo_visto_off"] == ultimo[c["zona"]] and ahora - p(c["ultimo_visto_off"]) < timedelta(hours=2):
            fin, estado = ahora, "en curso"
            en_curso[k] = c["inicio"]
        else:
            fin, estado = max(p(c["ultimo_visto_off"]), ini + DUR_TIPICA), "estimado"
        cortes[k].append((ini, fin))
        registro[k].append((ini, fin, estado))
    for m in leer(os.path.join(DATA, "manual.csv")):
        k = (m["zona"], m["circuito"])
        cortes[k].append((p(m["inicio"]), p(m["fin"])))
        registro[k].append((p(m["inicio"]), p(m["fin"]), "anotado a mano"))
        cobertura[m["zona"]].append((p(m["inicio"]), p(m["fin"])))
    return cortes, cobertura, urbs, en_curso, registro


def lista_registro(eventos, tope=40):
    """Cortes del más reciente al más viejo, fusionando reportes duplicados del mismo corte."""
    out = []
    for ini, fin, estado in sorted(eventos):
        if out and ini <= out[-1][1] and ini - out[-1][0] < timedelta(minutes=30):
            if out[-1][2] not in ("confirmado", "en curso"):  # un dato confirmado manda sobre uno estimado
                out[-1] = (out[-1][0], fin, estado)
            continue
        out.append((ini, fin, estado))
    return [{"inicio": a.strftime(FMT), "fin": b.strftime(FMT), "minutos": int((b - a).total_seconds() // 60),
             "estado": e} for a, b, e in reversed(out)][:tope]


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


def bloques_zona(registro, zona, hasta, dias=14):
    """Horas de inicio de los bloques de cortes que se repiten en la zona.

    Busca los picos de la hora de arranque (en tramos de 15 min, suavizados) separados al menos 2,5 h;
    cada bloque arranca en el primer cuartil de los cortes que caen a ±45 min de su pico (mejor avisar temprano)."""
    mins = sorted(ini.hour * 60 + ini.minute for (z, _), evs in registro.items() if z == zona
                  for ini, _, est in evs if est != "anotado a mano" and (hasta - ini.date()).days <= dias)
    cuenta = [0] * 96
    for m in mins:
        cuenta[m // 15] += 1
    suave = [cuenta[i - 1] + cuenta[i] + cuenta[(i + 1) % 96] for i in range(96)]
    picos = []
    for i in sorted(range(96), key=lambda i: -suave[i]):
        if suave[i] >= 6 and all(min(abs(i - j), 96 - abs(i - j)) >= 10 for j in picos):
            picos.append(i)
    out = []
    for i in picos:
        cerca = sorted(m for m in mins if min(abs(m - (i * 15 + 7)), 1440 - abs(m - (i * 15 + 7))) <= 45)
        q = cerca[len(cerca) // 4]
        out.append(f"{q // 60:02d}:{q % 60:02d}")
    return sorted(out)


def prob_bloque(perfil_c, hhmm):
    """Probabilidad de que el circuito quede sin luz en el bloque que arranca a hhmm."""
    h, m = map(int, hhmm.split(":"))
    return perfil_c[(h + (m + 60) // 60) % 24]


def riesgo_propio(registro, en_curso, ahora, bloques):
    """Modelo de riesgo (modelo.py) para el circuito propio: próximas 3/6 h, curva, bloques y diagnóstico."""
    k = (CONFIG["zona"], CONFIG["circuito"])
    if k not in registro:
        return None
    evs = modelo.eventos(lista_registro(registro[k], 9999))
    if k in en_curso:  # mientras siga sin luz, se asume que vuelve a la duración típica
        evs = [(a, max(ahora + timedelta(minutes=1), a + DUR_TIPICA) if a.strftime(FMT) == en_curso[k] else b)
               for a, b in evs]
    otros = {kk[1]: [a for a, _ in modelo.eventos(lista_registro(v, 9999))]
             for kk, v in registro.items() if kk[0] == k[0] and kk != k}
    m = modelo.ajustar(evs, otros, ahora)
    c, desde = modelo.curva(m, evs, ahora, horas=30)
    sin_luz, futuros = modelo.simular(m, evs, ahora, horas=30)
    prox = []
    for b in bloques:
        ini = datetime.strptime(f"{ahora:%Y-%m-%d} {b}", FMT)
        for dia in (0, 1):
            t = ini + timedelta(days=dia)
            if ahora - timedelta(minutes=30) < t <= ahora + timedelta(hours=24):
                a, z = t - timedelta(minutes=30), t + timedelta(minutes=90)
                p = sum(any(a <= x < z for x in f) for f in futuros) / len(futuros)
                prox.append({"inicio": t.strftime(FMT), "hora": b, "prob": round(p, 3)})
    activos = []
    for nombre, efecto, n in m["precursores"]:
        ult = max((a for a in otros[nombre] if a <= ahora), default=None)
        en_ventana = ult and modelo.VENTANA_PREVIA[0] <= ahora - ult <= modelo.VENTANA_PREVIA[1]
        activos.append({"circuito": nombre, "efecto": efecto, "veces": n,
                        "arranco": ult.strftime(FMT) if en_ventana else None})
    return {
        "p3": round(modelo.prob_en(c, ahora, 3), 3), "p6": round(modelo.prob_en(c, ahora, 6), 3),
        "desde": desde.strftime(FMT), "sin_luz": k in en_curso,
        "segura_hasta": next((p.strftime(FMT) for p, _, acc in c if acc >= 0.2), None),
        "mitad_antes_de": next(((p + modelo.PASO).strftime(FMT) for p, _, acc in c if acc >= 0.5), None),
        "curva": [{"hora": p.strftime(FMT), "riesgo": round(r, 3), "acumulada": round(acc, 3)} for p, r, acc in c],
        "sin_luz_por_hora": [{"hora": (ahora + modelo.PASO * i).strftime(FMT), "prob": round(p, 3)}
                             for i, p in enumerate(sin_luz)],
        "cortes_esperados_24h": round(sum(sum(x < ahora + timedelta(hours=24) for x in f) for f in futuros) / len(futuros), 2),
        "bloques": sorted(prox, key=lambda x: x["inicio"]),
        "precursores": activos,
        "factores": [{"nombre": n, "multiplica": round(math.exp(w), 2)}
                     for n, w in zip(modelo.NOMBRES, m["w"]) if n not in ("constante", "frecuencia de esa hora")],
        "backtest": modelo.backtest(evs, otros),
        "autocorrelacion": modelo.autocorrelacion(evs, ahora),
    }


def main():
    ahora = datetime.now(VE).replace(tzinfo=None, second=0, microsecond=0)
    cortes, cobertura, urbs, en_curso, registro = cargar(ahora)
    mats = {k: matriz(v, cobertura, k[0]) for k, v in cortes.items()}
    manana = ahora.date() + timedelta(days=1)
    salida = {"actualizado": ahora.strftime(FMT), "principal": CONFIG["circuito"], "zona": CONFIG["zona"],
              "circuitos": {}, "bloques": {}}
    for zona in {k[0] for k in mats}:
        mz = [m for k, m in mats.items() if k[0] == zona]
        prior = prior_zona(mz, manana)
        salida["bloques"][zona] = bloques_zona(registro, zona, ahora.date())
        for k, m in mats.items():
            if k[0] != zona:
                continue
            prob = perfil(m, manana, prior)
            salida["circuitos"][f"{zona}/{k[1]}"] = {
                "zona": zona, "circuito": k[1], "urbanizaciones": urbs.get(k, ""),
                "perfil": [round(x, 3) for x in prob],
                "en_curso_desde": en_curso.get(k),
                "registro": lista_registro(registro[k]),
                "historial": [{"fecha": d.isoformat(), "horas": f} for d, f in sorted(m.items())][-14:],
                "backtest": backtest(m, mz),
                "companeros": companeros(k, {kk: mm for kk, mm in mats.items()}),
                "dias_con_datos": len(m),
            }
    salida["riesgo"] = riesgo_propio(registro, en_curso, ahora, salida["bloques"].get(CONFIG["zona"], []))
    os.makedirs(os.path.join(RAIZ, "docs"), exist_ok=True)
    with open(os.path.join(RAIZ, "docs", "data.json"), "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, separators=(",", ":"))
    c = salida["circuitos"].get(f'{CONFIG["zona"]}/{CONFIG["circuito"]}')
    if c:
        print(CONFIG["circuito"], "perfil:", " ".join(f"{h}h:{round(x*100)}" for h, x in enumerate(c["perfil"])))
        print("backtest:", c["backtest"], "| compañeros:", c["companeros"])
        print("bloques:", [(b, round(prob_bloque(c["perfil"], b) * 100)) for b in salida["bloques"][CONFIG["zona"]]])
    r = salida["riesgo"]
    if r:
        print(f'riesgo: 3 h {r["p3"]:.0%} | 6 h {r["p6"]:.0%} | segura hasta {r["segura_hasta"]} | bloques',
              [(b["inicio"][5:], f'{b["prob"]:.0%}') for b in r["bloques"]])
        print("backtest riesgo:", {h: (b["mejora_vs_por_hora"], b["alertas_cumplidas"]) for h, b in r["backtest"].items()})


if __name__ == "__main__":
    main()
