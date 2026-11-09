#!/usr/bin/env python3
"""Paso 3: upsert de las alertas (con embedding) a public.pavs_alertas via
PostgREST. Dedupe por 'dedupe_key' (md5 de enlace|titulo|ifa|reaccion,
misma formula que la migracion pavs_alertas_dedupe_key). Usa la SERVICE_ROLE key (ignora RLS)."""
import hashlib
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
    "reaccion_adversa", "enlace", "embedding", "fuente_archivo",
    "dedupe_key",
    # OJO: NO enviar "atc" — la tabla usa "atc_code" (curado a mano,
    # con catalogo/historial); el sync no debe tocarlo.
]


def _norm(v):
    # Igual que lower(trim(coalesce(x,''))) de Postgres: trim solo quita espacios
    return ("" if v is None else str(v)).strip(" ").lower()


def dedupe_key(r):
    base = "|".join(_norm(r.get(c)) for c in
                    ("enlace", "titulo_alerta", "ifa", "reaccion_adversa"))
    return hashlib.md5(base.encode("utf-8")).hexdigest()


def main():
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not key:
        sys.exit("ERROR: define SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY "
                 "(en .env o como variables de entorno).")

    src = EMBEDDED if EMBEDDED.exists() else PLAIN
    records = json.loads(src.read_text(encoding="utf-8"))
    # Solo filas con enlace; un mismo enlace puede traer varias alertas,
    # por eso se deduplica por dedupe_key (no por enlace)
    seen, rows = set(), []
    for r in records:
        if not r.get("enlace"):
            continue
        k = dedupe_key(r)
        if k in seen:
            continue
        seen.add(k)
        row = {c: r.get(c) for c in COLUMNS}
        row["dedupe_key"] = k
        rows.append(row)

    if not rows:
        print("[3] No hay filas con 'enlace'; nada que sincronizar.")
        return

    endpoint = f"{url}/rest/v1/{TABLE}?on_conflict=dedupe_key"
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
