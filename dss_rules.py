"""
dss_rules.py -- Decision Support System (DSS) rules: turns the ML models' per-sensor flags into
plain-language "most common reasons" alerts, each backed by wastewater references.

Not a separate engine: anomaly_engine.py calls diagnose() for every live reading and stores what it
returns in notification_logs.dss (JSON), which the dashboard renders.

Sensor locations (one node): pH and temperature in the AERATION BASIN, turbidity in the CHLORINE
CONTACT TANK.

A rule needs the ML model to flag the sensor AND the reading to be past that node's configured
limit (system_thresholds), in the direction the rule is about. The first compound rule that
matches wins; any flagged sensor it doesn't cover gets its own single-sensor alert, so no flag is
dropped. The wording is deliberately hedged ("most common reasons", "may"): the sensors show a
pattern, not a confirmed cause, and every cause, impact and action below cites a source that
supports it. Every URL was checked to load (October 2026).

References carry a scope: "national" (Philippine law, DENR standards and Philippine incidents),
listed first, and "international" (mostly U.S. operator guides, which cover the hands-on process
troubleshooting no Philippine source does).
"""

REFERENCES = {
    # ---- National (Philippines) ----
    "ph_dao_2016_08": {
        "title": "DENR Administrative Order No. 2016-08: Water Quality Guidelines and General Effluent Standards",
        "publisher": "Department of Environment and Natural Resources (Philippines), 2016 (FAOLEX copy)",
        "url": "https://faolex.fao.org/docs/pdf/phi201041.pdf",
        "kind": "regulation",
        "scope": "national",
    },
    "ph_ra_9275": {
        "title": "Republic Act No. 9275: Philippine Clean Water Act of 2004",
        "publisher": "Congress of the Philippines, 2004 (LawPhil copy)",
        "url": "https://lawphil.net/statutes/repacts/ra2004/ra_9275_2004.html",
        "kind": "regulation",
        "scope": "national",
    },
    "ph_urc_llda": {
        "title": "Universal Robina Corp. v. Laguna Lake Development Authority (wastewater into the Pasig River failed "
                 "TSS, BOD, color and oil/grease standards; fines upheld)",
        "publisher": "Supreme Court of the Philippines, G.R. No. 191427, 30 May 2011",
        "url": "https://lawphil.net/judjuris/juri2011/may2011/gr_191427_2011.html",
        "kind": "incident",
        "scope": "national",
    },
    "ph_vitarich": {
        "title": "'The wastewater looked like mud': EMB goes after Vitarich Corp. (treatment not properly operated, "
                 "Marilao, Bulacan)",
        "publisher": "Philippine Center for Investigative Journalism, 16 February 2021",
        "url": "https://pcij.org/2021/02/16/the-wastewater-looked-like-mud-emb-goes-after-vitarich-corp/",
        "kind": "incident",
        "scope": "national",
    },
    "ph_roxol": {
        "title": "Fishkill cause confirmed as wastewater discharge from Roxol Bioenergy (treatment plant overflows, "
                 "La Carlota, Negros Occidental)",
        "publisher": "Biofuels Digest, 27 February 2017",
        "url": "https://biofuelsdigest.com/fishkill-cause-confirmed-as-wastewater-discharge-from-roxal-bioenergy/",
        "kind": "incident",
        "scope": "national",
    },
    # ---- International ----
    "maine": {
        "title": "Troubleshooting Activated Sludge Processes",
        "publisher": "Maine Department of Environmental Protection",
        "url": "https://www1.maine.gov/dep/water/wwtreatment/troubleshooting_guide.pdf",
        "kind": "guide",
        "scope": "international",
    },
    "nydec": {
        "title": "Activated Sludge Operational Strategies for Nitrogen Removal (Module 2)",
        "publisher": "New York State DEC - Long Island Sound Nitrogen Removal Training Program",
        "url": "https://extapps.dec.ny.gov/docs/water_pdf/module2.pdf",
        "kind": "guide",
        "scope": "international",
    },
    "epa_disinfection": {
        "title": "Wastewater Technology Fact Sheet: Disinfection for Small Systems",
        "publisher": "U.S. Environmental Protection Agency",
        "url": "https://www.epa.gov/sites/default/files/2015-06/documents/disinfection_small.pdf",
        "kind": "guide",
        "scope": "international",
    },
    "epa_ph": {
        "title": "Pretreatment Program pH Requirements for Industrial Users",
        "publisher": "U.S. Environmental Protection Agency",
        "url": "https://www.epa.gov/system/files/documents/2025-09/ph-fact-sheet.pdf",
        "kind": "regulation",
        "scope": "international",
    },
    "epa_prohibitions": {
        "title": "Pretreatment Standards and Requirements: General and Specific Prohibitions",
        "publisher": "U.S. Environmental Protection Agency",
        "url": "https://www.epa.gov/npdes/pretreatment-standards-and-requirements-general-and-specific-prohibitions",
        "kind": "regulation",
        "scope": "international",
    },
    "efc_pumps": {
        "title": "US Territories Wastewater Operator Training Series, Session 6: Collection Systems - Pumping & Force Mains",
        "publisher": "Environmental Finance Center Network (cooperative agreement with U.S. EPA), 3 September 2025",
        "url": "https://swefc.unm.edu/home/wp-content/uploads/2025/09/Collection-Systems-Pumps-and-Force-Mains-9-3-25.pdf",
        "kind": "guide",
        "scope": "international",
    },
    "epa_ii": {
        "title": "Infiltration/Inflow: I/I Analysis and Project Certification",
        "publisher": "U.S. EPA (1985), Washington State Dept. of Ecology Publication 97-03",
        "url": "https://apps.ecology.wa.gov/publications/documents/9703.pdf",
        "kind": "guide",
        "scope": "international",
    },
    # Real incidents with a similar pattern
    "ct_acid": {
        "title": "Connecticut Manufacturer Agrees to $1 Million Fine (acidic discharge killed the "
                 "treatment plant's bacteria)",
        "publisher": "U.S. EPA news release, 19 June 1998",
        "url": "https://www.epa.gov/archive/epapages/newsroom_archive/newsreleases/8d0c550a31b63d0d85256628005d7b2a.html",
        "kind": "incident",
        "scope": "international",
    },
    "mocksville": {
        "title": "Mocksville Wastewater Treatment Plant Impacted By Toxic Discharge",
        "publisher": "Yadkin Riverkeeper, 30 July 2025",
        "url": "https://www.yadkinriverkeeper.org/news/mocksville",
        "kind": "incident",
        "scope": "international",
    },
    "dannon": {
        "title": "Dannon Error Kills Canal Fish (caustic cleaning solution wiped out plant bacteria)",
        "publisher": "The Daily Standard (Celina, Ohio), 15 September 2005",
        "url": "https://dailystandard.com/date/2005/09/15/news/headline2.htm",
        "kind": "incident",
        "scope": "international",
    },
}


