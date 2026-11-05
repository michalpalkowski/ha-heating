# UFH Heating — Home Assistant / HACS

![UFH Heating](brand/icon.png)

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

Wymagane: HA **2026.10.2+**, działająca integracja MQTT, firmware **0.3.0+** do sterowania; **0.2.0** jest obsługiwane tylko do migracji OTA.
Najpierw zatrzymaj samodzielną bramkę `bridge`. Nie mogą sterować równocześnie.

Po instalacji strefy są wyłączone. Zgoda operatora `operation_enabled` jest
osobnym, trwałym ustawieniem sterownika, zmienianym uwierzytelnioną transakcją.
Nie zmienia informacji o wykonaniu pomiarów elektrycznych ani montażu odcięcia.
Wybierz jawnie interfejs wyjść (`direct_low_3v3` / `inverting_buffer`) i użyj
akcji `ufh_heating.set_operation`, następnie włącz wybrane termostaty.
Panel może udostępniać tę akcję jako zwykły przycisk z potwierdzonym stanem.

## Co pojawia się w HA

| Encja | Informacja / działanie |
| --- | --- |
| Termostat każdej wybranej strefy | Temperatura aktualna i zadana, OFF/HEAT, potwierdzona praca |
| Obiegi podłogówki | Żądanie grzania, stan wysterowania GPIO i faza — bez udawania pomiaru przepływu |
| Żądanie ciepła | Tylko świeże, uwierzytelnione i potwierdzone żądanie sterownika |
| Diagnostyka | Zgoda na pracę, niezależne fakty instalacyjne, faza, błąd, błędy RS485, odrzucone wiadomości, konflikt bramek |
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

## Potwierdzenie CO i wybieg — 0.2.3

Integracja odczytuje rzeczywisty stan przypisanego przełącznika HA przed każdym
pakietem sterowania, zamiast wysyłać starszy odczyt z kończącą się ważnością.
Odświeżanie sterowania odbywa się co 0,5 s. Nie zwiększono limitu ważności
potwierdzenia CO (3 s), zezwoleń właściciela (3 s) ani czasów firmware.
Odczyt OFF nie przedłuża zezwolenia na grzanie. Stan nieznany, niedostępny,
odtworzony albo brak encji nie staje się OFF. Po rzeczywistej utracie
potwierdzenia urządzenie nadal przerywa odliczanie wybiegu.
To raport stanu przełącznika w HA, nie pomiar pracy palnika lub pompy.
