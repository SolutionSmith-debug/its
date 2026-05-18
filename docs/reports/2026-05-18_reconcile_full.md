# parse_job_v3 reconcile report — Box listings

Source: /Users/sethsmith/Downloads/Box_listings_for_Seth  
Portfolios: 10  

## Global summary

Total unique folder-name strings across all portfolios: **5471**

### Claim coverage (unique names)

| Claim | Count | Share |  |
|---|---:|---:|---|
| active_subjob | 985 |  18.0% | ████░░░░░░░░░░░░░░░░░░░░ |
| portfolio_subject | 51 |   0.9% | ░░░░░░░░░░░░░░░░░░░░░░░░ |
| development_subject | 15 |   0.3% | ░░░░░░░░░░░░░░░░░░░░░░░░ |
| subsubject | 471 |   8.6% | ██░░░░░░░░░░░░░░░░░░░░░░ |
| canonical_non_job | 912 |  16.7% | ████░░░░░░░░░░░░░░░░░░░░ |
| identifiable_job | 31 |   0.6% | ░░░░░░░░░░░░░░░░░░░░░░░░ |
| unclaimed | 3006 |  54.9% | █████████████░░░░░░░░░░░ |

### Schema classification distribution

| Schema | Portfolios |
|---|---:|
| active_portfolio_modern | 7 |
| active_modern | 2 |
| active_single_project | 1 |

### Chaos-flag totals (unique names triggering each pattern)

| Pattern | Count |
|---|---:|
| person_tag_in_subject | 138 |
| pre_canonical_zero | 35 |
| double_space | 19 |
| unfilled_placeholder | 18 |
| instructional_name | 9 |
| date_prefix_lowercase | 7 |
| generic_new_folder | 6 |
| archive_letter_z | 4 |
| box_drive_copy | 2 |
| duplicate_suffix | 1 |
| exclamation_emphasis | 1 |
| sub_decimal_insert | 1 |

### Top unclaimed names (across all portfolios)

Names below survive the entire claim chain (active_subjob → portfolio_subject → development_subject → canonical_non_job → identifiable_job) without being recognized. Many are legitimate free-text folder names; the list is the parser-gap candidate pool.

| Count | Name |
|---:|---|
| 10 | `stale` |
| 9 | `FE` |
| 8 | `Archive` |
| 8 | `Quotes` |
| 8 | `Templates` |
| 7 | `Elect` |
| 7 | `ESS New Hires` |
| 7 | `T-Sheets` |
| 7 | `Stale` |
| 6 | `Civil QAQC` |
| 6 | `Equipment QAQC` |
| 6 | `Fencing QAQC` |
| 6 | `Racking & Module QAQC` |
| 6 | `As-Shipped Drawings` |
| 6 | `Purchase Order Draft` |
| 6 | `Purchase Order Executed` |
| 6 | `Working` |
| 6 | `Inverters` |
| 6 | `New folder` |
| 6 | `ESS` |
| 5 | `Array Wiring & Grounding QAQC` |
| 5 | `Compaction Tests` |
| 5 | `Concrete Tests` |
| 5 | `Point to Point Tests` |
| 5 | `CSP2 Fence Layout Specs` |
| 5 | `to attach` |
| 5 | `Civil` |
| 5 | `GOAB` |
| 5 | `Larson` |
| 5 | `ALTA` |
| 5 | `Equipment` |
| 5 | `EPEC` |
| 5 | `Drawings` |
| 5 | `OMCO` |
| 5 | `DAS` |
| 5 | `XFMR` |
| 5 | `Recloser` |
| 5 | `Chint` |
| 5 | `Inverter Pics` |
| 5 | `canceled` |

---

## Per-portfolio detail

### 1. 2025.201 KSI 4 IL

- Source: `folders__1. 2025.201 KSI 4 IL.txt`  
- Total folder paths: 1206  
- Unique folder-name strings: 779  
- Top-level folder count: 17  
- **Schema:** `active_portfolio_modern`  (signatures: 1. Portfolio Client Docs, 12. Portfolio Closeout, 2. Portfolio Buyout, 3. Portfolio Schedules, 4. Portfolio Dev Docs, 6. Portfolio Owner Correspond, 7. Portfolio Financials, 8. Portfolio Change Management)


**Top-level folder claims**

| Folder | Claim | Detail |
|---|---|---|
| `0. EEC Application` | canonical_non_job | subject |
| `1. Portfolio Client Docs` | portfolio_subject | — |
| `10. Submittal Logs` | canonical_non_job | subject |
| `11. De-Comm Bonds` | canonical_non_job | subject |
| `12. Portfolio Closeout` | portfolio_subject | — |
| `2. Portfolio Buyout` | portfolio_subject | — |
| `3. Portfolio Schedules` | portfolio_subject | — |
| `4. Portfolio Dev Docs` | portfolio_subject | — |
| `5. Engineering Gen` | canonical_non_job | subject |
| `6. Portfolio Owner Correspond` | portfolio_subject | — |
| `7. Portfolio Financials` | portfolio_subject | — |
| `8. Portfolio Change Management` | portfolio_subject | — |
| `9. Utility-Documents-Tracking` | canonical_non_job | subject |
| `A1. Kiwi` | active_subjob | letter_uc |
| `A2. Deeplake` | active_subjob | letter_uc |
| `A3. Indian Creek` | active_subjob | letter_uc |
| `A4. North Pasture` | active_subjob | letter_uc |

