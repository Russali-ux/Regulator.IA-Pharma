#!/usr/bin/env python3
"""Paso 1: toma el .xlsx más reciente de data/raw/ (hoja PAVS_BD), normaliza
columnas y genera:
  - data/csv/PAVS_BD_latest.csv
  - data/json/PAVS_BD_latest.json      (para embeddings + Supabase)
  - data/pavs_monitor.json             (PÚBLICO, alimenta la tabla del index.html)
"""
import hashlib
import json
import re
import sys
import unicodedata
from datetime import date, datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
CSV_OUT = ROOT / "data" / "csv" / "PAVS_BD_latest.csv"
JSON_OUT = ROOT / "data" / "json" / "PAVS_BD_latest.json"
MONITOR_OUT = ROOT / "data" / "pavs_monitor.json"
SHEET = "PAVS_BD"
MONITOR_MAX_ROWS = 500  # filas (más recientes) que se publican al sitio

# Campos destino (coinciden con la tabla public.pavs_alertas)
TARGET_FIELDS = [
    "anio", "mes", "fecha_emision", "fecha_revision", "pais", "agencia",
    "tipo_alerta", "titulo_alerta", "tipo_producto", "ifa",
    "reaccion_adversa", "enlace", "atc",
]

# Sinónimos de encabezados -> campo destino (clave = header normalizado)
HEADER_MAP = {
    "anio": "anio", "ano": "anio", "year": "anio",
    "mes": "mes", "month": "mes",
    "fecha_emision": "fecha_emision", "fecha_de_emision": "fecha_emision",
    "fecha": "fecha_emision", "fecha_alerta": "fecha_emision",
    "fecha_revision": "fecha_revision", "fecha_de_revision": "fecha_revision",
    "pais": "pais", "country": "pais",
    "agencia": "agencia", "autoridad": "agencia", "agency": "agencia",
    "tipo_alerta": "tipo_alerta", "tipo_de_alerta": "tipo_alerta",
    "tipo": "tipo_alerta", "clasificacion": "tipo_alerta",
    "titulo_alerta": "titulo_alerta", "titulo": "titulo_alerta",
    "titulo_de_alerta": "titulo_alerta",
    "titulo_de_la_alerta": "titulo_alerta", "descripcion": "titulo_alerta",
    "asunto": "titulo_alerta",
    "tipo_producto": "tipo_producto", "tipo_de_producto": "tipo_producto",
    "producto": "tipo_producto",
    "ifa": "ifa", "principio_activo": "ifa", "nombre_generico": "ifa",
    "dci": "ifa", "ifa_nombre_generico": "ifa",
    "reaccion_adversa": "reaccion_adversa", "ram": "reaccion_adversa",
    "reaccion": "reaccion_adversa", "evento_adverso": "reaccion_adversa",
    "reaccion_adversa_incidente_adverso": "reaccion_adversa",
    "incidente_adverso": "reaccion_adversa",
    "enlace": "enlace", "link": "enlace", "url": "enlace", "fuente": "enlace",
    "atc": "atc", "codigo_atc": "atc",
}

TIPO_LABEL = {
    "falsificacion": "Falsificación", "calidad": "Calidad",
    "seguridad": "Seguridad", "retiro": "Retiro",
    "desabastecimiento": "Desabastecimiento",
}


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s)
                   if not unicodedata.combining(c))


def norm_header(h) -> str:
    h = strip_accents(str(h)).lower().strip()
    h = re.sub(r"[^a-z0-9]+", "_", h).strip("_")
    return h


def find_latest_xlsx() -> Path:
    files = [f for f in RAW_DIR.glob("*.xlsx")
             if f.name.lower().startswith("ft-95")
             or "monitoreo de alertas de pavs" in f.name.lower()]
    if not files:
        files = list(RAW_DIR.glob("*.xlsx"))
    if not files:
        sys.exit("ERROR: no hay .xlsx en data/raw/. Corre primero el paso 0.")

    def keyf(p: Path):
        m = re.search(r"(\d{4})[-_]?(\d{2})[-_]?(\d{2})", p.name)
        if m:
            return (1, m.group(1) + m.group(2) + m.group(3))
        return (0, str(p.stat().st_mtime))

    return sorted(files, key=keyf)[-1]


def to_iso_date(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, (datetime, date)):
        return v.strftime("%Y-%m-%d")
    s = str(v).strip()
    if not s or s.lower() in ("nan", "nat"):
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s[:10], fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return s[:10]


