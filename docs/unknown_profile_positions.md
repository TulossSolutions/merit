# Players without verified profile positions

Production checked: 30 September 2026 at 21:41 (Europe/Paris).

There are 69 affected database player records across 96 season entries. Repeated names/IDs across seasons need only one position correction per API ID.

Accepted positions: **Goalkeeper**, **Defender**, **Midfielder**, **Attacker**. Fill the final column or send corrections as `469 = Defender`. Use the API ID, not the name alone: a provider may have more than one ID for the same name.

## Important identity warning

**Do not assign a position to API ID `0`.** The database record named Jose Marroquín contains appearances for unrelated clubs and national teams. For example, its 2019/20 rows span Cagliari, Celta Vigo, Leganes and Valladolid; its 2026/27 rows span multiple national teams and clubs. It is not safe to interpret this as one identifiable player. Resolving the original fixture-level identities is a separate repair; no such repair or position assignment has been performed.

Bryan Mbeumo in 2017/18 has API ID `90588` (Estac Troyes), distinct from the verified current-player ID `20589`. This report preserves the stored provider IDs and does not silently merge records.

## How to read the list

- **Published v1.4**: the list was checked against the exact UNKNOWN score records and frozen exclusion count of that season's latest public snapshot.
- **Loaded data only**: no profile-based snapshot is public yet; the list is a preview from currently loaded, finished, covered matches.
- Zero-minute records are included because the notice counts retained participation rows, including unused substitutes. A corrected position alone does not guarantee ranking eligibility.
- Unless marked ID 0, the retained profile exists but its position field is unavailable. No positions are inferred from match roles.
- Backfill is running, so future imports or publications may add records. This is a point-in-time report, not a claim that every season is completely loaded.
- No API calls, player corrections, scoring changes, snapshot replacements, commits or deployments were made to prepare this report.

## Season summary

| Season | Records | Publication status |
| --- | ---: | --- |
| 2014/15 | 0 | Loaded data only |
| 2015/16 | 0 | Loaded data only |
| 2016/17 | 7 | Published v1.4 |
| 2017/18 | 9 | Loaded data only |
| 2018/19 | 10 | Loaded data only |
| 2019/20 | 6 | Loaded data only |
| 2020/21 | 12 | Published v1.4 |
| 2021/22 | 19 | Published v1.4 |
| 2022/23 | 8 | Published v1.4 |
| 2023/24 | 6 | Published v1.4 |
| 2024/25 | 4 | Published v1.4 |
| 2025/26 | 9 | Loaded data only |
| 2026/27 | 6 | Published v1.4 |

## Per-season correction lists

### 2014/15

**0 records.** Loaded data only; no public profile-based snapshot yet.

None in the currently loaded covered-match data.

### 2015/16

**0 records.** Loaded data only; no public profile-based snapshot yet.

None in the currently loaded covered-match data.

### 2016/17

**7 records.** Published v1.4, snapshot #35; cutoff 12 June 2017 at 01:59 (Europe/Paris).

| API ID | Player | Teams in this season | Minutes | Problem | Correct position |
| --- | --- | --- | ---: | --- | --- |
| 469 | Benedikt Höwedes | FC Schalke 04 | 2790 | Profile position unavailable | Defender |
| 91377 | Gustavo Gómez | AC Milan | 1083 | Profile position unavailable | Defender |
| 87163 | Marko Baša | Lille | 1955 | Profile position unavailable | Defender |
| 35834 | Nicolás Pareja | Sevilla | 2705 | Profile position unavailable | Defender |
| 104624 | Samu García | Leganes | 526 | Profile position unavailable | Attacker |
| 104574 | Vasily Berezutskiy | CSKA Moscow | 405 | Profile position unavailable | Defender |
| 117 | William Vainqueur | Marseille | 2550 | Profile position unavailable | Midfielder |

### 2017/18

**9 records.** Loaded data only; no public profile-based snapshot yet.

