# Aktualizacje firmware i odzyskiwanie

HACS dostarcza kod integracji. Firmware pochodzi z zatwierdzonego, prywatnego
procesu wydawania. HA potrzebuje wyłącznie podpisanych plików `.ufu` i publicznego
klucza weryfikacji — bez kodu Rust, narzędzi provisioningu i klucza podpisującego.

Katalog: `/config/ufh_heating/releases/ID_PARTERU/`. Integracja tworzy go po
konfiguracji. Nazwy: `main-floor-0.2.1.ufu`, `upper-floor-0.2.1.ufu`.
Każdy plik zawiera manifest UFU1 + kompletny obraz aplikacji ESP32-C6.
Nie kopiuj surowego `.bin`, bootloadera ani obrazu całego flash.

Nowe pliki są sprawdzane co 30 s. Następnie standardowa encja firmware pokazuje
nowszą wersję; **Zainstaluj** lub akcja HA `update.install` rozpoczyna instalację.
Wersja musi być wyższa, podpis pasować do danej płytki, a obraz do jej roli,
profilu i układu A/B. Uszkodzone najnowsze wydanie powoduje widoczny błąd, zamiast
po cichu wybierać starszy plik. Maksymalnie 64 pliki na rolę; starsze archiwizuj.
Pliki instalacyjne przechowuj tylko w zaufanym katalogu HA.

Przebieg: wyłączenie żądań wszystkich stref → potwierdzenie CO OFF i końca wybiegu
→ zapis nieaktywnego slotu blokami po 128 B → hash/podpis → rozruch próbny
→ uwierzytelnione potwierdzenie → kolejny, normalny rozruch. Dopiero ostatni krok
oznacza sukces. Akcja HA oczekuje na końcowy wynik. Po instalacji strefy pozostają
wyłączone; świadomie przywróć żądane tryby.

Jedna instalacja naraz; przy aktualizacji obu płyt najpierw piętro, potem parter.
Postęp przesyłania obejmuje pierwsze 90%, następnie rozruch i potwierdzenie.
Granice: ACK bloku do 8 s, rozruch do 200 s, cała praca do 30 min. Błąd lub
rozłączenie pokazuje niepowodzenie; nie interpretuj wysłania ostatniego bloku
jako gotowej aktualizacji. Po przerwaniu najpierw odczytaj rzeczywistą wersję.

Pierwszy firmware 0.2.0 wymaga poprawnego bootloadera z rollback i tabeli 4 MB.
Integracja ich nie instaluje. OTA nie zastępuje odbioru fizycznych testów zaniku
zasilania. Nie wykonuje zmiany Wi-Fi/brokera ani wypalania eFuse. Jeśli żadna
zapisana sieć nie działa, użyj prywatnych narzędzi odzyskiwania przez USB.
