---
doc_id: TG-DEA-007
title: Deaerator and Feedwater Troubleshooting Guide
doc_type: troubleshooting_guide
equipment_types: [deaerator, pump, valve]
alarm_codes: [DEA-LVL-LO, DEA-PRESS-LO, PMP-SUCT-LO, VLV-POS-DEV]
site: NorthPlant
revision: A
effective_date: 2025-08-12
owner: Operations - Unit 1
---

# Deaerator and Feedwater Troubleshooting Guide

## 1. Low deaerator level (DEA-LVL-LO)

Likely causes are a makeup water supply problem, a stuck makeup valve, or feed pump demand above makeup
capacity. Actions: confirm the makeup water valve position, check the makeup water tank level, and reduce
feedwater demand if the level keeps falling.

## 2. Effect on the boiler feed pumps

The deaerator provides the suction head (NPSH) for BFP-101 and BFP-102. A low level reduces suction
pressure within a few minutes, which triggers PMP-SUCT-LO and can make the pumps cavitate. Cavitation shows
up as noise, unstable discharge pressure and high vibration. When low suction pressure and high vibration
happen together, treat the deaerator level as the first suspect.

## 3. Low deaerator pressure (DEA-PRESS-LO)

Check the pegging steam supply and the pressure control valve. Low pressure lowers the saturation
temperature and reduces the margin against flashing at the pump suction.

## 4. Feedwater control valve position deviation (VLV-POS-DEV)

A position deviation on FV-101 means the valve is not where the controller asked it to be. Check the
instrument air pressure to the positioner and the positioner calibration. A deviation that appears right
after feed pump vibration usually reflects the flow disturbance, not a valve fault.
