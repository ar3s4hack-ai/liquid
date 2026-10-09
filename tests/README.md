# Pruebas

Sin red: el servidor usa respuestas simuladas de los exchanges.

```bash
python3 tests/test_backend.py . /tmp/salida     # servidor: modelo, fuentes, rutas, validación, colectores, Hyperliquid, libro…
node tests/harness.js . /tmp/salida             # web: dibujo, paneles, directo, mapa de liquidez, manual… (usa lo que deja la anterior)
```

- `serve_test.py`: la web con datos simulados en `http://127.0.0.1:PUERTO` para verla en un navegador.
- `profile_server.py`: tiempos del servidor con datos de tamaño real (por temporalidad y modelo, validación, calibración, libro).