def _item(text, *refs):
    return {"text": text, "refs": list(refs)}


# Reused statements, each with the source that supports it
SS_SHIELD = _item("Suspended solids in the chlorine contact tank can shield microorganisms from chlorine "
                  "and increase chlorine demand, so disinfection may be incomplete.", "epa_disinfection")
NITRIF_PH = _item("Nitrification works best between pH 6.5 and 8.0; outside that range it can be "
                  "significantly inhibited.", "nydec")
NITRIF_COLD = _item("Nitrifier growth slows as water cools (roughly halving for every 10 °C drop), so "
                    "ammonia removal may fall behind.", "nydec")
ACID_DISCHARGE = _item("An acidic discharge reaching the plant, for example from an industrial user; "
                       "low influent pH tends to pass through to the aeration basin.", "maine", "epa_ph")
TOXIC_FLOC = _item("A toxic or shock load that inhibits the bacteria, leaving dispersed, very small floc "
                   "that settles poorly and carries over from the clarifier.", "maine")
NITRIF_ALKALINITY = _item("Nitrification consuming alkalinity in low-alkalinity wastewater, which lowers "
                          "the basin's pH.", "maine", "nydec")
STORM_INFLOW = _item("Stormwater or groundwater entering the sewers (inflow/infiltration) during wet "
                     "weather, which can hydraulically overload the plant.", "epa_ii")
