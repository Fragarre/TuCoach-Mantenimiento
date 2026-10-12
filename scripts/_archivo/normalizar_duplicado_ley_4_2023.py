"""Elimina del catálogo el identificador histórico duplicado de Ley 4/2023."""
import json
import shutil
from datetime import datetime
from pathlib import Path

root = Path(__file__).resolve().parent.parent
catalogo = root / "materiales_estudio" / "catalogo_materiales.json"
datos = json.loads(catalogo.read_text(encoding="utf-8"))
historico = next(x for x in datos if x["norma_id"] == 46)
canonico = next(x for x in datos if x["norma_id"] == 11798)
if historico.get("fuente_canonica_inicial") != "BOE-A-2023-5366" or canonico.get("fuente_canonica_actual") != "BOE-A-2023-5366":
    raise RuntimeError("Las dos entradas no comparten la fuente canónica esperada.")
backup = catalogo.with_name(f"catalogo_materiales_antes_normalizar_ley4_{datetime.now():%Y%m%d_%H%M%S}.json")
shutil.copy2(catalogo, backup)
catalogo.write_text(json.dumps([x for x in datos if x["norma_id"] != 46], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"NORMALIZADO: eliminada entrada histórica 46; se conserva 11798. Backup: {backup}")
