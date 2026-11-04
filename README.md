# UFH Heating — Home Assistant / HACS

Lokalna integracja do sterownika ogrzewania podłogowego UFH1 na XIAO ESP32-C6.
Konfiguracja z interfejsu HA, termostaty stref, diagnostyka obiegów i podpisane
aktualizacje firmware przez standardowe encje `update`.

Repozytorium zawiera wyłącznie klienta protokołu i integrację. Firmware, klucze
podpisujące, dane sieci i konfiguracja konkretnego domu pozostają prywatne.
Integracja nie wymaga zewnętrznej bramki ani własnego konta w chmurze.

## Instalacja przez HACS

1. W HACS otwórz **⋮ → Repozytoria niestandardowe**.
2. Dodaj `https://github.com/michalpalkowski/ha-heating`, kategoria **Integracja**.
3. Pobierz **UFH Heating** i uruchom ponownie Home Assistant.
4. W **Ustawienia → Urządzenia i usługi → Dodaj integrację** wybierz **UFH Heating**.
5. Podaj identyfikator urządzenia, dwa osobne klucze HMAC i publiczny klucz
   podpisu firmware z prywatnej konfiguracji instalacyjnej. Opcjonalnie dodaj
   identyfikator i klucz publiczny piętra. Nie podawaj prywatnego klucza podpisu.
6. Kreator sprawdzi odbiór raportów obu protokołów przez istniejącą integrację
   MQTT. Sterownik musi być włączony i połączony z tym samym brokerem.
7. W opcjach wybierz zamontowane strefy i przypisz termometry. Przejęcie kotła
   wykonaj osobno według [instrukcji uruchomienia](docs/installation.md).

Wymagane: HA **2026.10.2+**, działająca integracja MQTT, firmware UFH1/UFC3
**0.2.0+** i zatwierdzony profil sprzętowy przed fizycznym grzaniem.
Najpierw zatrzymaj samodzielną bramkę `bridge`. Nie mogą sterować równocześnie.

Po instalacji strefy są wyłączone. Integracja nie zmienia flag odbioru sprzętu.
Na płytce z profilem `UNVERIFIED` można sprawdzać połączenie i OTA; grzanie
pozostaje zablokowane. Tożsamość w kreatorze jest sprawdzana bez wysyłania
poleceń sterujących.

## Co pojawia się w HA

| Encja | Informacja / działanie |
| --- | --- |
| Termostat każdej wybranej strefy | Temperatura aktualna i zadana, OFF/HEAT, potwierdzona praca |
| Obiegi podłogówki | Żądanie grzania, stan wysterowania GPIO i faza — bez udawania pomiaru przepływu |
| Żądanie ciepła | Tylko świeże, uwierzytelnione i potwierdzone żądanie sterownika |
| Diagnostyka | Odbiór sprzętu, faza, błąd, błędy RS485, odrzucone wiadomości, konflikt bramek |
| Aktualizacja firmware | Wersja bieżąca/dostępna, instalacja i postęp dla parteru oraz opcjonalnie piętra |

Strefy mają ogólne nazwy `Zone 1…9`; możesz nadać własne nazwy i pomieszczenia
w HA. Mapa protokołu jest w [instrukcji](docs/installation.md). Po restarcie,
przerwie komunikacji lub zmianie opcji nie ma automatycznego ponownego grzania.

## Aktualizacje

HACS aktualizuje **integrację HA**. Encja `update` aktualizuje **firmware płytki**.
To dwa osobne, wersjonowane składniki.

Umieść podpisany plik `.ufu` w
`/config/ufh_heating/releases/ID_STEROWNIKA_PARTERU/` i wybierz **Zainstaluj**
w encji aktualizacji. Szczegóły: [OTA](docs/ota.md). Podpisywanie i budowanie
obrazów odbywa się w prywatnym repozytorium firmware.

## Dokumentacja i testy

- [Instalacja, pomiary i przekazanie sterowania kotłem](docs/installation.md)
- [Kontrakt protokołu i granice bezpieczeństwa](docs/protocol.md)
- [OTA i rozwiązywanie problemów](docs/ota.md)
- [Zakres weryfikacji](docs/verification.md)
- [Polityka bezpieczeństwa](SECURITY.md)

`bash tools/check.sh` uruchamia testy w przypiętym obrazie HA, bez sieci i bez
sprzętu. Testy nie wymagają dostępu do prywatnego repozytorium, kluczy ani domu.