**Claim counts (unique names in this portfolio)**

| Claim | Count |
|---|---:|
| active_subjob | 139 |
| portfolio_subject | 8 |
| development_subject | 2 |
| subsubject | 70 |
| canonical_non_job | 118 |
| identifiable_job | 2 |
| unclaimed | 440 |

**Chaos flags**

| Pattern | Count | Examples |
|---|---:|---|
| person_tag_in_subject | 12 | `11. AHJ & Utility Permits-Inspections`, `Example-Brim`, `20550 Indian Creek Rd - Lake` |
| pre_canonical_zero | 6 | `0. EEC Application`, `0. To attach`, `0. Kiwi` |
| instructional_name | 4 | `Kiwi Annex- shared do not store`, `Deep Lake Annex- shared do not store`, `to attach do not store` |
| date_prefix_lowercase | 3 | `r. 2.27.26 Demarcation`, `r. 4.17.26 AS-SURVEYED cad`, `s. 4.17.25 RESPONSE` |
| unfilled_placeholder | 3 | `99.2 Vendor Name (Copy Folder)`, `99.3 Sub Name (Copy Folder)`, `99.4 PSA (Copy Folder)` |
| double_space | 2 | `99.1 Buyout  (estimates & quotes)`, `Site Walk  8.26.25` |
| duplicate_suffix | 1 | `Mosaic 1 (1)` |
| generic_new_folder | 1 | `New folder` |

**Duplicate-number-at-level findings**

- `A1. Kiwi/A. Kiwi Office/`: ChaosFlag(pattern='duplicate_number_at_level', severity='warn', description='Number "3." appears on 2 sibling folders: [\'3. Change Management\', \'3. Utility-ComEd\']', match='3.')
- `A1. Kiwi/A. Kiwi Office/`: ChaosFlag(pattern='duplicate_number_at_level', severity='warn', description='Number "4." appears on 2 sibling folders: [\'4. Equip\', \'4. Submittal Logs\']', match='4.')

**Unclaimed names (top 20)**

| Name |
|---|
| `From Evergreen EPC` |
| `From KSI EPC` |
| `LNTP` |
| `LNTP 2` |
| `Array Wiring & Grounding QAQC` |
| `Civil QAQC` |
| `Compaction Tests` |
| `Concrete Tests` |
| `Equipment QAQC` |
| `Fencing QAQC` |
| `Point to Point Tests` |
| `Racking & Module QAQC` |
| `As-Shipped Drawings` |
| `Annex C SOV` |
| `Kiwi Annex- shared do not store` |
| `Kiwi Annexs` |
| `Deep Lake Annex- shared do not store` |
| `Deep Lake Annexs` |
| `to attach do not store` |
| `annexs` |


### 2. 2024.335 Forefront - Luminace

- Source: `folders__2. 2024.335 Forefront - Luminace.txt`  
- Total folder paths: 2447  
- Unique folder-name strings: 1223  
- Top-level folder count: 26  
- **Schema:** `active_modern`  (signatures: 1. EPC documents, 10. Submittals, 2. Project Schedules, 3. Permitting, 4. Buyout)


**Top-level folder claims**

| Folder | Claim | Detail |
|---|---|---|
| `1. EPC documents` | canonical_non_job | subject |
| `10. Submittals` | canonical_non_job | subject |
| `11. PORTFOLIO CLOSEOUT` | portfolio_subject | — |
| `12. PVsyst Exh Y Forefront Contract` | canonical_non_job | subject |
| `2. Project Schedules` | canonical_non_job | subject |
| `3. Permitting` | canonical_non_job | subject |
| `335.1 BRIMFIELD-1` | active_subjob | three_digit |
| `335.2 BRIMFIELD-2` | active_subjob | three_digit |
| `335.3 ROCKFORD` | active_subjob | three_digit |
| `335.4 BBCHS-1` | active_subjob | three_digit |
| `335.5 BBCHS-2` | active_subjob | three_digit |
| `335.6 HUNTLEY` | active_subjob | three_digit |
| `4. Buyout` | canonical_non_job | subject |
| `5. Engineering Gen` | canonical_non_job | subject |
| `6. Change Management` | canonical_non_job | subject |
| `7. Portfolio Financials` | portfolio_subject | — |
| `8. Correspondence` | canonical_non_job | subject |
| `9. Utility-Documents-Tracking` | canonical_non_job | subject |
| `Approved Venders - Designees Illinois Shines ABP Program` | unclaimed | — |
| `Decom Plans` | unclaimed | — |
| `ForeFront - Schools and Brimfield - IL Portfolio` | unclaimed | — |
| `Prevailing Wage Rates` | unclaimed | — |
| `Project Schedules` | unclaimed | — |
| `SHARED DONT STORE` | unclaimed | — |
| `Shared Huntley BBCS and Rockford` | unclaimed | — |
| `Site walk` | unclaimed | — |

**Claim counts (unique names in this portfolio)**

| Claim | Count |
|---|---:|
| active_subjob | 148 |
| portfolio_subject | 2 |
| development_subject | 0 |
| subsubject | 82 |
| canonical_non_job | 175 |
| identifiable_job | 15 |
| unclaimed | 801 |

**Chaos flags**

