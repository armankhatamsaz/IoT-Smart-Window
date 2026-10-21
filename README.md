# IoT Smart Window

A microservice-based smart home system that automatically controls windows across
several rooms based on indoor and outdoor conditions. Built for the *Programming for
IoT Applications* course.

The system reads temperature, humidity and light from each zone, decides how far to
open each window, logs everything to ThingSpeak, learns from that history to forecast
the room temperature, and exposes both a web dashboard and a Telegram bot for
monitoring and manual control.

## Architecture

Every component is an independent container. Services find each other through a central
registry (the service catalog) and communicate over an MQTT broker. The dashboard and
the Telegram bot enforce per-user access rules read from the catalog.

```
                               +-------------------+
                               |  ThingSpeak Cloud |
                               +-------------------+
                                  ^ HTTPS (adaptor only)
                                  v
 +------------------+     +----------------------+   GET /history   +--------------+
 |  Registry (REST) |     |  ThingSpeak Adaptor  |<-----------------|  Forecaster  |
 |     port 8080    |     |  MQTT + REST 8081    |                  | ML, REST 8082|
 +------------------+     +----------------------+                  +--------------+
    ^ config, discovery,       ^            ^ GET /history                 ^
    | registration             |            |  (dashboard)                 |
    v                          v            |                              v
 +---------------------------------------------------------------------------------+
 |                         Mosquitto MQTT broker (1883 / 9001)                     |
 +---------------------------------------------------------------------------------+
     ^              ^                     ^                           ^ WebSocket
     v              v                     v                           v
 +----------+  +----------+  +---------------------------+  +---------------------+
 | Analyzer |  | Telegram |  |  Zone gateways x4 (REST)  |  |    Web dashboard    |
 |          |  |   Bot    |  |  living / bed1 / bed2 /   |  |   (browser, port 80)|
 +----------+  +----------+  |  outdoor                  |  +---------------------+
                             +---------------------------+
```

Every service and device registers in the catalog at startup and refreshes its entry
with a heartbeat. Inside Docker no service has a hard-coded address of another one:
the broker comes from `GET /broker`, and the forecaster finds the adaptor's `/history`
endpoint in `servicesList`. The dashboard runs in the browser, outside the Docker
network, so it uses the ports published on the host.

## Components

| Service            | Folder                | Role                                                                     |
| ------------------ | --------------------- | ------------------------------------------------------------------------ |
| MQTT broker        | `mosquitto/`          | Eclipse Mosquitto: MQTT for the services, WebSockets for the dashboard.   |
| Registry           | `registry/`           | REST service catalog: broker config, users, registered devices/services. |
| Zone gateways (x4) | `gateway/`            | Simulate the sensors of a room, publish telemetry, move the window.       |
| Analyzer           | `control_logic/`      | Control logic that decides how far to open each window.                   |
| ThingSpeak adaptor | `thingspeak_adaptor/` | Logs telemetry to the cloud and serves the history over REST.             |
| Forecaster         | `forecaster/`         | Learns each room from the history and forecasts its temperature.          |
| Telegram bot       | `telegram_bot/`       | Login, live status, forecast, manual control and alerts over Telegram.    |
| Dashboard          | `dashboard/`          | Web UI for live monitoring, manual control and history charts.            |

## MQTT topics

```
home/<zone>/sensor/temp        home/<zone>/sensor/hum        home/<zone>/sensor/light
home/<zone>/actuator/window
home/<zone>/forecast/temp
```

Zones: `livingroom`, `bedroom1`, `bedroom2`, `outdoor` (the outdoor zone has no window).

Window commands are published as **retained** messages, so a service that restarts
(or a dashboard that just logged in) immediately knows where every window is.

### Data format

Telemetry is published in **SenML** (RFC 8428), using the standard unit symbols
`Cel`, `%RH` and `lx`:

```json
{ "bn": "home/livingroom/", "e": [ { "n": "temp", "u": "Cel", "t": 1784206016, "v": 26.3 } ] }
```

Forecasts use the same format; `t` is the time the forecast is for:

```json
{ "bn": "home/livingroom/", "e": [ { "n": "temp_forecast", "u": "Cel", "t": 1784206076, "v": 26.9 } ] }
```

Actuation is a command rather than a measurement, so it carries its own schema:
a continuous window opening plus the reason and origin of the decision.

