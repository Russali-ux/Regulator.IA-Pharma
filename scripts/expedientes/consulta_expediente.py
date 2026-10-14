"""
consulta_expediente.py — Consulta el estatus de expedientes en DIGEMID
(https://www.digemid.minsa.gob.pe/consultaExpediente/) y detecta cambios
respecto a la ultima consulta.

La pagina es un formulario Laravel Livewire con reCAPTCHA v3 invisible y
honeypot, por eso se usa un navegador real (Playwright/Chromium) que carga la
pagina tal cual, llena el formulario y lee el resultado. No se intenta saltar
ni resolver el reCAPTCHA: si DIGEMID rechaza la consulta, el expediente queda
marcado como "error" y el correo lo informa.

Entradas:
  config/expedientes.json            lista de expedientes
  data/expedientes/estado.json       ultimo estatus conocido (se actualiza)
Salidas:
  data/expedientes/estado.json       estado nuevo
  /tmp/expedientes_reporte.json      resultado de esta corrida (para el correo)
  /tmp/expedientes_<num>.png         captura de cada consulta (diagnostico)

Uso local:  python scripts/expedientes/consulta_expediente.py [--headed]
"""
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

URL = "https://www.digemid.minsa.gob.pe/consultaExpediente/"
ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config" / "expedientes.json"
STATE = ROOT / "data" / "expedientes" / "estado.json"
REPORT = Path(os.environ.get("REPORT_PATH", "/tmp/expedientes_reporte.json"))
SHOTS = Path(os.environ.get("SHOTS_DIR", "/tmp"))
LIMA = timezone(timedelta(hours=-5))

CAMPOS = {
    "asunto": "Asunto",
    "numero_expediente": "Número de expediente",
    "situacion": "Situación",
    "fecha": "Fecha",
    "remitente": "Remitente",
    "tipo_documento": "Tipo Documento",
    "tramite": "Trámite",
    "folios": "N° Folios",
    "anexos": "Anexos",
}

# JS que corre en la pagina: localiza un input Livewire por su wire:model
JS_MARK = """([model, tag]) => {
  const el = [...document.querySelectorAll('input')]
    .find(i => i.getAttribute('wire:model.defer') === model);
  if (!el) return false;
  el.setAttribute('data-cc', tag);
  return true;
}"""

# JS que extrae el bloque de resultado: pares "Etiqueta: valor" + tabla de movimientos
JS_PARSE = """() => {
  const txt = document.body.innerText;
  const i = txt.indexOf('Resultado Consulta');
  if (i < 0) return null;
  const bloque = txt.slice(i);
  const tabla = [...document.querySelectorAll('table')].find(t => /Movimiento/i.test(t.innerText));
  const filas = tabla ? [...tabla.querySelectorAll('tbody tr')].map(tr =>
      [...tr.querySelectorAll('td')].map(td => td.innerText.trim())).filter(r => r.length >= 5) : [];
  // pares etiqueta/valor: <strong>Etiqueta:</strong> valor, o texto plano
  const pares = {};
  document.querySelectorAll('strong, b, label, span').forEach(el => {
    const t = (el.innerText || '').trim();
    if (!/:$/.test(t) || t.length > 40) return;
    const parent = el.parentElement;
    if (!parent) return;
    const v = parent.innerText.trim().slice(t.length).trim();
    if (v && !(t in pares)) pares[t.replace(/:$/, '').trim()] = v.split('\\n')[0].trim();
  });
  return {bloque: bloque.slice(0, 4000), pares, filas};
}"""


def ahora_lima():
    return datetime.now(LIMA)


def partir_numero(numero):
    m = re.fullmatch(r"\s*(\d{2})-(\d{6})-(\d{1,2})\s*", numero)
    if not m:
        raise ValueError(f"Numero de expediente invalido: {numero!r} (esperado AA-NNNNNN-S)")
    return m.group(1), m.group(2), m.group(3)


def extraer_campos(parsed):
    """Combina los pares del DOM con un regex sobre el texto plano (respaldo)."""
    out = {}
    pares = {k.lower(): v for k, v in (parsed.get("pares") or {}).items()}
    bloque = parsed.get("bloque") or ""
    etiquetas = "|".join(re.escape(e) for e in CAMPOS.values())
    for clave, etiqueta in CAMPOS.items():
        val = pares.get(etiqueta.lower())
        if not val:
            m = re.search(rf"(?:^|\n|\t){re.escape(etiqueta)}:\s*(.*?)(?=\s*(?:{etiquetas}):|\n|$)", bloque)
            val = m.group(1).strip() if m else ""
        out[clave] = val
    movimientos = []
    for r in parsed.get("filas") or []:
        movimientos.append({
            "movimiento": r[0], "fecha": r[1], "remitente": r[2],
            "destinatario": r[3], "tarea": r[4],
        })
    out["movimientos"] = movimientos
    return out


