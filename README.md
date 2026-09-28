# Fuel Route Planner API

A Django REST API that takes a start and a finish location in the USA and returns:

- the driving route (GeoJSON), plus an interactive map;
- the most cost-effective places to fuel up, for a vehicle with a **500-mile range** and **10 MPG**;
- the gallons bought at each stop and the **total fuel cost**.

Fuel prices come from the provided `fuel-prices-for-be-assessment.csv`. Routing uses [OpenRouteService](https://openrouteservice.org/) (free tier).

**Performance:** each new trip makes **one** routing API call and responds in about 1–2 s, most of which is spent in OpenRouteService. Repeated trips are served from the cache in about 5 ms.

---

## Quick start

Requirements: Python 3.12+ and a free [OpenRouteService API key](https://openrouteservice.org/dev/#/signup).

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env              # then set ORS_API_KEY in .env

python manage.py migrate
python manage.py import_fuel_stations --offline   # ~2 s, no API calls
python manage.py runserver
```

Open a map in your browser:
<http://127.0.0.1:8000/api/v1/route/map/?start=New%20York,%20NY&finish=Los%20Angeles,%20CA>

Run the tests (about 600 tests, around 15 s, no network needed):

```bash
pytest
```

A Postman collection is included: [`postman/fuel-route-planner.postman_collection.json`](postman/fuel-route-planner.postman_collection.json).

---

## API

### `GET /api/v1/route/` (or `POST` with a JSON body)

| Parameter | Required | Description |
|---|---|---|
| `start` | yes | `"City, ST"`, `"City, State"`, `"lat,lng"`, or a free-form address |
| `finish` | yes | same formats as `start` |
| `start_fuel` | no | Gallons in the tank at the start, 0–50. Default `0`, so the trip's full fuel cost is counted. |
| `stop_penalty` | no | USD cost assigned to each fuel stop during optimisation. Default `10`. Set `0` for the lowest possible fuel cost regardless of the number of stops. |

```http
GET /api/v1/route/?start=Chicago, IL&finish=Denver, CO
```

```jsonc
{
  "start":  { "query": "Chicago, IL", "label": "Chicago, IL", "lat": 41.837, "lng": -87.685 },
  "finish": { "query": "Denver, CO",  "label": "Denver, CO",  "lat": 39.762, "lng": -104.881 },
  "route": {
    "distance_miles": 1000.7,
    "duration_hours": 15.9,
    "geometry": { "type": "LineString", "coordinates": [[-87.68489, 41.83705], ...] }
  },
  "fuel": {
    "vehicle_range_miles": 500,
    "miles_per_gallon": 10,
    "start_fuel_gallons": 0.0,
    "stop_penalty_usd": 10.0,
    "total_gallons": 100.074,
    "total_cost": 297.44,
    "average_price_per_gallon": 2.972,
    "currency": "USD",
    "stations_considered": 150
  },
  "fuel_stops": [
    {
      "stop": 1, "station_id": 72438, "name": "QUIKTRIP #7208",
      "address": "I-290 EXIT 18A", "city": "Bellwood", "state": "IL",
      "lat": 41.882901, "lng": -87.87617,
      "mile_marker": 12.9, "distance_from_route_miles": 0.8,
      "price_per_gallon": 3.079, "gallons": 32.168, "cost": 99.05
    },
    { "stop": 2, "name": "KWIK STAR #932",      "city": "Altoona",   "state": "IA", "mile_marker": 321.7, "price_per_gallon": 2.959, "gallons": 25.485, "cost": 75.41, ... },
    { "stop": 3, "name": "Henderson Fuel Mart", "city": "Henderson", "state": "NE", "mile_marker": 576.5, "price_per_gallon": 2.899, "gallons": 42.421, "cost": 122.98, ... }
  ],
  "map_url": "http://127.0.0.1:8000/api/v1/route/map/?start=Chicago%2C+IL&finish=Denver%2C+CO&start_fuel=0.0&stop_penalty=10",
  "meta": { "cached": false, "external_api_calls": 1, "response_time_ms": 1305.2 }
}
```

`meta.external_api_calls` reports how many third-party calls the request made. It is 1 for a new trip and 0 for a cached one.

### `GET /api/v1/route/map/`

Takes the same parameters and returns an HTML page with a Leaflet map. The map shows the route, numbered fuel-stop markers, and a summary panel. The JSON response includes a ready-made `map_url`.

### Errors

| Status | When |
|---|---|
| `400` | Missing or invalid parameters, a location that can't be found, a location outside the USA, or start and finish at the same place. The response has field-level messages. |
| `422` | No drivable route, or no reachable fuel station within 500 miles on part of the route |
| `502` / `503` | The routing provider failed, or its rate limit was reached |

---

## How it works

```
request ─► resolve locations ─► route (1 ORS call) ─► stations near route ─► optimiser ─► JSON / map
            offline gazetteer      cached               KD-tree, ~10 ms        DP, ~10 ms
```

| Module | Responsibility |
|---|---|
| `routes/services/locations.py` | Converts input to coordinates. It handles `lat,lng` and `City, ST` offline using the US Census Gazetteer, and only falls back to the ORS geocoder for other text. |
| `routes/services/routing.py` | Makes **one** ORS directions call, which also returns country info so endpoints outside the USA can be rejected. It also simplifies the geometry for the response. |
| `routes/services/corridor.py` | Finds stations within 5 miles of the route and the mile marker of each one. |
| `routes/services/optimizer.py` | Chooses where to stop and how much to buy. Pure Python with no Django or I/O. |
| `routes/services/trip_planner.py` | Runs the pipeline and handles caching, money rounding and the response shape. |
| `routes/views.py` | Thin DRF view that maps domain errors to HTTP status codes. |

### 1. Preparing the fuel data (offline, one time)

The CSV has addresses such as `I-44, EXIT 283 & US-69`, which can't be geocoded at street level, and it has no coordinates. Geocoding about 6,600 stations through a free API at request time would be slow and would exceed the quota. So `import_fuel_stations` does the work once:

- It keeps the source file untouched and cleans the data in code. It drops non-US rows (Canadian provinces), merges duplicate OPIS IDs (905 of them, keeping the lowest price), and upserts on OPIS ID so it can be re-run safely.
- It geocodes each station by **city and state** using the US Census Gazetteer: 67k places and county subdivisions, stored in `routes/data/us_places.csv`. Name normalisation handles `St.`/`Saint`, `Mc Lean`/`McLean`, Census suffixes, and consolidated cities such as `Macon-Bibb County`. This covers 97.4% of stations.
- Places the gazetteer doesn't know are geocoded once through ORS. The results are saved in `routes/data/geocode_cache.json`, which is committed, so later imports need no API calls.

Result: 6,626 US stations, of which 99.2% have coordinates. Station coordinates are city-level. That's accurate enough here because these are highway truck stops, but it's the main approximation in the project.

### 2. Stations along the route

The route polyline is densified to 0.5-mile spacing and converted to 3D coordinates on a sphere. It is then indexed in a SciPy `cKDTree`. A single vectorised query returns, for every station, its distance from the route and the nearest route point. Stations within 5 miles are kept, and each one gets a **mile marker**, i.e. its distance along the route. This takes about 10 ms for a coast-to-coast route.

### 3. Choosing fuel stops

The optimiser minimises:

```
total fuel cost  +  stop_penalty × number of stops
```

**Why add a stop penalty?** The pure minimum-cost plan (the classic greedy for the "gas station problem") is optimal but not practical. It will stop to buy 1 gallon at a station 1 cent cheaper. With the default $10 per stop, representing driver time:

| Trip | `stop_penalty=0` (cheapest fuel) | default `stop_penalty=10` |
|---|---|---|
| Chicago → Denver (1,001 mi) | 6 stops, $297.04 | **3 stops, $297.44** |
| New York → Los Angeles (2,812 mi) | 16 stops, $859.37 | **7 stops, $865.27** (+0.7%) |

**Algorithm:** dynamic programming based on Khuller, Malekian & Mestre, *"To fill or not to fill: the gas station problem"*. In an optimal plan, every stop either **fills the tank** or buys **just enough to reach the next stop**. That means the fuel level on arrival at a stop is either 0 or a full tank minus the last leg. The state space is therefore O(n·k), where k is the number of stations within one tank's range. A prefix-minimum over states sorted by fuel level evaluates each transition in O(log k). A 2,800-mile trip with about 350 candidate stations takes about 10 ms.

**Correctness is tested against independent solvers** (`routes/tests/test_optimizer.py`):
- With a penalty of 0, the plan's cost matches an exact **linear-programming** optimum (`scipy.optimize.linprog`) on 300 random trips.
- With a penalty above 0, it matches a **brute-force** search over every subset of stops on 150 random trips.
- A further 100 random plans are checked to be physically feasible: the tank never goes below empty or above capacity, and it arrives at the destination empty.

### 4. Keeping external calls to a minimum

| Step | External calls |
|---|---|
| `City, ST` or `lat,lng` input | 0 (offline gazetteer) |
| Free-form address | 1 per location, cached for 30 days |
| Route | 1, cached per origin/destination pair |
| Repeated trip | 0, the whole plan is cached |

Station data and the gazetteer are held in memory and loaded when the server starts, so the database is not queried per request.

---

## Assumptions

- **The tank starts empty** by default, so `total_cost` covers all the fuel for the trip (distance ÷ 10 MPG). The vehicle first fuels at the best station within 25 miles of the start (`FIRST_STOP_MAX_MILES`), or at the nearest station on the route if none is that close. The fuel used to reach that first station is charged at its price. Use `start_fuel` to start with fuel already in the tank.
- **Tank capacity** is 500 mi ÷ 10 MPG = 50 gallons.
- A station is considered **on the route** if it is within 5 miles of the route (`STATION_MAX_DETOUR_MILES`). The detour itself isn't added to the distance.
- **Prices** are the CSV retail prices. Money is calculated with `Decimal` and rounded to cents per stop, so the stop costs add up exactly to `total_cost`.
- Vehicle range, MPG, corridor width, first-stop distance and the default stop penalty are settings in `config/settings.py`.

## Trade-offs and next steps

- **Geocoding precision:** stations are placed at city level. Given more time, I'd geocode each highway exit, e.g. match `I-80, EXIT 143` against OpenStreetMap motorway junctions, to get exact coordinates.
- **Cache:** the in-process `LocMemCache` works for this demo. In production I'd use Redis, so all workers share cached routes and plans.
- **Station data refresh:** after re-importing, restart the workers or clear the cache. In production, a price-version key in the cache would handle this automatically.
- **Spatial database:** PostGIS would be unnecessary for 6.6k stations held in memory, but it would be the natural choice for a nationwide dataset that updates frequently.

## Project layout

```
config/                     Django project (settings read secrets from .env)
routes/
  management/commands/      build_gazetteer, import_fuel_stations
  data/                     us_places.csv (Census gazetteer), geocode_cache.json
  services/                 locations, routing, corridor, optimizer, trip_planner, ors_client
  templates/routes/map.html Leaflet map
  tests/                    optimizer, corridor, geocoding, routing, API
postman/                    Postman collection
fuel-prices-for-be-assessment.csv   provided data, unmodified
```
