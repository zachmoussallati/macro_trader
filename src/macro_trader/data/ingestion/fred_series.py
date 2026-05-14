"""FRED + ALFRED series we track in Stage 2.

Each tuple is ``(series_id, name, frequency, units, sa, category,
affected_instruments)``. Stage 2 is US-focused; later stages add EU / CN /
EM macro.
"""

from __future__ import annotations

# (series_id, name, frequency, units, sa, category, affected_instruments)
FREDSeriesSpec = tuple[str, str, str, str, str | None, str, tuple[str, ...]]

FRED_SERIES: tuple[FREDSeriesSpec, ...] = (
    # ----- Growth -----
    (
        "GDPC1",
        "Real Gross Domestic Product",
        "quarterly",
        "Billions of Chained 2017 Dollars",
        "SA",
        "growth",
        (),
    ),
    (
        "INDPRO",
        "Industrial Production Index",
        "monthly",
        "Index 2017=100",
        "SA",
        "growth",
        ("CL", "BZ", "HG", "ALI", "NG"),
    ),
    ("RSAFS", "Advance Retail Sales", "monthly", "Millions of Dollars", "SA", "growth", ()),
    (
        "PAYEMS",
        "All Employees: Total Nonfarm Payrolls",
        "monthly",
        "Thousands of Persons",
        "SA",
        "employment",
        ("GC", "SI"),
    ),
    ("UNRATE", "Unemployment Rate", "monthly", "Percent", "SA", "employment", ("GC", "SI")),
    # ----- Inflation -----
    (
        "CPIAUCSL",
        "Consumer Price Index for All Urban Consumers: All Items",
        "monthly",
        "Index 1982-1984=100",
        "SA",
        "inflation",
        ("GC", "SI", "PL"),
    ),
    (
        "CPILFESL",
        "Consumer Price Index Less Food and Energy",
        "monthly",
        "Index 1982-1984=100",
        "SA",
        "inflation",
        ("GC", "SI"),
    ),
    (
        "PCEPI",
        "Personal Consumption Expenditures Price Index",
        "monthly",
        "Index 2017=100",
        "SA",
        "inflation",
        ("GC", "SI"),
    ),
    (
        "PCEPILFE",
        "Core PCE Price Index",
        "monthly",
        "Index 2017=100",
        "SA",
        "inflation",
        ("GC", "SI"),
    ),
    # ----- Rates -----
    (
        "DGS10",
        "10-Year Treasury Constant Maturity Rate",
        "daily",
        "Percent",
        None,
        "rates",
        ("GC", "SI"),
    ),
    ("DGS2", "2-Year Treasury Constant Maturity Rate", "daily", "Percent", None, "rates", ()),
    ("DGS3MO", "3-Month Treasury Constant Maturity Rate", "daily", "Percent", None, "rates", ()),
    ("FEDFUNDS", "Federal Funds Effective Rate", "monthly", "Percent", None, "rates", ()),
    ("SOFR", "Secured Overnight Financing Rate", "daily", "Percent", None, "rates", ()),
    # ----- USD strength -----
    (
        "DTWEXBGS",
        "Nominal Broad U.S. Dollar Index",
        "daily",
        "Index Jan 2006=100",
        None,
        "fx",
        ("GC", "SI", "HG", "CL"),
    ),
    (
        "DTWEXAFEGS",
        "Nominal Advanced Foreign Economies Dollar Index",
        "daily",
        "Index Jan 2006=100",
        None,
        "fx",
        ("GC", "SI"),
    ),
    # ----- Commodity-relevant -----
    ("DCOILWTICO", "Crude Oil Prices: WTI", "daily", "Dollars per Barrel", None, "energy", ("CL",)),
    (
        "DCOILBRENTEU",
        "Crude Oil Prices: Brent",
        "daily",
        "Dollars per Barrel",
        None,
        "energy",
        ("BZ",),
    ),
    (
        "GASREGCOVM",
        "US Regular All Formulations Gas Price",
        "weekly",
        "Dollars per Gallon",
        None,
        "energy",
        ("RB",),
    ),
    (
        "GOLDAMGBD228NLBM",
        "Gold Fixing Price 10:30 AM London",
        "daily",
        "U.S. Dollars per Troy Ounce",
        None,
        "precious_metals",
        ("GC",),
    ),
)
