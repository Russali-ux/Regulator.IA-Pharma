#!/usr/bin/env python3
"""Paso 4: enriquece data/pavs_monitor.json con el ATC real curado en Supabase
(pavs_alertas.atc_code + nombre del catalogo atc_codes), cruzando por 'enlace'.
Las alertas sin atc_code quedan como 'Sin ATC'. Requiere SUPABASE_URL y
SUPABASE_SERVICE_ROLE_KEY (o cualquier key con lectura). Si no hay credenciales,
no hace nada (deja el json tal cual)."""
import json
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
MONITOR = ROOT / "data" / "pavs_monitor.json"


def main():
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not key:
        print("[4] Sin credenciales Supabase; se omite el enriquecimiento ATC.")
        return
    if not MONITOR.exists():
        sys.exit(f"ERROR: no existe {MONITOR}. Corre primero el paso 1.")

    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    # Embedding PostgREST: pavs_alertas.atc_code -> atc_codes
    endpoint = (f"{url}/rest/v1/pavs_alertas"
                f"?select=enlace,atc_code,atc_codes(atc_name,atc_name_es)"
                f"&atc_code=not.is.null")
    resp = requests.get(endpoint, headers=headers, timeout=60)
    if resp.status_code >= 300:
        print(f"[4] Aviso: no se pudo leer ATC de Supabase ({resp.status_code}): "
              f"{resp.text[:200]}. Se deja el json sin cambios.")
        return
    rows = resp.json()

    atc_by_enlace = {}
    for r in rows:
        enlace = r.get("enlace")
        code = r.get("atc_code")
        if not enlace or not code:
            continue
        cat = r.get("atc_codes") or {}
        name = cat.get("atc_name_es") or cat.get("atc_name")
        atc_by_enlace[enlace] = f"{code} — {name}" if name else code

    doc = json.loads(MONITOR.read_text(encoding="utf-8"))
    n = 0
    for row in doc.get("data", []):
        val = atc_by_enlace.get(row.get("enlace"))
        if val:
            row["atc"] = val
            n += 1
    MONITOR.write_text(json.dumps(doc, ensure_ascii=False, indent=2),
                       encoding="utf-8")
    print(f"[4] ATC enriquecido en {n} fila(s) del monitor "
          f"(de {len(atc_by_enlace)} alertas con atc_code en Supabase).")


if __name__ == "__main__":
    main()