CLARIFIER_OVERLOAD = _item("Hydraulic overload of the clarifier: solids are pushed in faster than they are "
                           "returned to the aeration basin, e.g. during storm inflows.", "maine")
HOT_DISCHARGE = _item("A hot industrial discharge; heat that inhibits the plant's biological activity is a "
                      "prohibited discharge.", "epa_prohibitions")

# National (Philippine) standards, for what the reading would mean if it reached the plant's effluent
PH_SOLIDS = _item("High turbidity usually means high suspended solids. Effluent must meet the DENR General "
                  "Effluent Standards, which limit total suspended solids to 70-150 mg/L depending on the "
                  "receiving water's class; violations can bring fines of PHP 10,000-200,000 per day and closure.",
                  "ph_dao_2016_08", "ph_ra_9275")
PH_PH = _item("If this pH reaches the effluent: the DENR General Effluent Standards require pH 6.0-9.0 for "
              "most receiving-water classes.", "ph_dao_2016_08")
PH_TEMP = _item("If this temperature carries through to the discharge: the DENR General Effluent Standards "
                "allow a temperature change of at most 3 °C.", "ph_dao_2016_08")

CONFIRM = _item("Confirm before acting: check the probes, take a grab sample, and run an oxygen uptake "
                "rate (OUR/SOUR) test and a microscope check of the sludge to find the cause.", "maine")
RAISE_PH = _item("If pH is confirmed low, raise it in the aeration basin with lime, soda ash or sodium "
                 "bicarbonate.", "maine")
TRACE_SOURCE = _item("Trace the source in the collection system; discharges below pH 5.0 are prohibited "
                     "under pretreatment rules, and the lasting fix is pretreatment at the source.",
                     "maine", "epa_ph")
REBUILD_BIOMASS = _item("Once the shock has passed, decrease wasting and return rates to rebuild the "
                        "mass of live microorganisms.", "maine")
CHECK_CHLORINE = _item("Check chlorine residual in the contact tank and adjust dosing while turbidity "
                       "is high; reduce solids reaching disinfection where possible.", "epa_disinfection")
CUT_CLARIFIER_LOAD = _item("Reduce the solids load on the clarifier: switch to step feed or contact "
                           "stabilization, or briefly turn off aeration so solids settle in the basin "
                           "(not if the basin discharges from the bottom).", "maine")
LONGER_MCRT = _item("While the water is cold, keep more sludge in the system (longer MCRT/sludge age) so "
                    "nitrification can keep up.", "nydec")
RECOVER_ALKALINITY = _item("Use on/off aeration or an anoxic zone to recover alkalinity; if that isn't "
                           "possible, add alkali as for low influent pH.", "maine")

