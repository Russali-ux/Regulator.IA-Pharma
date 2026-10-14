"""
send_status_email.py — Envia por correo el estatus de los expedientes DIGEMID
consultados por consulta_expediente.py (lee /tmp/expedientes_reporte.json).

Remitente: conkosafe.ai@gmail.com (SMTP_USER / SMTP_PASS, contrasena de aplicacion).
Destinatarios: EMAIL_TO (coma-separados), enviados en BCC por el sobre SMTP.
Adjunta la captura de cada consulta si existe.
"""
import html
import json
import os
import smtplib
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
import tempfile

REPORT = Path(os.environ.get("REPORT_PATH", Path(tempfile.gettempdir()) / "expedientes_reporte.json"))
SHOTS = Path(os.environ.get("SHOTS_DIR", tempfile.gettempdir()))
URL = "https://www.digemid.minsa.gob.pe/consultaExpediente/"

EMAIL_FROM = os.environ.get("EMAIL_FROM", "conkosafe.ai@gmail.com")
EMAIL_TO = [e.strip() for e in os.environ.get("EMAIL_TO", "").split(",") if e.strip()] or [EMAIL_FROM]
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"

e = html.escape


def bloque_expediente(r):
    d = r.get("datos") or {}
    titulo = f'Expediente {e(r["numero"])}'
    alias = f'<div style="font-size:12px;color:#BDD7EE;">{e(r["alias"])}</div>' if r.get("alias") else ""

    if not r["ok"]:
        estado = ('<div style="background:#C00000;color:#fff;padding:10px 14px;border-radius:6px;'
                  'font-weight:bold;">No se pudo consultar en esta corrida</div>'
                  f'<p style="font-size:12px;color:#666;">Detalle: {e(r.get("error", ""))}</p>')
        if d:
            estado += (f'<p style="font-size:13px;">Ultimo estatus conocido: <b>{e(d.get("situacion", ""))}</b> '
                       f'({len(d.get("movimientos", []))} movimientos).</p>')
        cuerpo = estado
    else:
        nuevos = r.get("movimientos_nuevos") or []
        cambio = r.get("cambio_situacion")
        if r.get("primera_consulta"):
            badge = ("#2E75B6", "Primera consulta registrada — linea base")
        elif cambio:
            badge = ("#C00000", f'Cambio de situacion: {cambio["antes"]} → {cambio["ahora"]}')
        elif nuevos:
            badge = ("#ED7D31", f"{len(nuevos)} movimiento(s) nuevo(s) desde la ultima consulta")
        else:
            badge = ("#548235", "Sin cambios desde la ultima consulta")
        claves_nuevas = {(m["movimiento"], m["fecha"], m["tarea"]) for m in nuevos} if not r.get("primera_consulta") else set()

        filas_info = "".join(
            f'<tr><td style="padding:4px 8px;color:#1F4E79;font-weight:bold;white-space:nowrap;">{e(k)}</td>'
            f'<td style="padding:4px 8px;">{e(d.get(c, "") or "—")}</td></tr>'
            for k, c in (("Situación", "situacion"), ("Asunto", "asunto"), ("Trámite", "tramite"),
                         ("Remitente", "remitente"), ("Fecha ingreso", "fecha"),
                         ("Tipo documento", "tipo_documento"), ("Anexos", "anexos"))
        )
        filas_mov = ""
        for m in d.get("movimientos", []):
            es_nuevo = (m["movimiento"], m["fecha"], m["tarea"]) in claves_nuevas
            bg = "#FFF4E5" if es_nuevo else "#fff"
            marca = ' <span style="color:#ED7D31;font-weight:bold;">NUEVO</span>' if es_nuevo else ""
            filas_mov += (f'<tr style="background:{bg};">'
                          f'<td style="padding:5px 8px;border-bottom:1px solid #eee;">{e(m["movimiento"])}</td>'
                          f'<td style="padding:5px 8px;border-bottom:1px solid #eee;white-space:nowrap;">{e(m["fecha"])}</td>'
                          f'<td style="padding:5px 8px;border-bottom:1px solid #eee;">{e(m["remitente"])}</td>'
                          f'<td style="padding:5px 8px;border-bottom:1px solid #eee;">{e(m["destinatario"])}</td>'
                          f'<td style="padding:5px 8px;border-bottom:1px solid #eee;">{e(m["tarea"])}{marca}</td></tr>')
        cuerpo = (
            f'<div style="background:{badge[0]};color:#fff;padding:10px 14px;border-radius:6px;'
            f'font-weight:bold;font-size:14px;">{e(badge[1])}</div>'
            f'<table cellpadding="0" cellspacing="0" style="margin:14px 0;font-size:13px;color:#333;">{filas_info}</table>'
            '<table width="100%" cellpadding="0" cellspacing="0" style="font-size:12px;border-collapse:collapse;">'
            '<tr style="background:#1F4E79;color:#fff;">'
            '<th align="left" style="padding:6px 8px;">#</th><th align="left" style="padding:6px 8px;">Fecha</th>'
            '<th align="left" style="padding:6px 8px;">Remitente</th><th align="left" style="padding:6px 8px;">Destinatario</th>'
            f'<th align="left" style="padding:6px 8px;">Tarea</th></tr>{filas_mov}</table>'
        )

    return (
        '<tr><td style="padding:16px 32px 6px;">'
        '<div style="background:#1F4E79;padding:10px 14px;border-radius:8px 8px 0 0;color:#fff;'
        f'font-weight:bold;font-size:15px;">{titulo}{alias}</div>'
        f'<div style="border:1px solid #DDE3EA;border-top:none;padding:14px;border-radius:0 0 8px 8px;">{cuerpo}</div>'
        '</td></tr>'
    )