| API ID | Player | Teams in this season | Minutes | Problem | Correct position |
| --- | --- | --- | ---: | --- | --- |
| 90583 | Amine Gouiri | Lyon | 76 | Profile position unavailable | Attacker |
| 469 | Benedikt Höwedes | Juventus | 248 | Profile position unavailable | Defender |
| 90588 | Bryan Mbeumo | Estac Troyes | 60 | Profile position unavailable | Attacker |
| 96842 | Dylan Vente | Feyenoord | 19 | Profile position unavailable | Attacker |
| 35834 | Nicolás Pareja | Sevilla | 765 | Profile position unavailable | Defender |
| 104624 | Samu García | Levante, Malaga | 358 | Profile position unavailable | Attacker |
| 49846 | Serdar Taşçı | Spartak Moscow | 450 | Profile position unavailable | Defender |
| 46767 | Sergio Sánchez | Espanyol | 102 | Profile position unavailable | Defender |
| 104574 | Vasily Berezutskiy | CSKA Moscow | 630 | Profile position unavailable | Defender |

### 2018/19

**10 records.** Loaded data only; no public profile-based snapshot yet.

| API ID | Player | Teams in this season | Minutes | Problem | Correct position |
| --- | --- | --- | ---: | --- | --- |
| 73806 | Ally Mtoni | Tanzania | 90 | Profile position unavailable | Defender |
| 469 | Benedikt Höwedes | Lokomotiv Moscow | 450 | Profile position unavailable | Defender |
| 122533 | Bernard Ochieng | Kenya | 45 | Profile position unavailable | Defender |
| 74066 | David Mwantika | Tanzania | 232 | Profile position unavailable | Defender |
| 91377 | Gustavo Gómez | Paraguay | 270 | Profile position unavailable | Defender |
| 0 | Jose Marroquín | Amiens, Fulham | 91 | Invalid identity placeholder; do not assign | Identity repair needed |
| 105790 | Josep Gomes | andorra | 360 | Profile position unavailable | Goalkeeper |
| 105788 | Kire Ristevski | FYR Macedonia | 54 | Profile position unavailable | Defender |
| 105792 | Teitur Matras Gestsson | Faroe Islands | 180 | Profile position unavailable | Goalkeeper |
| 117 | William Vainqueur | Monaco | 78 | Profile position unavailable | Midfielder |

### 2019/20

**6 records.** Loaded data only; no public profile-based snapshot yet.

| API ID | Player | Teams in this season | Minutes | Problem | Correct position |
| --- | --- | --- | ---: | --- | --- |
| 469 | Benedikt Höwedes | Lokomotiv Moscow | 540 | Profile position unavailable | Defender |
| 0 | Jose Marroquín | Cagliari, Celta Vigo, Leganes, Valladolid | 1774 | Invalid identity placeholder; do not assign | Identity repair needed |
| 105790 | Josep Gomes | andorra | 537 | Profile position unavailable | Goalkeeper |
| 90581 | Opa Nguette | Metz | 1841 | Profile position unavailable | Midfielder |
| 156480 | Victor Gómez Perea | Espanyol | 887 | Profile position unavailable | Defender |
| 117 | William Vainqueur | Toulouse | 1723 | Profile position unavailable | Midfielder |

### 2020/21

**12 records.** Published v1.4, snapshot #34; cutoff 31 July 2021 at 01:59 (Europe/Paris).

| API ID | Player | Teams in this season | Minutes | Problem | Correct position |
| --- | --- | --- | ---: | --- | --- |
| 300732 | Alvaro Verwey | Suriname | 8 | Profile position unavailable | Attacker |
| 214072 | Dimitri Ramothe | Guadeloupe | 86 | Profile position unavailable | Attacker |
| 0 | Jose Marroquín | El Salvador, Guatemala, Haiti, Honduras, Trinidad and Tobago | 100 | Invalid identity placeholder; do not assign | Identity repair needed |
| 105790 | Josep Gomes | andorra | 540 | Profile position unavailable | Goalkeeper |
| 214065 | Kevin Moeson | Guadeloupe | 96 | Profile position unavailable | Defender |
| 319581 | Kwesi Paul | Grenada | 0 | Profile position unavailable | Defender |
| 286350 | Martín Satriano | Inter | 0 | Profile position unavailable | Attacker |
| 321592 | Morgan Saint-Maximin | Guadeloupe | 242 | Profile position unavailable | Midfielder |
| 321594 | Skeveen Romage | Guadeloupe | 8 | Profile position unavailable | Attacker |
| 214198 | Stevenson Casimir | Guadeloupe | 180 | Profile position unavailable | Defender |
| 321593 | Thomas Pineau | Guadeloupe | 45 | Profile position unavailable | Defender |
| 214106 | Vikash Tillé | Guadeloupe | 82 | Profile position unavailable | Midfielder |

