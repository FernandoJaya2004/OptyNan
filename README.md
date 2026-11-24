# OptyNan

Panel de mantenimiento para Windows, con Python 3.10 o posterior y Tkinter.

## Inicio

### Ejecutable portable (sin instalar Python)

Extrae `OptyNan-Windows-x64.zip` completo y abre `OptyNan/OptyNan.exe`.
Conserva `_internal` junto al ejecutable. Para tareas administrativas, cierra la
aplicación y usa clic derecho sobre el EXE → **Ejecutar como administrador**.

### Ejecutar desde el código

```powershell
python -m pip install -r requirements.txt
python OptyNan.py
```

Conserva `OptyNan.py`, `optynan_core.py` y `optynan_hardware.py` en la misma carpeta. La aplicación abre
sin elevar permisos. Para SFC, DISM, optimización del disco y restablecimiento de
red, ejecútala desde una terminal abierta como administrador.

## Limpieza con revisión de archivos

1. Pulsa **Analizar espacio recuperable**, o el botón de una categoría.
2. Selecciona una categoría y pulsa **Seleccionar categoría**, o haz doble clic.
3. Revisa sus archivos en la tabla inferior. Selecciona uno o varios con Ctrl/Shift
   y usa **Excluir / incluir archivos**. También puedes hacer doble clic.
4. Pulsa **Limpiar selección** y confirma.

Todos los botones de limpieza de archivos utilizan este mismo flujo. El análisis
no borra nada y se puede detener con **Cancelar análisis**. Las reparaciones del
sistema no usan ese botón de cancelación.

Los archivos se muestran en páginas de 150 entradas. Los temporales de las
últimas 24 horas se conservan. Solo se intentan borrar los archivos incluidos en
la lista revisada: los nuevos no se añaden automáticamente y los modificados o
reemplazados después del análisis se omiten. También se omiten enlaces, junctions,
archivos bloqueados y rutas fuera de la carpeta analizada. Los directorios se
conservan. Los metadatos de candidatos se guardan en memoria; analizar carpetas
con muchos archivos requiere más memoria durante la revisión.

La limpieza de navegadores descubre `Default` y `Profile N` de Chrome y Edge en
sus ubicaciones habituales. Cada perfil aparece por separado; no cubre perfiles
con rutas personalizadas. Las cachés de Chrome, Edge o Steam se omiten si se detecta
la aplicación abierta al comenzar esa categoría. No elimina contraseñas ni historial.
Las cachés vuelven a generarse; eliminarlas no garantiza acelerar el equipo.

La papelera y Windows Update mantienen sus herramientas separadas. El tamaño
mostrado corresponde a bytes lógicos, no a espacio físico necesariamente liberado.

## Energía y recuperación

Antes de activar alto rendimiento se guarda el identificador del plan anterior
en un archivo escrito de forma atómica. Al desactivar se restaura ese plan y se
elimina el estado pendiente solo si Windows confirma la operación.

Tras un cierre inesperado, la siguiente apertura detecta el estado guardado y
ofrece restaurarlo. Si pospones la recuperación, puedes usar **Activar / restaurar
alto rendimiento**; también se intenta restaurar al cerrar normalmente. Si falla,
el estado se conserva para volver a intentarlo. OptyNan no detiene Windows Update
ni Windows Search. No todos los equipos ofrecen el plan de alto rendimiento.

Solo se permite una instancia por carpeta de datos, evitando conflictos en el
estado de energía y en los registros.

## Mi equipo: componentes de la computadora

La pestaña **Mi equipo** consulta los componentes al abrirla por primera vez.
**Actualizar componentes** permite repetir la consulta después de conectar una
pantalla o cambiar hardware. Se ejecuta en segundo plano y conserva el resultado
en memoria, sin volver a consultar en cada muestra del monitoreo.

- Equipo y Windows: fabricante, modelo, versión, compilación y arquitectura.
- Procesadores: modelo, núcleos físicos, procesadores lógicos y frecuencia máxima informada.
- RAM: capacidad instalada y módulos, ubicación, DDR/LPDDR, fabricante, referencia,
  formato DIMM/SO-DIMM y velocidades nominal/configurada informadas por el firmware.
- Gráficas: todas las detectadas por Windows, modelo, procesador gráfico y controlador.
- Pantallas activas: resolución actual en píxeles físicos, frecuencia y pantalla principal.
- Almacenamiento: modelo, capacidad, SSD/HDD e interfaz, cuando están disponibles.
- Placa base, versión de BIOS y dispositivos de audio.

Los campos ausentes se muestran como **No disponible**. El tipo DDR se obtiene del
código SMBIOS, sin adivinarlo por velocidad. La RAM instalada y la disponible para
Windows pueden diferir por reservas de hardware. Las pantallas reflejan modos
activos de Windows, no la resolución máxima del monitor; una conexión remota o
pantallas duplicadas pueden aparecer como dispositivos lógicos.

