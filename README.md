# BoxBlip for Home Assistant

A custom integration that shows your [BoxBlip](https://boxblip.daniele-messi.com) parcels in Home Assistant: one device
per parcel with its status and details, a few counters, a delivery calendar, a "delivery today" sensor for
automations, an event every time a parcel changes status and a ready-made dashboard.

Home Assistant can only **read** your parcels. It cannot add, change or delete anything.

Step-by-step guide on the BoxBlip website: <https://boxblip.daniele-messi.com/home-assistant/>

*[Versione italiana più sotto](#italiano).*

## Requirements

- Home Assistant 2026.3 or newer (tested with 2026.10).
- The BoxBlip app with notifications enabled (that is how the server knows your parcels).

## Installation

### HACS (recommended)

BoxBlip is installed in HACS as a custom repository:

1. In Home Assistant open **HACS**, then the three dots in the top right corner › **Custom repositories**.
2. Paste `https://github.com/danymexi/boxblip-home-assistant`, choose the type **Integration** and tap **Add**.
3. Search HACS for **BoxBlip**, open it and tap **Download**.
4. Restart Home Assistant: **Settings › System**, then the power button in the top right corner › **Restart Home
   Assistant**.

HACS tells you when a new version is out; update from the same page and restart.

Already installed by hand? Follow the same steps: HACS replaces the folder and from then on manages updates. The
integration, its settings and your entities stay as they are.

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=danymexi&repository=boxblip-home-assistant&category=integration)

### Manually

1. Download the source of the [latest release](https://github.com/danymexi/boxblip-home-assistant/releases/latest)
   and copy its folder `custom_components/boxblip` into `<your config>/custom_components/`, so that you end up
   with `<config>/custom_components/boxblip/manifest.json`.
2. Restart Home Assistant.

To update, replace the folder with the one from the new release and restart.

## Setup

1. In the BoxBlip app open **Import › Home Assistant** and tap **Connect Home Assistant**. Copy the code (it starts
   with `bbha_`). It is shown only once; you can also scan it as a QR code from another device.
2. In Home Assistant go to **Settings › Devices & services › Add integration** and search for **BoxBlip**.
3. Paste the code. Leave *Server address* as it is unless you run your own BoxBlip server.

| Field | Description |
|---|---|
| Code | The `bbha_…` code from the app. Required. |
| Server address | Default `https://api-boxblip.daniele-messi.com`. Must be https (plain http is accepted only for `localhost` / `127.0.0.1`, for development). |

If the code is revoked (you generated a new one, disconnected, or deleted your data) Home Assistant shows a
**Reconfigure** notification: paste the new code there, nothing else changes. *Reconfigure* in the integration menu
lets you change code and server address at any time.

### Options

| Option | Default | Description |
|---|---|---|
| Instant updates | on | Lets the server notify Home Assistant on every status change (see [Push](#push-optional)). |
| Keep delivered parcels for (days) | 3 | Delivered, returned and expired parcels keep their sensor for this many days (0 to 30). |

## Devices and entities

BoxBlip shows up as one **BoxBlip** service device per account. Each parcel is its own device, connected to it, so
the device list, each device page and Home Assistant's automatic dashboard group everything by parcel.

**BoxBlip** (the account):

| Entity | Example id | Description |
|---|---|---|
| On the way | `sensor.boxblip_on_the_way` | Number of parcels on the way. |
| Out for delivery | `sensor.boxblip_out_for_delivery` | Number of parcels out for delivery. |
| To pick up | `sensor.boxblip_to_pick_up` | Parcels waiting at a pickup point or locker. |
| Delivered today | `sensor.boxblip_delivered_today` | Parcels delivered today. |
| Problems | `sensor.boxblip_problems` | Parcels with a delivery problem. |
| Delivery today | `binary_sensor.boxblip_delivery_today` | On when an incoming parcel is out for delivery, or its expected delivery window ends today (Home Assistant time zone). Outgoing parcels are ignored. |
| Next delivery | `sensor.boxblip_next_delivery` | Date the next incoming parcel is expected by; attribute `parcels` lists which ones. |
| Deliveries | `calendar.boxblip_deliveries` | Calendar with the expected delivery window of every incoming parcel: a timed event when the carrier gives a time slot, all-day events otherwise. Shows up in Home Assistant's Calendar. |
| Last read (diagnostic) | `sensor.boxblip_last_read` | When the BoxBlip server last answered. |

The ids of the counters and of "Delivery today" follow your Home Assistant language (for example
`sensor.boxblip_in_arrivo` in Italian); the next three are the same in every language.

**One device per parcel**, named like the parcel, with the carrier as manufacturer and a link to the carrier's
tracking page (*Visit*):

| Entity | Example id | Description |
|---|---|---|
| Status | `sensor.boxblip_shoes` | The parcel's status (an enum sensor, translated in the UI): `pending`, `info_received`, `in_transit`, `out_for_delivery`, `available_for_pickup`, `delivered`, `exception`, `returned`, `expired`. Its name is the parcel's name. |
| Expected by | `sensor.boxblip_shoes_expected_delivery` | Last day of the expected delivery window (attributes `window_start`, `window_end`, `has_time`). |
| Last update | `sensor.boxblip_shoes_last_update` | Time of the last tracking event. |
| Last event | `sensor.boxblip_shoes_last_event` | Text of the last tracking event. |
| Last place | `sensor.boxblip_shoes_location` | Where the last tracking event happened. |
| Carrier (diagnostic) | `sensor.boxblip_shoes_carrier` | The carrier's name. |
| Tracking number (diagnostic) | `sensor.boxblip_shoes_tracking_number` | The tracking number (attribute `tracking_url`). |

A detail entity is created only once BoxBlip has that detail for the parcel (a parcel with no estimate yet has no
"Expected by" until one arrives). The status sensor keeps all details as attributes too: `name`, `display_name`,
`status_text` (localised), `carrier`, `carrier_code`, `tracking_number`, `last_event`, `last_event_time`,
`last_event_location`, `eta_start`, `eta_end`, `tracking_url`, `outgoing`, `shared`, `added_at`, `delivered_at`.

Lifecycle:

- A parcel added in the app gets its device and entities at the next update, without a restart. Renaming it in the
  app renames the device.
- Delivered, returned and expired parcels stay for the number of days set in the options, then their device and
  entities are removed from Home Assistant.
- Archived parcels have no device; if you unarchive one, it comes back.
- A parcel device that is no longer on the list can also be deleted by hand from its device page.

Upgrading from 0.1 (everything on one device): the parcel sensors keep their entity ids and move to their
parcel's device; the new entities are added next to them. Nothing is renamed or duplicated, so automations keep
working. Only the shown name changes, from "BoxBlip Shoes" to "Shoes".

## Dashboard

[`dashboards/boxblip.yaml`](dashboards/boxblip.yaml) is a ready-made dashboard (sections view, built-in cards only).
It finds your parcels by itself and groups them: *In consegna oggi*, *In viaggio*, *Da ritirare*, *Problemi*,
*Consegnati di recente*, with a summary on top and the delivery calendar. Empty groups are hidden. Each parcel shows
its name, carrier, last event, place and time; tap the name to open the parcel's device page. The labels are in
Italian; change the quoted text to translate them.

To add it: **Settings › Dashboards › Add dashboard › New dashboard from scratch**, open the new dashboard, then
**pencil › three dots › Raw configuration editor**, paste the file's content and **Save**.

## Event: `boxblip_parcel_updated`

Fired on the Home Assistant bus when a parcel's status changes, with this data:

| Key | Example |
|---|---|
| `config_entry_id` | `01J…` |
| `parcel_id` | `p_ab12…` (stable) |
| `name` / `display_name` | `Shoes` / `Shoes` (`display_name` is never empty, e.g. `BRT ··7890`) |
| `tracking_number` | `1234567890` |
| `carrier` / `carrier_code` | `BRT` / `brt` |
| `old_status` / `new_status` | `in_transit` / `out_for_delivery` |
| `status_text` | `Out for delivery` (localised) |
| `last_event` | `{"time": "…Z", "text": "…", "location": "Milano"}` or `null` |
| `eta` | `{"start": "…Z", "end": "…Z", "has_time": false}` or `null` |
| `tracking_url` | carrier page or `null` |
| `outgoing` | `false` |

Rules:

- No events are fired for what is found at startup (or after a reload): only for changes seen afterwards.
- A parcel that shows up after startup fires once with `old_status: null`.
- Archived parcels and parcels that leave the list fire nothing.
- Changes are detected when Home Assistant reads the list (every 5 minutes, or right away with push). If a parcel goes
  through two statuses between two reads, only the last one is reported.

## Action: `boxblip.refresh`

Reads your parcels now instead of waiting for the next update. Optional `config_entry_id` limits it to one account.

```yaml
action: boxblip.refresh
```

## Push (optional)

By default Home Assistant asks BoxBlip for your parcels every 5 minutes. With **Instant updates** on, and if Home
Assistant is reachable from the internet, the integration also registers a webhook and gives its address to the
BoxBlip server, which calls it when a status changes; Home Assistant then reads the list straight away.

- It uses the Home Assistant Cloud webhook if you have an active subscription, otherwise your **external URL**
  (Settings › System › Network). The address must be `https`, on port 443, 8123 or 8443, and public.
- Without a usable address the integration stays on polling and says nothing. If the server refuses the address,
  a warning with the reason is logged and polling continues.
- Every call is signed with a key derived from your code and must be less than 5 minutes old; anything else is
  rejected. The content of the call is never trusted: it only triggers a normal read.
- Turn it off in the integration options. Removing the integration unregisters the webhook.

The integration is declared as `cloud_polling` because polling is always on and push is only an accelerator.

## Examples

### Announcements on Alexa, Google Nest and HomePod

BoxBlip does not talk to speakers itself: these automations use integrations you may already have in Home Assistant.
Replace the entity ids with yours.

**Google Nest, Chromecast, HomePod and other speakers** that Home Assistant shows as a `media_player` (Google Cast,
Apple TV for HomePod, Sonos and so on), with any text-to-speech entity (`tts.home_assistant_cloud`,
`tts.google_translate_en_com`, …):

```yaml
automation:
  - alias: "Parcel out for delivery: announce"
    triggers:
      - trigger: event
        event_type: boxblip_parcel_updated
        event_data:
          new_status: out_for_delivery
    conditions:
      - condition: template
        value_template: "{{ not trigger.event.data.outgoing }}"
    actions:
      - action: tts.speak
        target:
          entity_id: tts.home_assistant_cloud
        data:
          media_player_entity_id: media_player.kitchen
          message: >
            {{ trigger.event.data.display_name }} is out for delivery
            with {{ trigger.event.data.carrier or 'the courier' }}.
```

**Amazon Echo (Alexa)** with the community integration Alexa Media Player (installed from HACS):

```yaml
automation:
  - alias: "Parcel out for delivery: Alexa"
    triggers:
      - trigger: event
        event_type: boxblip_parcel_updated
        event_data:
          new_status: out_for_delivery
    conditions:
      - condition: template
        value_template: "{{ not trigger.event.data.outgoing }}"
    actions:
      - action: notify.alexa_media
        data:
          target: media_player.kitchen_echo
          message: "{{ trigger.event.data.display_name }} is out for delivery."
          data:
            type: announce
```

Echo devices announce only with *Communications* enabled for them in the Alexa app, and stay silent in Do Not
Disturb.

### Notify when a parcel is ready at the pickup point

```yaml
automation:
  - alias: "Parcel ready for pickup"
    triggers:
      - trigger: event
        event_type: boxblip_parcel_updated
        event_data:
          new_status: available_for_pickup
    actions:
      - action: notify.mobile_app_my_phone
        data:
          title: "Ready for pickup"
          message: >
            {{ trigger.event.data.display_name }}
            {%- if trigger.event.data.last_event and trigger.event.data.last_event.location %}
            at {{ trigger.event.data.last_event.location }}
            {%- endif %}
          data:
            url: "{{ trigger.event.data.tracking_url }}"
```

### Turn on the porch light while a courier is expected

```yaml
automation:
  - alias: "Courier expected"
    triggers:
      - trigger: state
        entity_id: binary_sensor.boxblip_delivery_today
        to: "on"
    actions:
      - action: light.turn_on
        target:
          entity_id: light.porch
```

### Markdown card listing your parcels

```yaml
type: markdown
title: Parcels
content: >
  {% for e in integration_entities('boxblip') | select('match', 'sensor\\.')
     if state_attr(e, 'display_name') is not none %}
  **{{ state_attr(e, 'display_name') }}**: {{ state_attr(e, 'status_text') or states(e) }}
  {%- if state_attr(e, 'eta_end') %}, by {{ (state_attr(e, 'eta_end') | as_local).strftime('%a %d %b') }}{% endif %}
  {%- if state_attr(e, 'carrier') %} · {{ state_attr(e, 'carrier') }}{% endif %}
  {% else %}
  No parcels on the way.
  {% endfor %}
```

### Entities card

```yaml
type: entities
title: BoxBlip
entities:
  - binary_sensor.boxblip_delivery_today
  - sensor.boxblip_on_the_way
  - sensor.boxblip_out_for_delivery
  - sensor.boxblip_to_pick_up
  - sensor.boxblip_problems
```

## Disconnecting

1. In Home Assistant: **Settings › Devices & services › BoxBlip › ⋮ › Delete**.
2. In the app: **Import › Home Assistant › Disconnect Home Assistant**. The code stops working immediately.

**Delete my data** in the app also disconnects Home Assistant. If you only do step 2, Home Assistant asks for a new
code; if you only do step 1, the code keeps working until you disconnect it in the app.

## Privacy

- The code is read-only and specific to Home Assistant. It is stored in Home Assistant's config entry, like any
  other integration credential. Anyone with the code can read your parcel list, so treat it as a password and
  generate a new one if in doubt.
- What Home Assistant receives: for each parcel in transit or delivered in the last days, its name, tracking number,
  carrier, status, last tracking event (text, time, place), expected delivery window and carrier tracking link.
  Never addresses, pickup or delivery codes, or data from connected stores.
- Home Assistant reads the list every 5 minutes (and on push, when enabled). The server only answers "nothing
  changed" when that is the case.
- Diagnostics downloads hide the code, webhook id, parcel names, tracking numbers, event texts and places, and links.

## Troubleshooting

- **"The code was not accepted"**: the code was revoked or mistyped. Generate a new one in the app.
- **Entities unavailable**: the server is unreachable, or Home Assistant access is switched off on the server. The
  integration keeps retrying every 5 minutes; no action is needed.
- **No instant updates**: check that Home Assistant has an https external URL reachable from the internet (or Home
  Assistant Cloud) and look for a "refused the push URL" warning in the logs.

## Issues and contributions

Report problems or ideas in the [issue tracker](https://github.com/danymexi/boxblip-home-assistant/issues). Attach
the diagnostics file (integration page › ⋮ › **Download diagnostics**): parcel names, tracking numbers and the code
are already removed from it.

## Development

```sh
uv venv -p 3.14 .venv
uv pip install -p .venv -r requirements-test.txt
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy custom_components/boxblip
.venv/bin/python -m pytest tests -q
```

Licensed under the [MIT License](LICENSE).

---

## Italiano

Integrazione personalizzata che mostra i tuoi pacchi di BoxBlip in Home Assistant: un dispositivo per ogni pacco con
stato e dettagli, alcuni contatori, un calendario delle consegne, un sensore "consegna oggi" per le automazioni, un
evento ogni volta che un pacco cambia stato e una dashboard pronta.
Home Assistant può solo **leggere** i pacchi: non può aggiungere, modificare o cancellare nulla.

Guida passo passo sul sito: <https://boxblip.daniele-messi.com/it/home-assistant/>

### Installazione

**Con HACS (consigliato)**, come repository personalizzato:

1. In Home Assistant apri **HACS**, poi i tre puntini in alto a destra › **Repository personalizzati**.
2. Incolla `https://github.com/danymexi/boxblip-home-assistant`, scegli il tipo **Integrazione** e tocca
   **Aggiungi**.
3. Cerca **BoxBlip** in HACS, aprilo e tocca **Scarica**.
4. Riavvia Home Assistant: **Impostazioni › Sistema**, poi il pulsante di accensione in alto a destra › **Riavvia
   Home Assistant**.

HACS ti avvisa quando esce una nuova versione: aggiornala dalla stessa pagina e riavvia. Se l'avevi già installata a
mano, segui gli stessi passi: HACS sostituisce la cartella e l'integrazione, le impostazioni e le entità restano.

**A mano**: scarica il sorgente dell'[ultima versione](https://github.com/danymexi/boxblip-home-assistant/releases/latest),
copia la cartella `custom_components/boxblip` in `<config>/custom_components/` e riavvia Home Assistant.

### Configurazione

1. Nell'app BoxBlip apri **Importazione › Home Assistant** e tocca **Collega Home Assistant**. Copia il codice
   (inizia con `bbha_`): viene mostrato una sola volta, ma puoi anche leggerlo come QR da un altro dispositivo.
2. In Home Assistant vai in **Impostazioni › Dispositivi e servizi › Aggiungi integrazione** e cerca **BoxBlip**.
3. Incolla il codice. Lascia l'indirizzo del server com'è, a meno che tu non usi un tuo server.

Se il codice viene revocato, Home Assistant chiede di ricollegare l'integrazione: basta incollare il nuovo codice.
Nelle opzioni puoi spegnere gli aggiornamenti immediati e scegliere per quanti giorni tenere i pacchi consegnati
(predefinito 3).

### Dispositivi ed entità

BoxBlip compare come un servizio **BoxBlip** per account, e ogni pacco è un dispositivo a sé collegato a quello: la
lista dei dispositivi, la pagina di ogni dispositivo e la dashboard automatica di Home Assistant sono ordinate per pacco.

- **BoxBlip** (l'account): i contatori **In arrivo**, **In consegna**, **Da ritirare**, **Consegnati oggi**,
  **Problemi**; **Consegna oggi** (`binary_sensor`, acceso se un pacco in arrivo è in consegna o la sua finestra di
  consegna finisce oggi); **Prossima consegna** (`sensor.boxblip_next_delivery`); il calendario **Consegne**
  (`calendar.boxblip_deliveries`, con la finestra di consegna prevista di ogni pacco in arrivo, visibile nel
  Calendario di Home Assistant); **Ultima lettura** (diagnostica).
- **Un dispositivo per pacco**, con il nome del pacco, il corriere come produttore e il link alla pagina di
  tracciamento (*Visita*): lo **stato** (con il nome del pacco, tradotto nell'interfaccia e con tutti i dettagli come
  attributi), **Consegna entro**, **Ultimo aggiornamento**, **Ultimo evento**, **Ultimo luogo** e, tra la
  diagnostica, **Corriere** e **Numero di tracciamento**. Un'entità di dettaglio nasce solo quando BoxBlip ha quel
  dato per il pacco.
- I pacchi nuovi compaiono da soli; rinominarli nell'app rinomina il dispositivo; quelli consegnati restano per i
  giorni scelti e poi dispositivo ed entità vengono rimossi.
- Aggiornando dalla 0.1 (tutto su un solo dispositivo) i sensori dei pacchi tengono il loro `entity_id` e passano
  sul dispositivo del pacco: niente doppioni, le automazioni continuano a funzionare. Cambia solo il nome mostrato,
  da "BoxBlip Scarpe" a "Scarpe".

### Dashboard

[`dashboards/boxblip.yaml`](dashboards/boxblip.yaml) è una dashboard pronta (vista a sezioni, solo schede standard).
Trova i pacchi da sola e li raggruppa in *In consegna oggi*, *In viaggio*, *Da ritirare*, *Problemi* e *Consegnati di
recente*, con un riepilogo in alto e il calendario delle consegne; i gruppi vuoti non si vedono. Per ogni pacco:
nome, corriere, ultimo evento, luogo e ora; toccando il nome si apre la pagina del dispositivo del pacco.

Per aggiungerla: **Impostazioni › Plance › Aggiungi plancia › Nuova plancia da zero**, apri la plancia nuova, poi
**matita › tre puntini › Editor di configurazione testuale**, incolla il contenuto del file e **Salva**.

### Evento e azione

- `boxblip_parcel_updated`: a ogni cambio di stato, con `old_status`, `new_status`, `display_name`, `carrier`,
  `tracking_number`, `last_event`, `eta`, `tracking_url` e gli altri campi descritti sopra. Non viene generato per la
  situazione trovata all'avvio; un pacco comparso dopo l'avvio genera un evento con `old_status: null`.
- `boxblip.refresh`: legge subito i pacchi senza aspettare il prossimo controllo.

### Annunci su Alexa, Google Nest e HomePod

Con le integrazioni che hai già in Home Assistant puoi far annunciare i pacchi in consegna (esempi completi nella
sezione inglese *Announcements on Alexa, Google Nest and HomePod*):

- **Google Nest, Chromecast, HomePod** e altri altoparlanti visti come `media_player` (Google Cast, Apple TV per
  HomePod, Sonos…): azione `tts.speak` con un'entità di sintesi vocale e `media_player_entity_id`.
- **Amazon Echo** con l'integrazione della comunità Alexa Media Player: azione `notify.alexa_media` con
  `target: media_player.<tuo_echo>` e `data: {type: announce}`. Serve *Comunicazioni* attivo per l'Echo nell'app
  Alexa.

L'attivatore è l'evento `boxblip_parcel_updated` con `new_status: out_for_delivery`.

### Aggiornamenti immediati (facoltativi)

Normalmente Home Assistant legge i pacchi ogni 5 minuti. Se Home Assistant è raggiungibile da internet con un
indirizzo https (o con Home Assistant Cloud), il server BoxBlip lo avvisa a ogni cambio di stato e la lettura
avviene subito. Ogni chiamata è firmata e verificata; senza indirizzo pubblico resta tutto in polling, senza errori.

### Scollegare

1. In Home Assistant: **Impostazioni › Dispositivi e servizi › BoxBlip › ⋮ › Elimina**.
2. Nell'app: **Importazione › Home Assistant › Scollega Home Assistant**: il codice smette subito di funzionare.

Anche **Cancella i miei dati** nell'app scollega Home Assistant.

### Privacy

Il codice permette solo di leggere i pacchi. Home Assistant riceve nome, numero di tracciamento, corriere, stato,
ultimo evento (testo, ora, luogo), finestra di consegna prevista e link del corriere dei pacchi in viaggio o
consegnati negli ultimi giorni; mai indirizzi, codici di ritiro o di consegna, né dati dei negozi collegati. La
lettura avviene ogni 5 minuti. I file di diagnostica nascondono codice, nomi, numeri di tracciamento, testi e luoghi
degli eventi e link.
