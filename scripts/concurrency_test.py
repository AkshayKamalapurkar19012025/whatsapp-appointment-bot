"""
Manual verification script for double-booking protection.

Walks two different patients up to CONFIRM_BOOKING for the exact same
doctor/date/slot, then fires both final "1" (confirm) messages at the
same instant from two threads. Asserts exactly one booking succeeds.

Not part of the permanent test suite (it drives a running server over
HTTP and needs seeded reference data) -- kept as a standalone repro
script for this review. Run with the app already running against a
reachable database:

    python scripts/concurrency_test.py
"""

import sys
import threading

import httpx

BASE = "http://127.0.0.1:8000/api/booking"


def post(whatsapp_number: str, message: str) -> dict:
    response = httpx.post(
        BASE,
        json={"whatsapp_number": whatsapp_number, "message": message},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def walk_to_confirm_booking(whatsapp_number: str, name: str) -> None:
    post(whatsapp_number, "Hi")
    post(whatsapp_number, name)
    post(whatsapp_number, "main menu")
    post(whatsapp_number, "1")  # book
    post(whatsapp_number, "1")  # department
    post(whatsapp_number, "1")  # doctor
    post(whatsapp_number, "1")  # appointment type
    post(whatsapp_number, "4")  # date option 4
    post(whatsapp_number, "1")  # slot 1
    # now at CONFIRM_BOOKING


def main() -> int:
    number_a = "+919000000001"
    number_b = "+919000000002"

    walk_to_confirm_booking(number_a, "Racer A")
    walk_to_confirm_booking(number_b, "Racer B")

    results = {}
    barrier = threading.Barrier(2)

    def confirm(number: str, key: str) -> None:
        barrier.wait()  # release both threads at the same instant
        results[key] = post(number, "1")

    t1 = threading.Thread(target=confirm, args=(number_a, "a"))
    t2 = threading.Thread(target=confirm, args=(number_b, "b"))

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    print("Racer A result:", results["a"]["next_step"], "-", results["a"].get("error") or results["a"].get("message", "")[:60])
    print("Racer B result:", results["b"]["next_step"], "-", results["b"].get("error") or results["b"].get("message", "")[:60])

    booked = [k for k, v in results.items() if v["next_step"] == "BOOKED"]
    rejected = [k for k, v in results.items() if v["next_step"] != "BOOKED"]

    if len(booked) == 1 and len(rejected) == 1:
        print("PASS: exactly one booking succeeded, one was correctly rejected.")
        return 0
    else:
        print(f"FAIL: booked={booked} rejected={rejected} (expected exactly one of each)")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
