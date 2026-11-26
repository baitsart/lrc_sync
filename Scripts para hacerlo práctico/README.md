Scripts para hacerlo práctico y convertir archivos:

El script vtt_a_srt.py, en python, pasa de .vtt o .lrc, a .srt.

El script srt2lrc, en bash, pasa de .srt. a .vtt o .lrc.

El script reemplazar.sh, es para usar el texto traducido, y remplazar linea por linea, automáticamente, uso:
`reemplazar.sh archivo_en_idioma_original.vtt archivo_traducido.txt`

Y, organiza_code.sh se le indica el archivo de subtitulado, que carga el videos_con_letras.html, para generar el código html de la publicación, listo. Con el título del video, el link, y la letra.
`organiza_code.sh archivo_final.vtt` (él genera en el Home, el archivo para publicar)
