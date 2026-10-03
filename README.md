# BlockySetup — DNS i monitoring na DietPi

Konfiguracja Blocky v0.35.0 dla Raspberry Pi 5 / 64-bitowego DietPi.
Instalator uruchamia cztery kontenery Docker: Blocky, Prometheus, Grafana OSS i MariaDB.
Grafana automatycznie dostaje źródła danych oraz panele **Blocky — statystyki** i **Blocky — historia DNS**.

## Instalacja od zera

W konsoli SSH jako root:

```bash
apt-get update
apt-get install -y git ca-certificates
git clone https://github.com/stainleSSStar/BlockySetup.git /root/BlockySetup
cd /root/BlockySetup
bash install.sh
```

Jeśli Docker nie jest zainstalowany, skrypt instaluje go przez `dietpi-software install 162`.
Nie wykonuje aktualizacji systemu ani `apt upgrade`.
Poza DietPi zainstaluj najpierw Docker Engine. Instalator wymaga systemd, apt, Python 3 z PyYAML, curl i ss.

## Dodanie monitoringu do istniejącego Blocky

Dla kontenera utworzonego według wcześniejszych instrukcji:

```bash
git clone https://github.com/stainleSSStar/BlockySetup.git /root/BlockySetup
cd /root/BlockySetup
bash install.sh --monitoring-only
```

Jeśli checkout już istnieje, zamiast klonowania wykonaj `cd /root/BlockySetup && git pull --ff-only`.

Instalator wymaga kontenera `blocky` v0.35.0 z `--network host`, pliku `/opt/blocky/config.yml`
zamontowanego jako `/app/config.yml` i wolumenu `blocky_cache` w `/app/cache`.
Porty DNS i HTTP w konfiguracji muszą wynosić odpowiednio 53 i 4000.

Zachowuje upstreamy, listy i grupy klientów. Włącza eksport metryk oraz historię do MariaDB,
ustawia cache na `/app/cache/lists`, a przed zmianą zapisuje kopię `/opt/blocky/config.yml.backup-*`.
Istniejącą sekcję `queryLog` zastępuje, zamiast dopisywać powtórzony klucz YAML.
Waliduje konfigurację i sprawdza DNS; przy nieudanym starcie przywraca poprzednią konfigurację.
Włączenie historii wymaga jednego krótkiego restartu Blocky.

Kontenery monitoringu o nazwach `blocky-db`, `blocky-prometheus` i `blocky-grafana` są odtwarzane
z zachowaniem ich wolumenów i istniejących obrazów. Dotychczasowe hasło Grafany pozostaje aktywne.
Skrypt sprawdza obrazy, wolumeny oraz hasło bazy przed zastąpieniem istniejących kontenerów.
Nie usuwa wolumenów. Nie nadpisuje pliku `mariadb.env` z wcześniejszej instrukcji.

## Panel WWW

Otwórz `http://IP_RPI:3001`, login `admin`.
Hasło nowej instalacji:

```bash
sed -n 's/^GF_SECURITY_ADMIN_PASSWORD=//p' /opt/blocky/monitoring/grafana.env
```

Jeżeli Grafana już wcześniej działała, użyj jej dotychczasowego hasła; powyższy plik go nie resetuje.
W **Dashboards → Blocky** znajdziesz dwa gotowe panele. Nie trzeba dodawać źródeł danych ani importować ID.
Statystyki zaczynają się od uruchomienia Prometheusa; historia DNS od włączenia `queryLog`.
Historia jest zapisywana co 10 sekund i przechowywana 7 dni; panele odświeżają się co 15 sekund.
Każda tabela pokazuje najwyżej 1000 najnowszych wpisów z wybranego zakresu czasu.

Prometheus przechowuje metryki maksymalnie 15 dni lub do osiągnięcia limitu 2 GB danych blokowych
(WAL i bieżące dane mogą wymagać dodatkowego miejsca). Dane Grafany, metryk i bazy są w trwałych wolumenach.

## Sieć i działanie DNS

Ustaw stały IP RPi lub rezerwację DHCP. W routerze ustaw ten IP jako DNS przekazywany klientom przez DHCP.
Klienci muszą mieć dostęp do RPi na UDP/TCP 53; przeglądarka do TCP 3001.
Prometheus (`127.0.0.1:9090`) i MariaDB (`127.0.0.1:3307`) są dostępne lokalnie.
Blocky API i metryki są na porcie 4000. Kontener DNS działa w host network.

```bash
dig @IP_RPI example.com +short
dig @IP_RPI googlesyndication.com +short
dig @IP_RPI ad2.doubleclick.net +short
```

Pierwszy test powinien zwrócić adres, dwa pozostałe `0.0.0.0`, o ile domeny nadal figurują na listach.
Sam `doubleclick.net` lub `ad.doubleclick.net` nie jest wiarygodnym testem tych list.

## Konfiguracja DNS

`config.yml` zawiera:

- upstreamy DoH: Cloudflare, Google, Quad9, CZ.NIC i Digitale Gesellschaft; `parallel_best`;
- OISD big, HaGezi Pro i TIF; HaGezi Gambling jest zdefiniowana, ale nieprzypisana do klientów;
- DNSSEC, ochronę przed DNS rebinding, cache z prefetchingiem;
- metryki Prometheus i dyskowy cache list.

