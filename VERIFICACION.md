# Verificación de las mejoras

## Inventario de hardware · 23 de noviembre de 2026

29 pruebas automatizadas aprobadas, incluidas 7 del inventario: distinción de los
códigos SMBIOS/CIM, varias gráficas y pantallas, datos incompletos, estructuras
Win32, salida JSON, timeout y actualización de la pestaña sin repetir consultas.

Se verificó una consulta real de solo lectura fuera del entorno restringido:
procesador, dos módulos DDR4, dos gráficas, una pantalla activa de 1920 × 1080
a 144 Hz, placa base, BIOS, disco y audio. No se ejecutaron acciones de mantenimiento.
Los resultados concretos pueden variar según el firmware, los controladores y
los permisos del equipo donde se ejecute.

Fecha: 15 de noviembre de 2026.

## Pruebas

22 pruebas automatizadas aprobadas, además de la compilación de sintaxis. Las
operaciones de energía y mantenimiento se simularon. La eliminación se probó
únicamente con archivos creados para las pruebas en carpetas temporales aisladas.

## Medición breve

Comando: `python benchmark_optynan.py <archivo>`.

Se usó la misma máquina y una ventana oculta, durante unos seis segundos de reposo.
La versión anterior se tomó del respaldo previo a los cambios. Cada ejecución
utilizó un proceso nuevo. Son observaciones de ejecuciones individuales, no un
estudio estadístico ni una garantía de rendimiento.

| Medida | Anterior | Actual |
|---|---:|---:|
| Arranque medido | 144,6 ms | 96,6 ms |
| Memoria residente al final | 44,38 MB | 43,83 MB |
| Tiempo de CPU durante reposo | 0,0000 s | 0,0469 s |
| Consultas al disco durante reposo | 3 | 1 |
| Llamadas para dibujar la gráfica oculta | 3 | 0 |

El arranque y la memoria varían entre ejecuciones. La granularidad de los tiempos
de CPU y la duración breve no permiten afirmar una mejora de CPU. La reducción de
consultas al disco y la eliminación de dibujos ocultos corresponden a cambios
directos del código; también están comprobadas mediante pruebas automatizadas.

No se midió una aceleración de Windows ni se ejecutaron SFC, DISM, optimización
de disco, restablecimiento de red o limpieza del sistema real.
