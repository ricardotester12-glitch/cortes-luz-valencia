"""Modelo de riesgo por hora (supervivencia en tiempo discreto) para el circuito propio.

Cada hora con luz es una muestra: ¿arranca un corte en la próxima hora? Una regresión logística
estima ese riesgo con lo que se sabe en ese momento (horas desde que volvió la luz, franja del día,
cortes de hoy, día de la semana, qué pasó a la misma hora ayer y hace 7 días, y si arrancaron los
circuitos que suelen apagarse justo antes). Encadenando el riesgo hora a hora sale la probabilidad
de quedarse sin luz en las próximas 3 o 6 horas.
"""
import math
from datetime import datetime, timedelta

from collect import FMT

PASO = timedelta(hours=1)
DESDE = datetime(2026, 9, 29)    # antes de esto el canal borraba los mensajes
VIDA_MEDIA = 10.0                # días: las semanas viejas pesan menos (Corpoelec cambia de esquema)
L2 = 1.0
VENTANA_PREVIA = (timedelta(minutes=30), timedelta(hours=3, minutes=30))
N_PRECURSORES = 5
SUAVE = 10.0                     # horas "ficticias" con la tasa general para suavizar cada hora del día
NOMBRES = ["constante", "frecuencia de esa hora", "volvió hace <2,5 h", "2+ cortes hoy", "domingo",
           "hace 7 días a esta hora", "arrancó un precursor"]


def eventos(registro_lista):
    """[{inicio, fin, ...}] (de lista_registro) -> [(inicio, fin)] ordenado."""
    return sorted((datetime.strptime(r["inicio"], FMT), datetime.strptime(r["fin"], FMT)) for r in registro_lista)


def sin_luz(evs, t):
    return any(a <= t < b for a, b in evs)


def arranco(evs, t0, t1):
    return any(t0 <= a < t1 for a, _ in evs)


def logit(p):
    p = min(max(p, 1e-3), 1 - 1e-3)
    return math.log(p / (1 - p))


def rasgos(t, evs, inicios_prec, clim):
    """Vector de rasgos en el instante t, usando solo eventos que empezaron antes de t.

    `clim` es la frecuencia de cortes de cada hora del día: el modelo parte de ahí y la corrige."""
    pasados = [(a, b) for a, b in evs if a < t]
    ult = max((b for _, b in pasados if b <= t), default=None)
    h = (t - ult).total_seconds() / 3600 if ult else 99
    hoy = sum(a.date() == t.date() for a, _ in pasados)
    ref = t - timedelta(days=7)
    x = [1.0, logit(clim[t.hour]), h < 2.5, hoy >= 2, t.weekday() == 6,
         arranco(evs, ref - timedelta(hours=1), ref + timedelta(hours=1)),
         any(t - VENTANA_PREVIA[1] <= a <= t - VENTANA_PREVIA[0] for a in inicios_prec)]
    return [float(v) for v in x]


def frecuencia_horaria(evs, hasta):
    """Tasa de arranque de cortes por hora del día (horas con luz), con más peso a lo reciente."""
    num, den = [0.0] * 24, [0.0] * 24
    t = DESDE
    while t + PASO <= hasta:
        if not sin_luz(evs, t):
            w = 0.5 ** ((hasta - t).total_seconds() / 86400 / VIDA_MEDIA)
            num[t.hour] += w * arranco(evs, t, t + PASO)
            den[t.hour] += w
        t += PASO
    general = sum(num) / sum(den) if sum(den) else 0.05
    return [(num[h] + SUAVE * general) / (den[h] + SUAVE) for h in range(24)]


def precursores(evs, otros, hasta):
    """Circuitos que arrancan 0,5 a 3,5 h antes que el propio, con su 'efecto' sobre la tasa base."""
    propios = [a for a, _ in evs if a < hasta]
    if not propios:
        return []
    horas = (hasta - DESDE).total_seconds() / 3600
    base = len(propios) * 3 / horas
    res = []
    for nombre, ini in otros.items():
        ini = [a for a in ini if DESDE <= a < hasta - VENTANA_PREVIA[1]]
        if len(ini) < 6:
            continue
        seguido = sum(any(VENTANA_PREVIA[0] < m - x <= VENTANA_PREVIA[1] for m in propios) for x in ini)
        tasa = (seguido + base) / (len(ini) + 1)  # suavizado hacia la base
        res.append((tasa / base, nombre, len(ini)))
    return [(n, round(l, 2), k) for l, n, k in sorted(res, reverse=True)[:N_PRECURSORES] if l > 1.3]


