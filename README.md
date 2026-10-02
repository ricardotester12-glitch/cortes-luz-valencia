# Pronóstico de cortes de luz (Valencia, Carabobo)

Lee el canal público Monitor Vecinal cada 20 minutos, guarda el historial de cortes por circuito
y calcula la probabilidad de corte por hora. La página está en `docs/` (GitHub Pages).

- `collect.py`: recolecta reportes (web o `--export result.json` de Telegram Desktop)
- `forecast.py`: modelo y prueba hacia atrás; genera `docs/data.json`
- `alert.py`: aviso por Telegram (opcional, secretos `TELEGRAM_TOKEN` y `TELEGRAM_CHAT_ID`)
- `config.json`: tu circuito y los circuitos que disparan alerta
- `data/manual.csv`: cortes que anotas tú y que el canal no reportó
