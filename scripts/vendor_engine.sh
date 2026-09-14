#!/usr/bin/env bash
# Vendoriza el motor dte_chile en ./vendor/dte_chile, DESDE UN TAG.
#
# El repo del motor es privado, así que Dokploy no puede instalarlo durante el
# build: la copia viaja en este repositorio. Se saca del tag y no del árbol de
# trabajo, para que lo vendorizado sea exactamente la versión publicada y no
# arrastre cambios sin commitear. `scripts/check_vendor.py` lo verifica en CI.
#
# Uso: bash scripts/vendor_engine.sh v0.4.0 [ruta_al_motor]  (default: ../dte_chile)
set -euo pipefail

TAG="${1:?Indica el tag del motor, p.ej. v0.4.0}"
SRC="${2:-../dte_chile}"
DEST="vendor/dte_chile"

if [ ! -d "$SRC/.git" ]; then
  echo "No encuentro el repo del motor en '$SRC'." >&2
  exit 1
fi
if ! git -C "$SRC" rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  echo "El tag '$TAG' no existe en '$SRC'. ¿Falta 'git fetch --tags'?" >&2
  exit 1
fi

SHA="$(git -C "$SRC" rev-list -n 1 "$TAG")"
rm -rf "$DEST"
mkdir -p "$DEST"
git -C "$SRC" archive "$TAG" pyproject.toml src README.md | tar -x -C "$DEST"
printf '%s\n%s\n' "$TAG" "$SHA" > "$DEST/VERSION"

echo "Motor $TAG ($SHA) vendorizado en $DEST"
echo "Recuerda dejar el mismo tag en el extra 'engine' de pyproject.toml."
