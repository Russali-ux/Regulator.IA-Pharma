#!/usr/bin/env python3
"""Paso 2: genera embeddings locales (gratis, sin API key) para cada alerta y
escribe data/json/PAVS_BD_embedded.json. Modelo multilingüe de 384 dims."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JSON_IN = ROOT / "data" / "json" / "PAVS_BD_latest.json"
JSON_OUT = ROOT / "data" / "json" / "PAVS_BD_embedded.json"
MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


def build_text(r):
    parts = [r.get("titulo_alerta"), r.get("tipo_producto"), r.get("ifa"),
             r.get("reaccion_adversa"), r.get("agencia"), r.get("pais")]
    return " | ".join(p for p in parts if p)


def main():
    if not JSON_IN.exists():
        sys.exit(f"ERROR: no existe {JSON_IN}. Corre primero el paso 1.")
    records = json.loads(JSON_IN.read_text(encoding="utf-8"))
    if not records:
        print("[2] Sin registros, nada que embeber.")
        JSON_OUT.write_text("[]", encoding="utf-8")
        return

    from sentence_transformers import SentenceTransformer
    print(f"[2] Cargando modelo local '{MODEL_NAME}' ...")
    model = SentenceTransformer(MODEL_NAME)

    texts = [build_text(r) for r in records]
    print(f"[2] Generando embeddings para {len(texts)} alertas ...")
    vectors = model.encode(texts, batch_size=64, show_progress_bar=True,
                           normalize_embeddings=True)

    for r, v in zip(records, vectors):
        r["embedding"] = [round(float(x), 6) for x in v]

    JSON_OUT.write_text(json.dumps(records, ensure_ascii=False),
                        encoding="utf-8")
    print(f"[2] Escrito {JSON_OUT.relative_to(ROOT)} "
          f"(dim={len(records[0]['embedding'])})")


if __name__ == "__main__":
    main()
