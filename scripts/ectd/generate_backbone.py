#!/usr/bin/env python3
"""
Generador de backbone eCTD (ICH v3.2) — Regulator.IA Farma.

Toma un MANIFIESTO (JSON) que describe una secuencia (0000, 0001, ...) y sus
documentos (leaves), y produce una estructura eCTD REAL:

  <app-slug>/<seq>/
    ├── index.xml                 backbone ICH eCTD (leaves con operation + checksum MD5 real)
    ├── <pais>-regional.xml       backbone regional del Módulo 1
    ├── util/dtd/ich-ectd-3-2.dtd  (referencia; el DTD oficial es de ICH)
    ├── m1/ ... m5/               los PDFs copiados a su ruta CTD
    └── report.json               resumen (conteos, checksums, operaciones)

- Calcula el MD5 real de cada archivo.
- Soporta lifecycle: new | replace | append | delete (con modified-file al
  leaf de la secuencia anterior vía related_sequence).
- Es agnóstico de BD: el manifiesto refleja la vista ectd.v_sequence_manifest,
  así que puede venir de PostgreSQL o escribirse a mano.

Uso:
    python generate_backbone.py <manifiesto.json> [--out DIR]
"""
import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

MODULE_ORDER = ["m1-administrative-information", "m2-summary", "m3-quality",
                "m4-nonclinical-study-reports", "m5-clinical-study-reports"]
XLINK_NS = "http://www.w3.org/1999/xlink"
ECTD_NS = "http://www.ich.org/ectd"