RULES = {
    "cold_acid_turbidity": {
        "needs": {"ph": "low", "temperature": "low", "turbidity": "high"},
        "severity": "CRITICAL", "category": "PLANT_WIDE", "stage": "Aeration basin + chlorine contact tank",
        "title": "Possible plant-wide upset: cold, acidic aeration basin and turbid chlorine contact tank",
        "causes": [ACID_DISCHARGE, STORM_INFLOW, TOXIC_FLOC],
        "impact": [PH_SOLIDS, PH_PH, PH_TEMP, NITRIF_PH, NITRIF_COLD, SS_SHIELD],
        "actions": [CONFIRM, RAISE_PH, CUT_CLARIFIER_LOAD, CHECK_CHLORINE, TRACE_SOURCE],
        "incidents": ["ph_urc_llda", "ct_acid", "mocksville"],
    },
    "acid_turbidity": {
        "needs": {"ph": "low", "turbidity": "high"},
        "severity": "CRITICAL", "category": "AERATION_CHLORINE", "stage": "Aeration basin + chlorine contact tank",
        "title": "Possible acid shock in the aeration basin with solids carryover to the chlorine contact tank",
        "causes": [ACID_DISCHARGE, TOXIC_FLOC, NITRIF_ALKALINITY],
        "impact": [PH_SOLIDS, PH_PH, NITRIF_PH, SS_SHIELD],
        "actions": [CONFIRM, RAISE_PH, REBUILD_BIOMASS, CHECK_CHLORINE, TRACE_SOURCE],
        "incidents": ["ph_urc_llda", "ct_acid", "mocksville"],
    },
    "alkaline_turbidity": {
        "needs": {"ph": "high", "turbidity": "high"},
        "severity": "CRITICAL", "category": "AERATION_CHLORINE", "stage": "Aeration basin + chlorine contact tank",
        "title": "Possible alkaline (caustic) shock in the aeration basin with solids carryover",
        "causes": [_item("A caustic discharge, such as cleaning chemicals from an industrial user; "
                         "pretreatment rules set pH limits for industrial discharges.", "epa_ph"),
                   TOXIC_FLOC],
        "impact": [PH_SOLIDS, PH_PH, NITRIF_PH, SS_SHIELD],
        "actions": [CONFIRM, REBUILD_BIOMASS, CHECK_CHLORINE,
                    _item("Trace the source in the collection system and check industrial users' pH "
                          "limits.", "epa_ph")],
        "incidents": ["ph_urc_llda", "dannon"],
    },
    "cold_acid": {
        "needs": {"ph": "low", "temperature": "low"},
        "severity": "WARNING", "category": "AERATION_STAGE", "stage": "Aeration basin",
        "title": "Possible cold, acidic inflow to the aeration basin",
        "causes": [STORM_INFLOW, ACID_DISCHARGE],
        "impact": [PH_PH, PH_TEMP, NITRIF_COLD, NITRIF_PH],
        "actions": [CONFIRM, RAISE_PH, LONGER_MCRT,
                    _item("Check the collection system for wet-weather inflow/infiltration.", "epa_ii"),
                    TRACE_SOURCE],
        "incidents": ["ct_acid"],
    },
    "warm_acid": {
        "needs": {"ph": "low", "temperature": "high"},
        "severity": "WARNING", "category": "AERATION_STAGE", "stage": "Aeration basin",
        "title": "Possible alkalinity depletion in the aeration basin",
        "causes": [_item("Warmer water speeding up nitrification (nitrifier growth roughly doubles for "
                         "every 10 °C rise).", "nydec"),
                   NITRIF_ALKALINITY, HOT_DISCHARGE],
        "impact": [PH_PH, PH_TEMP, NITRIF_PH,
                   _item("Temperatures above 40 °C at the plant are not allowed without approval, because "
                         "heat can inhibit the biological process.", "epa_prohibitions")],
        "actions": [CONFIRM, RECOVER_ALKALINITY,
                    _item("Check alkalinity: nitrification consumes about 7.14 mg/L of alkalinity (as "
                          "CaCO3) per mg/L of ammonia-N oxidized.", "nydec"),
                    _item("Check industrial users for hot discharges.", "epa_prohibitions")],
        "incidents": [],
    },
    "cold_turbidity": {
        "needs": {"temperature": "low", "turbidity": "high"},
        "severity": "WARNING", "category": "HYDRAULIC_STORM", "stage": "Aeration basin + chlorine contact tank",
        "title": "Possible storm surge washing solids through the clarifier",
        "causes": [STORM_INFLOW, CLARIFIER_OVERLOAD],
        "impact": [PH_SOLIDS, PH_TEMP, SS_SHIELD, NITRIF_COLD],
        "actions": [CONFIRM, CUT_CLARIFIER_LOAD,
                    _item("Check the return sludge rate and return line; an overloaded or clogged "
                          "return system also washes solids out.", "maine"),
                    CHECK_CHLORINE,
                    _item("If this recurs in wet weather, study inflow/infiltration in the sewers.", "epa_ii")],
        "incidents": ["ph_roxol"],
    },
}