```json
{ "position": 0-100, "reason": "<why>", "source": "auto" | "manual" }
```

## REST APIs

### Catalog (registry, port 8080)

The registry exposes all four methods over `devicesList`, `servicesList` and
`usersList`, so devices, services and users can be added or removed at runtime
without touching the source code.

| Method   | Endpoint                  | Purpose                                          |
| -------- | ------------------------- | ------------------------------------------------ |
| `GET`    | `/<collection>`           | List all entries                                 |
| `GET`    | `/<collection>/<id>`      | Read a single entry                              |
| `GET`    | `/broker` · `/config`     | Broker settings · broker + users                 |
| `POST`   | `/<collection>`           | Register an entry (re-POST replaces it)          |
| `POST`   | `/login`                  | Check `userName`/`password`, returns the user    |
| `PUT`    | `/<collection>`           | Heartbeat / update fields of an entry            |
| `DELETE` | `/<collection>/<id>`      | Remove an entry                                  |

Passwords never leave the registry: they are removed from every `GET` response and
checked only by `POST /login` (401 on a wrong password). Devices and services that
stop sending heartbeats are evicted automatically after 150 s. The Telegram bot stores
each user's `chatID` in `usersList`, so users stay logged in when the bot restarts.

### Zone gateways (host ports 8091-8094)

| Method | Endpoint  | Purpose                                                        |
| ------ | --------- | -------------------------------------------------------------- |
| `GET`  | `/`       | Latest readings and window position of the zone                |
| `PUT`  | `/window` | Body `{"position": 0-100}`, sent to the window as a manual command |

8091 = Living Room, 8092 = Bedroom 1, 8093 = Bedroom 2, 8094 = Outdoor (read only).

### ThingSpeak adaptor (port 8081)

`GET /history?results=N` (1-8000) returns the last `N` rows stored in ThingSpeak.
The two channels are merged by time and every row is a snapshot of all zones:

```json
{ "samples": [ { "t": "2026-10-21T10:13:38Z",
                 "zones": { "livingroom": { "temp": 24.1, "hum": 50.2, "light": 310, "window": 40 },
                            "outdoor":    { "temp": 19.8, "hum": 61.0, "light": 820 } } } ] }
```

The dashboard charts and the forecaster both use this endpoint; neither talks to
ThingSpeak directly.

### Forecaster (port 8082)

| Method | Endpoint    | Purpose                                                    |
| ------ | ----------- | ---------------------------------------------------------- |
| `GET`  | `/model`    | Per room: training samples, error, coefficients, `usable`  |
| `GET`  | `/forecast` | Latest forecast per room                                   |

## Control logic

The analyzer runs a small processing pipeline per zone rather than a single fixed
threshold:

1. **Validation** – readings outside physical bounds or with an implausible jump from
   the previous sample are rejected as glitches.
2. **Statistics** – a sliding window feeds a moving average and a short-term trend
   for each sensor.
3. **Multi-factor decision** – a target window opening (0-100%) is derived from
   temperature, humidity and light together, in order of priority:
   - **Temperature** is compared against the outdoor average: open for free
     cooling/heating only when the outside actually helps, otherwise close.
   - **Humidity** calls for ventilation when it is high, but never against the
     temperature rule (a cold room is not ventilated with even colder air).
   - **Daylight** – a dark room on a bright day opens a little to let light in, with
     hysteresis so the extra light does not make the window flap.
   - **Strong light** caps how far the window opens, to limit glare and solar gain.
4. **Forecast** – if the forecaster expects the room to leave the comfort band soon,
   the analyzer acts on the forecast now instead of waiting for it to happen.
5. **Smoothing** – the target is averaged over the last few evaluations and the window
   moves toward it gradually, with a dead-band, so the actuator never oscillates or
   gets spammed.

A manual command (dashboard, bot or gateway REST) pauses automatic control for that
zone for 90 s, then the analyzer resumes. All comfort thresholds and timing live in
`control_logic/settings.json`.

## Temperature forecaster (machine learning)

The forecaster predicts each room's temperature about one minute ahead (three
ThingSpeak rows), from the history stored by the adaptor.

- **Model** – a linear regression on three features with a physical meaning: the
  indoor/outdoor gap (heat through the walls), the gap times the window opening
  (exchange through the open window) and the sunlight. Because every feature means
  something, the learned coefficients can be read and checked.