def muestras(evs, otros_ini, clim, desde, hasta):
    """Horas con luz entre desde y hasta, con su etiqueta (arrancó un corte en la hora siguiente)."""
    X, y, ts = [], [], []
    t = max(desde, DESDE)
    while t + PASO <= hasta:
        if not sin_luz(evs, t):
            X.append(rasgos(t, evs, otros_ini, clim))
            y.append(1.0 if arranco(evs, t, t + PASO) else 0.0)
            ts.append(t)
        t += PASO
    return X, y, ts


def entrenar(X, y, pesos, iters=400, lr=0.5):
    n, k = len(X), len(X[0])
    w = [0.0] * k
    sw = sum(pesos)
    for _ in range(iters):
        g = [0.0] * k
        for xi, yi, pi in zip(X, y, pesos):
            z = sum(a * b for a, b in zip(w, xi))
            e = (1 / (1 + math.exp(-z)) - yi) * pi
            for j in range(k):
                g[j] += e * xi[j]
        for j in range(k):
            w[j] -= lr * (g[j] / sw + (L2 * w[j] / sw if j else 0))
    return w


def riesgo(w, x):
    return 1 / (1 + math.exp(-sum(a * b for a, b in zip(w, x))))


def ajustar(evs, otros, hasta):
    """Entrena con todo lo anterior a `hasta`. Devuelve un dict con pesos, precursores y frecuencia horaria."""
    evs = [e for e in evs if e[0] < hasta]
    prec = precursores(evs, otros, hasta)
    ini_prec = sorted(a for n, _, _ in prec for a in otros[n])
    # la frecuencia horaria que ve el modelo al entrenar sale de días previos, igual que al predecir
    clim = frecuencia_horaria(evs, hasta)
    X, y, ts = muestras(evs, ini_prec, clim, DESDE, hasta)
    pesos = [0.5 ** ((hasta - t).total_seconds() / 86400 / VIDA_MEDIA) for t in ts]
    return {"w": entrenar(X, y, pesos), "precursores": prec, "ini_prec": ini_prec, "clim": clim}


def curva(m, evs, t, horas=24, dur_corte=timedelta(minutes=190)):
    """Riesgo hora a hora desde t, suponiendo que no se va la luz antes (si está sin luz, desde que vuelva).

    Devuelve [(hora, riesgo_de_esa_hora, prob_acumulada)]."""
    evs = [e for e in evs if e[0] <= t]
    en_curso = next(((a, b) for a, b in evs if a <= t < b), None)
    inicio = t
    if en_curso:
        vuelve = max(en_curso[1], en_curso[0] + dur_corte)
        evs = [e for e in evs if e != en_curso] + [(en_curso[0], vuelve)]
        inicio = vuelve
    out, sobrevive = [], 1.0
    paso = inicio
    while paso < t + timedelta(hours=horas):
        r = riesgo(m["w"], rasgos(paso, evs, m["ini_prec"], m["clim"]))
        sobrevive *= 1 - r
        out.append((paso, r, 1 - sobrevive))
        paso += PASO
    return out, inicio


def simular(m, evs, t, horas=30, n=400, dur_corte=timedelta(minutes=190), semilla=7):
    """Simula n futuros posibles (con todos sus cortes, no solo el primero).

    Devuelve (prob. de estar sin luz en cada hora desde t, lista de inicios simulados por futuro)."""
    import random
    rnd = random.Random(semilla)
    base = [e for e in evs if e[0] <= t]
    pasos = [t + PASO * i for i in range(horas)]
    sin = [0] * horas
    inicios = []
    for _ in range(n):
        ev = list(base)
        mios = []
        for i, paso in enumerate(pasos):
            if sin_luz(ev, paso):
                sin[i] += 1
                continue
            if rnd.random() < riesgo(m["w"], rasgos(paso, ev, m["ini_prec"], m["clim"])):
                a = paso + timedelta(minutes=rnd.randint(0, 59))
                ev.append((a, a + dur_corte))
                mios.append(a)
                if a < paso + PASO / 2:
                    sin[i] += 1
        inicios.append(mios)
    return [s / n for s in sin], inicios