No se utiliza `Win32_VideoController.AdapterRAM` para afirmar la VRAM dedicada:
su campo de 32 bits no permite representar con fiabilidad tarjetas de gran capacidad.
El inventario no consulta números de serie, claves de producto, direcciones MAC
ni IP, y no se adjunta automáticamente al registro ni se envía a Internet.

Fuentes técnicas: [memoria física de Windows](https://learn.microsoft.com/en-us/windows/win32/cimwin32prov/win32-physicalmemory),
[tipos de memoria SMBIOS](https://www.dmtf.org/sites/default/files/standards/documents/DSP0134_3.8.0.pdf)
y [modos de pantalla](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-enumdisplaysettingsw).

## Monitoreo y gráficas

- CPU y RAM: intervalo configurable de **1, 2, 5 o 10 segundos**, guardado entre sesiones.
- **Pausar monitoreo** detiene las muestras, sin detener tareas de mantenimiento.
- Disco: consulta cada **30 segundos**, con renovación en la siguiente muestra
  después de finalizar una tarea.
- Gráficas: últimos diez minutos, con hasta 601 muestras. Solo se dibujan cuando
  su pestaña está visible; las líneas se reutilizan y los ejes se reconstruyen
  únicamente al cambiar el tamaño. Las métricas no se guardan al salir.

## Registro e historial

**Exportar registro** guarda el contenido visible en un archivo `.txt` UTF-8.
**Historial** permite consultar los últimos 200 KB de los registros conservados,
incluidas sesiones anteriores. Las tareas, resultados, errores y cantidades
eliminadas se guardan automáticamente con fecha y hora.

Los datos se almacenan en `%LOCALAPPDATA%\OptyNan`:

- `activity.log` y hasta tres copias rotadas, de aproximadamente 1 MB cada una.
- `energy-plan.json`, mientras hay un plan pendiente de restauración.
- `settings.json`, con el intervalo de monitoreo.
- `session.lock`, para impedir instancias simultáneas con los mismos datos.

El historial más antiguo se descarta por rotación. No se carga entero en memoria.
Los comandos largos envían su salida al registro mientras se ejecutan, conforme
Windows la produce; algunos programas pueden acumular salida internamente.

## Diagnóstico de conexión

La herramienta diferencia tres comprobaciones sin cambiar configuraciones:

1. Adaptadores conectados, direcciones IPv4 y ping a la puerta de enlace.
2. Conexión TCP a `1.1.1.1:443` para comprobar acceso a un destino externo.
3. Resolución DNS de `example.com`.

Un destino inaccesible o un ping fallido puede deberse a filtrado y no demuestra
por sí solo una caída de Internet. El restablecimiento de red sigue siendo una
acción independiente, con confirmación, y requiere reiniciar Windows.
El estado de discos usa `Get-PhysicalDisk`; no es un diagnóstico SMART completo.

## Organización y verificación

### Generar el ejecutable en Windows x64

Desde PowerShell, situado en la carpeta del proyecto:

```powershell
.\build_windows.ps1
```

El script prepara un entorno local, instala las versiones de
`requirements-build.txt`, ejecuta las pruebas y construye el programa con
PyInstaller. Si las dependencias ya están instaladas, puedes usar
`.\build_windows.ps1 -SkipInstall`.

Resultados:

- `dist/OptyNan/OptyNan.exe`: aplicación portable, junto a `_internal`.
- `dist/OptyNan-Windows-x64.zip`: carpeta completa lista para compartir.
- `dist/SHA256SUMS.txt`: suma SHA-256 para verificar el ZIP.

Se utiliza el formato de carpeta de PyInstaller, adecuado para una aplicación
que puede ejecutarse con permisos elevados. El ejecutable no tiene firma digital.
Los respaldos, entornos virtuales y registros personales se excluyen de Git.

### Archivos del proyecto

- `OptyNan.py`: interfaz, coordinación de tareas y herramientas de Windows.
- `optynan_core.py`: análisis, eliminación de candidatos, persistencia y comandos
  con salida progresiva; se puede probar sin abrir la interfaz.
- `optynan_hardware.py`: consultas de componentes y pantallas, y formato del inventario.
- `test_optynan.py`: pruebas aisladas con datos temporales y comandos simulados.
- `benchmark_optynan.py`: medición breve de arranque y reposo, sin mantenimiento.

```powershell
python -m unittest -v
python -m py_compile OptyNan.py optynan_core.py
python benchmark_optynan.py OptyNan.py
```

Las pruebas cubren selección y exclusión desde la interfaz, cancelación, archivos
cambiados o nuevos, recuperación de energía, rotación de registros, perfiles,
monitoreo, salida progresiva y timeout. No ejecutan reparaciones ni limpieza real
del equipo. El diagnóstico de red no se ejecutó contra la conexión real durante
esta revisión.

El original se conserva en `OptyNan.original.py.bak`; la versión previa a estas
mejoras, en `OptyNan.before-improvements.py.bak`.