- **Training data** – only the last 6 hours, only stretches where the window did not
  move during the prediction horizon (so it learns the room, not the controller) and
  never across a gap in the data.
- **Validation** – the oldest 80% of the examples is used for training and the newest
  20% for testing. The model is compared with the naive guess "the temperature will
  not change" and is only used if its error is at least 10% lower. The result is
  shown on the dashboard and at `GET /model`.
- **Retraining** – every 10 minutes. After a fresh start it needs about 20 minutes of
  data (60 rows) before the first model is ready.

Since the gateways simulate the rooms with known constants, this can be checked: on
simulated data the model finds a wall coefficient close to the one in
`gateway/connector.py` without being told about it (see `tests/test_forecaster.py`).

## Simulated sensors

The zone gateways simulate their sensors with a simple physical model. Outdoor
temperature, humidity and light follow a day/night cycle (one simulated day lasts
16 real minutes, `DAY_MINUTES`). Each room drifts toward the outdoor temperature,
faster when its window is open, and is warmed by the sun, so the window really
changes the room and the control loop is closed.

## Access control

Users are defined in the catalog with an `allowed_zones` list. Both the dashboard and
the Telegram bot only show and allow control of the zones each user is authorized for.

Default demo users:

| Username | Password | Allowed zones               |
| -------- | -------- | --------------------------- |
| user1    | 1234     | Living Room, Bedroom 1      |
| user2    | 1234     | Living Room, Bedroom 2      |

## Running the project

Requirements: Docker and Docker Compose.

The Telegram token and the ThingSpeak keys are secrets, so they are not in the
repository. Copy `.env.example` to `.env` and fill them in (Docker Compose reads `.env`
automatically). Without them the rest of the system still runs: the bot stays off
and nothing is logged to ThingSpeak.

```bash
cp .env.example .env
docker compose up --build
```

This starts all services. Then:

- **Dashboard:** open <http://localhost> and log in with a demo user.
- **Telegram bot:** open the configured bot in Telegram and send `/login user1 1234`.

Ports exposed on the host:

| Port      | Service                                 |
| --------- | --------------------------------------- |
| 80        | Web dashboard                           |
| 1883      | MQTT broker                             |
| 9001      | MQTT over WebSockets (dashboard)        |
| 8080      | Registry REST API                       |
| 8081      | Adaptor REST API (`/history`)           |
| 8082      | Forecaster REST API                     |
| 8091-8094 | Zone gateway REST APIs                  |

### Using a public broker instead

The broker is read from the catalog, so switching to a public broker only needs the
`broker` entry in `registry/main.py`, for example:

```python
"broker": {"IP": "broker.hivemq.com", "port": 8000, "transport": "websockets",
           "webSocketURL": "wss://broker.hivemq.com:8884/mqtt"}
```

Delete `registry/catalog.json` afterwards so the registry recreates it. Some networks
(universities, companies) block MQTT ports, which is why the local broker is the
default.

## ThingSpeak setup

Two channels are used (`thingspeak_adaptor/settings.json`):

- **Channel 1** – temperature and humidity of the four zones (fields 1-8).
- **Channel 2** – light of the four zones (fields 1-4) and the window position of
  the three rooms (fields 5-7).

The channel IDs and the field mapping are in `thingspeak_adaptor/settings.json`; the
Write and Read API Keys of each channel go in `.env`. A channel without a write key is
not logged, and a channel without a read key is left out of `/history`.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests
```

The tests cover the control rules, the forecaster (including a closed-loop run of the
simulated room with the real analyzer), the history merge in the adaptor and the
catalog's TTL and password handling.

## Configuration

Each service reads its own `settings.json`:

- `gateway/settings_*.json` – zone name, sensors, whether the zone has a window, REST address.
- `control_logic/settings.json` – comfort thresholds and control timing.
- `forecaster/settings.json` – history size, horizon, validation split, retraining period.
- `telegram_bot/settings.json` – registry URL.
- `thingspeak_adaptor/settings.json` – ThingSpeak channel IDs and field mapping.
- `.env` – Telegram token and ThingSpeak API keys (not committed, see `.env.example`).

The registry keeps its state in `registry/catalog.json`; if that file is missing it is
recreated from the defaults in `registry/main.py`.
