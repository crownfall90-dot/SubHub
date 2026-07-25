"""Self-check: OTP extraction from raw PVAPins SMS text.

PVAPins returns the whole SMS body, not just the code, so the parser must pick
the code out of surrounding numbers (support phone numbers, years, order ids).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "subhub"))


def main() -> None:
    from pvapins_sms import PVAPinsSMSClient as C

    cases = [
        # Real Flipkart SMS — code appears twice (plain + "#" autofill hint)
        ("LOGIN to your Flipkart account using OTP 544741. DO NOT SHARE this "
         "code with anyone, including delivery agents. #544741", "544741"),
        # A number BEFORE the code must not win
        ("2024: Your Flipkart OTP is 544741", "544741"),
        ("Flipkart 24x7 support 1800 208 9898. Your OTP is 544741", "544741"),
        # A number after the code must not win either
        ("Valid 10 mins. OTP 544741 for order 88123", "544741"),
        ("Your Flipkart verification code is 123456. Valid for 10 mins.", "123456"),
        # Bare code / "#code" / JSON-wrapped body
        ("544741", "544741"),
        ("#987654", "987654"),
        ('{"sms": "Use OTP 112233 to login"}', "112233"),
        # Nothing extractable
        ("no digits here", None),
        ("You have not received any code yet.", None),
    ]

    failures = []
    for text, want in cases:
        got = C._extract_otp(text)
        if got != want:
            failures.append(f"  {text[:60]!r}\n    got {got!r}, want {want!r}")

    if failures:
        print("FAIL otp_extract:")
        print("\n".join(failures))
        sys.exit(1)
    print(f"PASS otp_extract ({len(cases)} cases)")


if __name__ == "__main__":
    main()