| Pattern | Count | Examples |
|---|---:|---|
| person_tag_in_subject | 31 | `NTP-Es`, `Golden Row Submittal - Old`, `Legacy - Rockford` |
| pre_canonical_zero | 11 | `0. Signature Version EPC`, `0. Permits Shared to LUM`, `0. Master Subcontract Files` |
| double_space | 5 | `99.1 Buyout  (estimates & quotes)`, `JAN  2026`, `OCTOBER  2025` |
| unfilled_placeholder | 3 | `99.2 Vendor Name (Copy Folder)`, `99.3 Sub Name (Copy Folder)`, `99.4 PSA (Copy Folder)` |
| instructional_name | 2 | `working temp - DO NOT SEND`, `shared do not store - eaxmple specs` |
| exclamation_emphasis | 1 | `Shared Folder !!` |
| archive_letter_z | 1 | `z. Old` |
| generic_new_folder | 1 | `New folder` |

**Duplicate-number-at-level findings**

- `3. Permitting/BBCHS/`: ChaosFlag(pattern='duplicate_number_at_level', severity='warn', description='Number "1." appears on 2 sibling folders: [\'1. All Permits Release\', \'1. Info Documents\']', match='1.')
- `3. Permitting/Brimfield/`: ChaosFlag(pattern='duplicate_number_at_level', severity='warn', description='Number "1." appears on 3 sibling folders: [\'1. All Permits Release\', \'1. Drawings Folder Shared\', \'1. Info Documents\']', match='1.')
- `3. Permitting/Huntley/`: ChaosFlag(pattern='duplicate_number_at_level', severity='warn', description='Number "1." appears on 3 sibling folders: [\'1. All Permits Release\', \'1. Drawings Folder Shared\', \'1. Info Documents\']', match='1.')
- `3. Permitting/Rockford/`: ChaosFlag(pattern='duplicate_number_at_level', severity='warn', description='Number "1." appears on 2 sibling folders: [\'1. All Permits Release\', \'1. Info Documents\']', match='1.')
- `4. Buyout/S1. Fence/Peerless Fence/Contracts/0. To attach/`: ChaosFlag(pattern='duplicate_number_at_level', severity='warn', description='Number "0." appears on 6 sibling folders: [\'0. Bradley 1\', \'0. Bradley 2\', \'0. Brimfield 1\', \'0. Brimfield 2\', \'0. Huntley\', \'0. Rockford\']', match='0.')

**Unclaimed names (top 20)**

| Name |
|---|
| `EPCs` |
| `Exhibits D and E` |
| `Exhibits Extracted` |
| `BBCHS 1` |
| `BBCHS 2` |
| `Brimfield 1` |
| `Brimfield 2` |
| `Huntley` |
| `Rockford` |
| `NTP-Es` |
| `PVSysts` |
| `Fw_ Saftey sheets brimfield` |
| `Golden Row` |
| `Bradley` |
| `FINAL GOLDEN ROW` |
| `Golden Row Submittal - Old` |
| `Gripple Straps` |
| `Huntley - Golden Row V5 5.14` |
| `New Photos` |
| `Huntley - New Golden Row` |


### 3. 2023.126 Oregon - Kendall

- Source: `folders__3. 2023.126 Oregon - Kendall.txt`  
- Total folder paths: 531  
- Unique folder-name strings: 441  
- Top-level folder count: 16  
- **Schema:** `active_modern`  (signatures: 1. EPC, 10. Submittals, 11. Permitting, 12. CLOSEOUT, 2. Buyout, 3. Project Schedules, 4. Developer Documents)


**Top-level folder claims**

| Folder | Claim | Detail |
|---|---|---|
| `1. EPC` | canonical_non_job | subject |
| `10. Submittals` | canonical_non_job | subject |
| `11. Permitting` | canonical_non_job | subject |
| `12. CLOSEOUT` | canonical_non_job | subject |
| `2. Buyout` | canonical_non_job | subject |
| `2023.126.1 - Rodeo` | active_subjob | full_dot |
| `2023.126.2 - Apricus` | active_subjob | full_dot |
| `2023.126.3 - Lincoln` | active_subjob | full_dot |
| `3. Project Schedules` | canonical_non_job | subject |
| `4. Developer Documents` | canonical_non_job | subject |
| `5. Engineering General` | canonical_non_job | subject |
| `5. Rodeo Entrance Drawings` | canonical_non_job | subject |
| `6. Correspondence` | canonical_non_job | subject |
| `7. Financials` | canonical_non_job | subject |
| `8. Change Management` | canonical_non_job | subject |
| `9. Utility-Documents-Tracking` | canonical_non_job | subject |

**Claim counts (unique names in this portfolio)**

| Claim | Count |
|---|---:|
| active_subjob | 112 |
| portfolio_subject | 0 |
| development_subject | 0 |
| subsubject | 59 |
| canonical_non_job | 62 |
| identifiable_job | 10 |
| unclaimed | 198 |

**Chaos flags**

| Pattern | Count | Examples |
|---|---:|---|
| person_tag_in_subject | 10 | `Shared Rodeo-Apricus`, `V4. CPS-Chint`, `2023.126.1 - Rodeo` |
| double_space | 3 | `V6. Zpower  B2 Sales`, `Lincoln  Field`, `99.1 Buyout  (estimates & quotes)` |
| unfilled_placeholder | 3 | `99.2 Vendor Name (Copy Folder)`, `99.3 Sub Name (Copy Folder)`, `99.4 PSA (Copy Folder)` |
| pre_canonical_zero | 2 | `0. Certificates`, `0. Master Subcontract Files` |

