"""rules_seed.py — the canonical seed list for classification_rules.

A1 (Phase 2 SPEC §3.4) migrated the hardcoded chain in
bank_statement_parser.py:categories() into rows of this list. db/seed_rules.py
walks RULES_SEED and inserts rows into the classification_rules table in
order — first match wins (lower id = earlier insertion).

Each entry is a dict with these keys:
    pattern       (str, required): Python regex, applied case-insensitively
                                   to the memo via SQLite's REGEXP operator.
    category_main (str, required)
    category_sub  (str | None)
    category_sub2 (str | None)
    details       (str | None)

Optional conditions (NULL = no constraint, applied additively to the regex):
    account_match (str): require an exact Account Number match
    type_match    (str): require an exact Type match (e.g. 'CASH')
    amount_min    (float): require abs(Amount) >= amount_min
    amount_max    (float): require abs(Amount) <= amount_max

Order matters. Conditional rules MUST appear before the unconditional
fallback they share a memo pattern with (PRET café before PRET restaurant,
^MTG mortgage before any generic memo match that could swallow it).
"""

from __future__ import annotations

RULES_SEED: list[dict] = [
    # =====================================================================
    # Withdrawals
    # =====================================================================
    # Cash from current account (conditional: account + type)
    {"pattern": ".*",
     "account_match": "ACCOUNT_CURRENT", "type_match": "CASH",
     "category_main": "Withdrawal"},
    # ATM patterns (for non-CASH types from credit cards)
    {"pattern": ".*CARDTRONICS|.*NOTEMACHINE|.*NAT WEST BANK.*NWB|.*TESCO.*TESCO FO|.*SAINSBURYS BANK.*Sainsburys Bank",
     "category_main": "Withdrawal"},

    # =====================================================================
    # Income
    # =====================================================================
    {"pattern": "COMPANY_A|COMPANY_B|COMPANY_C",
     "category_main": "Income", "category_sub": "Salary"},

    # =====================================================================
    # House
    # =====================================================================
    # Mortgage (conditional: account + memo)
    {"pattern": "^MTG|ACCORD MORTGAGES",
     "account_match": "ACCOUNT_CURRENT",
     "category_main": "House", "category_sub": "Mortgage"},
    # Renovation
    {"pattern": "BARCLAYS PRTNR FIN",
     "category_main": "House", "category_sub": "Maintenance",
     "category_sub2": "kitchen/bathroom", "details": "wren repayment"},
    # Maintenance
    {"pattern": ".*EUROKEN SUPPLIES|.*TOOLSTATION|.*BUILDERDEPO|.*SORODOC|BUILDER DEPOT|M P MORAN & SONS|SCREWFIX|WICKES|B & Q|B AND Q|B&Q",
     "category_main": "House", "category_sub": "Maintenance",
     "category_sub2": "TBC", "details": "TBC"},
    # Maintenance - lighting
    {"pattern": ".*HOMARY",
     "category_main": "House", "category_sub": "Maintenance",
     "category_sub2": "TBC", "details": "lighting"},

    # =====================================================================
    # Shopping
    # =====================================================================
    # Groceries
    {"pattern": ".*GAILS|.*GAIL'S|IRISH MEAT MARKET|.*O'Farrells|^HARRYS",
     "category_main": "Shopping", "category_sub": "Groceries"},
    # Groceries - supermarket
    {"pattern": ".*SAINSBURY'S S|.*SAINSBURYS TO YOU|.*SAINSBURYS S|.*TESCO SELF SERVICE|.*TESCO STORE|.*TESCO SUBSCRIPTION|.*WAITROSE.*|.*MORRISONS.*|.*MARKS.*SPENCER.*|.*M&S.*|.*M & S.*",
     "category_main": "Shopping", "category_sub": "Groceries",
     "category_sub2": "Supermarket"},
    # Groceries - corner shop
    {"pattern": "^SAVERS HEALTH & BE|.*EURO SUPERMARKET|.*SUPERSAVE|.*GOODGE STREET NEWS|.*FOOD|.*YOUR LOCAL STORE|.*NASH SUPERMARKET|.*CORNER XPRESS|.*EUROSUPERMARKET|.*MUNRO|.*FURNESS STORE|.*CASTLE NEWS|.*FASHION AND MEDIA|.*SHALPRIKA|.*MURCO S/STN|.*THE KENSAL STORE|.*EAST FINCH.*SUPER|.*HARROW ROAD CONVEN|.*GURU KRUPA RETAIL|.*DIAMOND NEWS|.*NISA LOCAL|.*CAVENDISH CANDY|.*SWEET TOUCH|.*NEW FASHION MEDIA NEWS|.*SJ NEWS|.*STREET NEWS|.*TEX COLLEGE PARK",
     "category_main": "Shopping", "category_sub": "Groceries",
     "category_sub2": "Corner Shop"},
    # Groceries - veg box
    {"pattern": ".*RIVERFORD",
     "category_main": "Shopping", "category_sub": "Groceries",
     "category_sub2": "veg box"},
    # Groceries - wine
    {"pattern": "JAZZ WINE MART|NAKED WINES",
     "category_main": "Shopping", "category_sub": "Groceries",
     "category_sub2": "wine"},
    # Household
    {"pattern": ".*HOMESENSE|.*EBAY",
     "category_main": "Shopping", "category_sub": "Household"},
    # Household - DIY
    {"pattern": r".*B\s*&\s*Q|.*B\s*AND\s*Q|.*HOMEBASE|.*SCREWFIX",
     "category_main": "Shopping", "category_sub": "Household",
     "category_sub2": "DIY"},
    # Clothes
    {"pattern": r".*TK\s*MAXX|.*T\s*K\s*MAXX|.*DYNAMIC.*DRY\s*CLEAN|.*SPORTSDIRECT|.*SPORTS\s*DIRECT|.*CHARLES\s*TYRWHITT|.*UNIQLO|.*ZARA",
     "category_main": "Shopping", "category_sub": "Clothes"},
    # Credit card payment
    {"pattern": ".*CARDHOLDER_NAME_CARDNUMBER|.*Payment, Thank You|^PAYMENT RECEIVED - THANK YOU|.*AMERICAN EXPRESS|.*DIRECT DEBIT RECEIVED, THANK YOU|SAINSBURYS BANK PL",
     "category_main": "Shopping", "category_sub": "CreditCard"},
    # Electronics
    {"pattern": "^CURRYS",
     "category_main": "Shopping", "category_sub": "electronics",
     "category_sub2": "camera"},

    # =====================================================================
    # Transport
    # =====================================================================
    # Automotive - petrol
    {"pattern": ".*PETROL|.*SHELL|TESCO PAY AT PUMP|TESCO PFS|TESCO PAYAT PUMP",
     "category_main": "Transport", "category_sub": "Automotive",
     "category_sub2": "Petrol"},
    # Taxi
    {"pattern": ".*UBER.*TRIP|.*CURB MOBILITY|.*CABVISION|.*VERIFONE|.*CMT UK LTD",
     "category_main": "Transport", "category_sub": "taxi"},
    # Automotive - road tax
    {"pattern": ".*DVLA VEHICLE TAX|DVLA AMEX VEHICLE TAX|WWW.DVLA.GOV.UK",
     "category_main": "Transport", "category_sub": "Automotive",
     "category_sub2": "Road tax"},
    # Automotive - parking & fees
    {"pattern": r".*PAY\s*BY\s*PHONE|.*APCOA|.*JUSTPARK|.*RING\s*GO",
     "category_main": "Transport", "category_sub": "Automotive",
     "category_sub2": "parking & fees"},
    # Tube
    {"pattern": ".*LUL TICKET MACHINE|.*OYSTERCARD|.*TFL TRAVEL CH|.*TFL.GOV.UK",
     "category_main": "Transport", "category_sub": "tube"},

    # =====================================================================
    # Leisure
    # =====================================================================
    # PRET under £5 = café (conditional: amount). MUST appear before the
    # restaurant rule, which also matches PRET A MANGER unconditionally.
    {"pattern": ".*PRET.*MANGER|.*PRET A MANGER",
     "amount_max": 5,
     "category_main": "Leisure", "category_sub": "food/drinks",
     "category_sub2": "café"},
    # Restaurants (long pattern includes PRET A MANGER as fallback above £5)
    {"pattern": "CONNECTCATERING.CO|.*CHIPOTLE|.*PAPA-DUM STREET|.*L_ANGOLO|.*H.T. Harris|.*DELIVEROO|.*JUST EAT.CO.UK|.*JUST-EAT.CO.UK|UBER EATS HELP.UBER.COM|LEON EASTCASTLE STREET|.*TORTILLA|.*PRET A MANGER|.*CARLUCCIO'S MARKET PLAC|.*VITAL INGREDIENT|.*AMICI MIEI|.*MCDONALDS|.*THE KATI ROLL|.*BANH MI BAY|.*FRANCO MANCA|.*PIZZA EXPRESS|.*KFC|.*BURGER KING|.*NANDO|.*ZIZZI|.*GOURMET BURGER|.*GBK|.*HONEST BURGER|.*PING PONG|.*KEU LONDON",
     "category_main": "Leisure", "category_sub": "food/drinks",
     "category_sub2": "restaurant"},
    # Pubs
    {"pattern": ".*THE ISLAND|.*Earl of Camden|.*THE WOODMAN|.*Big Red|.*BREWDOG|.*BULL & BUSH|.*CAMDEN HEAD|.*CHAMBERLAYNE PUB|.*CHAMPION|.*COCK TAVERN|.*CROWN & SCEPTRE|.*FITZROY TAVERN|.*ISLAND|.*MARKET PLACE|.*MASONS ARMS|.*NORTHUMBERLAND|.*OLD WHITE LION|.*THE OLD NICK|.*TWO DOORS DOWN|.*WILLIAM IV|.*ROSE AND CROWN|.*OLD BULL & BUSH|.*ONEILL'S|.*Aces and Eights|.*The Champion|.*The Mossy Well|.*The Queensbury|.*ADAM & EVE|.*BLUE POST|.*ADAM AND EVE|.*BLACK ORCHID THAI|.*CGK EVENTS LIMITED|.*COCK AND LION LONDON GBR|.*DUKE OF YORK|.*Devonshire Arms London|.*ESSEX ARMS|.*GIPSY QUEEN|.*IZ.*AIN.*T NOTHIN B|.*JACK HORNER LONDON|.*JUNCTION TAVERN|.*JUST DRINKS|.*Ku Bar|.*Lucy Wong|.*MAID OF MUSWELL|.*MARQUIS OF GRANBY RATHB LONDON|.*PEMBROKE|.*PHOENIX|.*Prince Of Wales|.*RAILWAY|.*ROUND APP LTD|.*ROYAL OAK|.*SALUSBURY|.*SAWYERS ARMS LONDON|.*SPREAD EAGLE CAMDE|.*THE GALLERY|.*THE HOPE LONDON|.*THE JOHN BAIRD|.*THE LONDON EDITION|.*THE NEWMAN ARMS|.*THE SHIP|.*TRUMANS BEER|.*The William|.*UNICORN|.*YORKSHIRE GREY",
     "category_main": "Leisure", "category_sub": "food/drinks",
     "category_sub2": "pub"},
    # Cafe
    {"pattern": ".*Black Sheep Co|.*BLACK SHEEP COFFEE|.*CAFFE NERO|.*COSTA|.*FIX COFFEE|.*KAFFEINE|LA GENOISE|ORTAKLAR LTD|REYNOLDS CAFE|BLANK STREET|.*STARBUCKS.*|.*GREGGS.*|.*SHARD.*|.*TRIPLE TWO COFF.*",
     "category_main": "Leisure", "category_sub": "food/drinks",
     "category_sub2": "café"},
    # Entertainment
    {"pattern": "^NATIONAL LOTTERY|^STARS |UDEMY",
     "category_main": "Leisure", "category_sub": "entertainment"},
    # Sport - gym
    {"pattern": r".*STUDIO[\s-]*SOCIETY|.*HARLANDS.*STUDIO|.*1\s*LIFE|.*ONELIFE|.*PURE\s*GYM|.*EASY\s*GYM",
     "category_main": "Leisure", "category_sub": "sport",
     "category_sub2": "gym"},

    # =====================================================================
    # Banking
    # =====================================================================
    # Savings transfer
    {"pattern": ".*ACCOUNT_SAVINGS|.*ACCOUNT_CURRENT|.*CARDHOLDER_NAME REGULAR SAVINGS|.*POT OF GOLD.*",
     "category_main": "Savings", "category_sub": "Transfer"},
    # Savings interest
    {"pattern": "INTEREST PAID GROSS|.*INT EARNED",
     "category_main": "Savings", "category_sub": "Interest"},

    # =====================================================================
    # Health
    # =====================================================================
    {"pattern": "COLNEY HATCH DENTAL",
     "category_main": "Health", "category_sub": "Dentist"},
    {"pattern": "SPECSAVERS",
     "category_main": "Health", "category_sub": "Eyecare",
     "category_sub2": "glasses"},
    {"pattern": "^BOOTS",
     "category_main": "Health", "category_sub": "General",
     "category_sub2": "Medicine"},
    {"pattern": "^PUSHDOCTOR",
     "category_main": "Health", "category_sub": "GP"},

    # =====================================================================
    # Bills
    # =====================================================================
    {"pattern": "THAMES WATER|AFFINITY WATER",
     "category_main": "Bills", "category_sub": "utilities",
     "category_sub2": "water"},
    {"pattern": "E.ON|EDF ENERGY",
     "category_main": "Bills", "category_sub": "utilities",
     "category_sub2": "gas/elec"},
    {"pattern": "L B BRENT COUNCIL|BARNET BOROUGH COU|HARINGEY COUNCIL|WWW.BRENTGOV.UK|LBB COUNCIL TAX DD",
     "category_main": "Bills", "category_sub": "utilities",
     "category_sub2": "council tax"},
    # Mobile phone — Skype era merged into Mobile Phone per SPEC §4
    {"pattern": ".*SKYPE",
     "category_main": "Bills", "category_sub": "utilities",
     "category_sub2": "Mobile Phone"},
    {"pattern": "VODAFONE|O2|H3G|CARPHONE WAREHOUSE|TELEFONICA UK|WWW.THREE.CO.UK",
     "category_main": "Bills", "category_sub": "utilities",
     "category_sub2": "Mobile Phone"},
    {"pattern": "TV LICENSE",
     "category_main": "Bills", "category_sub": "utilities",
     "category_sub2": "TV License"},
    # Bank fees
    {"pattern": "BLUE REWARDS|Loyalty Reward|EXPERIAN UK|^CHARGES|^COMMISSION|^INTEREST CHARGE",
     "category_main": "Bills", "category_sub": "Bank Fees"},
    # Broadband
    {"pattern": "PNET3462156|SKY DIGITAL|PLUSNET",
     "category_main": "Bills", "category_sub": "utilities",
     "category_sub2": "broadband"},
    # Charity
    {"pattern": r".*BRITISH\s*RED\s*CROSS|.*RED\s*CROSS|.*UNICEF",
     "category_main": "Bills", "category_sub": "Charity"},
    # Cleaner
    {"pattern": r".*CLEANER_A|.*CLEANER_B|.*CLEANER_C",
     "category_main": "Bills", "category_sub": "Household",
     "category_sub2": "cleaner"},
    # Loan
    {"pattern": "^LENDER_NAME_REFERENCE",
     "category_main": "Bills", "category_sub": "loan"},

    # =====================================================================
    # Subscriptions
    # =====================================================================
    {"pattern": ".*AMAZON PRIME|.*PRIME VIDEO",
     "category_main": "Leisure", "category_sub": "subscription",
     "category_sub2": "amazon"},
    {"pattern": ".*SPOTIFY",
     "category_main": "Leisure", "category_sub": "subscription",
     "category_sub2": "music"},
    {"pattern": "THE ECONOMIST",
     "category_main": "Leisure", "category_sub": "subscription",
     "category_sub2": "newspapers"},
    # A2 — new sub2. NETFLIX/DISNEY+ deliberately NOT included; they stay
    # in the generator's NOISE_MEMOS as Missing for the agent demo loop.
    {"pattern": ".*NOW TV|.*BRITBOX|.*APPLE TV",
     "category_main": "Leisure", "category_sub": "subscription",
     "category_sub2": "video"},

    # =====================================================================
    # Travel (A2 — new main)
    # =====================================================================
    # NOTE: AIRBNB stays in NOISE_MEMOS as Missing for the agent demo.
    {"pattern": ".*BOOKING\\.COM|.*HILTON|.*MARRIOTT",
     "category_main": "Travel", "category_sub": "accommodation",
     "category_sub2": "hotel"},

    # =====================================================================
    # Transport - rail (A2 — new sub)
    # =====================================================================
    # NOTE: TRAINLINE stays in NOISE_MEMOS as Missing for the agent demo.
    {"pattern": ".*AVANTI WEST COAST|.*LNER|.*GWR|.*SOUTHEASTERN",
     "category_main": "Transport", "category_sub": "rail"},
]
