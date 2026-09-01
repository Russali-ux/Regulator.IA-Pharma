#!/usr/bin/env python3
"""Paso 0: descarga los .xlsx nuevos desde OneDrive (carpeta 'Reportes PAVS')
a data/raw/ usando rclone. Solo baja lo que no exista ya localmente."""
import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
DEFAULT_REMOTE_PATH = "Documentos/Claude/Projects/Reportes PAVS"
DEFAULT_REMOTE_NAME = os.environ.get("RCLONE_REMOTE", "onedrive")


def main():
    ap = argparse.ArgumentParser(description="Trae los xlsx nuevos de OneDrive.")
    ap.add_argument("--remote-path", default=DEFAULT_REMOTE_PATH,
                    help="Ruta dentro de OneDrive (por defecto: %(default)s)")
    ap.add_argument("--remote-name", default=DEFAULT_REMOTE_NAME,
                    help="Nombre del remote de rclone (por defecto: %(default)s)")
    args = ap.parse_args()

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    src = f"{args.remote_name}:{args.remote_path}"
    print(f"[0] rclone copy '{src}' -> '{RAW_DIR}' (solo *.xlsx nuevos)")

    cmd = ["rclone", "copy", src, str(RAW_DIR),
           "--include", "*.xlsx", "--ignore-existing", "--progress"]
    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError:
        sys.exit("ERROR: rclone no está instalado o no está en el PATH.")
    except subprocess.CalledProcessError as e:
        sys.exit(f"ERROR: rclone terminó con código {e.returncode}.")

    xlsx = sorted(RAW_DIR.glob("*.xlsx"))
    print(f"[0] OK. {len(xlsx)} archivo(s) .xlsx en data/raw/.")


if __name__ == "__main__":
    main()
