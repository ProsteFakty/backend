## API rozpoznawania posłów

`POST /api/v1/politicians/recognize` · `multipart/form-data` · Swagger: `/docs` (lokalnie)

| Pole | Typ | Opis |
|---|---|---|
| `photo` | plik | JPEG/PNG/WebP, maks. 10 MB |

### Odpowiedź 200

```json
{ "faces": [ { "box": [412, 230, 318, 402], "similarity": 0.61, "politician": { "full_name": "Andrzej Adamczyk", "club": "PiS", "...": "..." } } ] }
```

| Pole | Typ | Znaczenie |
|---|---|---|
| `faces` | lista | Twarze na zdjęciu, największa pierwsza; pusta = brak twarzy |
| `box` | `[x, y, szer, wys]` | Ramka twarzy w pikselach wysłanego zdjęcia |
| `similarity` | liczba / `null` | Podobieństwo do najbliższego posła (próg 0.363, to nie procenty) |
| `politician` | obiekt / `null` | Rozpoznany poseł; `null` = nie rozpoznano |

### `politician`

| Pole | Typ | Przykład |
|---|---|---|
| `full_name` | string | Andrzej Adamczyk |
| `first_name`, `last_name` | string | Andrzej, Adamczyk |
| `club` | string? | PiS |
| `district` | string? | Kraków |
| `voivodeship` | string? | małopolskie |
| `birth_date` | string? | 1959-01-04 |
| `birth_location` | string? | Krzeszowice |
| `profession` | string? | ekonomista |
| `education` | string? | wyższe |
| `email` | string? | Andrzej.Adamczyk@sejm.pl |
| `number_of_votes` | int? | 45171 |
| `active` | bool | true (`false` = mandat wygasł) |
| `photo_url` | string | http://<backend>/api/v1/politicians/1/photo (JPEG z naszego backendu) |
| `profile_url` | string | https://www.sejm.gov.pl/Sejm10.nsf/posel.xsp?id=001&type=A |
| `mp_id`, `term` | int | 1, 10 |

`?` = może być `null`.

### Stany w UI

| Warunek | Pokaż |
|---|---|
| `faces[0].politician != null` | Karta posła |
| `faces[0].politician == null` | „Nie rozpoznano posła” |
| `faces.length == 0` | „Nie znaleziono twarzy” |

### Wyszukiwanie po nazwisku

`GET /api/v1/politicians/search?q=kowal&limit=20` → lista (nazwisko zaczynające się od frazy najpierw; polskie znaki opcjonalne)

| Pole | Typ | Przykład |
|---|---|---|
| `mp_id` | int | 1 |
| `full_name` | string | Andrzej Adamczyk |
| `club` | string? | PiS |
| `active` | bool | true |
| `photo_url` | string | http://<backend>/api/v1/politicians/1/photo (JPEG z naszego backendu) |

`q` min. 2 znaki (inaczej 422). Pusta lista = brak wyników.

### Profil posła

`GET /api/v1/politicians/{mp_id}` → ten sam obiekt co `politician` wyżej; 404 gdy brak posła.

### Błędy

| Kod | Kiedy |
|---|---|
| 400 | Plik nie jest obrazem |
| 413 | Zdjęcie > 10 MB |
| 422 | Brak pola `photo` |
| 500 | Błąd serwera |