**Duplicate-number-at-level findings**

- `<root>/`: ChaosFlag(pattern='duplicate_number_at_level', severity='warn', description='Number "5." appears on 2 sibling folders: [\'5. Engineering General\', \'5. Rodeo Entrance Drawings\']', match='5.')

**Unclaimed names (top 20)**

| Name |
|---|
| `BF Edits` |
| `Permit` |
| `Executed` |
| `Draft EPC` |
| `Exhibits` |
| `Rodeo` |
| `Apricus` |
| `Apricus DEQ Items` |
| `Elect` |
| `Hunter Permit request 2022` |
| `Lincoln` |
| `Permits` |
| `Rodeo ODOT` |
| `Final Completion Certs` |
| `Mechanical Completion Certs` |
| `Substantial Completion Certs` |
| `teala QAQC Documents - dont delete or touch` |
| `Apricus Photos` |
| `S- to KSI` |
| `Internal Only` |


### 4. 2025.358 Keystone  (Coast)

- Source: `folders__4. 2025.358 Keystone  (Coast).txt`  
- Total folder paths: 521  
- Unique folder-name strings: 421  
- Top-level folder count: 21  
- **Schema:** `active_portfolio_modern`  (signatures: 1. Portfolio Client Docs, 12. Portfolio Closeout, 2. Portfolio Buyout, 3. Portfolio Schedules, 6. Portfolio Owner Contract and Correspond, 7. Portfolio Financials, 8. Portfolio Change Management)


**Top-level folder claims**

| Folder | Claim | Detail |
|---|---|---|
| `0. Coast PA Contract Docs EJ` | canonical_non_job | subject |
| `0. Porfolio Permitting` | canonical_non_job | subject |
| `1. Portfolio Client Docs` | portfolio_subject | — |
| `1.5. Funaro Landowner Claim` | unclaimed | — |
| `10. Submittal Logs` | canonical_non_job | subject |
| `11. De-Comm Bonds` | canonical_non_job | subject |
| `12. Portfolio Closeout` | portfolio_subject | — |
| `2. Portfolio Buyout` | portfolio_subject | — |
| `2025.358.1 - Emmanuel Church` | active_subjob | full_dot |
| `2025.358.2 - Ridge Road` | active_subjob | full_dot |
| `2025.358.3 - Off Church` | active_subjob | full_dot |
| `2025.358.4 - Shamrock` | active_subjob | full_dot |
| `3. Portfolio Schedules` | portfolio_subject | — |
| `4. Dev Docs` | canonical_non_job | subject |
| `5. Engineering Gen` | canonical_non_job | subject |
| `6. Portfolio Owner Contract and Correspond` | portfolio_subject | — |
| `7. Portfolio Financials` | portfolio_subject | — |
| `8. Portfolio Change Management` | portfolio_subject | — |
| `9. Utility-Documents-Tracking` | canonical_non_job | subject |
| `Funaro Pre-Delivery of Excavator` | unclaimed | — |
| `Teala Organize folder` | unclaimed | — |

**Claim counts (unique names in this portfolio)**

| Claim | Count |
|---|---:|
| active_subjob | 83 |
| portfolio_subject | 7 |
| development_subject | 0 |
| subsubject | 45 |
| canonical_non_job | 84 |
| identifiable_job | 1 |
| unclaimed | 201 |

**Chaos flags**

| Pattern | Count | Examples |
|---|---:|---|
| person_tag_in_subject | 12 | `11. AHJ & Utility Permits-Inspections`, `NEW PROJECTS - Proposals`, `9. Utility-Documents-Tracking` |
| pre_canonical_zero | 4 | `0. Coast PA Contract Docs EJ`, `0. EPC Turn 6.18.25`, `0. Porfolio Permitting` |
| unfilled_placeholder | 3 | `99.2 Vendor Name (Copy Folder)`, `99.3 Sub Name (Copy Folder)`, `99.4 PSA (Copy Folder)` |
| sub_decimal_insert | 1 | `1.5. Funaro Landowner Claim` |
| double_space | 1 | `99.1 Buyout  (estimates & quotes)` |

**Duplicate-number-at-level findings**

- `<root>/`: ChaosFlag(pattern='duplicate_number_at_level', severity='warn', description='Number "0." appears on 2 sibling folders: [\'0. Coast PA Contract Docs EJ\', \'0. Porfolio Permitting\']', match='0.')

**Unclaimed names (top 20)**

| Name |
|---|
| `EPC Contract Body including Exhibits edited with body` |
| `Exh B - Scope of Work` |
| `Exh C-1 Contractor Permits` |
| `Exh C-2 Develper Permits` |
| `Exh D - Contractors Security and Safety Procedures` |
| `Exh E - Payment Schedule` |
| `Exh G - Project Schedule` |
| `Exh H - Premises Description` |
| `Exh L - Contractors Quality Assurance Plan` |
| `Exh M - List of Pre-Approved Major Subcontractors` |
| `Exh N - Spare Parts` |
| `Exh O - Environmental Report` |
| `Exh P - Performance Testing` |
| `Exh Q - List of Required Deliverables` |
| `Exh R - Solar Facility Design and Site Plan` |
| `Exh T - Key Personnel` |
| `Exh U - Form of Progress Report` |
| `Exh V - Mechanical & Commissioning Procedures` |
| `Exh W - Form of Invoice` |
| `Exh Y - Developer Provided Equipment` |


