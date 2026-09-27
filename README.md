# BlockySetup — serwer DNS blocky dla sieci lokalnej

Kompletna konfiguracja [blocky](https://github.com/0xERR0R/blocky) (DNS proxy + blokowanie reklam) dla Raspberry Pi 5 (8 GB) z DietPi / dowolnego systemu debianopodobnego (Debian, Ubuntu, Raspberry Pi OS).

## Co zawiera to repo

| Plik | Opis |
|---|---|
| `config.yml` | Konfiguracja blocky (v0.35.0, zweryfikowana względem oficjalnego JSON-schema) |
| `blocky.service` | Hardened unit systemd (użytkownik `blocky`, tylko `CAP_NET_BIND_SERVICE`) |
| `README.md` | Niniejsza instrukcja instalacji |
| `MONITORING.md` | Instrukcja monitoringu: Prometheus + Grafana (przeglądanie z innego komputera) |
| `monitoring/prometheus.yml` | Gotowa konfiguracja scrape blocky dla Prometheusa |

## Co robi ta konfiguracja

- **Upstream DoH** (szyfrowane DNS): Cloudflare (najszybszy w Polsce), Google, Quad9, CZ.NIC ODVR, Digitale Gesellschaft — strategia `parallel_best` (2 losowe serwery na zapytanie, wygrywa najszybsza odpowiedź)
- **Blokowanie reklam** — listy:
  - **oisd big** — `https://big.oisd.nl/` (~243 tys. domen, format Adblock Plus)
  - **HaGezi Pro** — reklamy, trackery, telemetria, phishing, malware (~229 tys. domen)
  - **HaGezi TIF** (Threat Intelligence Feeds) — dodatkowa warstwa bezpieczeństwa
  - **HaGezi Gambling** — hazard (domyślnie wyłączony, patrz `config.yml`)
- **DNSSEC** — walidacja kryptograficzna odpowiedzi
- **Ochrona przed DNS rebinding** — odpowiedzi z prywatnymi adresami IP z internetu są blokowane
- **Cache z prefetchingiem** — szybkie odpowiedzi na częste zapytania
- **Odrzucanie nie-FQDN**, prywatne TLD (RFC 6762), EDE (Extended DNS Errors)
- **Metrics Prometheus** — `http://<ip-rpi>:4000/metrics`
- **Cache list na dysku** — `/var/cache/blocky/lists` (radzi sobie, gdy serwer list chwilowo nie odpowiada)

## Instalacja na DietPi / Debian / Ubuntu

### 1. Pobierz pliki z repo na Raspberry Pi

```sh
git clone https://github.com/stainleSSStar/BlockySetup.git
cd BlockySetup
```

### 2. Utwórz użytkownika i katalogi

```sh
sudo useradd --system --no-create-home --shell /usr/sbin/nologin blocky
sudo mkdir -p /etc/blocky /var/cache/blocky
sudo chown blocky:blocky /var/cache/blocky
```

### 3. Pobierz binarkę blocky (arm64 dla Raspberry Pi 5)

```sh
BLOCKY_VERSION="v0.35.0"
curl -sL -o /tmp/blocky.tar.gz \
  "https://github.com/0xERR0R/blocky/releases/download/${BLOCKY_VERSION}/blocky_${BLOCKY_VERSION}_Linux_arm64.tar.gz"
tar -xzf /tmp/blocky.tar.gz -C /tmp
sudo install -m 0755 /tmp/blocky /usr/local/bin/blocky
blocky version
```

### 4. Zainstaluj konfigurację i usługę systemd

```sh
sudo install -m 0644 config.yml /etc/blocky/config.yml
sudo install -m 0644 blocky.service /etc/systemd/system/blocky.service
```

Jeśli chcesz dostosować konfigurację (np. własne urządzenia w `customDNS`), edytuj `/etc/blocky/config.yml`:

```sh
sudo nano /etc/blocky/config.yml
```

### 5. Zweryfikuj konfigurację przed startem

```sh
blocky validate --config /etc/blocky/config.yml
```

Dopóki polecenie nie zwróci sukcesu, nie startuj usługi — dzięki temu unikniesz problemów z błędnym znakiem w YAML.

### 6. Uruchom i włącz autostart

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now blocky
sudo systemctl status blocky
```

### 7. Przekieruj DNS sieci na Raspberry Pi

W routerze (opcja 1 — zalecana) ustaw serwer DNS DHCP na adres IP Raspberry Pi.
Alternatywnie (opcja 2) na każdym urządzeniu ręcznie ustaw DNS na IP Raspberry Pi.

**Ważne (DietPi):** aby blocky mógł bindować port 53, lokalny resolver `systemd-resolved`/`dnsmasq` nie może go zajmować. Na DietPi sprawdź:

```sh
sudo ss -lntup | grep :53
```

Jeśli coś nasłuchuje na 53, wyłącz to w `dietpi-services` lub odinstaluj `dnsmasq` (`sudo apt remove dnsmasq`). Dodatkowo w DietPi ustaw statyczne IP i własny DNS (np. 1.1.1.1) dla samego systemu — **nie** adres Raspberry Pi (pętla!).

## Weryfikacja działania

```sh
# Zwykłe zapytanie (powinno zwrócić adresy)
dig @<IP_RPI> google.com

# Domena reklamowa (powinna zwrócić 0.0.0.0)
dig @<IP_RPI> doubleclick.net
```

Statystyki i API (patrz [docs/api](https://github.com/0xERR0R/blocky/blob/main/docs/api/openapi.yaml)): `http://<IP_RPI>:4000/api/stats`, `http://<IP_RPI>:4000/api/blocking/status`, `http://<IP_RPI>:4000/api/cache/flush`

## Aktualizacja list i restarty

- Listy odświeżają się automatycznie co 24 h (`blocking.loading.refreshPeriod`).
- Po zmianie konfiguracji: `sudo systemctl restart blocky`
- Po aktualizacji binarki: `sudo systemctl restart blocky`

## Blokowanie hazardu (opcjonalnie)

W `/etc/blocky/config.yml` w sekcji `blocking.clientGroupsBlock` odkomentuj:

```yaml
    default:
      - ads
      - security
      - gambling
```

## Aktualizacja blocky

```sh
BLOCKY_VERSION="v0.35.1"   # sprawdź najnowszą na stronie releases
curl -sL -o /tmp/blocky.tar.gz \
  "https://github.com/0xERR0R/blocky/releases/download/${BLOCKY_VERSION}/blocky_${BLOCKY_VERSION}_Linux_arm64.tar.gz"
tar -xzf /tmp/blocky.tar.gz -C /tmp
sudo install -m 0755 /tmp/blocky /usr/local/bin/blocky
sudo systemctl restart blocky
```

## Rozwiązywanie problemów

| Objaw | Rozwiązanie |
|---|---|
| `bind: address already in use` | Coś zajmuje port 53 (`sudo ss -lntup \| grep :53`) — wyłącz dnsmasq/systemd-resolved |
| `permission denied` przy starcie | Sprawdź `AmbientCapabilities=CAP_NET_BIND_SERVICE` w unit file oraz czy usługa działa jako użytkownik `blocky` |
| Listy się nie pobierają | Sprawdź `journalctl -u blocky -e`; katalog `/var/cache/blocky` musi naleść do `blocky:blocky` |
| Niektóre strony nie działają | Dodaj domenę do allowlisty w `blocking.allowlists` lub usuń listę `security` z `clientGroupsBlock` |
| Wolne odpowiedzi | Sprawdź `http://<IP_RPI>:4000/api/status` i logi; rozważ zmianę `strategy: random` w `upstreams` (mniej ruchu do 2 dostawców naraz) |

## Alternatywa: Docker (opcjonalnie)

Jeśli wolisz Dockera zamiast systemd:

```sh
docker run -d --name blocky \
  --restart unless-stopped \
  -p 53:53/udp -p 53:53/tcp -p 4000:4000 \
  -v /etc/blocky/config.yml:/app/config.yml:ro \
  -v blocky_cache:/app/cache \
  spx01/blocky:latest
```

## Odinstalowanie

```sh
sudo systemctl disable --now blocky
sudo rm /etc/systemd/system/blocky.service /etc/blocky/config.yml /usr/local/bin/blocky
sudo rm -rf /etc/blocky /var/cache/blocky
sudo userdel blocky
sudo systemctl daemon-reload
```
