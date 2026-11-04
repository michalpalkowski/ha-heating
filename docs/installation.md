# Instalacja i przekazanie sterowania

## Wymagania i własność sterowania

Integracja jest klientem istniejącego UFH1. Nie zastępuje firmware ani układów
zabezpieczeń. Wersja 0.1 obsługuje jednego głównego sterownika na broker, stały
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

## Kocioł: osobna, kontrolowana dzierżawa

To jawny punkt integracji z instalacją: standardowy termostat domowy sterujący
kotłem musi oddać uprawnienie do sterowania przed uruchomieniem podłogówki UFH.
Nie łącz dwóch termostatów, które niezależnie włączają ten sam przekaźnik kotła.

UFH publikuje co sekundę `heating/house/v1/boiler/request`:

```json
{"schema":1,"request":"off","expires_at_unix_ms":1700000002500}
```

Automatyzacja kotła musi sprawdzić schemat i termin (najwyżej 3000 ms w przyszłość),
mieć własne jawne zezwolenie, sprawdzić że poprzedni regulator jest wyłączony,
oraz wyłączyć przejęte CO po wygaśnięciu dzierżawy najpóźniej po 3 sekundach.
Restart HA i cofnięcie zezwolenia również odbierają przejęte CO.
Wbudowane automatyzacje domu mogą nadal korzystać z identycznego kontraktu.

Na `heating/house/v1/boiler/state` dostarczaj co najwyżej co 1 s, bez retain:

```json
{"schema":1,"state":"off","reported_at_unix_ms":1700000000000}
```

`state` jest `on`, `off` albo `unknown`, i ma opisywać potwierdzony stan
przełącznika kotła pod kontrolą aktualnego właściciela. Nie dowodzi pracy palnika.
Bez uprawnienia lub poprawnego stanu raportuj `unknown`. MCU wymaga świeżego
potwierdzenia, obsługuje kolejność otwarcie zaworów → CO oraz CO OFF → wybieg.
Sama encja `heat_request` bez dzierżawy i potwierdzeń nie wystarcza.

## Odbiór przed uruchomieniem

1. Potwierdź elektrycznie zasilanie, poziomy wejść, polaryzację i stan przy resecie.
2. Sprawdź niezależne od CPU odcięcie zasilania zaworów przez watchdog.
3. Zweryfikuj mapę obiegów, NC, czasy otwarcia, przepływ minimalny i wybieg.
4. W prywatnym procesie provisioningu zatwierdź profil i odbiór sprzętu.
5. Przetestuj utratę MQTT/HA/RS485 i wygaśnięcie dzierżawy CO, przy nadzorze.
6. Dopiero wtedy włącz wybrane termostaty stref.

Integracja nie ustawia flag `commissioned`, `hardware_verified`, `watchdog_wired`.