### 5. 2025.108 Bonacci 1&2 (Generate)

- Source: `folders__5. 2025.108 Bonacci 1&2 (Generate).txt`  
- Total folder paths: 1178  
- Unique folder-name strings: 673  
- Top-level folder count: 2  
- **Schema:** `active_single_project`  (signatures: A. Bonacci Office, B. Bonacci Field)


**Top-level folder claims**

| Folder | Claim | Detail |
|---|---|---|
| `A. Bonacci Office` | unclaimed | — |
| `B. Bonacci Field` | unclaimed | — |

**Claim counts (unique names in this portfolio)**

| Claim | Count |
|---|---:|
| active_subjob | 119 |
| portfolio_subject | 3 |
| development_subject | 2 |
| subsubject | 10 |
| canonical_non_job | 98 |
| identifiable_job | 1 |
| unclaimed | 440 |

**Chaos flags**

| Pattern | Count | Examples |
|---|---:|---|
| person_tag_in_subject | 19 | `Exh H - Permits`, `11. AHJ & Utility Permits-Inspections`, `S4. Drain Tiles - Seevers` |
| pre_canonical_zero | 2 | `0. RFP`, `0. Geotech Report` |
| instructional_name | 1 | `shared do not store` |
| generic_new_folder | 1 | `New folder` |
| archive_letter_z | 1 | `z. Old` |
| date_prefix_lowercase | 1 | `r. 3.26.26 Rev2 Elect` |
| double_space | 1 | `Generate Bonacci 1  2 LNTP Values 3.19.25` |

**Duplicate-number-at-level findings**

- `A. Bonacci Office/`: ChaosFlag(pattern='duplicate_number_at_level', severity='warn', description='Number "9." appears on 2 sibling folders: [\'9. Permitting\', \'9. Utility-Documents-Tracking\']', match='9.')

**Unclaimed names (top 20)**

| Name |
|---|
| `A. Bonacci Office` |
| `Compressed` |
| `Working` |
| `EPC` |
| `EPC Exhibits Modified 4.30.25` |
| `EPC Exhibits Modified 7.10.25` |
| `Exh A - Scope of Work` |
| `Exh B - Technical Specifications` |
| `Exh C - Payment Schedule` |
| `Exh D - Project Schedule` |
| `Exh E - System Testing Protocals and Capacity Testing` |
| `Exh F - Start up and Commissioning Checklist` |
| `Exh H - Permits` |
| `Exh I-0 Form of Full Notice To Proceed` |
| `Exh I-1 Form of Mechanical Completion Certificate` |
| `Exh I-2 Form of Substantial Completion Certificate` |
| `Exh I-3 Form of Final Completion Certificate` |
| `Exh J - Form of Consent To Energize` |
| `Exh K - Uncon Waiver and Release on Progress Payment` |
| `Exh L- Uncon Waiver and Release on Final Payment` |


### 6. 2025.364 Steger & Roxbury

- Source: `folders__6. 2025.364 Steger & Roxbury.txt`  
- Total folder paths: 1007  
- Unique folder-name strings: 717  
- Top-level folder count: 16  
- **Schema:** `active_portfolio_modern`  (signatures: 1. Portfolio Client Docs, 12. Portfolio Closeout, 2. Portfolio Buyout, 3. Portfolio Schedules, 4. Portfolio Dev Docs, 6. Portfolio Owner Correspond, 7. Portfolio Financials, 8. Portfolio Change Management)


**Top-level folder claims**

| Folder | Claim | Detail |
|---|---|---|
| `1. Portfolio Client Docs` | portfolio_subject | — |
| `10. Submittal Logs` | canonical_non_job | subject |
| `11. De-Comm Bonds` | canonical_non_job | subject |
| `12. Portfolio Closeout` | portfolio_subject | — |
| `2. Portfolio Buyout` | portfolio_subject | — |
| `2025.364 CPG- Cook County- Steger` | identifiable_job | modern |
| `2025.364 CPG- Cook County- Steger - Copy` | identifiable_job | modern |
| `2025.364.1 Steger` | active_subjob | full_dot |
| `2025.364.2 Roxbury` | active_subjob | full_dot |
| `3. Portfolio Schedules` | portfolio_subject | — |
| `4. Portfolio Dev Docs` | portfolio_subject | — |
| `5. Engineering Gen` | canonical_non_job | subject |
| `6. Portfolio Owner Correspond` | portfolio_subject | — |
| `7. Portfolio Financials` | portfolio_subject | — |
| `8. Portfolio Change Management` | portfolio_subject | — |
| `9. Utility-Documents-Tracking` | canonical_non_job | subject |

**Claim counts (unique names in this portfolio)**

| Claim | Count |
|---|---:|
| active_subjob | 130 |
| portfolio_subject | 8 |
| development_subject | 0 |
| subsubject | 76 |
| canonical_non_job | 137 |
| identifiable_job | 2 |
| unclaimed | 364 |

**Chaos flags**