# One per sensor, for a flag no compound rule explains
SINGLE_RULES = {
    "ph": {
        "severity": "WARNING", "category": "AERATION_STAGE", "stage": "Aeration basin",
        "title": "Unusual pH change in the aeration basin",
        "causes": [ACID_DISCHARGE, NITRIF_ALKALINITY],
        "impact": [PH_PH, NITRIF_PH],
        "actions": [CONFIRM,
                    _item("Check influent pH and alkalinity.", "nydec"),
                    _item("If pH is confirmed low, raise it with lime, soda ash or sodium bicarbonate.", "maine")],
        "incidents": [],
    },
    "turbidity": {
        "severity": "WARNING", "category": "DISINFECTION_STAGE", "stage": "Chlorine contact tank",
        "title": "Unusual turbidity change in the chlorine contact tank",
        "causes": [_item("Solids carrying over from the clarifier: hydraulic overload, poor settling, pin "
                         "floc or dispersed growth.", "maine")],
        "impact": [PH_SOLIDS, SS_SHIELD],
        "actions": [_item("Check the clarifier: sludge blanket depth and a settling (settleometer) test "
                          "of the mixed liquor.", "maine"),
                    CHECK_CHLORINE],
        "incidents": ["ph_vitarich", "ph_urc_llda"],
    },
    "temperature": {
        "severity": "WARNING", "category": "AERATION_STAGE", "stage": "Aeration basin",
        "title": "Unusual temperature change in the aeration basin",
        "causes": [STORM_INFLOW, HOT_DISCHARGE],
        "impact": [PH_TEMP,
                   _item("Temperature changes how fast nitrifiers grow (roughly doubling per 10 °C rise, "
                         "halving per 10 °C drop).", "nydec")],
        "actions": [_item("If colder: keep more sludge in the system (longer MCRT) so nitrification keeps "
                          "up.", "nydec"),
                    _item("If hotter: check industrial users for hot discharges.", "epa_prohibitions")],
        "incidents": [],
    },
}

SENSOR_LABEL = {"ph": "pH", "temperature": "temperature", "turbidity": "turbidity"}
SENSOR_TAG = {"ph": "pH", "temperature": "Temperature", "turbidity": "Turbidity"}


def _limit_note(sensor: str, value: float, limits: dict) -> str:
    """Where the reading stands against the node's configured limits."""
    lo, hi, unit = {"ph": (limits.get("min_ph"), limits.get("max_ph"), ""),
                    "temperature": (limits.get("min_temp"), limits.get("max_temp"), " °C"),
                    "turbidity": (None, limits.get("max_turbidity"), " NTU")}[sensor]
    if lo is not None and value < lo:
        return f"Below its {lo:.1f}{unit} limit."
    if hi is not None and value > hi:
        return f"Above its {hi:.1f}{unit} limit."
    span = f"{lo:.1f}–{hi:.1f}{unit}" if lo is not None else f"≤ {hi:.1f}{unit}"
    return f"Within its limits ({span})."


def _evidence(sensors, readings: dict, flagged: dict, limits: dict) -> str:
    """One line per sensor, tagged like the threshold alerts ("[ML Model - pH] ...") so the dashboard
    and the PDF show each on its own line: the model's evidence, then the limit status."""
    return " ".join(f"[ML Model - {SENSOR_TAG[s]}] {flagged[s]} {_limit_note(s, readings[s], limits)}"
                    for s in sensors)


