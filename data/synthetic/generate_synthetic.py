#!/usr/bin/env python3
"""generate_synthetic.py — Step 1 of the personal-finance-agent build sequence.

Produces 15 years (2011-01-01 → 2025-12-31) of synthetic UK personal-finance
transactions covering every category branch in the canonical rule list at
`classifier/rules_seed.py` (mirrored here in redacted form per SPEC_AGENT §9
— the original chain lived in `bank_statement_parser.py`, now
`classifier/budget_importer.py` since B3).

Output: data/synthetic/transactions_synthetic.csv

CSV columns match the `transactions` table from SPEC_AGENT §4 (no id):
    date, account_number, amount, type, memo,
    account_currency, account_type, account_name,
    category_main, category_sub, category_sub2, details,
    data_source

Design notes
------------
* Memos are drawn from pools that match the real classifier's regexes, so
  the same `categories()` function would re-classify these rows into the same
  buckets they're already labelled with. This is what makes the dataset a
  safety-net for the real-data path (SPEC_AGENT §8 Step 1).
* A small fraction (~5%) of variable spend uses memos the classifier won't
  match — those rows are labelled `Missing` and become the agent's
  classification backlog. They're deliberately recognisable real merchants
  (NETFLIX, DISHOOM, …) so the agent has something believable to learn.
* Deterministic via fixed seed.
* Personally identifying values are redacted (account numbers, employer
  names, cleaner names, cardholder name + card number, loan reference)
  per SPEC_AGENT §9.

Note: the real classifier has a `Health` main category (Dentist, Eyecare,
General/Medicine, GP) which is NOT in SPEC_AGENT §4. This generator includes
Health because the safety-net property requires matching the classifier as
it actually exists. The spec taxonomy needs updating.
"""

from __future__ import annotations

import csv
import random
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SEED = 42
START_DATE = date(2011, 1, 1)
END_DATE = date(2025, 12, 31)
OUTPUT_PATH = Path(__file__).parent / "transactions_synthetic.csv"

# Annual inflation, applied multiplicatively from START_DATE to amounts.
INFLATION_RATE = 0.025

# Probability that a variable-spend transaction's memo is swapped for one the
# classifier doesn't recognise (and the row gets labelled Missing).
NOISE_RATIO = 0.05

# Account constants: (account_number, account_type, account_name)
ACCT_CURRENT = ("ACCOUNT_CURRENT", "Current Account", "Current")
ACCT_SAVINGS = ("ACCOUNT_SAVINGS", "Savings", "Pot of Gold")
ACCT_BARCLAYCARD = ("ACCOUNT_BARCLAYCARD", "Credit Card", "Barclaycard")
ACCT_AMEX = ("ACCOUNT_AMEX", "Credit Card", "Amex")
ACCT_SAINSBURY = ("ACCOUNT_SAINSBURY", "Credit Card", "Sainsbury")

# Stepwise salary history: (effective_date, monthly_net, employer_memo)
# Employer memos use the redacted forms from SPEC_AGENT §9.
SALARY_EVENTS = [
    (date(2011, 1, 1), 3200.00, "COMPANY_A SALARY"),
    (date(2013, 4, 1), 3600.00, "COMPANY_A SALARY"),
    (date(2015, 7, 1), 4100.00, "COMPANY_B SALARY"),
    (date(2018, 10, 1), 4600.00, "COMPANY_B SALARY"),
    (date(2021, 1, 1), 5200.00, "COMPANY_C SALARY"),
    (date(2023, 4, 1), 5800.00, "COMPANY_C SALARY"),
]

# Mortgage events: (effective_date, monthly_payment, memo)
# The classifier matches "^MTG" or "ACCORD MORTGAGES" on Current account only.
MORTGAGE_EVENTS = [
    (date(2011, 1, 1), 1050.00, "MTG 80451771"),
    (date(2016, 6, 1), 1180.00, "ACCORD MORTGAGES LTD"),  # remortgage
    (date(2022, 3, 1), 1420.00, "ACCORD MORTGAGES LTD"),  # rate rise
]

# CSV header in the order the transactions table expects (sans id).
COLUMNS = [
    "date",
    "account_number",
    "amount",
    "type",
    "memo",
    "account_currency",
    "account_type",
    "account_name",
    "category_main",
    "category_sub",
    "category_sub2",
    "details",
    "data_source",
]


# ---------------------------------------------------------------------------
# Transaction dataclass
# ---------------------------------------------------------------------------