| Pattern | Count | Examples |
|---|---:|---|
| person_tag_in_subject | 21 | `Re_ Final Golden Row Submittal - Steger`, `11. AHJ & Utility Permits-Inspections`, `V2. Rexel - Eaton` |
| pre_canonical_zero | 5 | `0. RFP`, `0. Steger`, `0. Roxbury` |
| double_space | 5 | `99.1 Buyout  (estimates & quotes)`, `R. 9.22.25  LUM-AECOM`, `R. 6.26.25 Lum 90%  Review` |
| unfilled_placeholder | 3 | `99.2 Vendor Name (Copy Folder)`, `99.3 Sub Name (Copy Folder)`, `99.4 PSA (Copy Folder)` |
| instructional_name | 2 | `1. Steger annex shared- do not store`, `1. Roxbury annex shared- do not store` |
| box_drive_copy | 1 | `2025.364 CPG- Cook County- Steger - Copy` |
| generic_new_folder | 1 | `New folder` |
| date_prefix_lowercase | 1 | `r. 8.5.25 Civil CAD` |

**Duplicate-number-at-level findings**

- `2. Portfolio Buyout/Redacted - master/Roxbury/`: ChaosFlag(pattern='duplicate_number_at_level', severity='warn', description='Number "4." appears on 2 sibling folders: [\'4. DAS-Also Energy\', \'4. Racking-Valmont\']', match='4.')

**Unclaimed names (top 20)**

| Name |
|---|
| `Roxbury` |
| `Mockup row updated pictures` |
| `Steger` |
| `Elect. Equip` |
| `CAB` |
| `Combiner` |
| `DAS` |
| `GOAB` |
| `Meter` |
| `PVSB` |
| `Recloser` |
| `XFMR` |
| `Golden Row` |
| `il_-_cpg-_steger-submittal#15-rev-0-golden_row_submittal-202603091919` |
| `Re_ Final Golden Row Submittal - Steger` |
| `Legacy-BOS` |
| `FE` |
| `Array Wiring & Grounding QAQC` |
| `Civil QAQC` |
| `Compaction Tests` |


### 7. 20171 - 20176 OR Portfolio (SPI)

- Source: `folders__7. 20171 - 20176 OR Portfolio (SPI).txt`  
- Total folder paths: 543  
- Unique folder-name strings: 407  
- Top-level folder count: 23  
- **Schema:** `active_portfolio_modern`  (signatures: 0. Portfolio Client Docs, 1. Portfolio Buyout, 12. PORTFOLIO CLOSEOUT, 4. Portfolio Dev Docs, 5. Portfolio Schedules)


**Top-level folder claims**

| Folder | Claim | Detail |
|---|---|---|
| `0. Portfolio Client Docs` | portfolio_subject | — |
| `1. Portfolio Buyout` | portfolio_subject | — |
| `10. Owner Docs` | canonical_non_job | subject |
| `11. EPC Contract Redlines for ZACK` | canonical_non_job | subject |
| `12. PORTFOLIO CLOSEOUT` | portfolio_subject | — |
| `13. Submittals` | canonical_non_job | subject |
| `14. Lien Waivers` | canonical_non_job | subject |
| `2. Accounting (to owner)` | canonical_non_job | subject |
| `2. EPC Agreement` | canonical_non_job | subject |
| `2020-1071 Belvedere` | active_subjob | dashed |
| `2020-1072 Dover` | active_subjob | dashed |
| `2020-1073 Clayfield` | active_subjob | dashed |
| `2020-1074 Waterford` | active_subjob | dashed |
| `2020-1075 Manchester` | active_subjob | dashed |
| `2020-1076 Cork` | active_subjob | dashed |
| `3. Engineering Gen` | canonical_non_job | subject |
| `4. Portfolio Dev Docs` | portfolio_subject | — |
| `5. Portfolio Schedules` | portfolio_subject | — |
| `6. Correspondence - Notices` | canonical_non_job | subject |
| `7. Change Management` | canonical_non_job | subject |
| `9. PGE-Documents-Tracking` | canonical_non_job | subject |
| `Drone Flights` | unclaimed | — |
| `SPI Safety and Reporting` | unclaimed | — |

**Claim counts (unique names in this portfolio)**

| Claim | Count |
|---|---:|
| active_subjob | 52 |
| portfolio_subject | 5 |
| development_subject | 0 |
| subsubject | 6 |
| canonical_non_job | 82 |
| identifiable_job | 0 |
| unclaimed | 262 |

**Chaos flags**

| Pattern | Count | Examples |
|---|---:|---|
| person_tag_in_subject | 11 | `11. EPC Contract Redlines for ZACK`, `11. AHJ & Utility Permits-Inspections`, `Waterford De-Rate` |
| pre_canonical_zero | 4 | `0. Portfolio Client Docs`, `0. ALL SITES ANOMALY MAPS`, `0. Status Emails` |
| date_prefix_lowercase | 1 | `r. 1.7.21 Mods Invert` |
| generic_new_folder | 1 | `New folder` |
| archive_letter_z | 1 | `z. Old` |

**Duplicate-number-at-level findings**

- `<root>/`: ChaosFlag(pattern='duplicate_number_at_level', severity='warn', description='Number "2." appears on 2 sibling folders: [\'2. Accounting (to owner)\', \'2. EPC Agreement\']', match='2.')

**Unclaimed names (top 20)**

