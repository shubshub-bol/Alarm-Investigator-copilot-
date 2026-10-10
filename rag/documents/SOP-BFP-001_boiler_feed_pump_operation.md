---
doc_id: SOP-BFP-001
title: Boiler Feed Pump Operation and Alarm Response
doc_type: operating_procedure
equipment_types: [pump]
alarm_codes: [PMP-VIB-HI, PMP-SUCT-LO, PMP-BRG-TEMP-HI, PMP-SEAL-LEAK]
site: NorthPlant
revision: C
effective_date: 2026-01-15
owner: Operations - Unit 1
---

# Boiler Feed Pump Operation and Alarm Response

## 1. Purpose and scope

This procedure covers normal operation of Boiler Feed Pumps 101 and 102 (BFP-101, BFP-102) in NorthPlant
Unit 1 and the operator response to their alarms. The two pumps are installed as duty and standby. Each pump
is driven by its own electric motor (M-101, M-102) and takes suction from Deaerator D-101. Feedwater flow to
the boiler is controlled by valve FV-101.

## 2. Responsibilities

The control room operator responds to alarms and performs pump changeovers. The field operator confirms
local readings and carries out isolations. The shift supervisor approves continued operation outside normal
limits and must be informed of every P1 or P2 alarm on a feed pump.

## 3. Normal operating limits

| Parameter | Normal | Alarm | Trip |
|---|---|---|---|
| Bearing vibration (velocity) | below 4.5 mm/s | 7.1 mm/s | 11.0 mm/s |
| Suction pressure | 3.0 to 4.0 bar | 2.5 bar (low) | 1.8 bar (low) |
| Pump bearing temperature | 55 to 70 degC | 85 degC | 95 degC |
| Drive motor bearing temperature | 50 to 75 degC | 90 degC | 100 degC |

## 4. Alarm response

### 4.1 High vibration (PMP-VIB-HI)

1. Confirm the reading on the local vibration indicator and the DCS trend. Note whether the drive-end,
   non-drive-end or both bearings are affected.
2. Check the drive motor bearing temperature (alarm MTR-BRG-TEMP-HI on M-101 / M-102). A rising motor
   bearing temperature shortly before pump vibration is a typical sign of coupling misalignment or a
   failing bearing. See MM-PUMP-002 section 4.
3. Check suction pressure and deaerator level. Low suction pressure causes cavitation, which also shows as
   high vibration. See section 4.2 and TG-DEA-007.
4. If vibration stays above 7.1 mm/s for more than 10 minutes, is rising, or exceeds 11.0 mm/s at any time:
   start the standby feed pump, transfer the load to it, and stop the affected pump.
5. Do not keep a feed pump in service above the vibration alarm limit for extended monitoring. Continued
   operation above 7.1 mm/s needs explicit shift supervisor approval and must not exceed 30 minutes.
6. Inform the shift supervisor and raise a maintenance notification for vibration analysis and a bearing and
   coupling inspection (MM-PUMP-002 section 5).
7. Before any field inspection of the stopped pump, apply isolation according to SAF-005.

### 4.2 Low suction pressure (PMP-SUCT-LO)

1. Check deaerator D-101 level and pressure. A low deaerator level is the most common cause.
2. Check the suction strainer differential pressure.
3. Reduce pump flow if suction pressure keeps falling. Below 1.8 bar the pump trips on low suction pressure.
4. Expect noise and vibration while the pump cavitates. Repeated cavitation damages the impeller.

### 4.3 High bearing temperature (PMP-BRG-TEMP-HI)

1. Check lube oil level, oil flow sight glass and oil cooler water flow.
2. Compare with the vibration reading. High temperature together with rising vibration indicates bearing
   distress: change over to the standby pump as in section 4.1 step 4.
3. At 95 degC the pump trips automatically.

### 4.4 Seal leak (PMP-SEAL-LEAK)

1. Confirm the leak locally and estimate the leak rate.
2. A steady drip is acceptable until the next planned stop. A continuous stream requires changeover to the
   standby pump and isolation for seal replacement.

## 5. Changeover to the standby pump

1. Confirm the standby pump is available (no active alarms, lube oil normal, isolation valves open).
2. Start the standby pump from the DCS and confirm discharge pressure rises above boiler drum pressure.
3. Gradually transfer load using the recirculation and discharge valves while watching drum level.
4. Stop the affected pump and close its discharge valve.
5. Record the changeover and the reason in the shift log.

## 6. References

- MM-PUMP-002 Pump and Drive Motor Maintenance Manual
- TG-DEA-007 Deaerator and Feedwater Troubleshooting Guide
- SAF-005 Rotating Equipment Safety and Isolation Instructions
- ALM-PHIL-006 Alarm Management Philosophy and Response Priorities
