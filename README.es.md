# ReviewPulse

[English](README.md) · [Русский](README.ru.md) · [Español](README.es.md) · [Italiano](README.it.md) · [中文](README.zh.md)

Un bot de Telegram que evita que las revisiones de código se estanquen: sigue el
estado de cada revisor asignado y le escribe por privado a quien tenga el turno —
estrictamente en horario laboral.

Detecta dos situaciones:

1. Un revisor nunca reaccionó, y el MR simplemente queda ahí parado.
2. Un revisor pidió cambios, el autor corrigió todo y cerró los hilos, pero el revisor
   nunca volvió a aprobar. El turno es suyo, pero nadie se lo recuerda.

Si el revisor vuelve a pedir cambios **después** de que las correcciones llegaron, el
turno vuelve al autor y los recordatorios se detienen — el bot nunca insiste con
alguien que ya hizo su parte.

---

## Inicio rápido

Imagen lista para usar: [`s1k0de/reviewpulse`](https://hub.docker.com/r/s1k0de/reviewpulse)
(linux/amd64 + linux/arm64). No hay nada que compilar.

**1. Crea un bot** con [@BotFather](https://t.me/BotFather) y copia el token. Ya que
estás ahí: `/setprivacy` → **Disable** — de lo contrario el bot nunca ve la
publicación que Telegram copia automáticamente al grupo de discusión.

**2. Agrega el bot** como administrador del canal de revisión — con **Publicar
mensajes** (para `/announce`) y **Eliminar mensajes** (para que la publicación de una
revisión cerrada desaparezca sola del canal) — **y** como miembro de su grupo de
discusión vinculado (los comentarios deben estar habilitados).

**3. Ejecútalo** — elige lo que mejor se ajuste a tu forma de desplegar:

<table>
<tr><th>docker run</th><th>docker compose</th></tr>
<tr valign="top"><td>

```bash
docker run -d --name reviewpulse \
  --restart unless-stopped \
  -e BOT_TOKEN='TU_TOKEN' \
  -e TIMEZONE_OFFSET_HOURS=3 \
  -e WORK_START=09:00 -e WORK_END=18:00 \
  -v reviewpulse-data:/app/data \
  s1k0de/reviewpulse:latest
```

</td><td>

```bash
curl -O https://raw.githubusercontent.com/\
esmaksimov/ReviewPulse/main/docker-compose.yml
curl -o .env https://raw.githubusercontent.com/\
esmaksimov/ReviewPulse/main/.env.example
# completa BOT_TOKEN en .env
docker compose up -d
```

</td></tr>
</table>

Vale la pena quedarse con compose — es lo que editarás cuando vuelvas a cambiar algún
ajuste, y `--profile postgres` está a un flag de distancia si alguna vez lo necesitas
(ver [Qué base de datos usar](#qué-base-de-datos-usar) más abajo).

Todo lo demás tiene un valor por defecto razonable: SLA de 2 horas, un recordatorio
cada 20 minutos, máximo 8 avisos al día, una revisión se cierra cuando aprueba cada
revisor nombrado. Lista completa en [`.env.example`](.env.example).

**4. Cada revisor le escribe `/start` al bot una vez.** Telegram prohíbe que un bot
escriba primero — sin esto, los recordatorios simplemente no pueden llegar. Si un
revisor asignado todavía no lo hizo, el bot lo dice una vez en el hilo de comentarios.

### Qué base de datos usar

**Usa SQLite** — es la opción por defecto y es suficiente. El bot es de un solo
proceso y escribe unas pocas decenas de filas al día; toda la base de datos es un
archivo en un volumen, y respaldarla es un `cp`.

Postgres vale la pena solo si ya tienes uno corriendo, o quieres respaldos listos:

```bash
docker compose --profile postgres up -d
```
y en `.env`:
```dotenv
DATABASE_URL=postgresql+asyncpg://reviewpulse:reviewpulse@db:5432/reviewpulse
```

Mismo esquema, mismas migraciones, ambos motores probados de punta a punta. Las
migraciones se aplican solas al iniciar.

---

## Cómo se ve

El bot lee las publicaciones por su forma, no por una plantilla rígida — los dos
ejemplos de abajo funcionan igual. Para que una publicación cuente como revisión hace
falta una de dos cosas: **al menos un enlace a un MR**, o **una línea de revisores
etiquetada explícitamente** (así un cambio solo de documentación o de infraestructura,
sin MR, también se rastrea, siempre que los revisores se hayan nombrado a propósito).
Sin ninguna de las dos, la publicación es un anuncio y no se rastrea.

> Las etiquetas ("Revisión:", "MR:", "Documentación:", ...) se reconocen en cualquier
> idioma que hable el propio bot — un equipo que escribe en ruso o chino obtiene el
> mismo análisis que uno que escribe en español. Ver [Idiomas](#idiomas).

Una publicación en el canal:

```
Pagos

Mejora del connection pool

MR API: https://gitlab.example.com/backend/services/api_controller/-/merge_requests/1112

MR Utils: https://gitlab.example.com/backend/packages/utils/-/merge_requests/223

Tarea: https://tasks.example.com/space/2829/boards/card/3517380

Revisión: @user1 @user2
```

Una plantilla más estricta también se analiza sin problema:

```
Catálogo
Arreglar la redirección de pago

MR: https://gitlab.example.com/backend/services/checkout/-/merge_requests/77

Documentación: https://wiki.example.com/pages/12345
Descripción: si falta la documentación
Revisor: @user2 para backend / @user1 para el resto, @user3
```

Qué extrae el bot de la publicación:

| Campo | Origen |
|---|---|
| producto | primera línea no vacía |
| título de la tarea | siguiente línea que no sea una etiqueta ni un enlace suelto |
| MRs | **todos** los enlaces con forma `…/-/merge_requests/<N>`, sean los que sean |
| revisores | cada `@usuario` en la línea "Revisión…"; si no existe, cada `@usuario` de la publicación |

Los enlaces al gestor de tareas o a la wiki nunca se confunden con un MR. El texto
mezclado en la línea de revisores no oculta los usuarios. Si no se pudo identificar a
ningún revisor, el bot no se queda callado: la tarjeta trae un botón "🙋 Soy revisor".

### Generar la publicación por ti

En vez de escribir todo a mano, deja que lo redacte el bot: rellena el nombre del
producto y elige a los revisores por su cuenta, y luego publica el resultado en el
canal.

Pulsa **📢 Anuncio** en el menú del bot (o envía un simple `/announce`) y te va a
preguntar una cosa por mensaje — título, luego enlaces a MR/PR, documentación, tarea —
con un botón **⏭ Omitir** en cada paso que sea opcional. Que cada respuesta llegue en
su propio mensaje es también lo que hace que funcionen los enlaces pegados como
hipervínculo: `Documentación: Confluence`, donde *Confluence* es un enlace, no lleva
ninguna URL en el texto del mensaje — y leer solo el texto visible es justo lo que
hacía que se publicara un post con la línea de documentación en blanco, sin que nadie
se diera cuenta.

De lo que omitas se derivan dos caminos:

- **Sin ningún MR/PR** — un arreglo solo de SQL, o un cambio de documentación — y el
  bot pregunta a qué producto pertenece, porque no hay repositorio del que deducirlo.
  La publicación se sigue rastreando igual: basta con una línea de revisores puesta a
  propósito, no hace falta ningún merge request.
- **Sin enlace a documentación** y el bot ofrece en su lugar una **Descripción** en
  texto libre, que sale como la línea `Descripción:` de la plantilla.

Si ya tienes el texto listo en el portapapeles, la forma de un solo mensaje sigue
funcionando:

```
/announce Mejora del connection pool
https://gitlab.example.com/example/demo-project/-/merge_requests/1112
Documentación: https://wiki.example.com/pages/1
```

En cualquiera de los dos casos el bot responde con una vista previa y tres botones —
**Publicar**, **🔁 Otro revisor**, **Cancelar** — para que puedas volver a sortear al
revisor antes de publicar (si el que salió está de vacaciones, por ejemplo) o cancelar
del todo. Una vez publicada, la publicación sigue exactamente el mismo camino de
análisis y seguimiento que una escrita a mano — no recibe ningún trato especial.

La selección de revisores se configura por entorno, una entrada por proyecto de
GitLab, con la misma clave `project_path` que ya lleva un enlace a un MR:

```dotenv
REVIEW_PROJECTS={"example/demo-project":{"product":"Demo Product","techlead":"user1","pool":["user2","user3","user4"]}}
```

- `product` — se muestra en la publicación generada.
- `techlead` *(opcional)* — se incluye siempre, salvo que sea quien está ejecutando
  `/announce`.
- `pool` — candidatos para el resto de plazas, elegidos al azar, excluyendo a quien
  redacta.
- `reviewer_count` *(opcional, por defecto 2)* — total de revisores en la publicación,
  el techlead incluido.

La línea de autor se resuelve gratis aquí — a diferencia de una publicación escrita a
mano, la identidad de quien redacta ya se conoce por el mensaje privado, sin
necesidad de ninguna etiqueta opcional.

Nombrar varios enlaces a MR trae varios repositorios de una vez — está bien siempre
que todos estén configurados de forma idéntica en `REVIEW_PROJECTS`. Si dos
repositorios nombrados no coinciden (producto, techlead o pool distintos), el
borrador se rechaza de entrada con los nombres de los proyectos en conflicto, en vez
de elegir uno en silencio.

La tarjeta que aparece en el hilo de comentarios bajo la publicación:

```
🤖 Pagos — Mejora del connection pool

   Aprobaciones: 1/2

   • @user1 — 👍 aprobado
   • @user2 — 🔁 cambios listos, esperando una nueva revisión

   api_controller!1112
   utils!223

   [👍 Aprobar]              [✍️ Solicitar cambios]
   [✅ Corregido]            [🗄 Cerrar]
```

Y a quien tenga el turno le llega un mensaje privado:

```
🔁 Los cambios están listos, pero ✍️ sigue puesto

El autor corrigió todo lo que pediste, pero aún no diste tu aprobación.

Pagos — Mejora del connection pool
Retraso: 1 h 20 min de tiempo laboral

https://gitlab.example.com/backend/services/api_controller/-/merge_requests/1112

Abrir discusión

   [🔕 1 h]  [🔕 Mañana]
```

---

## Por qué botones, y no reacciones

**Las reacciones en un canal de Telegram son anónimas.** Un bot administrador solo
recibe `message_reaction_count` (el agregado "👍 2"), nunca `message_reaction` con un
campo `user` — las reacciones por usuario solo existen en grupos y supergrupos. No hay
forma de que un bot, ni un userbot vía MTProto, sepa *quién específicamente* reaccionó
a una publicación de un canal; Telegram simplemente no entrega ese dato.
Ver [Bot API](https://core.telegram.org/bots/api#update) y
[api/reactions](https://core.telegram.org/api/reactions).

Por eso el bot publica **su propia tarjeta con botones en línea** en el hilo de
comentarios. Un `callback_query` siempre trae `from.id`, así que el estado de cada
revisor es 100% confiable. El canal en sí y el formato de las publicaciones no
cambian.

Cómo funciona:

```
publicación en el canal
     │
     │  Telegram la copia automáticamente al grupo de discusión vinculado
     ▼
copia en el grupo  ──►  el bot le responde con  ──►  la tarjeta queda dentro
                        una tarjeta + botones         del hilo de comentarios
                            │
                            │  pulsar un botón = callback_query,
                            ▼  que siempre trae from.id
                     estado confiable por revisor
```

Las dos actualizaciones — la publicación del canal y su copia en el grupo — llegan en
un orden impredecible, así que ambos caminos hacen upsert contra la misma clave, y la
que llega segunda es la que efectivamente publica la tarjeta.

---

## Modelo de estados

El estado vive en el par **(revisión × revisor)**, no en la revisión — un revisor
puede haber aprobado ya mientras otro todavía tiene el turno.

| Estado | Significado | Turno de | ¿Se avisa? |
|---|---|---|---|
| `PENDING` | sin veredicto todavía | revisor | sí, tras el SLA |
| `CHANGES_REQUESTED` | ✍️, correcciones pendientes | autor | no |
| `AWAITING_RECHECK` | correcciones listas, ✍️ sigue puesto | revisor | sí, tras el SLA |
| `APPROVED` | 👍 | — | no |

```
PENDING           --[👍]-------------------> APPROVED
PENDING           --[✍️]-------------------> CHANGES_REQUESTED
CHANGES_REQUESTED --[correcciones marcadas]-> AWAITING_RECHECK
AWAITING_RECHECK  --[👍]-------------------> APPROVED
AWAITING_RECHECK  --[✍️]-------------------> CHANGES_REQUESTED   ← "pidió más"
APPROVED          --[✍️]-------------------> CHANGES_REQUESTED   ← deshacer un clic accidental
```

El penúltimo salto es "el revisor miró las correcciones y pidió más": el turno vuelve
al autor, los avisos se detienen, el reloj del SLA se reinicia.

Implementación — [`domain/state.py`](src/reviewpulse/domain/state.py), un módulo puro
sin E/S.

### Cuántas aprobaciones necesita una revisión

`REQUIRED_APPROVALS` (2 por defecto) es un **techo**, no un objetivo fijo. El número
real necesario se ajusta a cuántos revisores fueron nombrados de verdad:

- si se nombra a un revisor → su aprobación sola cierra la revisión — nada queda
  esperando un segundo veredicto que nunca iba a llegar;
- si se nombra a dos → ambos deben aprobar;
- si se nombra a más que el techo → de todas formas solo hacen falta
  `REQUIRED_APPROVALS`, una lista larga de revisores no se convierte en un requisito
  de aprobación unánime.

Implementación — [`services/reviews.py:approvals_needed`](src/reviewpulse/services/reviews.py).

---

## Horario laboral

Tanto el SLA (2h) como el intervalo de repetición (20min) avanzan **solo dentro del
horario laboral** — 09:00–18:00 UTC+3, de lunes a viernes por defecto. Una publicación
a las 17:30 del viernes tiene su plazo a las 10:30 del lunes. Las noches y los fines de
semana no cuentan.

Aritmética — [`domain/workhours.py`](src/reviewpulse/domain/workhours.py). Los días
festivos todavía no se tienen en cuenta; `is_working_day` es el punto de extensión.

**Antispam:** como máximo `MAX_NUDGES_PER_DAY` (8) recordatorios al día por par,
botones "🔕 1h" / "🔕 Mañana" en cada mensaje privado, y el comando `/mute 2h`. Un
usuario que bloqueó al bot queda excluido de la consulta directamente, en vez de
reintentarse cada minuto.

---

## Cómo sabe el bot que las correcciones están listas

**Modo A (por defecto).** El autor pulsa "✅ Corregido" en la tarjeta — todos los
revisores que aún tienen ✍️ pasan a `AWAITING_RECHECK`.

**Modo B (`GITLAB_ENABLED=true` + un token).** El bot consulta GitLab por su cuenta y
lee los hilos:

- todos los hilos resolubles que abrió el revisor están resueltos, en **todos** los
  MRs de la revisión → "las correcciones están listas";
- aparece un hilo nuevo sin resolver de su parte → "pidió más", los avisos se
  detienen.

Esa segunda regla es una forma independiente de los botones de detectar que el
revisor volvió: aunque solo haya dejado un comentario en GitLab y nunca haya tocado
la tarjeta, el bot se queda en silencio.

Un revisor vincula su usuario de GitLab con `/link <usuario>`. Sin ese vínculo, se usa
en su lugar la bandera `blocking_discussions_resolved` de todo el MR — más gruesa,
pero mejor que nada. La sincronización nunca toca a los revisores que ya aprobaron,
para que un hilo reabierto por otra persona no revoque un 👍 en silencio.

---

## Estadísticas del equipo

Un resumen periódico por mensaje privado con dos números, desglosados por persona:
cuánto tardó un autor en atender un "se piden cambios" desde que llegó, y cuánto
tardó un revisor en dar su primer veredicto — la mediana, no la media, para que una
respuesta atípica no distorsione un número basado en tan pocas muestras. Las medianas
más lentas aparecen primero.

Ambos se miden en horario laboral, el mismo calendario que
[Horario laboral](#horario-laboral) más abajo — "se piden cambios" el viernes a las
17:30 y respondido el lunes a las 09:30 es una hora, no las ~64 horas que incluyen
el fin de semana.

No se envía nada a menos que haya al menos un destinatario configurado:

```dotenv
STATS_REPORT_RECIPIENT_IDS=123456789,987654321
STATS_REPORT_INTERVAL_DAYS=7
```

El mismo resumen está disponible bajo demanda con `/stats` — restringido a la misma
lista de destinatarios, ya que son datos de tiempos por persona.

Solo cuenta a partir del momento en que empezó a registrarse este historial — no hay
forma de rellenar hacia atrás revisiones que se cerraron antes de que existiera.

---

## Idiomas

Soportados: ruso, inglés, español, italiano, chino (`ru`, `en`, `es`, `it`, `zh`).

Dos cosas distintas necesitan un idioma, y no comparten uno:

- **La tarjeta compartida y el aviso de "escríbeme /start"** viven en el hilo de
  comentarios — todos los que lo ven, ven el mismo mensaje, así que tienen un único
  idioma: `DEFAULT_LOCALE` (por defecto `en`).
- **Los mensajes privados** — recordatorios, `/start`, `/status`, confirmaciones de
  botones — siguen el idioma propio de cada revisor: lo que haya elegido con `/lang`,
  si no el idioma del cliente de Telegram, si no `DEFAULT_LOCALE`.

```bash
/lang es   # cambia tus propios mensajes privados a español
/lang      # lista los idiomas disponibles
```

Las tablas de traducción viven en
[`telegram/texts.py`](src/reviewpulse/telegram/texts.py); la resolución del idioma en
[`i18n.py`](src/reviewpulse/i18n.py). Una prueba verifica que los cinco idiomas tengan
exactamente el mismo conjunto de claves, así que un texto agregado a un idioma y
olvidado en otro rompe el CI en vez de caer en silencio al inglés en producción.

---

## Comandos del bot

| Comando | Qué hace |
|---|---|
| `/start` | te registra; vincula tu @usuario a tu id y busca revisiones pendientes en ti |
| `/status` | qué tienes pendiente ahora mismo, con plazos y un enlace a cada publicación |
| `/announce` | redacta la publicación del canal por ti — ver [Generar la publicación por ti](#generar-la-publicación-por-ti) |
| `/link <usuario>` | vincula tu cuenta de GitLab (para el Modo B) |
| `/lang <código>` | cambia el idioma del bot para tus propios mensajes privados |
| `/mute 2h`, `/unmute` | silenciar / volver a avisar |
| `/stats` | el resumen de estadísticas del equipo, bajo demanda — solo destinatarios configurados, ver [Estadísticas del equipo](#estadísticas-del-equipo) |

---

## Desarrollo

```bash
poetry env use 3.12
poetry install
cp .env.example .env                # completa BOT_TOKEN
poetry run python -m reviewpulse    # las migraciones se aplican solas al iniciar

poetry run pytest                   # 240 pruebas
poetry run ruff check src tests
```

Cubre lo importante: la aritmética del horario laboral (viernes 17:30 → lunes 10:30),
cada arista de la máquina de estados, incluyendo "pidió más", la regla dinámica de
aprobaciones necesarias, el analizador de publicaciones contra una publicación real y
contra la plantilla estricta, el análisis de hilos de GitLab contra fixtures, la
completitud de las tablas de traducción en los cinco idiomas, y un ciclo completo sobre
una base de datos SQLite real que sobrevive a un reinicio.

Compila y publica tu propia imagen:

```bash
docker buildx build --platform linux/amd64,linux/arm64 \
  -t TU_CUENTA/reviewpulse:latest --push .
```

**Prueba manual de extremo a extremo.** Crea un canal de prueba con un grupo de
discusión vinculado, haz al bot administrador de ambos, y acelera el tiempo en `.env`:

```dotenv
SLA_MINUTES=1
RECHECK_SLA_MINUTES=1
NUDGE_INTERVAL_MINUTES=1
WORK_START=00:00
WORK_END=23:59
WORK_DAYS=0,1,2,3,4,5,6
```

Así el ciclo completo — aviso → ✍️ → "Corregido" → aviso de revisión → ✍️ otra vez →
silencio → 👍×2 → cierre — se completa en unos minutos.

---

## Estructura

```
src/reviewpulse/
  config.py              configuración desde el entorno
  i18n.py                 lista de idiomas y su resolución (privado vs. mensaje compartido)
  domain/                 lógica pura: máquina de estados, horario laboral, reglas de escalado
  parsing/                 análisis de publicaciones y extracción de enlaces a MR
  gitlab/                  cliente REST y análisis de hilos
  db/                      modelos, sesión, consultas
  services/                pegamento entre dominio y BD: revisiones, avisos, sincronización con GitLab, anuncios, estadísticas
  telegram/                 bot, handlers, tarjeta, renderizado de anuncios y estadísticas, textos traducidos
  scheduler/                el tick de avisos y el tick de sincronización
migrations/                Alembic
```

---

## Limitaciones conocidas

- **No se verifica quién pulsó "✅ Corregido".** Una publicación de canal es anónima
  — Telegram no reporta un autor — así que el botón está disponible para cualquiera
  en el hilo.
- **El bot no puede ver las reacciones en la publicación misma** (ver arriba); la
  tarjeta es la fuente de verdad.
- **Cerrar una revisión borra su publicación del canal** (quórum alcanzado, o el botón
  "🗄 Cerrar") — el hilo de discusión, con la tarjeta y todos los comentarios, queda
  intacto; solo se limpia el listado del canal. Requiere que el bot tenga el permiso
  de administrador **Eliminar mensajes**; sin él, el cierre ocurre igual, pero la
  publicación se queda.
- **Los días festivos no se tienen en cuenta** — el bot tratará un feriado nacional
  como un día laboral normal.
- **El análisis de publicaciones reconoce un conjunto fijo de palabras clave** por
  campo (ver [Cómo se ve](#cómo-se-ve)) — una etiqueta fuera de esa lista, en
  cualquier idioma, cae en la heurística posicional en vez de leerse directamente.
- **`/announce` exige que todos los proyectos referenciados estén configurados de
  forma idéntica** — un borrador que nombra MRs de varios repositorios está bien
  siempre que sus entradas en `REVIEW_PROJECTS` coincidan exactamente
  (producto/techlead/pool/reviewer_count); si no coinciden, el borrador se rechaza
  con los nombres de los proyectos en conflicto en vez de elegir uno.
- **Un `/announce` a medio terminar no sobrevive a un reinicio** — el asistente
  paso a paso guarda las respuestas en memoria, así que un redeploy a mitad de la
  conversación significa empezar de nuevo. El borrador ya terminado es una fila en
  la BD y no se ve afectado.
- **Las estadísticas solo cubren transiciones registradas desde que se lanzó la
  función** — no hay forma de rellenar hacia atrás revisiones que se cerraron antes
  de que existiera esa tabla de historial.