| Name |
|---|
| `6.12.23` |
| `6.19.23` |
| `6.26.23` |
| `6.30.23` |
| `6.5.23` |
| `7.14.23` |
| `Insurance` |
| `CSP2 Fence Layout Specs` |
| `concrete` |
| `MC4` |
| `S10. Northwest Drilling and Boring` |
| `stale` |
| `S11. Double Down Post Pounding` |
| `S12. Mill Plain` |
| `S13. Diamond B Solutions` |
| `S14. Line Scape` |
| `S21. Pro Panel` |
| `Pro Panel Numbers_files` |
| `Stale` |
| `S22. AQS Contracting` |


### 12. 2024.112 Almon, Lomaside, Perrydale (Hawthorne)

- Source: `folders__12. 2024.112 Almon, Lomaside, Perrydale (Hawthorne).txt`  
- Total folder paths: 416  
- Unique folder-name strings: 309  
- Top-level folder count: 14  
- **Schema:** `active_portfolio_modern`  (signatures: 1. Portfolio Client Docs, 3. Portfolio Schedules, 4. Portfolio Dev Docs, 7. PORTFOLIO CLOSEOUT)


**Top-level folder claims**

| Folder | Claim | Detail |
|---|---|---|
| `1. Portfolio Client Docs` | portfolio_subject | — |
| `3. Buyout` | canonical_non_job | subject |
| `3. Portfolio Schedules` | portfolio_subject | — |
| `4. Portfolio Dev Docs` | portfolio_subject | — |
| `5. Eng. Gen` | development_subject | — |
| `6. Safe Harbor (Pads)` | canonical_non_job | subject |
| `7. PORTFOLIO CLOSEOUT` | portfolio_subject | — |
| `a. Almon` | active_subjob | letter_lc |
| `b. Lomaside` | active_subjob | letter_lc |
| `c. Perrydale` | active_subjob | letter_lc |
| `Common Energy Service agreements` | unclaimed | — |
| `Dev Docs-Bidding` | unclaimed | — |
| `Hawthorne documents` | unclaimed | — |
| `PUBLIC-Dev Docs-Bidding- Shared - Copy` | unclaimed | — |

**Claim counts (unique names in this portfolio)**

| Claim | Count |
|---|---:|
| active_subjob | 87 |
| portfolio_subject | 4 |
| development_subject | 1 |
| subsubject | 67 |
| canonical_non_job | 45 |
| identifiable_job | 0 |
| unclaimed | 105 |

**Chaos flags**

| Pattern | Count | Examples |
|---|---:|---|
| person_tag_in_subject | 13 | `V2. Chint-Inverters`, `T-Sheets`, `9. Utility-Documents-Tracking` |
| double_space | 1 | `V11.  McKaig` |
| box_drive_copy | 1 | `PUBLIC-Dev Docs-Bidding- Shared - Copy` |

**Duplicate-number-at-level findings**

- `<root>/`: ChaosFlag(pattern='duplicate_number_at_level', severity='warn', description='Number "3." appears on 2 sibling folders: [\'3. Buyout\', \'3. Portfolio Schedules\']', match='3.')

**Unclaimed names (top 20)**

| Name |
|---|
| `LNTPs` |
| `Signed Proposals` |
| `finished reports` |
| `supporting` |
| `Rabe shared docs - almon and lomaside` |
| `stale` |
| `Almon` |
| `Almon Dev Files` |
| `Lomaside` |
| `Lomaside Dev Files` |
| `ALTA` |
| `CAD working` |
| `3.13.25` |
| `Draft SLD` |
| `Deko Solar Almon` |
| `Equipment` |
| `Dual Voltage XFMR` |
| `Inverter` |
| `Archived` |
| `CPS-String-Config-Tool-6.0.3-1-30-25` |


### 13. 2025.112 Kendall CSP Portfolio 5

- Source: `folders__13. 2025.112 Kendall CSP Portfolio 5.txt`  
- Total folder paths: 561  
- Unique folder-name strings: 389  
- Top-level folder count: 18  
- **Schema:** `active_portfolio_modern`  (signatures: 1. Portfolio Client Docs, 12. Portfolio Closeout, 2. Portfolio Buyout, 3. Portfolio Schedules, 4. Portfolio Dev Docs, 6. Portfolio Owner Correspond, 7. Portfolio Financials, 8. Portfolio Change Management)


**Top-level folder claims**

| Folder | Claim | Detail |
|---|---|---|
| `1. Portfolio Client Docs` | portfolio_subject | — |
| `10. Permitting` | canonical_non_job | subject |
| `11. De-Comm Bonds` | canonical_non_job | subject |
| `12. Portfolio Closeout` | portfolio_subject | — |
| `2. Portfolio Buyout` | portfolio_subject | — |
| `3. Portfolio Schedules` | portfolio_subject | — |
| `4. Portfolio Dev Docs` | portfolio_subject | — |
| `5. Engineering Gen` | canonical_non_job | subject |
| `6. Portfolio Owner Correspond` | portfolio_subject | — |
| `7. Portfolio Financials` | portfolio_subject | — |
| `8. Portfolio Change Management` | portfolio_subject | — |
| `9. Utility-Documents-Tracking` | canonical_non_job | subject |
| `a. Colfax Solar` | active_subjob | letter_lc |
| `b. Coker Solar` | active_subjob | letter_lc |
| `c. Crawford Solar` | active_subjob | letter_lc |
| `d. Bradley Solar` | active_subjob | letter_lc |
| `DEV DATAROOM - KSI- Hawthorne (OR) EPC` | unclaimed | — |
| `z. ARCHIVE PROJ` | unclaimed | — |