def _directions(readings: dict, limits: dict) -> dict:
    """'low' / 'high' for each sensor past its configured limit; sensors within limits are absent."""
    checks = {
        "ph": (limits.get("min_ph"), limits.get("max_ph")),
        "temperature": (limits.get("min_temp"), limits.get("max_temp")),
        "turbidity": (None, limits.get("max_turbidity")),
    }
    out = {}
    for sensor, (low, high) in checks.items():
        value = readings[sensor]
        if low is not None and value < low:
            out[sensor] = "low"
        elif high is not None and value > high:
            out[sensor] = "high"
    return out


def _package(key: str, rule: dict, summary: str) -> dict:
    """The JSON stored with the alert: the rule's text plus the references it cites."""
    ref_ids = {ref for section in ("causes", "impact", "actions") for item in rule[section] for ref in item["refs"]}
    ref_ids.update(rule["incidents"])
    return {
        "rule": key,
        "severity": rule["severity"],
        "category": rule["category"],
        "stage": rule["stage"],
        "summary": summary,
        "causes": rule["causes"],
        "impact": rule["impact"],
        "actions": rule["actions"],
        "incidents": rule["incidents"],
        "references": {ref: REFERENCES[ref] for ref in sorted(ref_ids)},
    }


def diagnose(readings: dict, flagged: dict, limits: dict) -> list:
    """Alerts for one reading.

    readings: {"ph", "temperature", "turbidity"} values.
    flagged:  sensor -> the ML model's finding message, for each sensor the models flagged.
    limits:   the node's system_thresholds row.
    Returns [(key, title, sensors, dss_json)], at most one compound alert plus a single-sensor
    alert for each flagged sensor the compound alert doesn't cover.
    """
    if not flagged:
        return []
    past = _directions(readings, limits)
    state = {sensor: past.get(sensor) for sensor in flagged}     # flagged and past the limit that way

    results, covered = [], set()
    for key, rule in RULES.items():
        if all(state.get(sensor) == direction for sensor, direction in rule["needs"].items()):
            sensors = list(rule["needs"])
            summary = _evidence(sensors, readings, flagged, limits)
            results.append((key, rule["title"], sensors, _package(key, rule, summary)))
            covered.update(sensors)
            break

    for sensor in ("ph", "temperature", "turbidity"):
        if sensor in flagged and sensor not in covered:
            rule = SINGLE_RULES[sensor]
            summary = _evidence([sensor], readings, flagged, limits)
            results.append((f"single_{sensor}", rule["title"], [sensor], _package(f"single_{sensor}", rule, summary)))
    return results


