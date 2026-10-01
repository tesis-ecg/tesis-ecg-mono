# Evaluación contra registros reales de PhysioNet

Los datos **no se commitean**: `data/` está en el `.gitignore` de la raíz. Hay que
bajarlos antes de correr la evaluación.

## Descarga

```bash
cd back
uv run --with wfdb python -m tools.physionet.evaluate --download            # Etapa 2
uv run --with wfdb python -m tools.physionet.evaluate --download --stage1   # Etapa 1
```

Se baja a `back/tools/physionet/data/`, archivo por archivo: PhysioNet corta
descargas largas con un 502 cada tanto, y el script reintenta solo lo que falta.

| Base | Qué es | Para qué se usa acá |
|---|---|---|
| `mitdb` | MIT-BIH Arrhythmia Database, 48 registros de 30 min a 360 Hz, con **cada latido anotado** por dos cardiólogos | Etapa 2: ¿los latidos que el motor marca como atípicos son los que están anotados como no-normales? |
| `nstdb` | MIT-BIH Noise Stress Test Database: el registro 118 con ruido **real** de electrodo sumado a SNR conocida (24 a −6 dB) | Etapa 1: ¿el gate rechaza el ruido y **no** rechaza la señal limpia? |

El ruido de `nstdb` no cubre el registro entero: los primeros 5 min quedan
limpios y después se alternan bloques de 2 min con ruido y 2 min sin, o sea un
**43,3 % contaminado**. Por eso `--stage1` no reporta solo cuánto rechaza el gate
sino *dónde*: un gate que descarte el 42 % al azar da el mismo porcentaje y no
sirve para nada.

## Licencia y cita

Ambas bases se distribuyen en PhysioNet bajo **Open Data Commons Attribution
License v1.0 (ODC-By 1.0)**. Verificar la licencia en la página del registro al
momento de bajar: PhysioNet la puede cambiar por versión.

> Moody GB, Mark RG. The impact of the MIT-BIH Arrhythmia Database.
> *IEEE Eng in Med and Biol* 20(3):45-50 (2001).
>
> Moody GB, Muldrow WE, Mark RG. A noise stress test for arrhythmia detectors.
> *Computers in Cardiology* 11:381-384 (1984).
>
> Goldberger AL, Amaral LAN, Glass L, et al. PhysioBank, PhysioToolkit, and
> PhysioNet. *Circulation* 101(23):e215-e220 (2000).

## Qué se puede y qué no se puede concluir

**Se puede** calibrar umbrales y comparar con un baseline. El detector es **no
supervisado**: nunca se entrena con estas etiquetas, solo se las usa para medir
después. Por eso reportar sobre el mismo dataset es válido acá y no lo sería en
un clasificador entrenado.

**No se puede** afirmar que estos números se trasladan a nuestro hardware. Los
registros de PhysioNet vienen de **electrodos de gel colocados por un técnico**;
nuestro dispositivo usa **electrodos secos en un chaleco textil que se coloca el
paciente**. La relación señal-ruido y los modos de falla son distintos. Estos
datasets alcanzan para calibrar, no para validar.

Dos consecuencias concretas que el script ya aplica:

- **Los umbrales de flatline y saturación se apagan.** Son propiedades del AFE
  DC-acoplado del chaleco; los registros públicos vienen AC-acoplados y ya
  centrados, y dejarlos prendidos mediría un artefacto del formato.
- **El bSQI no se aplica.** Necesita los R-peaks del firmware, que estos
  registros obviamente no traen. El motor lo detecta solo
  (`firmware_peaks_available` en falso) y no degrada el registro por una ausencia
  que no dice nada sobre la señal.