def prob_en(curva_, t, horas):
    """Probabilidad de que arranque un corte antes de t + horas."""
    p = 0.0
    for paso, _, acum in curva_:
        if paso < t + timedelta(hours=horas):
            p = acum
    return p


def backtest(evs, otros, dias_min=7):
    """Validación hacia adelante: cada día se predice con lo anterior. Brier para 3 h y 6 h vs. referencias."""
    fin = max(b for _, b in evs)
    dia = DESDE + timedelta(days=dias_min)
    res = {3: [], 6: []}
    while dia + timedelta(days=1) <= fin:
        m = ajustar(evs, otros, dia)
        # referencias: frecuencia de cada hora del día (lo que hacía el modelo viejo) y una tasa fija
        _, yr, _ = muestras([e for e in evs if e[0] < dia], [], m["clim"], DESDE, dia)
        tasa = sum(yr) / len(yr)
        t = dia
        while t < dia + timedelta(days=1):
            if not sin_luz(evs, t):
                c, _ = curva(m, evs, t, horas=6)
                for H in (3, 6):
                    real = arranco(evs, t, t + timedelta(hours=H))
                    clim = 1.0
                    for k in range(H):
                        clim *= 1 - m["clim"][(t + timedelta(hours=k)).hour]
                    res[H].append((prob_en(c, t, H), 1 - clim, 1 - (1 - tasa) ** H, real))
            t += PASO
        dia += timedelta(days=1)
    out = {}
    for H, filas in res.items():
        if not filas:
            continue
        def brier(i):
            return sum((f[i] - f[3]) ** 2 for f in filas) / len(filas)
        altas = [f for f in filas if f[0] >= 0.5]
        out[f"{H}h"] = {
            "horas": len(filas),
            "brier_modelo": round(brier(0), 4), "brier_por_hora": round(brier(1), 4), "brier_tasa_fija": round(brier(2), 4),
            "mejora_vs_por_hora": round(1 - brier(0) / brier(1), 3),
            "alertas": len(altas),
            "alertas_cumplidas": round(sum(f[3] for f in altas) / len(altas), 3) if altas else None,
            "cortes_avisados": round(sum(f[3] for f in altas) / max(1, sum(f[3] for f in filas)), 3),
            "calibracion": calibracion(filas),
        }
    return out


def calibracion(filas):
    """Por tramo de probabilidad predicha: cuántas veces pasó de verdad."""
    tramos = [(0, .2), (.2, .4), (.4, .6), (.6, .8), (.8, 1.01)]
    out = []
    for a, b in tramos:
        sel = [f for f in filas if a <= f[0] < b]
        if sel:
            out.append({"tramo": f"{round(a*100)}-{min(100, round(b*100))}%", "n": len(sel),
                        "predicho": round(sum(f[0] for f in sel) / len(sel), 3),
                        "real": round(sum(f[3] for f in sel) / len(sel), 3)})
    return out


def autocorrelacion(evs, hasta, dias=14):
    """Cuánto se parece la serie hora a hora (con/sin luz) a sí misma desplazada 1..14 días."""
    n = int((hasta - DESDE).total_seconds() // 3600)
    serie = [1.0 if sin_luz(evs, DESDE + timedelta(hours=i, minutes=30)) else 0.0 for i in range(n)]
    m = sum(serie) / n
    var = sum((v - m) ** 2 for v in serie) or 1
    return [{"dias": d, "valor": round(sum((serie[i] - m) * (serie[i + 24 * d] - m) for i in range(n - 24 * d)) / var, 3)}
            for d in range(1, dias + 1) if n > 24 * d]
