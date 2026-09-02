# eCTD Generator — núcleo real (Capa 2 del producto)

Base técnica del *eCTD Publisher* de Regulator.IA Farma: convierte una
**secuencia** (0000, 0001, …) descrita en la BD en una estructura eCTD real
(ICH v3.2), con backbone `index.xml`, backbone regional del Módulo 1,
**checksums MD5 reales** y **lifecycle** (new/replace/append/delete).

## Piezas

- `../../sql/002_ectd_schema.sql` — esquema PostgreSQL (schema `ectd`):
  `authorities`, `ctd_sections` (motor de reglas por autoridad), `applicants`,
  `products`, `applications`, `sequences`, `leaves` (con lifecycle y
  `modified_leaf_id`), `validations`, y la vista `v_sequence_manifest`.
- `generate_backbone.py` — el generador. Toma un **manifiesto JSON** (que
  refleja `ectd.v_sequence_manifest`) y produce la carpeta eCTD.
- `sample/` — manifiestos de ejemplo (`0000` inicial, `0001` con un `replace`)
  y PDFs dummy para probar sin datos reales.

## Uso

```bash
# Secuencia inicial
python scripts/ectd/generate_backbone.py scripts/ectd/sample/manifest_0000.json --out build_ectd
# Secuencia de respuesta que reemplaza un documento (lifecycle)
python scripts/ectd/generate_backbone.py scripts/ectd/sample/manifest_0001.json --out build_ectd
```

Salida por secuencia:

```
<pais>-<producto>/<seq>/
├── index.xml                # backbone ICH eCTD 3.2 (leaf + operation + checksum md5 real + xlink:href)
├── <pais>-regional.xml      # backbone regional del Módulo 1
├── util/dtd/ich-ectd-3-2.dtd
├── m1/ … m5/                # PDFs copiados a su ruta CTD
└── report.json              # conteos, operaciones y checksums
```

## Manifiesto (formato)

```json
{
  "application": {"number","product","applicant","applicant_key","authority","country","submission_type"},
  "sequence":    {"number","ectd_version","submission_date","submission_type","related_sequence"},
  "source_dir":  "carpeta con los PDF de origen (relativa al manifiesto)",
  "leaves": [
    {"module":3,"backbone_element":"m3-quality","section":"3.2.P.4",
     "title":"…","rel_path":"m3/32p4/archivo.pdf","source":"archivo.pdf",
     "operation":"new|replace|append|delete",
     "modified_rel_path":"ruta del leaf anterior (para replace/append/delete)",
     "sort_order":3}
  ]
}
```

En producción el manifiesto se arma con `select * from ectd.v_sequence_manifest
where sequence_id = ...` (una fila por leaf) → objeto JSON.

## Lifecycle

- `new` → documento nuevo (con checksum).
- `replace` → nuevo archivo que sustituye a un leaf de una secuencia anterior;
  el backbone emite `modified-file="../<related_sequence>/<ruta_anterior>"`.
- `append` → añade contenido relacionado al leaf anterior.
- `delete` → retira un leaf anterior (sin archivo nuevo, solo `modified-file`).

## Implementado vs pendiente

Implementado: árbol M1–M5, `index.xml` + regional, MD5 reales, lifecycle,
sequence 0000→0001, XML bien formado (verificado), motor agnóstico de BD.

Pendiente (roadmap):
- Clasificación automática de PDFs a secciones (hoy el manifiesto asigna la ruta).
- Hyperlinks y bookmarks dentro de los PDF.
- Empaquetado ZIP cifrado + transmisión por autoridad (CESP/DIGIPRiS/ESG/ventanilla).
- Perfil eCTD **v4.0** (además de 3.2) — el generador ya lee `ectd_version`.
- Backbone anidado completo (m3-2-body-of-data, etc.); hoy los leaves cuelgan
  directo del elemento de módulo (simplificación válida para el núcleo).
- Validador de Capa 3 (las verificaciones ya están modeladas en `ectd.validations`).

## Nota de estándar
El DTD oficial ICH eCTD 3.2 es distribuido por ICH; aquí se referencia
(`util/dtd/ich-ectd-3-2.dtd`) para validación estricta con un validador externo.