**Claim counts (unique names in this portfolio)**

| Claim | Count |
|---|---:|
| active_subjob | 87 |
| portfolio_subject | 8 |
| development_subject | 2 |
| subsubject | 55 |
| canonical_non_job | 100 |
| identifiable_job | 0 |
| unclaimed | 137 |

**Chaos flags**

| Pattern | Count | Examples |
|---|---:|---|
| person_tag_in_subject | 9 | `11. AHJ & Utility Permits-Inspections`, `V11. Z-Power`, `V6. Maddox-Coker` |
| unfilled_placeholder | 3 | `99.2 Vendor Name (Copy Folder)`, `99.3 Sub Name (Copy Folder)`, `99.4 PSA (Copy Folder)` |
| generic_new_folder | 1 | `New folder` |
| double_space | 1 | `99.1 Buyout  (estimates & quotes)` |
| pre_canonical_zero | 1 | `0. Master Subcontract Files` |
| date_prefix_lowercase | 1 | `r. 12.29.25 KSI review` |
| archive_letter_z | 1 | `z. ARCHIVE PROJ` |

**Duplicate-number-at-level findings**

- `a. Colfax Solar/2. Colfax Office/`: ChaosFlag(pattern='duplicate_number_at_level', severity='warn', description='Number "1." appears on 2 sibling folders: [\'1. Buyout\', \'1. ESS Contract & LNTP (to owner)\']', match='1.')

**Unclaimed names (top 20)**

| Name |
|---|
| `EPC Draft` |
| `EPC FE` |
| `LNTP's FE` |
| `LNTP's Sent` |
| `Stale` |
| `Bradley` |
| `Crawford` |
| `Array Wiring & Grounding QAQC` |
| `Civil QAQC` |
| `Compaction Tests` |
| `Concrete Tests` |
| `Equipment QAQC` |
| `Fencing QAQC` |
| `Point to Point Tests` |
| `Racking & Module QAQC` |
| `As-Shipped Drawings` |
| `CSP2 Fence Layout Specs` |
| `S15. Onpoint` |
| `S16. 4 Seasons` |
| `S17. Xfmr Testing (Optimal & EPS)` |


### 15. 2025.127 Dolphin and Shoestring- Kendall

- Source: `folders__15. 2025.127 Dolphin and Shoestring- Kendall.txt`  
- Total folder paths: 181  
- Unique folder-name strings: 112  
- Top-level folder count: 11  
- **Schema:** `active_portfolio_modern`  (signatures: 3. Portfolio Client Docs, 3. Portfolio Schedules, 4. Portfolio Buyout, 4. Portfolio Dev Docs, 6. Portfolio Owner Correspondence, 8. Portfolio Change Management)


**Top-level folder claims**

| Folder | Claim | Detail |
|---|---|---|
| `1. Dolphin` | canonical_non_job | subject |
| `2. Shoestring` | canonical_non_job | subject |
| `3. Portfolio Client Docs` | portfolio_subject | — |
| `3. Portfolio Schedules` | portfolio_subject | — |
| `4. Portfolio Buyout` | portfolio_subject | — |
| `4. Portfolio Dev Docs` | portfolio_subject | — |
| `5. Engineering Gen` | canonical_non_job | subject |
| `6. Portfolio Owner Correspondence` | portfolio_subject | — |
| `7. Financials` | canonical_non_job | subject |
| `8. Portfolio Change Management` | portfolio_subject | — |
| `R. 5.6.25 Chint Quote` | active_subjob | letter_uc |

**Claim counts (unique names in this portfolio)**

| Claim | Count |
|---|---:|
| active_subjob | 28 |
| portfolio_subject | 6 |
| development_subject | 8 |
| subsubject | 1 |
| canonical_non_job | 11 |
| identifiable_job | 0 |
| unclaimed | 58 |

_No chaos flags in this portfolio._

**Duplicate-number-at-level findings**

- `<root>/`: ChaosFlag(pattern='duplicate_number_at_level', severity='warn', description='Number "3." appears on 2 sibling folders: [\'3. Portfolio Client Docs\', \'3. Portfolio Schedules\']', match='3.')
- `<root>/`: ChaosFlag(pattern='duplicate_number_at_level', severity='warn', description='Number "4." appears on 2 sibling folders: [\'4. Portfolio Buyout\', \'4. Portfolio Dev Docs\']', match='4.')

**Unclaimed names (top 20)**

| Name |
|---|
| `Pre-Construction Site Photos` |
| `Meeting min` |
| `CUP Application` |
| `Design` |
| `Development Permit` |
| `multi` |
| `Environmental` |
| `DEQ 1200C` |
| `S- 1.12.26 Comment Responses` |
| `S- 9.26.25 Application` |
| `ODEQ` |
| `Fire Access Approval` |
| `Submit to County` |
| `Submit to County Next 2.26.26` |
| `Lease & Corresponding Docs` |
| `Title & Survey` |
| `Community Solar` |
| `FAA` |
| `FERC` |
| `H2DC` |

