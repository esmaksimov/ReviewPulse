# ReviewPulse

[English](README.md) · [Русский](README.ru.md) · [Español](README.es.md) · [Italiano](README.it.md) · [中文](README.zh.md)

Un bot Telegram che evita che le code review si arenino: tiene traccia dello stato per
ogni revisore assegnato e scrive in privato a chi ha la palla in mano — rigorosamente
durante l'orario di lavoro.

Copre due situazioni:

1. Un revisore non ha mai reagito, e la MR resta lì ferma.
2. Un revisore ha chiesto modifiche, l'autore ha sistemato tutto e chiuso i thread, ma
   il revisore non è mai tornato ad approvare. La palla è sua, ma nessuno gliela
   ricorda.

Se il revisore chiede di nuovo modifiche **dopo** che le correzioni sono arrivate, la
palla torna all'autore e i promemoria si fermano — il bot non insiste mai con chi ha
già fatto la sua parte.

---

## Avvio rapido

Immagine pronta: [`s1k0de/reviewpulse`](https://hub.docker.com/r/s1k0de/reviewpulse)
(linux/amd64 + linux/arm64). Niente da compilare.

**1. Crea un bot** con [@BotFather](https://t.me/BotFather) e copia il token. Già che
ci sei: `/setprivacy` → **Disable** — altrimenti il bot non vede mai il post che
Telegram copia automaticamente nel gruppo di discussione.

**2. Aggiungi il bot** come amministratore del canale di review **e** come membro del
suo gruppo di discussione collegato (i commenti devono essere abilitati).

**3. Avvialo** — scegli quello che si adatta meglio al tuo modo di fare deploy:

<table>
<tr><th>docker run</th><th>docker compose</th></tr>
<tr valign="top"><td>

```bash
docker run -d --name reviewpulse \
  --restart unless-stopped \
  -e BOT_TOKEN='IL_TUO_TOKEN' \
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
# compila BOT_TOKEN in .env
docker compose up -d
```

</td></tr>
</table>

Vale la pena tenersi compose — è quello che modificherai quando tornerai a cambiare
un'impostazione, e `--profile postgres` è a un flag di distanza se mai ti servisse
(vedi [Quale database usare](#quale-database-usare) più sotto).

Tutto il resto ha un valore predefinito ragionevole: SLA di 2 ore, un promemoria ogni
20 minuti, al massimo 8 avvisi al giorno, una review si chiude quando ogni revisore
nominato ha approvato. Elenco completo in [`.env.example`](.env.example).

**4. Ogni revisore scrive `/start` al bot una volta.** Telegram vieta a un bot di
scrivere per primo — senza questo, i promemoria non possono proprio arrivare. Se un
revisore assegnato non l'ha ancora fatto, il bot lo segnala una volta nel thread dei
commenti.

### Quale database usare

**Usa SQLite** — è quello predefinito ed è sufficiente. Il bot è a processo singolo e
scrive poche decine di righe al giorno; l'intero database è un file su un volume, e il
backup è un `cp`.

Postgres vale la pena solo se ne hai già uno in funzione, o vuoi backup pronti
all'uso:

```bash
docker compose --profile postgres up -d
```
e in `.env`:
```dotenv
DATABASE_URL=postgresql+asyncpg://reviewpulse:reviewpulse@db:5432/reviewpulse
```

Stesso schema, stesse migrazioni, entrambi i backend testati end-to-end. Le
migrazioni si applicano da sole all'avvio.

---

## Come si presenta

Il bot legge i post in base alla loro forma, non a un template rigido — entrambi gli
esempi qui sotto funzionano. Perché un post conti come review serve una di due cose:
**almeno un link a una MR**, oppure **una riga di revisori etichettata
esplicitamente** (così una modifica solo di documentazione o di infrastruttura, senza
MR, viene comunque tracciata, purché i revisori siano stati nominati di proposito).
Senza nessuna delle due, il post è un annuncio e non viene tracciato.

> Le etichette ("Revisori:", "MR:", "Documentazione:", ...) sono riconosciute in
> qualunque lingua parli il bot stesso — un team che scrive in russo o cinese ottiene
> la stessa analisi di uno che scrive in italiano. Vedi
> [Lingue supportate](#lingue-supportate).

Un post nel canale:

```
Pagamenti

Miglioramento del connection pool

MR API: https://gitlab.example.com/backend/services/api_controller/-/merge_requests/1112

MR Utils: https://gitlab.example.com/backend/packages/utils/-/merge_requests/223

Task: https://tasks.example.com/space/2829/boards/card/3517380

Revisori: @user1 @user2
```

Anche un template più rigido viene analizzato correttamente:

```
Catalogo
Correggere il redirect di pagamento

MR: https://gitlab.example.com/backend/services/checkout/-/merge_requests/77

Documentazione: https://wiki.example.com/pages/12345
Descrizione: se manca la documentazione
Revisore: @user2 per il backend / @user1 per il resto, @user3
```

Cosa estrae il bot dal post:

| Campo | Origine |
|---|---|
| prodotto | prima riga non vuota |
| titolo del task | riga successiva che non sia un'etichetta o un link nudo |
| MR | **tutti** i link con forma `…/-/merge_requests/<N>`, quanti siano |
| revisori | ogni `@utente` sulla riga "Revisori…"; altrimenti ogni `@utente` nel post |

I link al task tracker o alla wiki non vengono mai scambiati per una MR. Del testo
mescolato nella riga dei revisori non nasconde gli username. Se non è stato possibile
identificare i revisori, il bot non resta in silenzio: la card arriva con un pulsante
"🙋 Sono un revisore".

La card che compare nel thread dei commenti sotto il post:

```
🤖 Pagamenti — Miglioramento del connection pool

   Approvazioni: 1/2

   • @user1 — 👍 approvato
   • @user2 — 🔁 modifiche pronte, in attesa di un nuovo controllo

   api_controller!1112
   utils!223

   [👍 Approva]              [✍️ Richiedi modifiche]
   [✅ Corretto]             [🗄 Chiudi]
```

E a chi ha la palla in mano arriva un messaggio privato:

```
🔁 Le modifiche sono pronte, ma ✍️ è ancora attivo

L'autore ha sistemato tutto quello che avevi chiesto, ma non hai ancora dato
l'approvazione.

Pagamenti — Miglioramento del connection pool
In ritardo di: 1 h 20 min di tempo lavorativo

https://gitlab.example.com/backend/services/api_controller/-/merge_requests/1112

Apri la discussione

   [🔕 1 h]  [🔕 Domani]
```

---

## Perché pulsanti e non reazioni

**Le reazioni su un canale Telegram sono anonime.** Un bot amministratore riceve solo
`message_reaction_count` (l'aggregato "👍 2"), mai `message_reaction` con un campo
`user` — le reazioni per singolo utente esistono solo in gruppi e supergruppi. Non
c'è modo per un bot, né per uno userbot via MTProto, di sapere *chi esattamente* ha
reagito a un post di un canale; Telegram semplicemente non fornisce questo dato.
Vedi [Bot API](https://core.telegram.org/bots/api#update) e
[api/reactions](https://core.telegram.org/api/reactions).

Per questo il bot pubblica **una propria card con pulsanti inline** nel thread dei
commenti. Un `callback_query` porta sempre `from.id`, quindi lo stato per ogni
revisore è affidabile al 100%. Il canale stesso e il formato dei post restano
invariati.

Come funziona:

```
post nel canale
     │
     │  Telegram lo copia automaticamente nel gruppo di discussione collegato
     ▼
copia nel gruppo  ──►  il bot risponde con  ──►  la card finisce dentro
                       una card + pulsanti        il thread dei commenti
                            │
                            │  premere un pulsante = callback_query,
                            ▼  che porta sempre from.id
                     stato affidabile per revisore
```

I due aggiornamenti — il post del canale e la sua copia nel gruppo — arrivano in un
ordine imprevedibile, quindi entrambi i percorsi fanno upsert sulla stessa chiave, e
chi arriva secondo è quello che pubblica davvero la card.

---

## Modello degli stati

Lo stato vive sulla coppia **(review × revisore)**, non sulla review — un revisore
può aver già approvato mentre un altro ha ancora la palla.

| Stato | Significato | Palla a | Promemoria? |
|---|---|---|---|
| `PENDING` | ancora nessun verdetto | revisore | sì, dopo lo SLA |
| `CHANGES_REQUESTED` | ✍️, modifiche non ancora pronte | autore | no |
| `AWAITING_RECHECK` | modifiche pronte, ✍️ ancora attivo | revisore | sì, dopo lo SLA |
| `APPROVED` | 👍 | — | no |

```
PENDING           --[👍]-------------------> APPROVED
PENDING           --[✍️]-------------------> CHANGES_REQUESTED
CHANGES_REQUESTED --[modifiche segnate]-----> AWAITING_RECHECK
AWAITING_RECHECK  --[👍]-------------------> APPROVED
AWAITING_RECHECK  --[✍️]-------------------> CHANGES_REQUESTED   ← "ne ha chieste altre"
APPROVED          --[✍️]-------------------> CHANGES_REQUESTED   ← annulla un clic per errore
```

Il penultimo passaggio è "il revisore ha guardato le modifiche e ne ha chieste
altre": la palla torna all'autore, i promemoria si fermano, il timer dello SLA
riparte.

Implementazione — [`domain/state.py`](src/reviewpulse/domain/state.py), un modulo
puro senza I/O.

### Quante approvazioni servono a una review

`REQUIRED_APPROVALS` (2 di default) è un **tetto**, non un obiettivo fisso. Il numero
effettivo richiesto si adatta a quanti revisori sono stati davvero nominati:

- se viene nominato un solo revisore → la sua sola approvazione chiude la review —
  nessuno resta in attesa di un secondo verdetto che non sarebbe mai arrivato;
- se ne vengono nominati due → devono approvare entrambi;
- se ne vengono nominati più del tetto → servono comunque solo `REQUIRED_APPROVALS`,
  quindi un elenco lungo di revisori non si trasforma in un requisito di approvazione
  unanime.

Implementazione — [`services/reviews.py:approvals_needed`](src/reviewpulse/services/reviews.py).

---

## Orario di lavoro

Sia lo SLA (2h) sia l'intervallo di ripetizione (20min) avanzano **solo dentro
l'orario di lavoro** — 09:00–18:00 UTC+3, lun–ven di default. Un post pubblicato alle
17:30 di venerdì ha la scadenza alle 10:30 di lunedì. Notti e weekend non contano.

Aritmetica — [`domain/workhours.py`](src/reviewpulse/domain/workhours.py). I giorni
festivi non sono ancora gestiti; `is_working_day` è il punto di estensione.

**Anti-spam:** al massimo `MAX_NUDGES_PER_DAY` (8) promemoria al giorno per coppia,
pulsanti "🔕 1h" / "🔕 Domani" su ogni messaggio privato, e un comando `/mute 2h`. Un
utente che ha bloccato il bot viene escluso direttamente dalla query, invece di essere
ritentato ogni minuto.

---

## Come il bot capisce che le modifiche sono state fatte

**Modalità A (default).** L'autore tocca "✅ Corretto" sulla card — tutti i revisori
ancora su ✍️ passano ad `AWAITING_RECHECK`.

**Modalità B (`GITLAB_ENABLED=true` + un token).** Il bot interroga GitLab da solo e
legge i thread:

- tutti i thread risolvibili aperti dal revisore sono risolti, su **tutte** le MR
  della review → "le modifiche sono pronte";
- compare un nuovo thread non risolto da parte sua → "ne ha chieste altre", i
  promemoria si fermano.

Quella seconda regola è un modo indipendente dai pulsanti per accorgersi che il
revisore è tornato: anche se ha lasciato un commento solo su GitLab e non ha mai
toccato la card, il bot resta in silenzio.

Un revisore collega il proprio username GitLab con `/link <username>`. Senza quel
collegamento, si usa il flag `blocking_discussions_resolved` dell'intera MR — più
grezzo, ma meglio di niente. La sincronizzazione non tocca mai i revisori già
approvati, così che un thread riaperto da qualcun altro non possa revocare in
silenzio un 👍.

---

## Lingue supportate

Supportate: russo, inglese, spagnolo, italiano, cinese (`ru`, `en`, `es`, `it`, `zh`).

Due cose diverse hanno bisogno di una lingua, e non ne condividono una:

- **La card condivisa e l'avviso "scrivimi /start"** vivono nel thread dei commenti —
  chiunque lo veda, vede lo stesso messaggio, quindi hanno un'unica lingua:
  `DEFAULT_LOCALE` (default `en`).
- **I messaggi privati** — promemoria, `/start`, `/status`, conferme dei pulsanti —
  seguono la lingua di ciascun revisore: quella impostata con `/lang`, altrimenti la
  lingua del client Telegram, altrimenti `DEFAULT_LOCALE`.

```bash
/lang it   # passa i tuoi messaggi privati all'italiano
/lang      # elenca le lingue disponibili
```

Le tabelle di traduzione vivono in
[`telegram/texts.py`](src/reviewpulse/telegram/texts.py); la risoluzione della
lingua in [`i18n.py`](src/reviewpulse/i18n.py). Un test verifica che tutte e cinque
le lingue abbiano esattamente lo stesso insieme di chiavi, così una stringa aggiunta
a una lingua e dimenticata in un'altra fa fallire la CI invece di ricadere in
silenzio sull'inglese in produzione.

---

## Comandi del bot

| Comando | Cosa fa |
|---|---|
| `/start` | ti registra; collega il tuo @username al tuo id e trova le review in sospeso su di te |
| `/status` | cosa è in sospeso su di te adesso, con le scadenze e un link a ogni post |
| `/link <username>` | collega il tuo account GitLab (per la Modalità B) |
| `/lang <codice>` | cambia la lingua del bot per i tuoi messaggi privati |
| `/mute 2h`, `/unmute` | silenzia / riattiva i promemoria |

---

## Sviluppo

```bash
poetry env use 3.12
poetry install
cp .env.example .env                # compila BOT_TOKEN
poetry run python -m reviewpulse    # le migrazioni si applicano da sole all'avvio

poetry run pytest                   # 122 test
poetry run ruff check src tests
```

Copre le parti che contano: l'aritmetica dell'orario di lavoro (venerdì 17:30 →
lunedì 10:30), ogni transizione della macchina a stati, incluso "ne ha chieste
altre", la regola dinamica delle approvazioni necessarie, il parser dei post
confrontato con un post reale e con il template rigido, il parsing dei thread GitLab
su fixture, la completezza delle tabelle di traduzione su tutte e cinque le lingue, e
un ciclo completo su un database SQLite reale che sopravvive a un riavvio.

Compila e pubblica la tua immagine:

```bash
docker buildx build --platform linux/amd64,linux/arm64 \
  -t IL_TUO_ACCOUNT/reviewpulse:latest --push .
```

**Prova manuale end-to-end.** Configura un canale di test con un gruppo di
discussione collegato, rendi il bot amministratore di entrambi, e accelera il tempo
in `.env`:

```dotenv
SLA_MINUTES=1
RECHECK_SLA_MINUTES=1
NUDGE_INTERVAL_MINUTES=1
WORK_START=00:00
WORK_END=23:59
WORK_DAYS=0,1,2,3,4,5,6
```

Così l'intero ciclo — promemoria → ✍️ → "Corretto" → promemoria di ricontrollo →
✍️ di nuovo → silenzio → 👍×2 → chiusura — si svolge in pochi minuti.

---

## Struttura

```
src/reviewpulse/
  config.py              impostazioni dall'ambiente
  i18n.py                 elenco delle lingue e loro risoluzione (privato vs. messaggio condiviso)
  domain/                 logica pura: macchina a stati, orario di lavoro, regole di escalation
  parsing/                 analisi dei post ed estrazione dei link alle MR
  gitlab/                  client REST e analisi dei thread
  db/                      modelli, sessione, query
  services/                collante dominio + DB: review, promemoria, sincronizzazione GitLab
  telegram/                 bot, handler, card, testi tradotti
  scheduler/                il tick dei promemoria e il tick di sincronizzazione
migrations/                Alembic
```

---

## Limiti noti

- **Chi ha premuto "✅ Corretto" non viene verificato.** Un post di canale è
  anonimo — Telegram non riporta un autore — quindi il pulsante è disponibile a
  chiunque nel thread.
- **Il bot non può vedere le reazioni sul post stesso** (vedi sopra); la card è la
  fonte di verità.
- **Il post del canale non viene eliminato alla chiusura** — il bot si limita a
  cambiare la sua card in "✅ Chiuso". Eliminare il post di qualcun altro rompe la
  cronologia del thread.
- **I giorni festivi non sono gestiti** — il bot tratterà una festività nazionale
  come un normale giorno lavorativo.
- **L'analisi dei post riconosce un insieme fisso di parole-etichetta** per campo
  (vedi [Come si presenta](#come-si-presenta)) — un'etichetta fuori da quell'elenco,
  in qualsiasi lingua, ricade sull'euristica posizionale invece di essere letta
  direttamente.
