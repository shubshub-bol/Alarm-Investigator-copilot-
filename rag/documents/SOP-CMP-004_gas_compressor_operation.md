---
doc_id: SOP-CMP-004
title: Gas Compressor Operation and Alarm Response
doc_type: operating_procedure
equipment_types: [compressor, heat_exchanger]
alarm_codes: [CMP-DISCH-PRESS-HI, CMP-SURGE, CMP-VIB-HI, HX-OUT-TEMP-HI, HX-DP-HI]
site: EastRefinery
revision: D
effective_date: 2026-02-01
owner: Operations - Unit 3
---

# Gas Compressor Operation and Alarm Response

## 1. Scope

This procedure applies to Gas Compressor C-301 and its Discharge Cooler E-301 in EastRefinery Unit 3, and
to Recycle Compressor C-502 in Unit 5.

## 2. Operating limits

| Parameter | Normal | Alarm | Trip |
|---|---|---|---|
| Discharge pressure | 36 to 40 bar | 42 bar | 45 bar |
| Discharge cooler outlet temperature | 40 to 50 degC | 55 degC | - |
| Cooler differential pressure | below 0.5 bar | 0.8 bar | - |

## 3. Alarm response

### 3.1 High discharge pressure (CMP-DISCH-PRESS-HI)

1. Check the discharge cooler E-301 outlet temperature first. A fouled cooler raises gas temperature and
   pressure; recurring high discharge pressure alarms are most often a cooler problem, not a compressor one.
2. Open the recycle valve to bring discharge pressure below 42 bar.
3. Reduce compressor load if pressure keeps rising. At 45 bar the compressor trips.
4. If high discharge pressure alarms keep returning within minutes of clearing (chattering), report it for
   alarm rationalization (ALM-PHIL-006 section 5); do not shelve it without approval.

### 3.2 Surge (CMP-SURGE)

1. Confirm the anti-surge valve opened. If it did not, open it manually.
2. Increase recycle flow until the operating point is clear of the surge line.
3. Repeated surge overloads the drive motor (see TG-MTR-003 section 2).

### 3.3 High vibration (CMP-VIB-HI)

1. Check for surge first.
2. If vibration persists without surge, reduce load and request vibration analysis.

## 4. Discharge cooler cleaning criteria

Schedule cooler cleaning when the high differential pressure alarm (HX-DP-HI) has been standing for more than
24 hours, or when the high outlet temperature alarm (HX-OUT-TEMP-HI) occurs more than three times in a week.
A standing HX-DP-HI alarm is a stale alarm and must be reviewed at the daily operations meeting.
