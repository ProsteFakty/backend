## MP profile: current and possible fields

Source: Sejm API (`https://api.sejm.gov.pl/sejm/term10`). Profile = `GET /api/v1/politicians/{mp_id}`, the same object as `politician` in the camera scan response.

### Returned now

| Field | Example |
|---|---|
| `full_name`, `first_name`, `last_name` | Andrzej Adamczyk |
| `club` | PiS |
| `district`, `voivodeship` | Kraków, małopolskie |
| `birth_date`, `birth_location` | 1959-01-04, Krzeszowice |
| `profession`, `education` | ekonomista, wyższe |
| `email` | Andrzej.Adamczyk@sejm.pl |
| `number_of_votes` | 45171 |
| `active` | true (false = mandate expired) |
| `photo_url` | Official photo, served by our backend (`/api/v1/politicians/{mp_id}/photo`) |
| `profile_url` | MP page on sejm.gov.pl |
| `mp_id`, `term` | 1, 10 |

### Possible additions

**Already fetched from `/MP`, just not passed on:**

| Sejm API field | What it is |
|---|---|
| `secondName` | Middle name |
| `districtNum` | District number |
| `oathDate` | Date of the oath (start of mandate) |
| `mandateExpiryDate`, `inactiveCause`, `waiverDesc` | When and why the mandate ended (inactive MPs only) |
| `genitiveName`, `accusativeName` | Inflected name, e.g. "Andrzeja Adamczyka" for UI texts |

**From other Sejm API endpoints (checked, working):**

| Endpoint | What we could show |
|---|---|
| `/MP/{id}/votings/stats` | Voting attendance per sitting day (votings held / missed), e.g. "took part in 94% of votes" |
| `/clubs` | Full club name and the MP's role in the club (e.g. chair) |
| `/clubs/{id}/logo` | Club logo |
