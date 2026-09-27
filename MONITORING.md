# Monitoring blocky — Prometheus + Grafana (przeglądanie z innego komputera)

Instrukcja instalacji stosu monitorującego dla [blocky](https://github.com/0xERR0R/blocky) na Raspberry Pi 5 z DietPi (lub dowolnym systemem debianopodobnym). Po wykonaniu tych kroków będziesz mógł przeglądać statystyki i logi DNS z dowolnego komputera w sieci lokalnej, w przeglądarce.

## Architektura

```
blocky (:4000/metrics) ──scrape──> Prometheus (:9090) ──data──> Grafana (:3001) <── Twoja przeglądarka
blocky (queryLog: CSV/SQLite) ────────────────────────────────> Grafana (dashboard 14980)
```

- **Prometheus** — zbiera metryki z blocky co 15 s i przechowuje je w szeregach czasowych
- **Grafana** — panel WWW z gotowym dashboardem blocky (ID **13768**): wykresy zapytań, zablokowań, cache, DNSSEC, top klientów
- **queryLog blocky** (opcjonalnie) — logi pojedynczych zapytań DNS (kto, co, kiedy, czy zablokowane) + dashboard ID **14980**

> Wymagania: działający blocky zgodnie z [README.md](README.md) (w tym `prometheus.enable: true`, które jest już w `config.yml`).

---

## Krok 1: Prometheus

### DietPi (zalecane)

```sh
dietpi-software install 218    # Prometheus (serwer)
dietpi-software install 99     # Prometheus Node Exporter (metryki RPi, opcjonalnie)
```

Config Prometheusa na DietPi: `/mnt/dietpi_userdata/prometheus/prometheus.yml`

### Debian / Ubuntu (bez DietPi)

```sh
sudo apt update
sudo apt install prometheus
```

Config: `/etc/prometheus/prometheus.yml`

### Konfiguracja scrape blocky

Z repo skopiuj przygotowany plik:

```sh
# DietPi:
sudo cp monitoring/prometheus.yml /mnt/dietpi_userdata/prometheus/prometheus.yml

# Debian/Ubuntu:
sudo cp monitoring/prometheus.yml /etc/prometheus/prometheus.yml
```

Zawartość (`monitoring/prometheus.yml`):

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: blocky
    static_configs:
      - targets: ['127.0.0.1:4000']
  - job_name: node
    static_configs:
      - targets: ['127.0.0.1:9100']
  - job_name: prometheus
    static_configs:
      - targets: ['127.0.0.1:9090']
```

Restart i weryfikacja:

```sh
sudo systemctl restart prometheus
sudo systemctl status prometheus

# sprawdź, czy target blocky jest UP:
# otwórz http://<IP_RPI>:9090/targets w przeglądarce
# lub z konsoli:
curl -s http://127.0.0.1:9090/api/v1/targets | grep -o '"health":"up"' | head -3
```

Jeśli przy bloku `blocky` widzisz `DOWN`, sprawdź czy blocky działa: `curl -s http://127.0.0.1:4000/metrics | head`.

---

## Krok 2: Grafana

### DietPi (zalecane)

```sh
dietpi-software            # Browse Software → Grafana → Install
```

Po instalacji Grafana działa na porcie **3001** (DietPi), dane w `/mnt/dietpi_userdata/grafana`.

### Debian / Ubuntu (bez DietPi) — oficjalne repo APT

```sh
sudo apt-get install -y apt-transport-https wget gnupg
sudo mkdir -p /etc/apt/keyrings
sudo wget -O /etc/apt/keyrings/grafana.asc https://apt.grafana.com/gpg-full.key
sudo chmod 644 /etc/apt/keyrings/grafana.asc
echo "deb [signed-by=/etc/apt/keyrings/grafana.asc] https://apt.grafana.com stable main" | sudo tee /etc/apt/sources.list.d/grafana.list
sudo apt-get update
sudo apt-get install grafana
sudo systemctl enable --now grafana-server
```

W tej wersji Grafana działa na porcie **3000** (standardowym).

### Logowanie

Otwórz w przeglądarce z **innego komputera**:

- DietPi: `http://<IP_RPI>:3001`
- Debian/Ubuntu: `http://<IP_RPI>:3000`

Login: `admin`, hasło: `admin` (przy pierwszym logowaniu Grafana każe je zmienić; na DietPi hasło startowe to globalne hasło DietPi).

---

## Krok 3: Dodaj źródło danych Prometheus w Grafanie

1. Menu (☰) → **Connections** → **Data sources** → **Add data source**
2. Wybierz **Prometheus**
3. URL: `http://localhost:9090` (Grafana i Prometheus są na tym samym Raspberry Pi)
4. **Save & Test** — powinno pokazać „Successfully queried the Prometheus API"

## Krok 4: Importuj oficjalny dashboard blocky

1. Menu (☰) → **Dashboards** → **New** → **Import**
2. W polu **Import via grafana.com** wpisz ID: **13768** → **Load**
3. W polu **DS_PROMETHEUS** wybierz źródło Prometheus utworzone w kroku 3
4. W polu **blocky API URL** wpisz adres API blocky widziany z Twojej przeglądarki, np. `http://192.168.1.10:4000` (bez ukośnika na końcu) — dzięki temu zadziałają przyciski włącz/wyłącz blokowania na dashboardzie
5. **Import**

Gotowe — masz pełny podgląd: zapytania na sekundę, % zablokowań, czas odpowiedzi, cache hits, DNSSEC, refresh list, top klientów i typy zapytań.

> Wymagania dashboardu: Grafana ≥ 10.2 (przyciski blokowania) i blocky > v0.31 przy CORS — v0.35.0 spełnia to z nadwyżką.

---

## Krok 5 (opcjonalnie): Logi pojedynczych zapytań DNS (query log)

Dashboard 13768 pokazuje **agregaty** (wykresy). Jeśli chcesz też przeglądać **logi pojedynczych zapytań** (kto zapytał o jaką domenę i czy została zablokowana), włącz `queryLog` w blocky.

### Wariant A: SQLite (najprostszy, plik lokalny)

1. Utwórz katalog na logi:

```sh
sudo mkdir -p /var/lib/blocky
sudo chown blocky:blocky /var/lib/blocky
```

2. W `/etc/blocky/config.yml` dodaj sekcję:

```yaml
queryLog:
  type: sqlite
  target: /var/lib/blocky/querylog.db
  logRetentionDays: 7
  # creationAttempts: 3
  # creationCooldown: 2s
  # fields: [clientIP, clientName, responseReason, responseAnswer, question, duration]
```

3. Zweryfikuj i zrestartuj:

```sh
blocky validate --config /etc/blocky/config.yml
sudo systemctl restart blocky
```

### Wariant B: CSV (jeden plik dziennie)

```yaml
queryLog:
  type: csv
  target: /var/lib/blocky
  logRetentionDays: 7
```

Każdego dnia powstaje nowy plik CSV, który można też otwierać ręcznie w programie arkuszowym.

### Wgląd w logi zapytań w Grafanie

Oficjalny dashboard blocky do logów zapytań ma ID **14980** ([grafana.com/dashboards/14980](https://grafana.com/dashboards/14980)) i wymaga logowania do **MySQL/MariaDB** (wariant `type: mysql` + źródło danych MySQL w Grafanie; jest też wersja dla Postgresa w [docs blocky](https://github.com/0xERR0R/blocky/blob/main/docs/prometheus_grafana.md)).

Dla wariantu **SQLite** zainstaluj w Grafanie wtyczkę **frser-sqlite-datasource** (→ **Plugins**), dodaj źródło danych wskazujące na `/var/lib/blocky/querylog.db` i buduj własne panele (Explore → SQL).

Najprostsza opcja bez bazy: REST API blocky — `http://<IP_RPI>:4000/api/query?name=example.com` zwraca historię zapytań w JSON. Działa niezależnie od wybranego wariantu queryLog.

> **Prywatność i dysk:** query log zapisuje nazwy domen wszystkich zapytań z LAN. Ustaw `logRetentionDays` rozsądnie (np. 7 dni) i pamiętaj, że karta SD w Raspberry Pi ma ograniczoną liczbę zapisów — przy dużym ruchu rozważ dysk SSD/USB dla `/var/lib/blocky`.

---

## Przydatne metryki blocky (PromQL)

W Grafana → **Explore** (źródło Prometheus) możesz użyć zapytań:

```promql
# zapytania na sekundę (wszystkie)
sum(rate(blocky_query_total[5m]))

# % zapytań zablokowanych
100 * sum(rate(blocky_response_total{response_type="BLOCKED"}[5m]))
  / sum(rate(blocky_query_total[5m]))

# trafienia cache
sum(rate(blocky_cache_hits_total[5m]))

# czas odpowiedzi (percentyl 95)
histogram_quantile(0.95, sum(rate(blocky_request_duration_seconds_bucket[5m])) by (le))

# błędne walidacje DNSSEC (powinno być ~0)
sum(rate(blocky_dnssec_validation_total{result="bogus"}[5m]))
```

Pełna lista metryk: [prometheus_grafana.md w docs blocky](https://github.com/0xERR0R/blocky/blob/main/docs/prometheus_grafana.md).

---

## Zabezpieczenie (ważne)

Grafana, Prometheus i API blocky są domyślnie **bez szyfrowania** — zabezpiecz je, aby były dostępne tylko w Twojej LAN:

- Grafana: zmień hasło admina (→ **Administration** → **Users**) i w `grafana.ini` ustaw `http_addr = 127.0.0.1` + reverse proxy z HTTPS, jeśli wystawiasz poza LAN
- Prometheus: nie wystawiaj portu 9090 poza LAN (`--web.listen-address=127.0.0.1` w unit file, jeśli używasz go tylko przez Grafanę)
- blocky API (port 4000): zawiera przyciski wyłączające blokowanie — jeśli nie potrzebujesz go zdalnie, w `config.yml` ustaw `ports.http: 127.0.0.1:4000` (uwaga: wtedy przyciski na dashboardzie i metryki z innych hostów przestaną działać; Prometheus scrapuje z localhost, więc metryki zostaną)

## Rozwiązywanie problemów

| Objaw | Rozwiązanie |
|---|---|
| Target blocky `DOWN` w `/targets` | `curl -s http://127.0.0.1:4000/metrics` — brak odpowiedzi = blocky nie działa lub `prometheus.enable: false` |
| Grafana nie widzi danych | Sprawdź zakres czasu (prawy górny róg) i czy scrape działa ≥ 1 min |
| Przyciski blokowania nie działają | W dashboardzie ustaw „blocky API URL" na adres **z perspektywy przeglądarki**, nie localhost |
| `permission denied` przy zapisie querylog.db | `sudo chown blocky:blocky /var/lib/blocky` |
| Zużycie dysku rośnie | Zmniejsz `logRetentionDays` w `queryLog` lub wyłącz query log (`type: none`) |

## Aktualizacje

```sh
# DietPi:
dietpi-software reinstall 218   # Prometheus
dietpi-software reinstall 99    # Node Exporter
# Grafana (DietPi): reinstall przez dietpi-software

# Debian/Ubuntu (APT):
sudo apt update && sudo apt upgrade
```
