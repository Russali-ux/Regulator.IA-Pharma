#!/usr/bin/env python3
"""Paso 3: upsert de las alertas (con embedding) a public.pavs_alertas via
PostgREST. Dedupe por 'enlace'. Usa la SERVICE_ROLE key (ignora RLS)."""
import json
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
EMBEDDED = ROOT / "data" / "json" / "PAVS_BD_embedded.json"
PLAIN = ROOT / "data" / "json" / "PAVS_BD_latest.json"
TABLE = "pavs_alertas"
CHUNK = 400

COLUMNS = [
    "anio", "mes", "fecha_emision", "fecha_revision", "pais", "agencia",
    "tipo_alerta", "titulo_alerta", "tipo_producto", "ifa",
    "reaccion_adversa", "enlace", "atc", "embedding", "fuente_archivo",
]


def main():
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not key:
        sys.exit("ERROR: define SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY "
                 "(en .env o como variables de entorno).")

    src = EMBEDDED if EMBEDDED.exists() else PLAIN
    records = json.loads(src.read_text(encoding="utf-8"))
    # Solo filas con enlace (clave de conflicto) y sin duplicar enlace
    seen, rows = set(), []
    for r in records:
        enlace = r.get("enlace")
        if not enlace or enlace in seen:
            continue
        seen.add(enlace)
        rows.append({c: r.get(c) for c in COLUMNS})

    if not rows:
        print("[3] No hay filas con 'enlace'; nada que sincronizar.")
        return

    endpoint = f"{url}/rest/v1/{TABLE}?on_conflict=enlace"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }

    total = 0
    for i in range(0, len(rows), CHUNK):
        batch = rows[i:i + CHUNK]
        resp = requests.post(endpoint, headers=headers,
                             data=json.dumps(batch), timeout=120)
        if resp.status_code >= 300:
            sys.exit(f"ERROR Supabase {resp.status_code}: {resp.text[:500]}")
        total += len(batch)
        print(f"[3] Upsert {total}/{len(rows)} ...")

    print(f"[3] OK. {total} alertas sincronizadas en public.{TABLE}.")


if __name__ == "__main__":
    main()
