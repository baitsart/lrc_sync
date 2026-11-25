#!/bin/bash

archivo="$1"

link=$(grep "#Link a youtube:" "$archivo" | cut -d'=' -f2 | tr -d '\r')
letra=$(grep -i -A 999999 "Link a youtube:" "$archivo" | tail -n +2)
titulo=$(grep "Título de la canción" "$archivo" | cut -d']' -f2 | cut -d':' -f2 | sed 's/^ //; s/\r$//')

TITULO="$titulo" LINK="$link" LETRA="$letra" awk '
{
  gsub(/TITULO/, ENVIRON["TITULO"])
  gsub(/LINK/, ENVIRON["LINK"])
  idx = index($0, "LA_LETRA_AQUÍ")
  if (idx > 0) {
    before = substr($0, 1, idx - 1)
    after = substr($0, idx + length("LA_LETRA_AQUÍ"))
    print before ENVIRON["LETRA"] after
  } else {
    print
  }
}' "/home/pc/videos_con_letras/videos_con_letras.html" > "$HOME/$titulo.lrc"

