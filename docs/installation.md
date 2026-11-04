# Instalacja i przekazanie sterowania

## Wymagania i własność sterowania

Integracja jest klientem istniejącego UFH1. Nie zastępuje firmware ani układów
zabezpieczeń. Wersja 0.2 obsługuje jednego głównego sterownika na broker, stały
prefiks `heating/house/v1`, dziewięć stref oraz 6 + 8 obiegów. Nie jest uniwersalnym
sterownikiem dowolnych modułów przekaźnikowych.

Zatrzymaj usługę samodzielnej bramki przed dodaniem integracji. Usuń jej stare
encje MQTT Discovery dopiero po zidentyfikowaniu konkretnych wpisów; integracja
nie kasuje cudzych encji. Nie uruchamiaj jednocześnie dwóch klientów sterujących.
Konflikt po uzyskaniu własnego potwierdzenia blokuje publikowanie nowych żądań
aż do usunięcia przyczyny i przeładowania integracji.

Kreator pyta o pola z prywatnego pliku instalacyjnego:

| Pole HA | Prywatna konfiguracja bramki |
| --- | --- |
| device_id | management.nodes[node=1].device_id_hex |
| control_key | mqtt_hmac_key_hex |
| management_key | management.hmac_key_hex |
| public_key | management.nodes[node=1].ota_public_key_hex |
| upper_device_id | management.nodes[node=2].device_id_hex, jeśli zamontowane |
| upper_public_key | management.nodes[node=2].ota_public_key_hex |

HA przechowuje te dane w swoim magazynie wpisów konfiguracji. Chroń dostęp do HA
oraz jego kopii zapasowych; dane nie są dodatkowo szyfrowane przez tę integrację.
Diagnostyka eksportuje wyłącznie jawnie wskazane, niesekretne pola.
Zmiana kluczy klienta: menu integracji **Zmień konfigurację**; oba HMAC trzeba
podać ponownie. To nie zmienia konfiguracji Wi-Fi/MQTT ani kluczy w urządzeniu.

## Strefy i kanały

| Nr | Slot protokołu | Obiegi |
| --- | --- | --- |
| 1 | ground_bathroom | Parter 1 |
| 2 | office | Parter 2 |
| 3 | living | Parter 3, 4, 5 |
| 4 | kitchen_hall_boiler | Parter 6 |
| 5 | wardrobe | Piętro 1 |
| 6 | bedroom | Piętro 2, 3 |
| 7 | bedroom_right | Piętro 4, 5 |
| 8 | bedroom_left | Piętro 6, 7 |
| 9 | upper_bathroom_hall | Piętro 8 |

Nazwy slotów i zgrupowanie są częścią protokołu firmware 0.2. Kanały w opcjach
muszą odpowiadać provisioningowi: domyślnie `[0,1,2,3,4,5]` i
`[0,1,2,3,4,5,6,7]`. Zmiana mapy na panelu nie przestawia przewodów ani
konfiguracji MCU. Niewykorzystane wyjścia nie dostają oddzielnego sterowania.

Każda strefa potrzebuje własnego czujnika referencyjnego. Wybierz go w opcjach.
Wspólnych obiegów kilku pomieszczeń nie da się regulować niezależnie zaworem.
Nie wybieraj stref piętra, zanim nie dodasz jego tożsamości i nie uruchomisz RS485.
Po zmianie opcji wszystkie żądania grzania są wyłączane.

## Świeżość temperatury

Przypisany czujnik HA zasila strefę nowymi zdarzeniami raportowania, również gdy
wartość się nie zmieniła. `unknown`, `unavailable`, brak jednostki lub błędny
pomiar natychmiast odbierają ważność poprzedniej próbce. Obsługiwane °C i °F.
Przy starcie istniejący odczyt jest kwalifikowany z jego oryginalnym
`last_reported`: zachowuje rzeczywisty wiek i pozostały czas ważności.
Wartości odtworzone (`restored`), przeterminowane i błędne nie są przyjmowane.
Ponowny start ani okresowy tick nie zamieniają starej próbki w nowy pomiar.

Maksymalny wiek pomiaru ustaw w opcjach, 1–900 s. Dobierz go do faktycznej
częstotliwości raportowania czujnika. Czas raportu HA nie dowodzi świeżości
fizycznego pomiaru w urządzeniu: integracja czujnika musi rzetelnie raportować.
Nie używaj template-sensora, który okresowo przepisuje nieaktualną wartość.

Alternatywnie pozostaw czujnik HA nieprzypisany i dostarczaj surowe, rzeczywiste
raporty MQTT na `heating/house/v1/temperature/SLOT`, bez retain:

```json
{"schema":1,"sensor_id":"office","event_id":"sensor-boot:42","reported_at_unix_ms":1700000000000,"temperature_centi_c":2150,"quality":"valid"}
```

Znacznik czasu w przykładzie jest fikcyjny. Nadajnik musi użyć faktycznego czasu
pomiaru i nowego event_id. `quality="fault"` wymaga `temperature_centi_c=null`.
Nie odświeżaj czasu starego raportu. Buforowane, ponowione i przeterminowane
wiadomości są odrzucane.

## Kocioł: jeden właściciel i jawna zgoda na źródło

Koordynator HA przejmuje sterowanie CO wyłącznie po wybraniu trybu strefowego.
Obserwuje encję `heat_request` UFH, wymaga jej aktualizacji co najwyżej 3 s i
wyłącza CO przy niedostępności lub przeterminowaniu. Nie uruchamiaj równolegle
starego regulatora z histerezą temperatury pokoju ani samodzielnej bramki.

Co sekundę koordynator (kontekst systemowy) wywołuje `ufh_heating.report_source`:
`controller_id`, `entity_id` rzeczywistego przełącznika CO oraz `authority`.
Integracja sama odczytuje stan encji. Przy AC, ręcznym sterowaniu klimatyzacją,
wyłączeniu regulatora lub poza sezonem zgoda musi wynosić false.
`unknown` i `unavailable` nie są potwierdzeniem CO OFF. Nie publikuj stanów
na MQTT `boiler/state`: nowy klient ich nie konsumuje.

MCU zapewnia kolejność zawory → czas otwierania → CO oraz CO OFF → wybieg →
zamknięcie. Samo kliknięcie termostatu nie potwierdza grzania ani przepływu.

## Zezwolenie operatora

Na firmware 0.3+ akcja `ufh_heating.set_operation` przyjmuje `controller_id`,
`enabled` oraz `relay_profile` (`direct_low_3v3` albo `inverting_buffer`).
Użytkownik musi mieć uprawnienie CONTROL do encji zgody sterownika. Transakcja
wstrzymuje strefy, czeka na CO OFF i wybieg, zapisuje dziennik, restartuje,
potwierdza rozruch próbny i dopiero po normalnym rozruchu pokazuje nowy stan.
Po niej włącz świadomie wybrane strefy. Nastawy temperatur są zapamiętywane.

`electrical_tested` i `external_cutoff_wired` pozostają osobnymi informacjami
instalacyjnymi; akcja nie ustawia ich na true i nie wymaga fikcyjnego odbioru.
Profil wyjść musi odpowiadać rzeczywistemu interfejsowi. Oprogramowanie nie
zastępuje poprawnego zasilania wejść, doboru izolacji i zabezpieczeń instalacji.