# --------------------------------------------------------------------------------------------------
# Threshold alerts: the most common reasons and actions for exactly the limits that were crossed,
# per sensor and direction (low pH gets low-pH guidance only, never high-pH). Same wording rules and
# references as the ML diagnoses above.
# --------------------------------------------------------------------------------------------------
THRESHOLD_GUIDANCE = {
    ("ph", "low"): {
        "label": "Low pH", "stage": "Aeration basin",
        "causes": [ACID_DISCHARGE, NITRIF_ALKALINITY],
        "impact": [PH_PH, NITRIF_PH],
        "actions": [CONFIRM, RAISE_PH, _item("Check influent pH and alkalinity.", "nydec"), TRACE_SOURCE],
        "incidents": ["ct_acid"],
    },
    ("ph", "high"): {
        "label": "High pH", "stage": "Aeration basin",
        "causes": [_item("A caustic or alkaline discharge, such as cleaning chemicals from an industrial user; "
                         "pretreatment rules set pH limits for industrial discharges.", "epa_ph")],
        "impact": [PH_PH, NITRIF_PH],
        "actions": [CONFIRM, _item("Trace the source in the collection system and check industrial users' pH "
                                   "limits.", "epa_ph")],
        "incidents": ["dannon"],
    },
    ("turbidity", "high"): {
        "label": "High turbidity", "stage": "Chlorine contact tank",
        "causes": [_item("Solids carrying over from the clarifier: hydraulic overload, poor settling, pin floc or "
                         "dispersed growth.", "maine"), STORM_INFLOW],
        "impact": [PH_SOLIDS, SS_SHIELD],
        "actions": [_item("Check the clarifier: sludge blanket depth and a settling (settleometer) test of the "
                          "mixed liquor.", "maine"), CUT_CLARIFIER_LOAD, CHECK_CHLORINE],
        "incidents": ["ph_urc_llda", "ph_vitarich"],
    },
    ("temperature", "low"): {
        "label": "Low temperature", "stage": "Aeration basin",
        "causes": [STORM_INFLOW],
        "impact": [PH_TEMP, NITRIF_COLD],
        "actions": [LONGER_MCRT, _item("Check the collection system for wet-weather inflow/infiltration.", "epa_ii")],
        "incidents": [],
    },
    ("temperature", "high"): {
        "label": "High temperature", "stage": "Aeration basin",
        "causes": [HOT_DISCHARGE],
        "impact": [PH_TEMP, _item("Temperatures above 40 °C at the plant are not allowed without approval, because "
                                  "heat can inhibit the biological process.", "epa_prohibitions")],
        "actions": [_item("Check industrial users for hot discharges.", "epa_prohibitions")],
        "incidents": [],
    },
    ("level", "low"): {
        "label": "Low water level", "stage": "Tank / pump",
        "causes": [_item("Water being pumped out faster than it comes in, until the level falls to the pump's "
                         "low set point.", "efc_pumps"),
                   _item("A level float or probe giving a false reading: floats can stick in grease and "
                         "debris.", "efc_pumps")],
        "impact": [_item("A pump should not be started or run dry: the water lubricates it, so running dry "
                         "damages it.", "efc_pumps")],
        "actions": [_item("Stop or lock out the pump until the level recovers, and confirm the low-level cut-off "
                          "works.", "efc_pumps"),
                    _item("Inspect and clean the level float or probe, and check the inlet for clogging.", "efc_pumps")],
        "incidents": [],
    },
    ("level", "high"): {
        "label": "High water level", "stage": "Tank / pump",
        "causes": [_item("Inflow exceeding what is pumped out, for example a clogged suction or discharge pipe or "
                         "a pump that has failed.", "efc_pumps"),
                   STORM_INFLOW],
        "impact": [_item("High-level alarms exist to prevent overflows: an overflowing tank spills untreated "
                         "wastewater.", "efc_pumps"),
                   _item("Discharging untreated wastewater into a water body is a prohibited act under the "
                         "Clean Water Act.", "ph_ra_9275")],
        "actions": [_item("Check the outlet pump and its controls, and clear clogged suction or discharge "
                          "pipes.", "efc_pumps"),
                    _item("Inspect the level float or probe for grease and debris.", "efc_pumps")],
        "incidents": ["ph_roxol"],
    },
    ("flow", "high"): {
        "label": "High flow", "stage": "Outflow",
        "causes": [STORM_INFLOW, CLARIFIER_OVERLOAD],
        "impact": [_item("Hydraulic overload pushes solids through the clarifier before they settle, raising "
                         "effluent suspended solids.", "maine"), PH_SOLIDS],
        "actions": [CUT_CLARIFIER_LOAD,
                    _item("If this recurs in wet weather, study inflow/infiltration in the sewers.", "epa_ii")],
        "incidents": ["ph_roxol"],
    },
}


def _threshold_direction(param: str, value: float, limits: dict):
    lo, hi = {"ph": ("min_ph", "max_ph"), "temperature": ("min_temp", "max_temp"), "turbidity": (None, "max_turbidity"),
              "level": ("min_level", "max_level"), "flow": (None, "max_flow")}[param]
    if lo and limits.get(lo) is not None and value < limits[lo]:
        return "low"
    if hi and limits.get(hi) is not None and value > limits[hi]:
        return "high"
    return None


