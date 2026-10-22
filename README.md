# Bot Annunci

Bot Telegram che ogni 5–15 minuti cerca annunci di lavoro **nuovi** adatti al tuo profilo e ti manda una notifica con ruolo, azienda, località, RAL (se indicata), skill in comune o mancanti, punteggio e link.

Gira gratis su **GitHub Actions**; il profilo si modifica dal bot. Il repository è pubblico, ma profilo e stato sono cifrati.

## Come funziona

```
GitHub Actions (cron)
  └─ ogni giro:
       1. legge i comandi Telegram arrivati nel frattempo (/skill, /zone, CV in PDF…)
       2. raccoglie gli annunci: LinkedIn · jobs.ch · siti carriere delle aziende
       3. scarta quelli già visti, quelli pubblicati da più di 48 h, fuori zona o con titolo non pertinente
       4. scarica la descrizione dei rimanenti e calcola il punteggio 0–100
       5. notifica quelli sopra soglia (12 al massimo per giro, gli altri in un riepilogo)
       6. salva profilo e stato nel branch `bot-state`
```

**Orari**: un giro ogni 5 minuti, 24 ore su 24 (il minimo consentito da GitHub; nei repository pubblici i minuti sono illimitati). GitHub non è puntuale: nelle ore di punta alcuni giri partono in ritardo o saltano, quindi nella pratica l'intervallo è di 5–15 minuti.

