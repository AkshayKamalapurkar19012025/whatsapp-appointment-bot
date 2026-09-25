"""
OPD/HIMS master spec sections 67-68: module licensing/enablement and
degradation. See migrations/0052_module_licensing.sql's own header for
the Licensed/Enabled/Available split and why only these three modules
are in scope.

is_module_available is the one function every gated action (order
creation for LAB/RADIOLOGY, prescribing/dispensing, package billing)
calls before proceeding -- a missing hospital_modules row (a hospital
that's never had this module's license touched) is treated the same
as an explicit licensed=FALSE row: not available.
"""

from app.services.exceptions import ModuleNotFound, ModuleNotLicensed

MODULES: dict[str, dict[str, str]] = {
    "LAB_RADIOLOGY": {
        "name": "Lab / Radiology Orders",
        "degradation": "EXTERNAL",
        "description": (
            "Ordering and resulting LAB/RADIOLOGY tests. Disabled: doctors "
            "route those tests through External Referral instead; existing "
            "orders and results stay fully visible."
        ),
    },
    "PHARMACY": {
        "name": "Pharmacy",
        "degradation": "BLOCKED",
        "description": (
            "Prescribing and dispensing medicine. Disabled: new prescribing "
            "and dispensing is blocked; existing prescriptions and stock "
            "history stay visible."
        ),
    },
    "PACKAGES": {
        "name": "Packages / Insurance Billing",
        "degradation": "HIDDEN",
        "description": (
            "Priced bundles billed as a single line item. Disabled: creating "
            "packages and billing via a package is blocked; existing "
            "package-based charges and invoices stay untouched."
        ),
    },
}


def list_hospital_modules_service(cur, hospital_id: int) -> list[dict]:
    cur.execute(
        "SELECT module_key, licensed, enabled FROM hospital_modules WHERE hospital_id = %s",
        (hospital_id,),
    )
    rows = {key: {"licensed": licensed, "enabled": enabled} for key, licensed, enabled in cur.fetchall()}

    return [
        {
            "module_key": key,
            "name": MODULES[key]["name"],
            "description": MODULES[key]["description"],
            "degradation": MODULES[key]["degradation"],
            "licensed": rows.get(key, {}).get("licensed", False),
            "enabled": rows.get(key, {}).get("enabled", False),
            "available": rows.get(key, {}).get("licensed", False) and rows.get(key, {}).get("enabled", False),
        }
        for key in MODULES
    ]


def set_module_licensed_service(cur, hospital_id: int, module_key: str, *, licensed: bool, staff_id: int) -> dict:
    if module_key not in MODULES:
        raise ModuleNotFound()

    cur.execute(
        """
        INSERT INTO hospital_modules (hospital_id, module_key, licensed, enabled, updated_by)
        VALUES (%s, %s, %s, FALSE, %s)
        ON CONFLICT (hospital_id, module_key) DO UPDATE
        SET licensed = EXCLUDED.licensed,
            -- Un-licensing forces enabled off too (a hospital can't stay
            -- "enabled" on a module it's no longer licensed for);
            -- re-licensing never auto-enables -- that stays a separate,
            -- deliberate action on the hospital-admin enablement screen.
            enabled = hospital_modules.enabled AND EXCLUDED.licensed,
            updated_by = EXCLUDED.updated_by,
            updated_at = NOW()
        RETURNING module_key, licensed, enabled
        """,
        (hospital_id, module_key, licensed, staff_id),
    )
    key, licensed_now, enabled_now = cur.fetchone()
    return {"module_key": key, "licensed": licensed_now, "enabled": enabled_now}


def set_module_enabled_service(cur, hospital_id: int, module_key: str, *, enabled: bool, staff_id: int) -> dict:
    if module_key not in MODULES:
        raise ModuleNotFound()

    cur.execute(
        "SELECT licensed FROM hospital_modules WHERE hospital_id = %s AND module_key = %s",
        (hospital_id, module_key),
    )
    row = cur.fetchone()
    licensed = row[0] if row else False

    if enabled and not licensed:
        raise ModuleNotLicensed()

    cur.execute(
        """
        INSERT INTO hospital_modules (hospital_id, module_key, licensed, enabled, updated_by)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (hospital_id, module_key) DO UPDATE
        SET enabled = EXCLUDED.enabled, updated_by = EXCLUDED.updated_by, updated_at = NOW()
        RETURNING module_key, licensed, enabled
        """,
        (hospital_id, module_key, licensed, enabled, staff_id),
    )
    key, licensed_now, enabled_now = cur.fetchone()
    return {"module_key": key, "licensed": licensed_now, "enabled": enabled_now}


def is_module_available(cur, hospital_id: int, module_key: str) -> bool:
    cur.execute(
        "SELECT licensed AND enabled FROM hospital_modules WHERE hospital_id = %s AND module_key = %s",
        (hospital_id, module_key),
    )
    row = cur.fetchone()
    return bool(row and row[0])
