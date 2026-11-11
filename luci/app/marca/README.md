# Luci · identidad

El farol de una luciérnaga con una pequeña cabeza café (`#A9784F`) en el eje del vuelo. El
farol va en diagonal, centrado según su masa visual, con un halo sobre fondo espresso. Comparte
con Mantis las formas planas (cuadrado de radio 14 sobre 64).

El cuadrado sigue la grilla de íconos de macOS: ocupa 824 de los 1024 px, centrado, con un
margen transparente. A todo el lienzo se vería más grande que los demás íconos del Dock.

## Paleta (app y web)

| Rol | Color |
|---|---|
| Principal: latón (farol, acciones, «te escucha») | `#D6CC6D` |
| Fondo oscuro: espresso | `#231914` |
| Superficie: café | `#3A2A20` |
| Bordes y texto secundario: tostado | `#6B4A35` |
| Acento: caramelo (decorativo) | `#A9784F` |
| Fondo claro / texto sobre oscuro: crema | `#F4EEDC` |
| Brillo del farol | `#F6EFAA`, `#FBF7D8` |

El latón sobre crema no se lee: en modo claro va como relleno con texto espresso encima.

- `luci.svg`: fuente del ícono de la app.
- `luci-1024.png`: rasterización de `luci.svg`; de ella sale `../Luci.icns`.
- `menu.svg` y `menu-luz.svg`: símbolos monocromos para la barra de menú (farol en contorno en
  espera y relleno mientras escucha, piensa o habla). Se rasterizan a `../menu.png` y
  `../menu-luz.png` (36 px, plantilla de 18 pt).

Para regenerar el `.icns` desde el PNG de 1024 px:

```bash
mkdir Luci.iconset
for s in 16 32 128 256 512; do
  sips -z $s $s luci-1024.png --out Luci.iconset/icon_${s}x${s}.png
  sips -z $((s*2)) $((s*2)) luci-1024.png --out Luci.iconset/icon_${s}x${s}@2x.png
done
iconutil -c icns Luci.iconset -o ../Luci.icns && rm -r Luci.iconset
```