**Avvio dei giri**: i giri programmati di GitHub (`schedule`) sui repository nuovi possono partire con ore di ritardo o non partire affatto. Per questo il bot viene avviato da **[cron-job.org](https://cron-job.org)** ogni 5 minuti con una chiamata all'API di GitHub:

```
POST https://api.github.com/repos/KevinTr95/bot-annunci/actions/workflows/bot.yml/dispatches
Accept: application/vnd.github+json
Authorization: Bearer <token fine-grained: solo questo repo, permesso Actions: Read and write>
X-GitHub-Api-Version: 2022-11-28
Content-Type: application/json

{"ref":"main"}
```

Risposta attesa: `204 No Content`. Il token ha una scadenza: quando scade cron-job.org riceve `401` e, con le notifiche attive, manda un'email. Va rigenerato su GitHub e aggiornato nell'header `Authorization`. Il `schedule` interno del workflow resta come riserva.

**Privacy**: profilo e stato stanno nel branch `bot-state` cifrati con la chiave `STATE_KEY` (secret del repository). Il profilo in `bot/default_profile.json` è solo un modello generico; i log pubblici contengono soltanto conteggi. Se perdi `STATE_KEY` il bot non riesce più a leggere lo stato: va cancellato il branch `bot-state` e si riparte da zero.

**Solo annunci nuovi**: il bot ricorda gli annunci già visti e ignora quelli pubblicati da più di `/eta` ore (48 per default). Per jobs.ch usa la data della *prima* pubblicazione, così un annuncio ripubblicato non sembra nuovo. Lo stesso annuncio trovato su due fonti arriva una volta sola.

### Punteggio

| Componente | Punti | Come |
|---|---|---|
| Ruolo | 25 (15 se parziale) | Il titolo contiene uno dei tuoi `/ruoli` |
| Skill | 55 | Quota delle skill citate nell'annuncio che possiedi. Una skill "parente" vale in parte (Snowflake ≈ BigQuery, Informatica ≈ Talend, Power BI ≈ Qlik…) |
| Zona | 10 (5 se non indicata) | Località dentro le tue `/zone` |
| Seniority | 10 | Penalità per titoli junior e ruoli da direttore; −4 per ogni anno richiesto oltre i tuoi |
| Lingue | −10 / −3 | Lingua richiesta (o solo gradita) che non hai nel profilo |

Filtri rigidi, che scartano l'annuncio:
- parole escluse nel titolo, aziende escluse, zona sbagliata;
- **modalità di lavoro** non ammessa (`work_modes`: remote / hybrid / onsite, ricavata dal testo dell'annuncio);
- **RAL** dichiarata sotto il minimo. Se è "a partire da X", scarta solo se X è sotto il 65% del minimo;
- **% di skill**: quota delle skill citate nell'annuncio che possiedi (le "parenti" valgono mezza), sotto `min_skill_match`;
- **lingue**: con `languages_strict`, annunci che richiedono (o sono scritti in) una lingua che non hai;
- **esperienza**: annunci che chiedono più di `years_experience + max_extra_years` anni (6 + 2: 8 passa, 9 no);
- nessuna delle parole obbligatorie.

## Installazione (una volta sola)

### 1. Crea il bot Telegram
1. Su Telegram apri **@BotFather** → `/newbot` → scegli nome e username.
2. Copia il **token** che ti restituisce.

### 2. Repository GitHub
```bash
gh repo create bot-annunci --public --source . --push
```

### 3. Secret
Nel repository: **Settings → Secrets and variables → Actions → New repository secret**
- `TELEGRAM_BOT_TOKEN` = il token di BotFather
- `TELEGRAM_CHAT_ID` = *(facoltativo)* il tuo chat id. Se lo lasci vuoto, il primo che scrive `/start` al bot ne diventa il proprietario: fallo subito tu.
- `STATE_KEY` = chiave di cifratura dello stato, generata con
  `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`

### 4. Avvio
1. Scrivi `/start` al tuo bot su Telegram.
2. Su GitHub: **Actions → job-bot → Run workflow** per il primo giro (poi parte da solo).
3. Il primo giro può mandare fino a 12 notifiche più un riepilogo: sono gli annunci delle ultime 48 ore. Dal secondo in poi arrivano solo le novità.

## Comandi principali

| Comando | Cosa fa |
|---|---|
| *invia il CV in PDF* | Aggiunge al profilo le skill trovate |
| `/profilo` | Riepilogo completo |
| `/skill add dbt, spark` · `/skill rm qlik` | Le tue skill |
| `/zone add Torino` · `/zone rm Remote` | Lombardia, Milano, Ticino, Svizzera, Italia, Remote o una città |
| `/ruoli add data architect` | Ruoli confrontati col titolo |
| `/keyword add spark` | Parole cercate su LinkedIn e jobs.ch |
| `/aziende add satispay` · `/aziende add <link pagina carriere>` | Segue un sito carriere (Greenhouse, Lever, Workable, SmartRecruiters, Recruitee, Personio) |
| `/ral 50000` · `/ral_chf 95k` | RAL minima (scarta solo annunci con RAL dichiarata più bassa) |
| `/soglia 60` | Punteggio minimo per la notifica |
| `/skill_min 60` | % minima di skill richieste che possiedi |
| `/modalita remote, hybrid` · `/modalita tutte` | Modalità di lavoro ammesse |
| `/modalita_ignota scarta` · `tieni` | Cosa fare se l'annuncio non dice la modalità |
| `/lingue_obbligatorie on` · `off` | Scarta gli annunci che richiedono lingue che non hai |
| `/escludi add junior` · `/obbligatorie add bigquery` | Filtri extra |
| `/esperienza 6` · `/esperienza_extra 2` | Anni di esperienza e tolleranza |
| `/silenzio 23 7` | Notifiche senza suono di notte |
| `/stato` · `/ultimi` · `/pausa` · `/riprendi` | Gestione |

`/aiuto` mostra l'elenco completo. **I comandi vengono letti al giro successivo**, quindi la risposta può arrivare dopo 5–15 minuti.

## Modificare il profilo da file

In alternativa ai comandi Telegram, dal Mac (servono `STATE_KEY` nel file `.env` e `gh` autenticato):

```bash
.venv/bin/python -m bot.profile_cli scarica
```

Scrive `profilo.json` (in chiaro, escluso da git) nella cartella del progetto. Modificalo con qualsiasi editor, poi:

```bash
.venv/bin/python -m bot.profile_cli carica
```

Prima controlla il file, poi mette in pausa il bot, aspetta che finisca l'eventuale giro in corso, pubblica il profilo cifrato e riattiva il bot. Se nel frattempo hai cambiato il profilo da Telegram, si ferma e ti chiede di riscaricarlo, così non perdi nessuna modifica.

Campi principali di `profilo.json`: `skills` (lista, es. `["SQL", "dbt"]`), `zones`, `roles`, `keywords`, `ral_min_eur` / `ral_min_chf` (numero o `null`), `min_score`, `min_skill_match` (0–100), `work_modes` (`["remote", "hybrid"]`, vuota = tutte), `work_mode_strict`, `languages_strict`, `max_age_hours`, `exclude_words`, `exclude_companies`, `required_words`, `quiet_hours` (`[23, 7]` o `null`), `companies` (`{"name", "ats", "slug"}`; usa `--detect nome` per trovare ats e slug).

## Fonti e limiti

| Fonte | Stato |
|---|---|
| LinkedIn | Ricerca pubblica senza login. Funziona, ma LinkedIn può limitare le richieste (errore 429): in quel caso gli annunci si recuperano al giro dopo. Se fallisce per circa 2 ore di fila ricevi un avviso. |
| jobs.ch | API JSON pubblica. Copre il Ticino e la Svizzera. |
| Siti carriere | API pubbliche degli ATS: la fonte più rapida e affidabile per le aziende che ti interessano. |
| Indeed | Non incluso: blocca le richieste automatiche (403). |
| InfoJobs | Non incluso: ha chiuso in Italia. |

## Sviluppo locale

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env              # facoltativo, per provare anche Telegram
.venv/bin/python -m bot.main --dry-run          # stampa le notifiche, non salva nulla
BOT_VERBOSE=1 .venv/bin/python -m bot.main --dry-run   # anche i motivi di ogni scarto
.venv/bin/python -m bot.main --detect satispay  # cerca la pagina carriere di un'azienda
```

Struttura: `bot/sources/` contiene le fonti, `bot/matcher.py` i filtri e il punteggio, `bot/commands.py` i comandi Telegram, `bot/default_profile.json` il profilo iniziale (ricavato dal CV) e `scripts/state.sh` il salvataggio dello stato.
