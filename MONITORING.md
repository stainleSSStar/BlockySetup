# Monitoring Blocky: statystyki i historia DNS

Instalacja: [README.md](README.md). Zalecana komenda dla działającego serwera:

```bash
bash install.sh --monitoring-only
```

Skrypt automatycznie instaluje i konfiguruje monitoring w Dockerze, z uwzględnieniem domyślnego
sterownika `journald` DietPi: dla kontenerów monitoringu jawnie ustawia `json-file` i rotację logów.

## Co pokazuje Grafana

| Panel | Źródło | Informacje |
|---|---|---|
| Blocky — statystyki | Prometheus | stan DNS, blokowanie, liczba i tempo zapytań, klienci, p95 opóźnienia, cache |
| Blocky — historia DNS | MariaDB | czas UTC przeliczany przez Grafanę, IP i nazwa klienta, domena, rodzaj zapytania, wynik, powód, odpowiedź, czas obsługi |

W folderze **Blocky** dostępne są gotowe panele z repo. Nie wymagają wtyczek Grafany.
Tabela historii respektuje wybrany zakres czasu i pokazuje najwyżej 1000 najnowszych wpisów.
Wybierz krótszy przedział czasu, żeby przeglądać wcześniejsze fragmenty historii.
Jeśli nazwa klienta jest pusta, IP nadal identyfikuje urządzenie; nazwy zależą od konfiguracji reverse DNS.

Własne zapytanie w **Explore → BlockyLogs → Code**, format **Table**:

```sql
SELECT request_ts AS time,
       client_ip AS klient,
       question_name AS domena,
       response_type AS typ,
       reason AS powod
FROM log_entries
WHERE $__timeFilter(request_ts)
ORDER BY request_ts DESC
LIMIT 1000;
```

Blocky zapisuje daty do MariaDB w UTC (`loc=UTC`); źródło MySQL w Grafanie używa sesji `+00:00`.
Retencja historii: 7 dni. `queryLog.flushInterval`: 10 sekund.
Logi uruchomienia/błędów kontenerów pozostają dostępne przez `docker logs`; panel historii dotyczy zapytań DNS.

## Pliki i dane

| Lokalizacja | Zawartość |
|---|---|
| `/opt/blocky/config.yml` | aktywna konfiguracja DNS i queryLog |
| `/opt/blocky/monitoring/mariadb.env` | dane dostępowe bazy; uprawnienia 600 |
| `/opt/blocky/monitoring/grafana.env` | początkowe hasło nowej Grafany i hasło źródła MySQL; uprawnienia 600 |
| `/opt/blocky/monitoring/prometheus.yml` | zbieranie metryk z Blocky i Prometheusa |
| `/opt/blocky/monitoring/grafana/` | provisioning i definicje dashboardów |
| wolumen `blocky_db_data` | historia DNS w MariaDB |
| wolumen `blocky_prometheus_data` | metryki Prometheusa |
| wolumen `blocky_grafana_data` | użytkownicy i ustawienia Grafany |
| wolumen `blocky_cache` | pobrane listy blokowania |

Hasła i konfiguracja produkcyjna powstają poza checkoutem repozytorium.
Ponowna instalacja zachowuje hasła oraz wolumeny. Zmiana `GF_SECURITY_ADMIN_PASSWORD` w env nie resetuje
hasła już istniejącego użytkownika w bazie Grafany.

## Sprawdzenie pobierania metryk

```bash
curl -fsS 'http://127.0.0.1:9090/api/v1/query?query=up%7Bjob%3D%22blocky%22%7D'
```

Wynik `value` powinien zawierać `1`. Poczekaj co najmniej 15 sekund od startu Prometheusa.
Wykresy wykorzystujące `rate(...[5m])` potrzebują kilku próbek.
W konsoli Grafany źródło **Prometheus** wskazuje `http://127.0.0.1:9090`, bo Grafana używa host network.

## Sprawdzenie historii

```bash
dig @127.0.0.1 example.com +short
dig @127.0.0.1 googlesyndication.com +short
```

Po 10–30 sekundach odśwież panel historii. Historię można sprawdzić też bez Grafany:

```bash
docker exec blocky-db sh -c 'MYSQL_PWD="$MARIADB_PASSWORD" mariadb --user=blocky --database=blocky -e "SELECT request_ts,client_ip,question_name,response_type FROM log_entries ORDER BY request_ts DESC LIMIT 10"'
```

## Błędy w starszej instrukcji

- `/api/query` to **POST wykonujący zapytanie DNS**, nie GET zwracający historię.
- Prometheus przechowuje metryki; nie zawiera pełnej historii domen.
- Dashboard `14980` wymaga MySQL/MariaDB; nie odczytuje bezpośrednio CSV ani SQLite.
- Użytkownik systemowy `blocky` nie wyznacza UID kontenera; oficjalny obraz działa jako UID 100.
- Wolumen cache musi odpowiadać ścieżce `cachePath` w konfiguracji.
- `RuntimeCacheDirectory` nie jest poprawną dyrektywą systemd; właściwa to `CacheDirectory`.
- Wyłączenie DNS dla samego hosta nie jest wymagane: jego zapytania do lokalnego Blocky nie tworzą
  automatycznie pętli. Blocky ma własne zewnętrzne upstreamy i bootstrap DNS.

## Oficjalne źródła

- [Blocky: Prometheus i Grafana](https://0xerr0r.github.io/blocky/latest/prometheus_grafana/)
- [Blocky: konfiguracja queryLog](https://0xerr0r.github.io/blocky/latest/configuration/#query-logging)
- [Grafana: provisioning](https://grafana.com/docs/grafana/latest/administration/provisioning/)
- [Prometheus: instalacja](https://prometheus.io/docs/prometheus/latest/installation/)
- [MariaDB: obraz Docker](https://hub.docker.com/_/mariadb)