### 2021/22

**19 records.** Published v1.4, snapshot #33; cutoff 15 June 2022 at 01:59 (Europe/Paris).

| API ID | Player | Teams in this season | Minutes | Problem | Correct position |
| --- | --- | --- | ---: | --- | --- |
| 350494 | Amjed Ismael | Sudan | 0 | Profile position unavailable | Defender |
| 175478 | Charles Thom | Malawi | 180 | Profile position unavailable | Goalkeeper |
| 124291 | Chikoti Chirwa | Malawi | 0 | Profile position unavailable | Midfielder |
| 124113 | Ernest Kakhobwe | Malawi | 180 | Profile position unavailable | Goalkeeper |
| 331829 | Jalal Huseynov | Azerbaijan | 270 | Profile position unavailable | Defender |
| 0 | Jose Marroquín | Brondby | 25 | Invalid identity placeholder; do not assign | Identity repair needed |
| 339850 | Mariano Magno Mba | Equatorial Guinea | 0 | Profile position unavailable | Goalkeeper |
| 321637 | Mark Fodyah | Malawi | 12 | Profile position unavailable | Defender |
| 286350 | Martín Satriano | Inter, Stade Brestois 29 | 982 | Profile position unavailable | Attacker |
| 350498 | Mohamed Hassoiun | Sudan | 71 | Profile position unavailable | Midfielder |
| 309167 | Mustafa Elfadni | Sudan | 270 | Profile position unavailable | Midfielder |
| 309164 | Paul Ndhlovu | Malawi | 0 | Profile position unavailable | Defender |
| 154983 | Peter Cholopi | Malawi | 0 | Profile position unavailable | Defender |
| 169210 | Rafael Grünenfelder | Liechtenstein | 360 | Profile position unavailable | Defender |
| 267991 | Ross Doohan | Celtic | 0 | Profile position unavailable | Goalkeeper |
| 350804 | Sharif Omar Abdalla Makki | Sudan | 22 | Profile position unavailable | Midfielder |
| 122519 | Sheikh Sibi | Gambia | 0 | Profile position unavailable | Goalkeeper |
| 175487 | Stain Davie | Malawi | 11 | Profile position unavailable | Attacker |
| 291481 | Teklemariam Shanko | Ethiopia | 270 | Profile position unavailable | Goalkeeper |

### 2022/23

**8 records.** Published v1.4, snapshot #32; cutoff 17 July 2023 at 01:59 (Europe/Paris).

| API ID | Player | Teams in this season | Minutes | Problem | Correct position |
| --- | --- | --- | ---: | --- | --- |
| 214072 | Dimitri Ramothe | Guadeloupe | 0 | Profile position unavailable | Attacker |
| 331829 | Jalal Huseynov | Azerbaijan | 0 | Profile position unavailable | Defender |
| 0 | Jose Marroquín | Canada, Costa Rica, Cuba, Guatemala, Honduras, Reims | 190 | Invalid identity placeholder; do not assign | Identity repair needed |
| 286350 | Martín Satriano | Empoli | 878 | Profile position unavailable | Attacker |
| 396757 | Mateo Fernandez | Leeds | 2 | Profile position unavailable | Attacker |
| 169210 | Rafael Grünenfelder | Liechtenstein | 90 | Profile position unavailable | Defender |
| 423871 | Steven Davidas | Guadeloupe | 29 | Profile position unavailable | Midfielder |
| 214198 | Stevenson Casimir | Guadeloupe | 0 | Profile position unavailable | Defender |

### 2023/24

**6 records.** Published v1.4, snapshot #31; cutoff 16 July 2024 at 01:59 (Europe/Paris).