def main():
    rep = json.loads(REPORT.read_text(encoding="utf-8"))
    res = rep["resultados"]
    errores = [r for r in res if not r["ok"]]
    cambios = [r for r in res if r["ok"] and not r.get("primera_consulta")
               and (r.get("movimientos_nuevos") or r.get("cambio_situacion"))]

    if len(res) == 1 and res[0]["ok"]:
        d = res[0]["datos"]
        asunto = f'Expediente {res[0]["numero"]} — {d.get("situacion", "")}'
        if cambios:
            asunto += " (con novedades)"
    else:
        asunto = f"Estatus de {len(res)} expediente(s) DIGEMID"
        if cambios:
            asunto += f" — {len(cambios)} con novedades"
    if errores:
        asunto += f" — {len(errores)} sin consultar"
    asunto = f"[ConkoSafe IA] {asunto} · {rep['fecha']}"

    cuerpo = "".join(bloque_expediente(r) for r in res)
    doc = (
        '<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8"></head>'
        '<body style="margin:0;padding:0;background:#F0F2F5;font-family:Arial,sans-serif;">'
        '<table width="100%" cellpadding="0" cellspacing="0" style="background:#F0F2F5;padding:28px 0;"><tr><td align="center">'
        '<table width="680" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:12px;overflow:hidden;">'
        '<tr><td style="background:linear-gradient(135deg,#1F4E79 0%,#2E75B6 100%);padding:24px 32px;">'
        '<p style="margin:0;color:#BDD7EE;font-size:11px;text-transform:uppercase;letter-spacing:1.2px;">'
        'CONKOSAFE IA &mdash; Regulatory Intelligence</p>'
        '<h1 style="margin:6px 0 0;color:#fff;font-size:21px;">Seguimiento de Expedientes DIGEMID</h1>'
        f'<p style="margin:4px 0 0;color:#BDD7EE;font-size:12px;">Consulta automatica cada 3 dias &mdash; {e(rep["fecha"])} hora Lima</p>'
        f'</td></tr>{cuerpo}'
        '<tr><td style="padding:18px 32px 26px;font-size:11px;color:#888;">'
        f'Fuente: <a href="{URL}" style="color:#2E75B6;">DIGEMID &mdash; Consulta de Expedientes</a>. '
        'Las capturas de cada consulta van adjuntas. Este correo es informativo; '
        'verifique en el portal antes de tomar decisiones regulatorias.</td></tr>'
        '</table></td></tr></table></body></html>'
    )

    msg = MIMEMultipart("mixed")
    msg["Subject"] = asunto
    msg["From"] = f"ConkoSafe IA <{EMAIL_FROM}>"
    msg["To"] = EMAIL_FROM
    msg.attach(MIMEText(doc, "html", "utf-8"))
    for r in res:
        shot = SHOTS / f'expedientes_{r["numero"]}.png'
        if shot.exists():
            img = MIMEImage(shot.read_bytes(), "png")
            img.add_header("Content-Disposition", "attachment", filename=f'expediente_{r["numero"]}.png')
            msg.attach(img)

    print(f"Asunto: {asunto}\nDestinatarios: {len(EMAIL_TO)}")
    if DRY_RUN:
        (SHOTS / "expedientes_email.html").write_text(doc, encoding="utf-8")
        print(f"DRY_RUN: correo no enviado (HTML en {SHOTS / 'expedientes_email.html'})")
        return
    with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587"))) as s:
        s.starttls()
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
        s.sendmail(EMAIL_FROM, sorted(set(EMAIL_TO + [EMAIL_FROM])), msg.as_string())
    print("Correo enviado.")


if __name__ == "__main__":
    main()
