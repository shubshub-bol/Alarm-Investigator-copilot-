---
doc_id: TG-MTR-003
title: Electric Motor Troubleshooting Guide
doc_type: troubleshooting_guide
equipment_types: [motor, compressor]
alarm_codes: [MTR-TRIP, MTR-OVERLOAD, MTR-WIND-TEMP-HI, MTR-BRG-TEMP-HI, CMP-SURGE]
site: all
revision: A
effective_date: 2025-06-10
owner: Electrical Engineering
---

# Electric Motor Troubleshooting Guide

## 1. How to use this guide

Find the alarm, then work through the checks in order. Record what you found before resetting any
protection. A motor that trips repeatedly is telling you about its driven equipment as often as about itself.

## 2. Motor trip (MTR-TRIP)

1. Read the protection relay first-out flag (overcurrent, thermal overload, earth fault, undervoltage).
2. Do not reset and restart more than once. A second trip requires an electrical inspection before restart.
3. Review the winding temperature history for the 30 minutes before the trip. A high winding temperature
   alarm (MTR-WIND-TEMP-HI) before the trip points to cooling or sustained overload.
4. Check the driven equipment. For a compressor drive such as M-501 driving C-502, look for surge events
   (CMP-SURGE) just before the trip: surge causes current swings that end in an overload trip.
5. Check the motor cooling water supply and the cooling water pump (for Unit 5, motor M-502 driving the
   cooling water pump). Loss of cooling water raises winding temperature within minutes.

## 3. High winding temperature (MTR-WIND-TEMP-HI)

1. Confirm cooling air or cooling water flow.
2. Reduce load if possible.
3. At 155 degC the thermal protection trips the motor.

## 4. Overload current (MTR-OVERLOAD)

1. Compare current with the nameplate full-load current.
2. Check for mechanical binding in the driven machine and for process overload (high flow, high density).

## 5. High motor bearing temperature (MTR-BRG-TEMP-HI)

1. Check the grease or oil condition and regreasing records (MM-PUMP-002 section 3).
2. When the motor drives a pump, check coupling alignment and the pump vibration trend. A hot motor bearing
   followed by pump vibration is the classic misalignment pattern (MM-PUMP-002 section 4).

## 6. Related assets to inspect after a motor trip

| Related asset | What to check |
|---|---|
| Driven compressor (e.g. C-502) | Surge events, anti-surge valve response time |
| Cooling water pump motor (e.g. M-502) | Overload alarms, flow to the motor cooler |
| Motor protection relay | First-out flag and settings |
| Power supply / switchgear | Voltage dips at the time of the trip |