def consultar(page, numero):
    ano, num, sec = partir_numero(numero)
    page.goto(URL, wait_until="networkidle", timeout=60000)
    page.select_option("select.form-select", "3")
    for model, tag, val in (("anoExpediente", "ano", ano),
                            ("numeroExpediente", "num", num),
                            ("secuenciaExpediente", "sec", sec)):
        if not page.evaluate(JS_MARK, [model, tag]):
            raise RuntimeError(f"No se encontro el campo {model} (cambio la pagina?)")
        page.fill(f'input[data-cc="{tag}"]', val)
    # el honeypot exige que pase un tiempo minimo y que el JS de la pagina
    # rellene su token; esperamos como lo haria una persona
    page.wait_for_timeout(5000)
    page.get_by_role("button", name=re.compile("Realizar Consulta", re.I)).click()
    try:
        page.wait_for_function(
            "() => /Resultado Consulta|no se encontr|no existe|error/i.test(document.body.innerText)",
            timeout=45000,
        )
    except PWTimeout:
        raise RuntimeError("DIGEMID no devolvio resultado (posible bloqueo o reCAPTCHA rechazado)")
    page.wait_for_timeout(1500)
    page.screenshot(path=str(SHOTS / f"expedientes_{numero}.png"), full_page=True)
    parsed = page.evaluate(JS_PARSE)
    if not parsed:
        texto = page.inner_text("body")[:600]
        raise RuntimeError(f"Sin bloque 'Resultado Consulta'. Respuesta: {texto!r}")
    datos = extraer_campos(parsed)
    if not datos["movimientos"] and not datos["situacion"]:
        raise RuntimeError("Se encontro el resultado pero no se pudo leer situacion ni movimientos")
    return datos


def comparar(prev, nuevo):
    """Devuelve (movimientos_nuevos, cambio_situacion)."""
    if not prev:
        return nuevo["movimientos"], None
    vistos = {(m["movimiento"], m["fecha"], m["tarea"]) for m in prev.get("movimientos", [])}
    nuevos = [m for m in nuevo["movimientos"] if (m["movimiento"], m["fecha"], m["tarea"]) not in vistos]
    cambio = None
    if prev.get("situacion") and prev.get("situacion") != nuevo.get("situacion"):
        cambio = {"antes": prev["situacion"], "ahora": nuevo["situacion"]}
    return nuevos, cambio


def main():
    headed = "--headed" in sys.argv
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    estado = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    resultados = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headed)
        ctx = browser.new_context(
            locale="es-PE", timezone_id="America/Lima",
            viewport={"width": 1366, "height": 900},
        )
        page = ctx.new_page()
        for exp in cfg["expedientes"]:
            numero = exp["numero"].strip()
            item = {"numero": numero, "alias": exp.get("alias", "")}
            prev = estado.get(numero)
            for intento in (1, 2):
                try:
                    datos = consultar(page, numero)
                    nuevos, cambio = comparar(prev, datos)
                    item.update(ok=True, datos=datos, movimientos_nuevos=nuevos,
                                cambio_situacion=cambio, primera_consulta=prev is None)
                    estado[numero] = {**datos, "consultado": ahora_lima().isoformat(timespec="minutes")}
                    print(f"[OK] {numero}: {datos['situacion']} · {len(datos['movimientos'])} mov. "
                          f"({len(nuevos)} nuevos)")
                    break
                except Exception as e:  # noqa: BLE001
                    print(f"[ERROR] {numero} intento {intento}: {e}")
                    item.update(ok=False, error=str(e), datos=prev)
                    page.wait_for_timeout(8000)
            resultados.append(item)
        browser.close()

    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(estado, ensure_ascii=False, indent=2), encoding="utf-8")
    reporte = {"fecha": ahora_lima().strftime("%d/%m/%Y %H:%M"), "resultados": resultados}
    REPORT.write_text(json.dumps(reporte, ensure_ascii=False, indent=2), encoding="utf-8")

    ok = sum(r["ok"] for r in resultados)
    cambios = sum(bool(r.get("movimientos_nuevos") or r.get("cambio_situacion"))
                  for r in resultados if r["ok"] and not r.get("primera_consulta"))
    _gh_output(total=len(resultados), ok=ok, errores=len(resultados) - ok, con_cambios=cambios)
    # no falla el job por errores de consulta: el correo informa el estado igual


def _gh_output(**kv):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            for k, v in kv.items():
                f.write(f"{k}={v}\n")


if __name__ == "__main__":
    main()
