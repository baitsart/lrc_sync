#!/usr/bin/env bash

if [ "$#" -lt 2 ]; then
  echo "Uso: $0 <archivo_objetivo> <archivo_traduccion> [archivo_salida]" >&2
  exit 1
fi

archivo_objetivo="$1"
archivo_traduccion="$2"

if [ -n "${3:-}" ]; then
  archivo_salida="$3"
else
  base="${archivo_objetivo%.*}"
  ext="${archivo_objetivo##*.}"
  if [ "$base" = "$ext" ]; then
    archivo_salida="${archivo_objetivo}_traducido"
  else
    archivo_salida="${base}_traducido.${ext}"
  fi
fi

# Copiamos el original al archivo de salida para no tocar el original
cp "$archivo_objetivo" "$archivo_salida"

mapfile -t lineas_es < <(grep -v '^[[:space:]]*$' "$archivo_traduccion")
mapfile -t num_lineas < <(grep -nE '^\[[0-9]{2}:[0-9]{2}\.[0-9]{2}\]' "$archivo_salida" | grep -v '©' | cut -d: -f1)

expresiones=()
i=0

while [ "$i" -lt "${#num_lineas[@]}" ] && [ "$i" -lt "${#lineas_es[@]}" ]; do
  linea_actual="${num_lineas[$i]}"
  texto_escapado=$(printf '%s' "${lineas_es[$i]}" | sed -e 's/[&\\/]/\\&/g')
  expresiones+=(-e "${linea_actual}s/^(\[[0-9]{2}:[0-9]{2}\.[0-9]{2}\]).*/\1${texto_escapado}/")
  i=$((i + 1))
done

if [ "${#expresiones[@]}" -gt 0 ]; then
  sed -i -E "${expresiones[@]}" "$archivo_salida"
fi

echo "Listo. Resultado guardado en: $archivo_salida"