def clean(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    return s if s and s.lower() not in ("nan", "nat") else None


def tipo_slug(tipo_alerta):
    t = strip_accents(str(tipo_alerta or "")).lower()
    if "falsif" in t:
        return "falsificacion"
    if "calidad" in t or "especific" in t or "defecto" in t:
        return "calidad"
    if "retiro" in t or "recall" in t or "suspens" in t:
        return "retiro"
    if "desabast" in t:
        return "desabastecimiento"
    return "seguridad"


def main():
    src = find_latest_xlsx()
    print(f"[1] Leyendo '{src.name}' hoja '{SHEET}'")
    df = pd.read_excel(src, sheet_name=SHEET, dtype=object)

    # Mapear encabezados
    rename = {}
    for col in df.columns:
        key = norm_header(col)
        if key in HEADER_MAP:
            rename[col] = HEADER_MAP[key]
    df = df.rename(columns=rename)
    for f in TARGET_FIELDS:
        if f not in df.columns:
            df[f] = None

    unmapped = [c for c in df.columns if c not in TARGET_FIELDS]
    if unmapped:
        print(f"[1] Aviso: columnas no mapeadas (ignoradas): {unmapped}")

    records = []
    for _, row in df.iterrows():
        enlace = clean(row.get("enlace"))
        titulo = clean(row.get("titulo_alerta"))
        if not enlace and not titulo:
            continue  # fila vacía
        fecha_em = to_iso_date(row.get("fecha_emision"))
        anio = clean(row.get("anio"))
        try:
            anio = int(float(anio)) if anio is not None else (
                int(fecha_em[:4]) if fecha_em else None)
        except (ValueError, TypeError):
            anio = int(fecha_em[:4]) if fecha_em else None
        mes = clean(row.get("mes"))
        try:
            mes = int(float(mes)) if mes is not None else (
                int(fecha_em[5:7]) if fecha_em else None)
        except (ValueError, TypeError):
            mes = int(fecha_em[5:7]) if fecha_em else None

        rec = {
            "anio": anio,
            "mes": mes,
            "fecha_emision": fecha_em,
            "fecha_revision": to_iso_date(row.get("fecha_revision")),
            "pais": clean(row.get("pais")),
            "agencia": clean(row.get("agencia")),
            "tipo_alerta": clean(row.get("tipo_alerta")),
            "titulo_alerta": titulo,
            "tipo_producto": clean(row.get("tipo_producto")),
            "ifa": clean(row.get("ifa")),
            "reaccion_adversa": clean(row.get("reaccion_adversa")),
            "enlace": enlace,
            "atc": clean(row.get("atc")),
            "fuente_archivo": src.name,
        }
        base = enlace or (titulo or "") + (fecha_em or "")
        rec["local_id"] = hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]
        records.append(rec)

    print(f"[1] {len(records)} alertas procesadas")

    # Salidas para el pipeline
    CSV_OUT.parent.mkdir(parents=True, exist_ok=True)
    JSON_OUT.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(records).to_csv(CSV_OUT, index=False, encoding="utf-8-sig")
    JSON_OUT.write_text(json.dumps(records, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"[1] Escrito {CSV_OUT.relative_to(ROOT)} y {JSON_OUT.relative_to(ROOT)}")

    write_monitor_json(records)


def write_monitor_json(records):
    """Genera data/pavs_monitor.json en el formato que consume index.html."""
    # Gráfico por año (sobre TODO el histórico)
    by_year = {}
    by_agency = {}
    for r in records:
        y = r.get("anio")
        if y:
            by_year[str(y)] = by_year.get(str(y), 0) + 1
        ag = r.get("agencia")
        if ag:
            by_agency[ag] = by_agency.get(ag, 0) + 1

    year_chart = [[y, by_year[y]] for y in sorted(by_year)]
    agency_chart = sorted(([a, c] for a, c in by_agency.items()),
                          key=lambda x: x[1], reverse=True)[:10]

    # Filas para la tabla: más recientes primero
    def fkey(r):
        return r.get("fecha_emision") or ""

    recent = sorted(records, key=fkey, reverse=True)[:MONITOR_MAX_ROWS]
    data = []
    for r in recent:
        slug = tipo_slug(r.get("tipo_alerta"))
        label = (r.get("tipo_alerta") or TIPO_LABEL.get(slug, "Seguridad")).strip()
        data.append({
            "y": r.get("anio") or (int(r["fecha_emision"][:4])
                                    if r.get("fecha_emision") else ""),
            "fecha": r.get("fecha_emision") or "",
            "pais": r.get("pais") or "",
            "agencia": r.get("agencia") or "",
            "tipo": slug,
            "tipoLabel": label,
            "titulo": r.get("titulo_alerta") or "",
            "producto": r.get("tipo_producto") or "",
            "ifa": r.get("ifa") or "",
            "atc": r.get("atc") or "Sin ATC",
            "enlace": r.get("enlace") or "",
        })

    # Estadísticas del dashboard (sobre todo el histórico)
    max_year = max((int(y) for y in by_year), default=None)
    this_year = by_year.get(str(max_year), 0) if max_year else 0
    n_agencies = len({r.get("agencia") for r in records if r.get("agencia")})
    n_countries = len({r.get("pais") for r in records if r.get("pais")})

    out = {
        "generatedAt": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total": len(records),
        "stats": {
            "total": len(records),
            "thisYear": this_year,
            "agencies": n_agencies,
            "countries": n_countries,
        },
        "yearChart": year_chart,
        "agencyChart": agency_chart,
        "data": data,
    }
    MONITOR_OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print(f"[1] Escrito {MONITOR_OUT.relative_to(ROOT)} "
          f"({len(data)} filas, {len(records)} totales)")


if __name__ == "__main__":
    main()