Nie gwarantujemy, że konkretny upstream będzie najszybszy w każdej sieci.
Definicja nieaktywnej listy może nadal powodować jej pobranie i zużycie pamięci.

Po instalacji edytuj **aktywny** plik:

```bash
nano /opt/blocky/config.yml
docker exec blocky /app/blocky validate --config /app/config.yml && docker restart blocky
```

Skrypt generuje dane dostępowe lokalnie w `/opt/blocky/monitoring`, poza repozytorium.
Nie dodawaj aktywnej konfiguracji zawierającej hasło bazy do publicznego repo.

## Diagnostyka

```bash
docker ps
docker logs --tail 100 blocky
docker logs --tail 100 blocky-db
docker logs --tail 100 blocky-prometheus
docker logs --tail 100 blocky-grafana
curl -fsS http://127.0.0.1:4000/api/blocking/status
curl -fsS http://127.0.0.1:9090/-/ready
curl -fsS http://127.0.0.1:3001/api/health
```

Więcej: [MONITORING.md](MONITORING.md).

## Alternatywa: sam Blocky jako usługa systemd

`blocky.service` jest przeznaczony dla binarki `/usr/local/bin/blocky`, konfiguracji `/etc/blocky/config.yml`
i użytkownika systemowego `blocky`. Używa `CacheDirectory=blocky` dla `/var/cache/blocky`.
Nie uruchamiaj go jednocześnie z kontenerem na tym samym porcie 53.
Ten wariant nie jest używany przez `install.sh`.

## Weryfikacja repozytorium

```bash
bash -n install.sh
python3 -m unittest discover -s tests -v
```

Instalator przypina wersje nowych usług; ponowne uruchomienie zachowuje obrazy już istniejących kontenerów.
Nie pobiera konfiguracji ani dashboardów z zewnętrznych serwisów podczas instalacji.

## Aktualizacja istniejącego środowiska

Jako root:

```bash
git -C /root/BlockySetup pull --ff-only && bash /root/BlockySetup/update.sh
```

Sam podgląd wybranych wersji, bez pobierania obrazów i restartowania kontenerów:

```bash
bash /root/BlockySetup/update.sh --check
```

Aktualizator wybiera najnowsze stabilne wydania Blocky, Prometheusa i Grafany z oficjalnych
GitHub Releases. MariaDB dostaje najnowszą poprawkę w **obecnej serii major.minor**
(np. 11.4.x pozostaje 11.4.x), z oficjalnego obrazu Docker Hub. Wydania testowe i obniżanie
rozpoznanej wersji są odrzucane. Wymaga dostępu do GitHub API, Docker Hub i rejestrów obrazów;
błąd pobrania metadanych lub obrazu przerywa pracę przed zatrzymaniem usług. Nie aktualizuje
DietPi, pakietów APT ani Docker Engine.

Pobiera wszystkie obrazy i waliduje aktywną konfigurację Blocky z istniejącymi montowaniami,
w tym lokalnymi whitelist/blacklist w `/root/blocky`. Zachowuje porty, sieć, hasła, wolumeny,
mocowania plików, politykę restartu i limity logów istniejących kontenerów. Nie kopiuje konfiguracji
z repo na serwer. Usługi z niezmienionym obrazem nie są restartowane.

Przed aktualizacją tworzy prywatny katalog `/opt/blocky-backups/DATA/`: konfiguracja, lokalne
pliki Blocky, opis kontenerów i pełny zrzut SQL MariaDB. Przed zmianą obrazu MariaDB lub Grafany
kopiuje także ich katalog danych po zatrzymaniu danego kontenera. Kopie i stare kontenery
`NAZWA-before-DATA` nie są automatycznie usuwane. Udane stare kontenery mają wyłączony autostart.
Kopie zawierają hasła i historię DNS: katalog dostępny tylko dla roota.

Aktualizacja powoduje krótkie przerwy w usługach; DNS podczas wymiany Blocky, zapis historii
podczas wymiany MariaDB. Grafana może potrzebować kilku minut na migrację bazy; skrypt czeka
do 10 minut na gotowość każdej usługi. Nie przerywaj go podczas wymiany kontenerów.

Gdy utworzenie kontenera się nie uda, przywraca nazwę i uruchamia poprzedni kontener.
Jeśli nowa wersja już wystartowała, lecz nie przejdzie kontroli gotowości, skrypt przerywa dalsze
aktualizacje i podaje ścieżkę kopii oraz nazwę starego kontenera. Nie wykonuje automatycznego
downgrade: MariaDB lub Grafana mogły już zmigrować dane. Przy takim powrocie należy odtworzyć
kopię danych z tego samego uruchomienia, a dopiero potem uruchomić poprzedni obraz.

Po aktualizacji używaj `update.sh`, nie instalatora przypiętego do Blocky v0.35.0.
Lista blokowania odświeża się niezależnie, według `refreshPeriod` (domyślnie 24h).