| API ID | Player | Teams in this season | Minutes | Problem | Correct position |
| --- | --- | --- | ---: | --- | --- |
| 453072 | Allan Omeer | Iraq | 67 | Profile position unavailable | Defender |
| 0 | Jose Marroquín | KI Klaksvik, Swift Hesperange, Valmiera / BSS | 54 | Invalid identity placeholder; do not assign | Identity repair needed |
| 316888 | Nuriddin Khamrokulov | Tajikistan | 91 | Profile position unavailable | Attacker |
| 451311 | Rayane Belaid Khellil | Atletico Madrid | 0 | Profile position unavailable | Midfielder |
| 291472 | Salomón Obama | Equatorial Guinea | 0 | Profile position unavailable | Attacker |
| 440958 | Yousef Abu Jalboush | Jordan | 86 | Profile position unavailable | Midfielder |

### 2024/25

**4 records.** Published v1.4, snapshot #29; cutoff 7 July 2025 at 01:59 (Europe/Paris).

| API ID | Player | Teams in this season | Minutes | Problem | Correct position |
| --- | --- | --- | ---: | --- | --- |
| 0 | Jose Marroquín | El Salvador, Guatemala, Honduras | 52 | Invalid identity placeholder; do not assign | Identity repair needed |
| 438606 | Keyvan Beaumont | Guadeloupe | 0 | Profile position unavailable | Defender |
| 496425 | Lucas García | Girona | 0 | Profile position unavailable | Goalkeeper |
| 214106 | Vikash Tillé | Guadeloupe | 19 | Profile position unavailable | Midfielder |

### 2025/26

**9 records.** Loaded data only; no public profile-based snapshot yet.

| API ID | Player | Teams in this season | Minutes | Problem | Correct position |
| --- | --- | --- | ---: | --- | --- |
| 639289 | Axel Lassus | Stade Brestois 29 | 0 | Profile position unavailable | Midfielder |
| 569021 | Dani Meso | Real Madrid | 11 | Profile position unavailable | Midfielder |
| 625744 | Izei Hernandez | Alaves | 0 | Profile position unavailable | Attacker |
| 0 | Jose Marroquín | Gibraltar, Real Madrid, Slavia Praha | 0 | Invalid identity placeholder; do not assign | Identity repair needed |
| 461595 | Mohamed Benoit-Dao | Paris FC | 9 | Profile position unavailable | Attacker |
| 362187 | Mohamed Kasri | Sudan | 81 | Profile position unavailable | Defender |
| 659101 | Mor Talla Ndiaye | Liverpool | 0 | Profile position unavailable | Defender |
| 314882 | Saad Al Rousan | Jordan | 9 | Profile position unavailable | Defender |
| 610955 | Salvatore Cianciulli | Fiorentina | 0 | Profile position unavailable | Midfielder |

### 2026/27

**6 records.** Published v1.4, snapshot #30; cutoff 30 September 2026 at 01:59 (Europe/Paris).

| API ID | Player | Teams in this season | Minutes | Problem | Correct position |
| --- | --- | --- | ---: | --- | --- |
| 625744 | Izei Hernandez | Alaves | 0 | Profile position unavailable | Attacker |
| 0 | Jose Marroquín | Alaves, Albania, Auxerre, Azerbaijan, Bodo/Glimt, Estac Troyes, FYR Macedonia, Feyenoord, Inter Club d'Escaldes, Kosovo, Liechtenstein, Moldova, Omonia Nicosia, RB Leipzig, San Marino, Shakhtar Donetsk, Stade Brestois 29, Toulouse, andorra | 578 | Invalid identity placeholder; do not assign | Identity repair needed |
| 677159 | Jules Bouffandeau | Angers | 0 | Profile position unavailable | Midfielder |
| 668535 | Rael Nakuzola | FSV Mainz 05 | 18 | Profile position unavailable | Defender |
| 585484 | Rodrigo Gamón | Valencia | 0 | Profile position unavailable | Defender |
| 670620 | Yacouba Kone | Estac Troyes | 41 | Profile position unavailable | Attacker |