def md5_of(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def slugify(*parts) -> str:
    s = "-".join(str(p) for p in parts if p)
    keep = "abcdefghijklmnopqrstuvwxyz0123456789-"
    s = "".join(c if c in keep else "-" for c in s.lower())
    while "--" in s:
        s = s.replace("--", "-")
    return s.strip("-")


def leaf_xml(leaf, related_sequence, indent="      "):
    op = leaf["operation"]
    attrs = [f'operation="{op}"']
    if op != "delete" and leaf.get("checksum"):
        attrs.append(f'checksum={quoteattr(leaf["checksum"])}')
        attrs.append(f'checksum-type="{leaf.get("checksum_type","md5")}"')
    if op != "delete" and leaf.get("rel_path"):
        attrs.append(f'xlink:href={quoteattr(leaf["rel_path"])}')
    # modified-file: apunta al leaf afectado en la secuencia anterior
    if op in ("replace", "append", "delete") and leaf.get("modified_rel_path"):
        mref = f'../{related_sequence}/{leaf["modified_rel_path"]}' if related_sequence \
               else leaf["modified_rel_path"]
        attrs.append(f'modified-file={quoteattr(mref)}')
    out = f'{indent}<leaf {" ".join(attrs)}>\n'
    out += f'{indent}  <title>{escape(leaf["title"])}</title>\n'
    out += f'{indent}</leaf>\n'
    return out


def build_index_xml(manifest, leaves):
    seq = manifest["sequence"]
    ver = seq.get("ectd_version", "3.2")
    rel = seq.get("related_sequence")
    lines = []
    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append('<!DOCTYPE ectd:ectd SYSTEM "util/dtd/ich-ectd-3-2.dtd">')
    lines.append(f'<ectd:ectd xmlns:ectd={quoteattr(ECTD_NS)} '
                 f'xmlns:xlink={quoteattr(XLINK_NS)} dtd-version="{ver}">')
    by_be = {}
    for lf in leaves:
        by_be.setdefault(lf["backbone_element"], []).append(lf)
    for be in MODULE_ORDER:
        group = by_be.get(be)
        if not group:
            continue
        lines.append(f'  <{be}>')
        for lf in sorted(group, key=lambda x: x.get("sort_order", 0)):
            lines.append(leaf_xml(lf, rel).rstrip("\n"))
        lines.append(f'  </{be}>')
    lines.append('</ectd:ectd>')
    return "\n".join(lines) + "\n"


def build_regional_xml(manifest, leaves):
    app = manifest["application"]
    country = app.get("country", "xx")
    rel = manifest["sequence"].get("related_sequence")
    m1 = [l for l in leaves if l["module"] == 1]
    lines = []
    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append(f'<{country}-regional xmlns:xlink={quoteattr(XLINK_NS)}>')
    lines.append('  <admin>')
    lines.append(f'    <applicant>{escape(app.get("applicant",""))}</applicant>')
    lines.append(f'    <application-number>{escape(app.get("number",""))}</application-number>')
    lines.append(f'    <authority>{escape(app.get("authority",""))}</authority>')
    lines.append(f'    <submission-type>{escape(manifest["sequence"].get("submission_type",""))}</submission-type>')
    lines.append(f'    <sequence>{escape(manifest["sequence"].get("number",""))}</sequence>')
    lines.append('  </admin>')
    lines.append('  <m1-regional>')
    for lf in sorted(m1, key=lambda x: x.get("sort_order", 0)):
        lines.append(leaf_xml(lf, rel, indent="    ").rstrip("\n"))
    lines.append('  </m1-regional>')
    lines.append(f'</{country}-regional>')
    return "\n".join(lines) + "\n"


DTD_PLACEHOLDER = (
    "<!-- Referencia: el DTD oficial ICH eCTD 3.2 (ich-ectd-3-2.dtd) se\n"
    "     distribuye por ICH. Colóquelo aquí para validación estricta.\n"
    "     https://admin.ich.org/ (eCTD v3.2.2 specification and related files) -->\n"
)


def generate(manifest_path: Path, out_root: Path):
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    app = manifest["application"]
    seq = manifest["sequence"]
    source_dir = Path(manifest.get("source_dir", manifest_path.parent))
    if not source_dir.is_absolute():
        source_dir = (manifest_path.parent / source_dir).resolve()

    slug = slugify(app.get("country"), app.get("product"))
    seq_dir = out_root / slug / seq["number"]
    seq_dir.mkdir(parents=True, exist_ok=True)

    leaves = manifest["leaves"]
    report = {"application": app.get("number"), "sequence": seq["number"],
              "operations": {}, "leaves": [], "missing_sources": []}

    for lf in leaves:
        op = lf["operation"]
        report["operations"][op] = report["operations"].get(op, 0) + 1
        if op == "delete":
            lf["checksum"] = None
            report["leaves"].append({"op": op, "title": lf["title"],
                                     "modifies": lf.get("modified_rel_path")})
            continue
        # localizar el archivo fuente
        src_name = lf.get("source") or Path(lf["rel_path"]).name
        src = source_dir / src_name
        if not src.exists():
            report["missing_sources"].append(str(src))
            print(f"  [!] fuente no encontrada: {src}")
            continue
        dst = seq_dir / lf["rel_path"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        lf["checksum"] = md5_of(dst)
        lf["checksum_type"] = "md5"
        report["leaves"].append({"op": op, "path": lf["rel_path"],
                                 "md5": lf["checksum"]})

    # backbone + regional + dtd
    (seq_dir / "index.xml").write_text(build_index_xml(manifest, leaves), encoding="utf-8")
    (seq_dir / f'{app.get("country","xx")}-regional.xml').write_text(
        build_regional_xml(manifest, leaves), encoding="utf-8")
    dtd_dir = seq_dir / "util" / "dtd"
    dtd_dir.mkdir(parents=True, exist_ok=True)
    (dtd_dir / "ich-ectd-3-2.dtd").write_text(DTD_PLACEHOLDER, encoding="utf-8")
    (seq_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[eCTD] Secuencia {seq['number']} generada en: {seq_dir}")
    print(f"       leaves: {len(leaves)} | operaciones: {report['operations']}")
    if report["missing_sources"]:
        print(f"       AVISO: {len(report['missing_sources'])} fuente(s) faltante(s)")
    return seq_dir


def main():
    ap = argparse.ArgumentParser(description="Genera backbone eCTD desde un manifiesto JSON.")
    ap.add_argument("manifest", help="Ruta al manifiesto JSON de la secuencia.")
    ap.add_argument("--out", default="build_ectd", help="Directorio de salida (default: build_ectd).")
    args = ap.parse_args()
    mp = Path(args.manifest)
    if not mp.exists():
        sys.exit(f"ERROR: no existe el manifiesto {mp}")
    generate(mp, Path(args.out))


if __name__ == "__main__":
    main()