def threshold_guidance(readings: dict, limits: dict, params) -> dict | None:
    """DSS guidance for a threshold alert: only the crossed limits, each sensor in its own direction.
    readings: ph, temperature, turbidity, level, flow. params: the alert's crossed parameters."""
    picked = []
    for param in dict.fromkeys(params):
        direction = _threshold_direction(param, readings[param], limits)
        if (param, direction) in THRESHOLD_GUIDANCE:
            picked.append(THRESHOLD_GUIDANCE[(param, direction)])
    if not picked:
        return None

    def merged(section):
        # Each statement once; with several conditions, prefixed with the one it is about
        out, seen = [], set()
        for g in picked:
            for item in g[section]:
                text = f"{g['label']}: {item['text']}" if len(picked) > 1 else item["text"]
                if text not in seen:
                    seen.add(text)
                    out.append({"text": text, "refs": item["refs"]})
        return out

    rule = {"causes": merged("causes"), "impact": merged("impact"), "actions": merged("actions"),
            "incidents": list(dict.fromkeys(i for g in picked for i in g["incidents"]))}
    ref_ids = {r for sec in ("causes", "impact", "actions") for item in rule[sec] for r in item["refs"]}
    ref_ids.update(rule["incidents"])
    return {
        "rule": "threshold:" + ",".join(f"{p}_{_threshold_direction(p, readings[p], limits)}" for p in dict.fromkeys(params)),
        "severity": None,
        "category": "THRESHOLD",
        "stage": " + ".join(dict.fromkeys(g["stage"] for g in picked)),
        "summary": "",
        **rule,
        "references": {r: REFERENCES[r] for r in sorted(ref_ids)},
    }


# --------------------------------------------------------------------------------------------------
# The DSS Guide (Alerts page): every condition the system can alert on, from the same rules the
# alerts use, so the guide and the alerts never disagree.
# --------------------------------------------------------------------------------------------------
_PARAM_NAME = {"ph": "pH", "temperature": "temperature", "turbidity": "turbidity", "level": "water level", "flow": "flow"}
_NEED_TEXT = {"low": "below its lower limit", "high": "above its upper limit"}


def _guide_entry(group, name, trigger, rule, sensors, severity=None):
    ref_ids = {r for sec in ("causes", "impact", "actions") for item in rule[sec] for r in item["refs"]}
    ref_ids.update(rule["incidents"])
    return {
        "group": group, "name": name, "trigger": trigger, "severity": severity, "stage": rule["stage"],
        "sensors": list(sensors),           # for the guide's sensor buttons
        "causes": rule["causes"], "impact": rule["impact"], "actions": rule["actions"],
        "incidents": rule["incidents"], "references": {r: REFERENCES[r] for r in sorted(ref_ids)},
    }


def guide_entries() -> list:
    entries = [
        _guide_entry("Threshold breach (limits crossed)", g["label"],
                     f"The {_PARAM_NAME[param]} reading is {_NEED_TEXT[direction]}.", g, [param])
        for (param, direction), g in THRESHOLD_GUIDANCE.items()
    ]
    for rule in RULES.values():
        parts = [f"{_PARAM_NAME[s]} {_NEED_TEXT[d]}" for s, d in rule["needs"].items()]
        entries.append(_guide_entry(
            "Anomaly (unusual changes)", rule["title"],
            f"Unusual changes at the same moment: {'; '.join(parts)}.", rule, rule["needs"], rule["severity"]))
    for sensor, rule in SINGLE_RULES.items():
        entries.append(_guide_entry(
            "Anomaly (unusual changes)", rule["title"],
            f"An unusual {_PARAM_NAME[sensor]} change on its own.",
            rule, [sensor], rule["severity"]))
    return entries