@dataclass
class Txn:
    date: date
    account: tuple
    amount: float
    type: str
    memo: str
    cat_main: str
    cat_sub: str | None = None
    cat_sub2: str | None = None
    details: str | None = None

    def to_row(self) -> dict:
        acct_number, acct_type, acct_name = self.account
        return {
            "date": self.date.isoformat(),
            "account_number": acct_number,
            "amount": f"{self.amount:.2f}",
            "type": self.type,
            "memo": self.memo,
            "account_currency": "£",
            "account_type": acct_type,
            "account_name": acct_name,
            "category_main": self.cat_main,
            "category_sub": self.cat_sub or "",
            "category_sub2": self.cat_sub2 or "",
            "details": self.details or "",
            "data_source": "synthetic",
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def inflation(d: date) -> float:
    years = (d - START_DATE).days / 365.25
    return (1 + INFLATION_RATE) ** years


def jitter(amount: float, pct: float = 0.06) -> float:
    return amount * (1 + random.uniform(-pct, pct))


def safe_day(year: int, month: int, day: int) -> date:
    """Return date(year, month, day), clamping day to the month's last day."""
    last = monthrange(year, month)[1]
    return date(year, month, min(day, last))


def iter_months(start: date, end: date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def iter_dates(start: date, end: date):
    d = start
    one = timedelta(days=1)
    while d <= end:
        yield d
        d += one


def latest_at(events: list[tuple[date, str]], d: date) -> str:
    cur = events[0][1]
    for ev in events:
        if ev[0] <= d:
            cur = ev[1]
    return cur


def salary_for(d: date) -> tuple[float, str]:
    cur = SALARY_EVENTS[0]
    for ev in SALARY_EVENTS:
        if ev[0] <= d:
            cur = ev
    return cur[1], cur[2]


def mortgage_for(d: date) -> tuple[float, str]:
    cur = MORTGAGE_EVENTS[0]
    for ev in MORTGAGE_EVENTS:
        if ev[0] <= d:
            cur = ev
    return cur[1], cur[2]


# ---------------------------------------------------------------------------
# Merchant pools — every entry matches a real-classifier regex except
# NOISE_MEMOS which is built to NOT match (→ Missing).
# ---------------------------------------------------------------------------

# Shopping/Groceries (no sub2) — matches `GAILS|IRISH MEAT MARKET|O'Farrells|^HARRYS`
GROCERY_LOCAL = ["GAILS BAKERY", "IRISH MEAT MARKET LTD", "O'Farrells Butchers", "HARRYS DELI"]

# Shopping/Groceries/Supermarket
GROCERY_SUPER = [
    "TESCO STORES 2384",
    "TESCO SELF SERVICE 4521",
    "SAINSBURY'S S/MKTS",
    "SAINSBURYS TO YOU",
    "WAITROSE 0123",
    "MORRISONS PETROL",
    "M&S SIMPLY FOOD",
    "MARKS & SPENCER",
]

# Shopping/Groceries/Corner Shop
GROCERY_CORNER = [
    "SAVERS HEALTH & BE",
    "EURO SUPERMARKET",
    "SUPERSAVE LONDON",
    "GOODGE STREET NEWS",
    "YOUR LOCAL STORE",
    "NISA LOCAL",
    "FURNESS STORE",
    "CASTLE NEWS LTD",
    "DIAMOND NEWS",
    "NASH SUPERMARKET",
    "SJ NEWS",
    "TEX COLLEGE PARK",
    "CORNER XPRESS",
]

# Shopping/Groceries/veg box
VEG_BOX = ["RIVERFORD ORGANIC", "RIVERFORD FARMS"]

# Shopping/Groceries/wine
WINE = ["JAZZ WINE MART", "NAKED WINES UK"]

# Shopping/Household (no sub2)
HOUSEHOLD_SHOP = ["HOMESENSE LONDON", "EBAY UK", "EBAY MARKETPLACE"]

# Shopping/Household/DIY  (HOMEBASE only — B&Q/SCREWFIX/WICKES are matched
# earlier by the Maintenance rule)
DIY = ["HOMEBASE LTD"]

# Shopping/Clothes
CLOTHES = [
    "TK MAXX LONDON",
    "TKMAXX KILBURN",
    "SPORTSDIRECT.COM",
    "CHARLES TYRWHITT",
    "UNIQLO UK LTD",
    "ZARA UK",
]

# Shopping/electronics/camera
ELECTRONICS = ["CURRYS PCWORLD", "CURRYS DIGITAL"]

# Transport/Automotive/Petrol
PETROL = ["SHELL SERVICE STN", "TESCO PFS", "TESCO PAY AT PUMP", "PETROL EXPRESS"]

# Transport/taxi
TAXI = ["UBER   TRIP HELP.UBER.COM", "CABVISION NETWORK", "VERIFONE TAXI", "CMT UK LTD"]

# Transport/Automotive/parking & fees
PARKING = ["PAYBYPHONE PARKING", "APCOA PARKING", "JUSTPARK.COM", "RINGGO LONDON"]

# Transport/tube
TUBE = ["TFL TRAVEL CH", "LUL TICKET MACHINE", "OYSTERCARD TOPUP"]

# Leisure/food/drinks/restaurant — also catches PRET when >=£5
RESTAURANTS = [
    "DELIVEROO",
    "JUST EAT.CO.UK",
    "UBER EATS HELP.UBER.COM",
    "PIZZA EXPRESS",
    "NANDOS CHICKEN",
    "FRANCO MANCA",
    "HONEST BURGER",
    "CHIPOTLE LONDON",
    "ZIZZI RESTAURANT",
    "GOURMET BURGER KIT",
    "MCDONALDS RESTAURANT",
    "BURGER KING",
    "TORTILLA EUSTON",
    "KFC LONDON",
    "VITAL INGREDIENT",
]

# Leisure/food/drinks/pub
PUBS = [
    "THE WOODMAN PUB",
    "BREWDOG SHOREDITCH",
    "CAMDEN HEAD",
    "WILLIAM IV PUB",
    "ROSE AND CROWN",
    "THE SHIP TAVERN",
    "FITZROY TAVERN",
    "MASONS ARMS",
    "BULL & BUSH PH",
    "THE OLD NICK",
    "DUKE OF YORK",
    "ROYAL OAK PUB",
    "THE GALLERY PUB",
    "Prince Of Wales",
]

# Leisure/food/drinks/café (Pret <£5 also lands here via separate rule)
CAFES = [
    "CAFFE NERO",
    "COSTA COFFEE",
    "STARBUCKS COFFEE",
    "GREGGS PLC",
    "KAFFEINE LONDON",
    "BLACK SHEEP COFFEE",
    "BLANK STREET COFFEE",
]

# Leisure/entertainment
ENTERTAINMENT = ["NATIONAL LOTTERY", "STARS POKER", "UDEMY ONLINE COURSE"]

# Leisure/sport/gym
GYM = ["PURE GYM LTD", "EASY GYM", "ONELIFE FITNESS", "STUDIO SOCIETY LDN"]

# House/Maintenance/TBC/TBC
MAINTENANCE = [
    "TOOLSTATION LTD",
    "B & Q WAREHOUSE",
    "B&Q FINCHLEY",
    "SCREWFIX DIRECT",
    "WICKES STORE",
    "BUILDER DEPOT",
    "M P MORAN & SONS",
    "EUROKEN SUPPLIES",
]

# Bills/utilities/water
WATER = ["THAMES WATER", "AFFINITY WATER LTD"]

# Bills/utilities/gas/elec
ENERGY = ["E.ON ENERGY", "EDF ENERGY"]

# Bills/utilities/council tax
COUNCIL = ["L B BRENT COUNCIL", "BARNET BOROUGH COU", "HARINGEY COUNCIL", "LBB COUNCIL TAX DD"]

# Bills/utilities/Mobile Phone
MOBILE = ["VODAFONE LTD", "O2 UK LTD", "H3G HUTCHINSON 3G", "WWW.THREE.CO.UK"]

# Bills/utilities/broadband
BROADBAND = ["SKY DIGITAL", "PLUSNET PLC", "PNET3462156"]

# Bills/Charity
CHARITY = ["BRITISH RED CROSS", "UNICEF UK"]

# Bills/Bank Fees
BANK_FEES = ["INTEREST CHARGE", "CHARGES OVERDRAFT", "BLUE REWARDS", "EXPERIAN UK"]

# Travel/accommodation/hotel (A2 — new taxonomy entry, baseline merchants
# so the category exists in the DB without auto-classifying AIRBNB).
TRAVEL_HOTELS = ["BOOKING.COM", "HILTON LONDON", "MARRIOTT HOTELS"]

# Transport/rail (A2 — new sub. TRAINLINE stays in NOISE_MEMOS as a Missing).
RAIL_OPERATORS = ["AVANTI WEST COAST", "LNER TICKETS", "GWR TRAINS",
                  "SOUTHEASTERN RAIL"]

# Leisure/subscription/video (A2 — new sub2. NETFLIX & DISNEY+ stay
# in NOISE_MEMOS as Missing.).
VIDEO_SUBS = ["NOW TV SUBSCRIPTION", "BRITBOX UK", "APPLE TV+"]

# Cleaners (redacted from EMELYN COMINTAN / CELY CASTILLO / CELY NAVARRO)
CLEANERS_EVENTS = [
    (date(2011, 1, 1), "CLEANER_A"),
    (date(2016, 4, 1), "CLEANER_B"),
    (date(2020, 9, 1), "CLEANER_C"),
]

# Memos the classifier will NOT match — these become `Missing` and feed the
# agent's classification backlog. All are recognisable real merchants so the
# agent has plausible work to do.
NOISE_MEMOS = [
    "NETFLIX.COM",
    "DISHOOM SHOREDITCH",
    "WAGAMAMA SOHO",
    "PADELLA RESTAURANT",
    "DISNEY+ SUBSCRIPTION",
    "APPLE.COM/BILL",
    "STEAM GAMES PURCHASE",
    "AIRBNB UK",
    "GOOGLE *YOUTUBE PRE",
    "PAYPAL *UNKNOWN MRC",
    "REVOLUT TOPUP",
    "GOCARDLESS-MERCHANT",
    "TRAINLINE.COM",
    "WHSMITH STATION",
    "BOOTH POLAROID UK",
]


def pick(pool: list[str]) -> str:
    """Weighted pick — earlier entries are more frequent (feels more realistic)."""
    n = len(pool)
    weights = [n - i for i in range(n)]
    return random.choices(pool, weights=weights, k=1)[0]


def maybe_noise(t: Txn) -> Txn:
    """With small probability swap the memo for one the classifier won't match.

    The row's category fields are blanked to 'Missing' to mirror what would
    happen if the same memo were fed through the real `categories()` function.
    """
    if random.random() < NOISE_RATIO:
        t.memo = f"{random.choice(NOISE_MEMOS)} {random.randint(100, 9999)}"
        t.cat_main = "Missing"
        t.cat_sub = None
        t.cat_sub2 = None
        t.details = None
    return t


# ---------------------------------------------------------------------------
# Recurring generators (income, fixed outgoings, subscriptions)
# ---------------------------------------------------------------------------

def gen_salary(out: list[Txn]) -> None:
    """Monthly salary credited on or near the 25th (bumped back to Fri on weekends)."""
    for y, m in iter_months(START_DATE, END_DATE):
        d = safe_day(y, m, 25)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        amount, memo = salary_for(d)
        out.append(
            Txn(
                date=d,
                account=ACCT_CURRENT,
                amount=round(jitter(amount, 0.01), 2),
                type="FPI",
                memo=memo,
                cat_main="Income",
                cat_sub="Salary",
            )
        )


def gen_mortgage(out: list[Txn]) -> None:
    """Monthly mortgage on the 1st (Current account only — that's what the rule requires)."""
    for y, m in iter_months(START_DATE, END_DATE):
        d = safe_day(y, m, 1)
        amount, memo = mortgage_for(d)
        out.append(
            Txn(
                date=d,
                account=ACCT_CURRENT,
                amount=-round(amount, 2),
                type="DD",
                memo=memo,
                cat_main="House",
                cat_sub="Mortgage",
            )
        )


def gen_council_tax(out: list[Txn]) -> None:
    """Council tax: UK convention is 10 instalments Apr–Jan; we use Feb–Nov."""
    council_history = [
        (date(2011, 1, 1), "L B BRENT COUNCIL"),
        (date(2018, 4, 1), "BARNET BOROUGH COU"),
        (date(2024, 4, 1), "HARINGEY COUNCIL"),
    ]
    for y, m in iter_months(START_DATE, END_DATE):
        if m in (12, 1):
            continue
        d = safe_day(y, m, 4)
        base = 148.00 * inflation(d)
        out.append(
            Txn(
                date=d,
                account=ACCT_CURRENT,
                amount=-round(jitter(base, 0.02), 2),
                type="DD",
                memo=latest_at(council_history, d),
                cat_main="Bills",
                cat_sub="utilities",
                cat_sub2="council tax",
            )
        )


SKYPE_END = date(2015, 12, 31)  # legacy landline/Skype era — merged into Mobile Phone


def gen_utilities(out: list[Txn]) -> None:
    """Water, gas/elec, broadband, mobile, TV licence — monthly DDs.

    Pre-2016 also includes monthly Skype credit (landline-era), categorised
    as Mobile Phone since the legacy `phone` sub2 has been merged.
    """
    water_history = [(date(2011, 1, 1), "THAMES WATER"), (date(2019, 6, 1), "AFFINITY WATER LTD")]
    energy_history = [(date(2011, 1, 1), "E.ON ENERGY"), (date(2018, 11, 1), "EDF ENERGY")]
    broadband_history = [
        (date(2011, 1, 1), "PLUSNET PLC"),
        (date(2017, 2, 1), "SKY DIGITAL"),
        (date(2023, 8, 1), "PNET3462156"),
    ]
    mobile_history = [
        (date(2011, 1, 1), "VODAFONE LTD"),
        (date(2016, 11, 1), "O2 UK LTD"),
        (date(2022, 7, 1), "H3G HUTCHINSON 3G"),
    ]

    for y, m in iter_months(START_DATE, END_DATE):
        # Water (6th)
        d = safe_day(y, m, 6)
        out.append(
            Txn(
                date=d, account=ACCT_CURRENT,
                amount=-round(jitter(34.00 * inflation(d), 0.04), 2),
                type="DD", memo=latest_at(water_history, d),
                cat_main="Bills", cat_sub="utilities", cat_sub2="water",
            )
        )

        # Gas/elec (8th) — seasonal swing
        d = safe_day(y, m, 8)
        seasonal = 1.45 if m in (11, 12, 1, 2) else (0.65 if m in (6, 7, 8) else 1.0)
        out.append(
            Txn(
                date=d, account=ACCT_CURRENT,
                amount=-round(jitter(95.00 * inflation(d) * seasonal, 0.08), 2),
                type="DD", memo=latest_at(energy_history, d),
                cat_main="Bills", cat_sub="utilities", cat_sub2="gas/elec",
            )
        )

        # Broadband (12th)
        d = safe_day(y, m, 12)
        out.append(
            Txn(
                date=d, account=ACCT_CURRENT,
                amount=-round(jitter(36.00 * inflation(d), 0.03), 2),
                type="DD", memo=latest_at(broadband_history, d),
                cat_main="Bills", cat_sub="utilities", cat_sub2="broadband",
            )
        )

        # Mobile (15th)
        d = safe_day(y, m, 15)
        out.append(
            Txn(
                date=d, account=ACCT_CURRENT,
                amount=-round(jitter(22.00 * inflation(d), 0.05), 2),
                type="DD", memo=latest_at(mobile_history, d),
                cat_main="Bills", cat_sub="utilities", cat_sub2="Mobile Phone",
            )
        )

        # Legacy Skype credit (landline era, pre-2016). Categorised as Mobile
        # Phone because the `phone` sub2 has been merged.
        if date(y, m, 1) <= SKYPE_END:
            d = safe_day(y, m, 11)
            out.append(
                Txn(
                    date=d, account=ACCT_CURRENT,
                    amount=-round(jitter(10.00 * inflation(d), 0.05), 2),
                    type="DD", memo="SKYPE COMMUNICATIONS",
                    cat_main="Bills", cat_sub="utilities", cat_sub2="Mobile Phone",
                )
            )

        # TV licence (20th)
        d = safe_day(y, m, 20)
        out.append(
            Txn(
                date=d, account=ACCT_CURRENT,
                amount=-round(jitter(12.50 * inflation(d), 0.01), 2),
                type="DD", memo="TV LICENSE MTHLY",
                cat_main="Bills", cat_sub="utilities", cat_sub2="TV License",
            )
        )


def gen_cleaner(out: list[Txn]) -> None:
    """Weekly cleaner on Wednesdays — alternates between CLEANER_A/B/C over years."""
    rate = 55.00
    d = START_DATE
    while d.weekday() != 2:
        d += timedelta(days=1)
    while d <= END_DATE:
        out.append(
            Txn(
                date=d, account=ACCT_CURRENT,
                amount=-round(jitter(rate * inflation(d), 0.05), 2),
                type="TFR", memo=latest_at(CLEANERS_EVENTS, d),
                cat_main="Bills", cat_sub="Household", cat_sub2="cleaner",
            )
        )
        d += timedelta(days=7)


def gen_subscriptions(out: list[Txn]) -> None:
    """Monthly subscriptions: Amazon Prime, Spotify, The Economist, gym."""
    for y, m in iter_months(START_DATE, END_DATE):
        d0 = date(y, m, 1)

        d = safe_day(y, m, 3)
        out.append(
            Txn(
                date=d, account=ACCT_CURRENT,
                amount=-round(jitter(7.99 * inflation(d), 0.01), 2),
                type="DD", memo="AMAZON PRIME MEMBER",
                cat_main="Leisure", cat_sub="subscription", cat_sub2="amazon",
            )
        )

        d = safe_day(y, m, 10)
        out.append(
            Txn(
                date=d, account=ACCT_CURRENT,
                amount=-round(jitter(9.99 * inflation(d), 0.01), 2),
                type="DD", memo="SPOTIFY UK",
                cat_main="Leisure", cat_sub="subscription", cat_sub2="music",
            )
        )

        if d0 >= date(2013, 1, 1):
            d = safe_day(y, m, 17)
            out.append(
                Txn(
                    date=d, account=ACCT_CURRENT,
                    amount=-round(jitter(13.00 * inflation(d), 0.01), 2),
                    type="DD", memo="THE ECONOMIST",
                    cat_main="Leisure", cat_sub="subscription", cat_sub2="newspapers",
                )
            )

        d = safe_day(y, m, 2)
        out.append(
            Txn(
                date=d, account=ACCT_CURRENT,
                amount=-round(jitter(38.00 * inflation(d), 0.02), 2),
                type="DD", memo=pick(GYM),
                cat_main="Leisure", cat_sub="sport", cat_sub2="gym",
            )
        )


def gen_video_subs(out: list[Txn]) -> None:
    """Monthly video streaming, starting 2018-01 (era when the user picked
    these up). One subscription per merchant, all running concurrently."""
    for y, m in iter_months(date(2018, 1, 1), END_DATE):
        for i, merchant in enumerate(VIDEO_SUBS):
            d = safe_day(y, m, 5 + i * 7)
            base = {"NOW TV SUBSCRIPTION": 9.99,
                    "BRITBOX UK": 5.99,
                    "APPLE TV+": 6.99}[merchant]
            out.append(
                Txn(
                    date=d, account=ACCT_CURRENT,
                    amount=-round(jitter(base * inflation(d), 0.01), 2),
                    type="DD", memo=merchant,
                    cat_main="Leisure", cat_sub="subscription",
                    cat_sub2="video",
                )
            )


def gen_travel_hotels(out: list[Txn]) -> None:
    """~1 hotel booking per year, mostly between Apr–Sep. Charged to Amex."""
    for y, m in iter_months(START_DATE, END_DATE):
        if m not in (4, 6, 9):
            continue
        if random.random() > 0.35:
            continue
        d = safe_day(y, m, random.randint(5, 25))
        merchant = pick(TRAVEL_HOTELS)
        out.append(
            Txn(
                date=d, account=ACCT_AMEX,
                amount=-round(jitter(280.00 * inflation(d), 0.30), 2),
                type="PURCHASE", memo=f"{merchant} {random.randint(1000, 9999)}",
                cat_main="Travel", cat_sub="accommodation",
                cat_sub2="hotel",
            )
        )


def gen_rail(out: list[Txn]) -> None:
    """~6 train tickets per year. Charged to Current or Amex."""
    for y, m in iter_months(START_DATE, END_DATE):
        n_trips = random.choices([0, 1, 2], weights=[7, 4, 1])[0]
        for _ in range(n_trips):
            d = safe_day(y, m, random.randint(2, 27))
            merchant = pick(RAIL_OPERATORS)
            account = random.choice([ACCT_CURRENT, ACCT_AMEX])
            out.append(
                Txn(
                    date=d, account=account,
                    amount=-round(jitter(48.00 * inflation(d), 0.50), 2),
                    type="PURCHASE", memo=f"{merchant} {random.randint(100, 9999)}",
                    cat_main="Transport", cat_sub="rail",
                )
            )


def gen_loan(out: list[Txn]) -> None:
    """Personal loan running 2014-06 → 2019-05 (LENDER_NAME_REFERENCE)."""
    for y, m in iter_months(date(2014, 6, 1), date(2019, 5, 1)):
        d = safe_day(y, m, 18)
        out.append(
            Txn(
                date=d, account=ACCT_CURRENT,
                amount=-238.50, type="DD", memo="LENDER_NAME_REFERENCE",
                cat_main="Bills", cat_sub="loan",
            )
        )


def gen_wren_loan(out: list[Txn]) -> None:
    """Kitchen renovation paid via Barclays Partner Finance over 24 months from 2017-06."""
    for y, m in iter_months(date(2017, 6, 1), date(2019, 5, 1)):
        d = safe_day(y, m, 24)
        out.append(
            Txn(
                date=d, account=ACCT_CURRENT,
                amount=-279.00, type="DD", memo="BARCLAYS PRTNR FIN",
                cat_main="House", cat_sub="Maintenance",
                cat_sub2="kitchen/bathroom", details="wren repayment",
            )
        )


def gen_charity(out: list[Txn]) -> None:
    """Monthly charity DD plus occasional one-off donations."""
    for y, m in iter_months(START_DATE, END_DATE):
        d = safe_day(y, m, 28)
        out.append(
            Txn(
                date=d, account=ACCT_CURRENT,
                amount=-15.00, type="DD", memo="BRITISH RED CROSS",
                cat_main="Bills", cat_sub="Charity",
            )
        )
        if random.random() < 0.08:
            d2 = safe_day(y, m, random.randint(5, 26))
            out.append(
                Txn(
                    date=d2, account=ACCT_CURRENT,
                    amount=-round(random.choice([10, 20, 25, 50]), 2),
                    type="FPO", memo=random.choice(CHARITY),
                    cat_main="Bills", cat_sub="Charity",
                )
            )


def gen_road_tax(out: list[Txn]) -> None:
    """Annual road tax."""
    for y in range(START_DATE.year, END_DATE.year + 1):
        d = date(y, 3, 12)
        out.append(
            Txn(
                date=d, account=ACCT_CURRENT,
                amount=-round(jitter(165.00 * inflation(d), 0.01), 2),
                type="PAYMENT", memo="DVLA VEHICLE TAX",
                cat_main="Transport", cat_sub="Automotive", cat_sub2="Road tax",
            )
        )


def gen_savings_transfer(out: list[Txn]) -> None:
    """Monthly Current → Pot of Gold transfer with matching credit on the savings side."""
    for y, m in iter_months(START_DATE, END_DATE):
        d = safe_day(y, m, 26)
        amount = round(jitter(400.00 * inflation(d), 0.10), 2)
        out.append(
            Txn(
                date=d, account=ACCT_CURRENT, amount=-amount,
                type="TFR", memo="POT OF GOLD TRANSFER",
                cat_main="Savings", cat_sub="Transfer",
            )
        )
        out.append(
            Txn(
                date=d, account=ACCT_SAVINGS, amount=amount,
                type="TFR", memo="POT OF GOLD CREDIT",
                cat_main="Savings", cat_sub="Transfer",
            )
        )


def gen_savings_interest(out: list[Txn]) -> None:
    """Monthly savings interest — `INTEREST PAID GROSS` matches the rule."""
    for y, m in iter_months(START_DATE, END_DATE):
        d = safe_day(y, m, monthrange(y, m)[1])
        years_in = (d - START_DATE).days / 365.25
        rate = 0.012 if d < date(2022, 6, 1) else 0.045
        balance_proxy = 400 * 12 * years_in
        monthly = max(0.50, balance_proxy * rate / 12)
        out.append(
            Txn(
                date=d, account=ACCT_SAVINGS,
                amount=round(jitter(monthly, 0.05), 2),
                type="INT", memo="INTEREST PAID GROSS",
                cat_main="Savings", cat_sub="Interest",
            )
        )


def gen_bank_fees(out: list[Txn]) -> None:
    """Occasional overdraft interest + monthly Blue Rewards fee (positive — Barclays reward)."""
    for y, m in iter_months(START_DATE, END_DATE):
        if random.random() < 0.12:
            d = safe_day(y, m, 28)
            out.append(
                Txn(
                    date=d, account=ACCT_CURRENT,
                    amount=-round(random.uniform(3.50, 22.00), 2),
                    type="DD", memo="INTEREST CHARGE",
                    cat_main="Bills", cat_sub="Bank Fees",
                )
            )
        # Blue Rewards cashback (positive small credit) — appears 2015 onwards
        if y >= 2015:
            d = safe_day(y, m, 7)
            out.append(
                Txn(
                    date=d, account=ACCT_CURRENT,
                    amount=round(random.uniform(3.00, 7.00), 2),
                    type="CR", memo="BLUE REWARDS",
                    cat_main="Bills", cat_sub="Bank Fees",
                )
            )


# ---------------------------------------------------------------------------
# Variable spend (per week / month / ad-hoc)
# ---------------------------------------------------------------------------

def gen_groceries(out: list[Txn]) -> None:
    """Weekly main shop Saturdays + ~3-4 corner-shop top-ups per week.

    A subset of supermarket shops go on the Sainsbury credit card.
    """
    for d in iter_dates(START_DATE, END_DATE):
        if d.weekday() == 5:
            amount = jitter(82.00 * inflation(d), 0.20)
            memo = pick(GROCERY_SUPER)
            # Sainsbury Nectar card era: roughly 30% of shops on the Sainsbury CC,
            # especially when memo is a Sainsbury's store.
            acct = ACCT_SAINSBURY if ("SAINSBURY" in memo and random.random() < 0.6) else ACCT_CURRENT
            out.append(
                maybe_noise(
                    Txn(
                        date=d, account=acct,
                        amount=-round(amount, 2), type="PAYMENT", memo=memo,
                        cat_main="Shopping", cat_sub="Groceries", cat_sub2="Supermarket",
                    )
                )
            )
        if random.random() < 0.45:
            amount = jitter(8.50 * inflation(d), 0.30)
            out.append(
                maybe_noise(
                    Txn(
                        date=d, account=ACCT_CURRENT,
                        amount=-round(amount, 2), type="PAYMENT",
                        memo=pick(GROCERY_CORNER),
                        cat_main="Shopping", cat_sub="Groceries", cat_sub2="Corner Shop",
                    )
                )
            )
        # Local bakery / butcher (no sub2)
        if random.random() < 0.15:
            amount = jitter(11.00 * inflation(d), 0.30)
            out.append(
                Txn(
                    date=d, account=ACCT_CURRENT,
                    amount=-round(amount, 2), type="PAYMENT",
                    memo=pick(GROCERY_LOCAL),
                    cat_main="Shopping", cat_sub="Groceries",
                )
            )


def gen_veg_box(out: list[Txn]) -> None:
    """Weekly Riverford box (started 2014-03)."""
    d = date(2014, 3, 1)
    while d.weekday() != 3:
        d += timedelta(days=1)
    while d <= END_DATE:
        amount = jitter(30.00 * inflation(d), 0.05)
        out.append(
            Txn(
                date=d, account=ACCT_CURRENT,
                amount=-round(amount, 2), type="PAYMENT", memo=random.choice(VEG_BOX),
                cat_main="Shopping", cat_sub="Groceries", cat_sub2="veg box",
            )
        )
        d += timedelta(days=7)


def gen_wine(out: list[Txn]) -> None:
    """Roughly fortnightly wine."""
    d = START_DATE + timedelta(days=10)
    while d <= END_DATE:
        amount = jitter(72.00 * inflation(d), 0.15)
        out.append(
            Txn(
                date=d, account=ACCT_CURRENT,
                amount=-round(amount, 2), type="PAYMENT", memo=random.choice(WINE),
                cat_main="Shopping", cat_sub="Groceries", cat_sub2="wine",
            )
        )
        d += timedelta(days=random.choice([12, 14, 16, 18]))


def gen_petrol(out: list[Txn]) -> None:
    """Roughly fortnightly fill-ups."""
    d = START_DATE + timedelta(days=3)
    while d <= END_DATE:
        amount = jitter(62.00 * inflation(d), 0.18)
        out.append(
            maybe_noise(
                Txn(
                    date=d, account=ACCT_CURRENT,
                    amount=-round(amount, 2), type="PAYMENT", memo=pick(PETROL),
                    cat_main="Transport", cat_sub="Automotive", cat_sub2="Petrol",
                )
            )
        )
        d += timedelta(days=random.choice([10, 12, 14, 16]))


def gen_tube(out: list[Txn]) -> None:
    """Weekly Oyster top-ups on Mondays."""
    for d in iter_dates(START_DATE, END_DATE):
        if d.weekday() == 0:
            amount = jitter(35.00 * inflation(d), 0.20)
            out.append(
                Txn(
                    date=d, account=ACCT_CURRENT,
                    amount=-round(amount, 2), type="PAYMENT", memo=pick(TUBE),
                    cat_main="Transport", cat_sub="tube",
                )
            )


def gen_taxi(out: list[Txn]) -> None:
    """4-8 taxis per month."""
    for y, m in iter_months(START_DATE, END_DATE):
        last = monthrange(y, m)[1]
        for _ in range(random.randint(4, 8)):
            d = date(y, m, random.randint(1, last))
            amount = jitter(18.50 * inflation(d), 0.50)
            out.append(
                Txn(
                    date=d, account=ACCT_CURRENT,
                    amount=-round(amount, 2), type="PAYMENT", memo=pick(TAXI),
                    cat_main="Transport", cat_sub="taxi",
                )
            )


def gen_parking(out: list[Txn]) -> None:
    """2-4 parking events per month."""
    for y, m in iter_months(START_DATE, END_DATE):
        last = monthrange(y, m)[1]
        for _ in range(random.randint(2, 4)):
            d = date(y, m, random.randint(1, last))
            amount = jitter(6.50 * inflation(d), 0.4)
            out.append(
                Txn(
                    date=d, account=ACCT_CURRENT,
                    amount=-round(amount, 2), type="PAYMENT", memo=pick(PARKING),
                    cat_main="Transport", cat_sub="Automotive", cat_sub2="parking & fees",
                )
            )


def gen_restaurants(out: list[Txn]) -> None:
    """4-6 restaurant outings per month — split across Current/Amex/Barclaycard."""
    for y, m in iter_months(START_DATE, END_DATE):
        last = monthrange(y, m)[1]
        for _ in range(random.randint(4, 6)):
            d = date(y, m, random.randint(1, last))
            amount = jitter(58.00 * inflation(d), 0.45)
            r = random.random()
            if r < 0.4:
                acct = ACCT_AMEX
            elif r < 0.55:
                acct = ACCT_BARCLAYCARD
            else:
                acct = ACCT_CURRENT
            out.append(
                maybe_noise(
                    Txn(
                        date=d, account=acct,
                        amount=-round(amount, 2), type="PAYMENT", memo=random.choice(RESTAURANTS),
                        cat_main="Leisure", cat_sub="food/drinks", cat_sub2="restaurant",
                    )
                )
            )

        # PRET visits — 4-7/month. <£5 = café, >=£5 = restaurant (matches the
        # price-based rule in the real classifier). Round before bucketing so
        # the value written to CSV and the value the classifier reads agree.
        for _ in range(random.randint(4, 7)):
            d = date(y, m, random.randint(1, last))
            amount = round(jitter(random.choice([3.20, 4.40, 4.80, 6.20, 7.50, 9.40]) * inflation(d), 0.05), 2)
            is_cafe = amount < 5.0
            out.append(
                Txn(
                    date=d, account=ACCT_CURRENT,
                    amount=-amount, type="PAYMENT", memo="PRET A MANGER",
                    cat_main="Leisure", cat_sub="food/drinks",
                    cat_sub2="café" if is_cafe else "restaurant",
                )
            )


def gen_pubs(out: list[Txn]) -> None:
    """4-6 pub visits per month."""
    for y, m in iter_months(START_DATE, END_DATE):
        last = monthrange(y, m)[1]
        for _ in range(random.randint(4, 6)):
            d = date(y, m, random.randint(1, last))
            amount = jitter(28.00 * inflation(d), 0.40)
            out.append(
                Txn(
                    date=d, account=ACCT_CURRENT,
                    amount=-round(amount, 2), type="PAYMENT", memo=random.choice(PUBS),
                    cat_main="Leisure", cat_sub="food/drinks", cat_sub2="pub",
                )
            )


def gen_cafes(out: list[Txn]) -> None:
    """6-12 café visits per month (Pret handled in gen_restaurants)."""
    for y, m in iter_months(START_DATE, END_DATE):
        last = monthrange(y, m)[1]
        for _ in range(random.randint(6, 12)):
            d = date(y, m, random.randint(1, last))
            amount = jitter(4.20 * inflation(d), 0.35)
            out.append(
                Txn(
                    date=d, account=ACCT_CURRENT,
                    amount=-round(amount, 2), type="PAYMENT", memo=random.choice(CAFES),
                    cat_main="Leisure", cat_sub="food/drinks", cat_sub2="café",
                )
            )


def gen_entertainment(out: list[Txn]) -> None:
    """1-3 entertainment items per month — lottery, poker, udemy course."""
    for y, m in iter_months(START_DATE, END_DATE):
        last = monthrange(y, m)[1]
        for _ in range(random.randint(1, 3)):
            d = date(y, m, random.randint(1, last))
            amount = jitter(18.00 * inflation(d), 0.60)
            out.append(
                Txn(
                    date=d, account=ACCT_CURRENT,
                    amount=-round(amount, 2), type="PAYMENT", memo=random.choice(ENTERTAINMENT),
                    cat_main="Leisure", cat_sub="entertainment",
                )
            )


def gen_clothes(out: list[Txn]) -> None:
    """1-3 clothes purchases per month — mostly on Barclaycard."""
    for y, m in iter_months(START_DATE, END_DATE):
        last = monthrange(y, m)[1]
        for _ in range(random.randint(1, 3)):
            d = date(y, m, random.randint(1, last))
            amount = jitter(62.00 * inflation(d), 0.60)
            acct = ACCT_BARCLAYCARD if random.random() < 0.7 else ACCT_CURRENT
            out.append(
                Txn(
                    date=d, account=acct,
                    amount=-round(amount, 2), type="PAYMENT", memo=random.choice(CLOTHES),
                    cat_main="Shopping", cat_sub="Clothes",
                )
            )


def gen_household_shop(out: list[Txn]) -> None:
    """0-2 household purchases per month (HomeSense, eBay)."""
    for y, m in iter_months(START_DATE, END_DATE):
        last = monthrange(y, m)[1]
        for _ in range(random.randint(0, 2)):
            d = date(y, m, random.randint(1, last))
            amount = jitter(42.00 * inflation(d), 0.60)
            out.append(
                Txn(
                    date=d, account=ACCT_CURRENT,
                    amount=-round(amount, 2), type="PAYMENT", memo=random.choice(HOUSEHOLD_SHOP),
                    cat_main="Shopping", cat_sub="Household",
                )
            )


def gen_diy(out: list[Txn]) -> None:
    """Occasional Homebase run (Shopping/Household/DIY)."""
    for y, m in iter_months(START_DATE, END_DATE):
        if random.random() < 0.4:
            last = monthrange(y, m)[1]
            d = date(y, m, random.randint(1, last))
            amount = jitter(36.00 * inflation(d), 0.70)
            out.append(
                Txn(
                    date=d, account=ACCT_CURRENT,
                    amount=-round(amount, 2), type="PAYMENT", memo=random.choice(DIY),
                    cat_main="Shopping", cat_sub="Household", cat_sub2="DIY",
                )
            )


def gen_electronics(out: list[Txn]) -> None:
    """2-4 Currys purchases per year (camera / kit upgrades)."""
    for y in range(START_DATE.year, END_DATE.year + 1):
        for _ in range(random.randint(2, 4)):
            m = random.randint(1, 12)
            last = monthrange(y, m)[1]
            d = date(y, m, random.randint(1, last))
            amount = jitter(220.00 * inflation(d), 0.70)
            acct = ACCT_BARCLAYCARD if random.random() < 0.6 else ACCT_CURRENT
            out.append(
                Txn(
                    date=d, account=acct,
                    amount=-round(amount, 2), type="PAYMENT", memo=random.choice(ELECTRONICS),
                    cat_main="Shopping", cat_sub="electronics", cat_sub2="camera",
                )
            )


def gen_house_maintenance(out: list[Txn]) -> None:
    """Several small maintenance jobs per year + one big-ticket renovation event."""
    for y in range(START_DATE.year, END_DATE.year + 1):
        for _ in range(random.randint(3, 6)):
            m = random.randint(1, 12)
            last = monthrange(y, m)[1]
            d = date(y, m, random.randint(1, last))
            amount = jitter(120.00 * inflation(d), 0.80)
            out.append(
                Txn(
                    date=d, account=ACCT_CURRENT,
                    amount=-round(amount, 2), type="PAYMENT", memo=random.choice(MAINTENANCE),
                    cat_main="House", cat_sub="Maintenance",
                    cat_sub2="TBC", details="TBC",
                )
            )
        # Lighting via HOMARY occasionally
        if random.random() < 0.4:
            m = random.randint(1, 12)
            last = monthrange(y, m)[1]
            d = date(y, m, random.randint(1, last))
            amount = jitter(180.00 * inflation(d), 0.50)
            out.append(
                Txn(
                    date=d, account=ACCT_CURRENT,
                    amount=-round(amount, 2), type="PAYMENT", memo="HOMARY LIGHTING",
                    cat_main="House", cat_sub="Maintenance",
                    cat_sub2="TBC", details="lighting",
                )
            )


def gen_withdrawals(out: list[Txn]) -> None:
    """1-3 ATM withdrawals per month from the Current account (CASH type)."""
    for y, m in iter_months(START_DATE, END_DATE):
        last = monthrange(y, m)[1]
        for _ in range(random.randint(1, 3)):
            d = date(y, m, random.randint(1, last))
            amount = random.choice([20, 40, 50, 80, 100, 150, 200])
            out.append(
                Txn(
                    date=d, account=ACCT_CURRENT,
                    amount=-float(amount), type="CASH", memo="CASH WITHDRAWAL ATM",
                    cat_main="Withdrawal",
                )
            )
    # A handful per year of CC ATM withdrawals (rare, expensive — match the
    # CARDTRONICS / NOTEMACHINE / NAT WEST regex).
    for y in range(START_DATE.year, END_DATE.year + 1):
        for _ in range(random.randint(2, 4)):
            m = random.randint(1, 12)
            last = monthrange(y, m)[1]
            d = date(y, m, random.randint(1, last))
            memo = random.choice(["CARDTRONICS ATM", "NOTEMACHINE LTD", "NAT WEST BANK NWB"])
            out.append(
                Txn(
                    date=d, account=ACCT_BARCLAYCARD,
                    amount=-float(random.choice([50, 100, 150, 200])),
                    type="PAYMENT", memo=memo,
                    cat_main="Withdrawal",
                )
            )


def gen_health(out: list[Txn]) -> None:
    """Dentist (annual), Boots (monthly-ish), Specsavers (every 2y), PushDoctor (rare).

    Real classifier has a Health main category; SPEC §4 does not — flagged in
    the module docstring.
    """
    for y in range(START_DATE.year, END_DATE.year + 1):
        # Annual dental check-up
        d = date(y, random.randint(2, 11), random.randint(1, 28))
        out.append(
            Txn(
                date=d, account=ACCT_CURRENT,
                amount=-round(jitter(85.00 * inflation(d), 0.10), 2),
                type="PAYMENT", memo="COLNEY HATCH DENTAL",
                cat_main="Health", cat_sub="Dentist",
            )
        )
        # ~Monthly Boots run
        for _ in range(random.randint(6, 12)):
            m = random.randint(1, 12)
            last = monthrange(y, m)[1]
            d = date(y, m, random.randint(1, last))
            amount = jitter(11.50 * inflation(d), 0.50)
            out.append(
                Txn(
                    date=d, account=ACCT_CURRENT,
                    amount=-round(amount, 2), type="PAYMENT", memo="BOOTS THE CHEMIST",
                    cat_main="Health", cat_sub="General", cat_sub2="Medicine",
                )
            )
        # Specsavers every 2 years
        if y % 2 == 0:
            d = date(y, random.randint(3, 10), random.randint(1, 28))
            out.append(
                Txn(
                    date=d, account=ACCT_CURRENT,
                    amount=-round(jitter(180.00 * inflation(d), 0.15), 2),
                    type="PAYMENT", memo="SPECSAVERS OPTICAL",
                    cat_main="Health", cat_sub="Eyecare", cat_sub2="glasses",
                )
            )
        # Push Doctor — modern, from 2018
        if y >= 2018 and random.random() < 0.4:
            d = date(y, random.randint(1, 12), random.randint(1, 28))
            out.append(
                Txn(
                    date=d, account=ACCT_CURRENT,
                    amount=-round(20.00 * inflation(d), 2),
                    type="PAYMENT", memo="PUSHDOCTOR ONLINE",
                    cat_main="Health", cat_sub="GP",
                )
            )


# ---------------------------------------------------------------------------
# Derived (depends on transactions generated above)
# ---------------------------------------------------------------------------

def gen_cc_payments(out: list[Txn]) -> None:
    """One monthly payoff per credit card (Barclaycard, Amex, Sainsbury).

    Outgoing leg sits on Current and uses the redacted cardholder/card memo;
    the incoming leg on each CC uses the exact memo string the real
    `categories()` function expects for that CC.

    DOUBLE-COUNTING CONTRACT — both legs are tagged `Shopping/CreditCard`.
    Every individual CC purchase is already in the dataset (as a negative on
    the Barclaycard / Amex / Sainsbury account), so the payoff round-trip
    MUST be excluded from any spend total — otherwise a £50 dinner shows up
    as £50 dinner + £50 CC payment = £100 spend. The exclusion rule is
    simply: drop rows where (category_main='Shopping' AND
    category_sub='CreditCard'). This matches the default behaviour of
    `get_spending_summary` in SPEC_AGENT §5.2.
    """
    # Sum CC spend per (year, month, account_number).
    cc_spend: dict[tuple[int, int, str], float] = {}
    for t in out:
        if t.account in (ACCT_BARCLAYCARD, ACCT_AMEX, ACCT_SAINSBURY) and t.amount < 0:
            key = (t.date.year, t.date.month, t.account[0])
            cc_spend[key] = cc_spend.get(key, 0.0) + (-t.amount)

    cc_meta = {
        ACCT_BARCLAYCARD[0]: (
            ACCT_BARCLAYCARD,
            "CARDHOLDER_NAME_CARDNUMBER",  # redacted from "MR MICHEL GUILLON     4929499678409008"
            "PAYMENT RECEIVED - THANK YOU",
        ),
        ACCT_AMEX[0]: (
            ACCT_AMEX,
            "AMERICAN EXPRESS PAYMENT",
            "AMERICAN EXPRESS",
        ),
        ACCT_SAINSBURY[0]: (
            ACCT_SAINSBURY,
            "SAINSBURYS BANK PL",
            "DIRECT DEBIT RECEIVED, THANK YOU",
        ),
    }

    new_txns: list[Txn] = []
    for (y, m, acct_num), total in cc_spend.items():
        if total <= 0:
            continue
        pay_year, pay_month = (y, m + 1) if m < 12 else (y + 1, 1)
        if (pay_year, pay_month) > (END_DATE.year, END_DATE.month):
            continue
        pay_date = safe_day(pay_year, pay_month, 5)
        amount = round(total, 2)

        cc_account, outgoing_memo, incoming_memo = cc_meta[acct_num]

        new_txns.append(
            Txn(
                date=pay_date, account=ACCT_CURRENT, amount=-amount,
                type="DD", memo=outgoing_memo,
                cat_main="Shopping", cat_sub="CreditCard",
            )
        )
        new_txns.append(
            Txn(
                date=pay_date, account=cc_account, amount=amount,
                type="CR", memo=incoming_memo,
                cat_main="Shopping", cat_sub="CreditCard",
            )
        )

    out.extend(new_txns)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def generate() -> list[Txn]:
    random.seed(SEED)
    out: list[Txn] = []

    # Recurring income & fixed outgoings
    gen_salary(out)
    gen_mortgage(out)
    gen_council_tax(out)
    gen_utilities(out)
    gen_cleaner(out)
    gen_subscriptions(out)
    gen_video_subs(out)
    gen_loan(out)
    gen_wren_loan(out)
    gen_charity(out)
    gen_road_tax(out)
    gen_savings_transfer(out)
    gen_savings_interest(out)
    gen_bank_fees(out)

    # Variable / discretionary
    gen_groceries(out)
    gen_veg_box(out)
    gen_wine(out)
    gen_petrol(out)
    gen_tube(out)
    gen_taxi(out)
    gen_parking(out)
    gen_rail(out)
    gen_travel_hotels(out)
    gen_restaurants(out)
    gen_pubs(out)
    gen_cafes(out)
    gen_entertainment(out)
    gen_clothes(out)
    gen_household_shop(out)
    gen_diy(out)
    gen_electronics(out)
    gen_house_maintenance(out)
    gen_withdrawals(out)
    gen_health(out)

    # Derived from the CC spend above
    gen_cc_payments(out)

    out.sort(key=lambda t: (t.date, t.account[0], t.memo))
    return out


def write_csv(transactions: list[Txn], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        for t in transactions:
            writer.writerow(t.to_row())


def summarise(transactions: list[Txn]) -> None:
    """Human-readable sanity summary."""
    by_main: dict[str, int] = {}
    by_year: dict[int, int] = {}
    by_account: dict[str, int] = {}
    total_outgoing = 0.0
    total_incoming = 0.0
    for t in transactions:
        by_main[t.cat_main] = by_main.get(t.cat_main, 0) + 1
        by_year[t.date.year] = by_year.get(t.date.year, 0) + 1
        by_account[t.account[2]] = by_account.get(t.account[2], 0) + 1
        if t.amount < 0:
            total_outgoing += -t.amount
        else:
            total_incoming += t.amount

    print(f"Generated {len(transactions):,} transactions")
    print(f"  Date range : {transactions[0].date} -> {transactions[-1].date}")
    print(f"  Incoming   : £{total_incoming:>12,.2f}")
    print(f"  Outgoing   : £{total_outgoing:>12,.2f}")
    print("  By category_main:")
    for k in sorted(by_main):
        print(f"    {k:<12} {by_main[k]:>6,}")
    print("  By account:")
    for k in sorted(by_account):
        print(f"    {k:<14} {by_account[k]:>6,}")
    print("  Per year (count):")
    years = sorted(by_year)
    for y in years:
        print(f"    {y}: {by_year[y]:>5,}")


def main() -> None:
    transactions = generate()
    write_csv(transactions, OUTPUT_PATH)
    print(f"Wrote {OUTPUT_PATH}")
    summarise(transactions)


if __name__ == "__main__":
    main()
