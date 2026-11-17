"""Congressional (STOCK Act) disclosure types for the public REST API at
``/api/political-disclosures``.

``availableAt`` is when a disclosure became public. It is the only date a
strategy may act on: ``transactionDate`` is when the member traded, typically
weeks before anyone outside Congress could know. Date filters and ``sort`` use
``firstAvailableAt``, when the first version of a disclosure became public.
"""

from __future__ import annotations

from typing import Literal, TypedDict

__all__ = [
    "CongressAction",
    "CongressActionFilter",
    "CongressChamber",
    "CongressChamberCoverage",
    "CongressChamberFilter",
    "CongressCommitteeAssignment",
    "CongressInstrument",
    "CongressInstrumentFilter",
    "CongressMemberBadge",
    "CongressOwner",
    "CongressParseMethod",
    "CongressParty",
    "CongressSort",
    "CongressionalAppliedFilters",
    "CongressionalCoverage",
    "CongressionalCoverageResponse",
    "CongressionalEnvelope",
    "CongressionalFiler",
    "CongressionalFilerActivity",
    "CongressionalFilerDetail",
    "CongressionalFilerResponse",
    "CongressionalFilerVolume",
    "CongressionalFilerYearActivity",
    "CongressionalFilersResponse",
    "CongressionalFiling",
    "CongressionalFilingLatency",
    "CongressionalFilingLatencyResponse",
    "CongressionalFilingResponse",
    "CongressionalLeaderboard",
    "CongressionalLeaderboardBasis",
    "CongressionalLeaderboardFeatured",
    "CongressionalLeaderboardParty",
    "CongressionalLeaderboardRow",
    "CongressionalLeaderboardSpark",
    "CongressionalTickerCount",
    "CongressionalTrade",
    "CongressionalTradeDetail",
    "CongressionalTradePage",
    "CongressionalTradeResponse",
    "CongressionalTradeSummary",
    "CongressionalTradeSummaryResponse",
    "CongressionalTradeVolumeResponse",
    "CongressionalTradesResponse",
    "CongressionalVolumeRank",
    "Politician",
    "PoliticianBot",
    "PoliticianBotCurvePoint",
    "PoliticianBotStatistics",
    "PoliticianProfile",
    "PoliticianResponse",
]

CongressChamber = Literal["house", "senate"]
CongressAction = Literal["purchase", "sale", "exchange", "unmarked"]
CongressInstrument = Literal["equity", "option", "fund", "bond", "other"]
CongressOwner = Literal["self", "spouse", "dependent_child", "joint", "not_indicated"]
CongressParty = Literal["Democrat", "Republican", "Independent", "Other"]
CongressParseMethod = Literal["text", "scan", "html", "paper"]
CongressSort = Literal["newest", "oldest"]
CongressChamberFilter = Literal["house", "senate", "all"]
CongressActionFilter = Literal["purchase", "sale", "exchange", "unmarked", "all"]
CongressInstrumentFilter = Literal["equity", "option", "fund", "bond", "other", "all"]


# The filters the server applied, defaults included. Functional syntax because
# `from` is a Python keyword.
CongressionalAppliedFilters = TypedDict(
    "CongressionalAppliedFilters",
    {
        "q": str | None,
        "filerKey": str | None,
        "ticker": str | None,
        "chamber": CongressChamberFilter,
        "action": CongressActionFilter,
        "instrument": CongressInstrumentFilter,
        "sort": CongressSort,
        "from": str,
        "to": str,
        "asOf": str,
        "cursor": str | None,
        "limit": int,
    },
)


class CongressChamberCoverage(TypedDict):
    filings: int | None
    latestFilingDate: str | None


class CongressionalCoverage(TypedDict):
    filings: int
    events: int
    failedFilings: int
    latestAvailableAt: str | None
    latestProcessedAt: str | None
    byChamber: dict[CongressChamber, CongressChamberCoverage]


class CongressionalEnvelope(TypedDict):
    """Fields every disclosure read carries beside ``data``.

    The leaderboard and the copy bot are the exceptions: they return their
    payload bare.
    """

    snapshotId: str
    asOf: str
    dataUpdatedAt: str
    stale: bool
    cache: Literal["hit", "miss", "stale"]
    coverage: CongressionalCoverage | None
    filters: CongressionalAppliedFilters | None


class CongressionalTrade(TypedDict):
    # `<chamber>:<docId>:<rowIndex>`, for example "house:20035831:0".
    eventId: str
    version: int
    filerKey: str
    filerName: str
    chamber: CongressChamber
    owner: CongressOwner
    action: CongressAction
    partialSale: bool
    instrument: CongressInstrument
    # When the member traded. Not public knowledge on this date.
    transactionDate: str | None
    filingDate: str
    # When the first version became public. `from`, `to` and `sort` use this.
    firstAvailableAt: str
    # When this version became public. The date to trade from.
    availableAt: str
    supersededAt: str | None
    ticker: str | None
    assetDescription: str
    assetTypeCode: str | None
    assetTypeLabel: str | None
    # Disclosures report a bracket, never an amount.
    amountBracket: str | None
    amountLow: float | None
    amountHigh: float | None
    tickerResolutionStatus: str | None
    sourceDocId: str
    sourceRowIndex: int
    sourceUrl: str
    # "scan" and "paper" rows were read from an image.
    sourceParseMethod: CongressParseMethod | None


class CongressMemberBadge(TypedDict):
    party: CongressParty | None
    state: str | None
    slug: str


class _CongressionalTradePageRequired(TypedDict):
    trades: list[CongressionalTrade]
    # Pass as `cursor` with the same filters; None on the last page.
    nextCursor: str | None


class CongressionalTradePage(_CongressionalTradePageRequired, total=False):
    # filerKey -> badge, for the members on this page.
    members: dict[str, CongressMemberBadge]


class CongressionalTradesResponse(CongressionalEnvelope):
    data: CongressionalTradePage


class CongressionalTickerCount(TypedDict):
    ticker: str
    events: int


class CongressionalVolumeRank(TypedDict):
    ticker: str
    events: int
    amountLow: float | None
    amountHigh: float | None


class _CongressionalTradeSummaryRequired(TypedDict):
    purchaseEvents: int
    saleEvents: int
    otherEvents: int
    activeFilers: int
    disclosedEvents: int
    purchaseAmountLow: float | None
    purchaseAmountHigh: float | None
    purchaseMissingLow: int
    purchaseMissingHigh: int
    saleAmountLow: float | None
    saleAmountHigh: float | None
    saleMissingLow: int
    saleMissingHigh: int
    netAmountLow: float | None
    netAmountHigh: float | None
    unresolvedTickerEvents: int
    mostPurchased: list[CongressionalTickerCount]


class CongressionalTradeSummary(_CongressionalTradeSummaryRequired, total=False):
    mostPurchasedByVolume: list[CongressionalVolumeRank]


class CongressionalTradeSummaryResponse(CongressionalEnvelope):
    data: CongressionalTradeSummary


class CongressionalFiler(TypedDict):
    filerKey: str
    displayName: str
    chamber: CongressChamber
    disclosureEvents: int
    purchaseEvents: int
    saleEvents: int
    latestDisclosureAt: str | None
    identityStatus: Literal["member"]


class CongressionalFilersResponse(CongressionalEnvelope):
    data: list[CongressionalFiler]


class CongressionalFilerYearActivity(TypedDict):
    year: int
    purchases: int
    sales: int
    purchaseMidpoint: float | None
    saleMidpoint: float | None


class CongressionalFilerActivity(TypedDict):
    byYear: list[CongressionalFilerYearActivity]
    firstPublicAt: str | None
    lastPublicAt: str | None
    filings: int
    # Filings carrying a trade made public more than 45 days after it.
    lateFilings: int


class CongressionalFilerDetail(TypedDict):
    filer: CongressionalFiler
    summary: CongressionalTradeSummary
    recentTrades: list[CongressionalTrade]
    recentTradesNextCursor: str | None
    activity: CongressionalFilerActivity


class CongressionalFilerResponse(CongressionalEnvelope):
    data: CongressionalFilerDetail


class CongressionalFilingLatency(TypedDict):
    """How long one member's disclosures take to become public."""

    filerKey: str
    displayName: str
    transactions: int
    filings: int
    medianDaysToPublic: float | None
    p95DaysToPublic: float | None
    transactionsOverFortyFiveDays: int
    filingsOverFortyFiveDays: int


class CongressionalFilingLatencyResponse(CongressionalEnvelope):
    data: list[CongressionalFilingLatency]


class _CongressionalFilerVolumeRequired(TypedDict):
    filerKey: str
    displayName: str
    transactions: int
    purchaseTransactions: int
    saleTransactions: int
    filings: int
    volumeLow: float
    # None when any bracket is open-ended.
    volumeHigh: float | None
    volumeMidpoint: float
    openEndedTransactions: int


class CongressionalFilerVolume(_CongressionalFilerVolumeRequired, total=False):
    """One member's disclosed dollars, as sums of bracket bounds."""

    party: CongressParty | None
    state: str | None
    slug: str | None


class CongressionalTradeVolumeResponse(CongressionalEnvelope):
    data: list[CongressionalFilerVolume]


class CongressCommitteeAssignment(TypedDict):
    id: str
    name: str
    title: str | None


class Politician(TypedDict):
    # Bioguide ID.
    memberId: str
    slug: str
    displayName: str
    party: CongressParty | None
    committees: list[CongressCommitteeAssignment]
    imageUrl: str | None
    chamber: CongressChamber | None
    state: str | None
    district: int | None
    firstTookOffice: str | None
    lastServedEnd: str | None


class PoliticianProfile(TypedDict):
    politician: Politician
    profile: CongressionalFilerDetail


class PoliticianResponse(CongressionalEnvelope):
    data: PoliticianProfile


class PoliticianBotStatistics(TypedDict):
    totalReturn: float
    cagr: float | None
    fiveYearEquivalent: float | None
    spyTotalReturn: float | None
    spyCagr: float | None
    spyFiveYearEquivalent: float | None
    sharpe: float | None
    sortino: float | None
    maxDrawdown: float | None
    closedTrades: int | None
    fees: float | None
    medianDeployment: float | None
    distinctNames: int | None


class _PoliticianBotCurvePointRequired(TypedDict):
    date: str
    value: float
    spy: float | None


class PoliticianBotCurvePoint(_PoliticianBotCurvePointRequired, total=False):
    performanceValue: float


class PoliticianBot(TypedDict):
    """A member's copy bot: a cached engine backtest of following their disclosures."""

    status: Literal["ready", "stale", "computing", "ineligible"]
    memberId: str
    displayName: str | None
    purchaseCount: int | None
    minPurchases: int
    profileId: str | None
    # Pass to fork_public_portfolio to copy the bot into your workspace.
    sharedPortfolioId: str | None
    windowStart: str | None
    windowEnd: str | None
    refreshedAt: str | None
    refreshRequestedAt: str | None
    refreshPending: bool
    initialValue: float
    statistics: PoliticianBotStatistics | None
    curve: list[PoliticianBotCurvePoint]
    mixed: bool


class CongressionalLeaderboardSpark(TypedDict):
    bot: list[float]
    spy: list[float | None]


class CongressionalLeaderboardRow(TypedDict):
    memberId: str
    displayName: str
    slug: str | None
    party: CongressParty | None
    chamber: CongressChamber | None
    state: str | None
    windowStart: str
    windowEnd: str
    mixed: bool
    totalReturn: float
    spyTotalReturn: float
    # totalReturn minus spyTotalReturn.
    excessReturn: float
    # (1 + cagr)^5 - 1, the ranking basis.
    fiveYearEquivalent: float | None
    sharpe: float | None
    maxDrawdown: float | None
    purchaseCount: int
    imageUrl: str | None
    lastServedEnd: str | None
    spark: CongressionalLeaderboardSpark | None


class CongressionalLeaderboardFeatured(TypedDict):
    memberId: str
    initialValue: float
    windowEnd: str
    curve: list[PoliticianBotCurvePoint]


class CongressionalLeaderboardParty(TypedDict):
    party: Literal["Democrat", "Republican", "Independent", "Other", "Unknown"]
    members: int
    meanExcessReturn: float


class CongressionalLeaderboardBasis(TypedDict):
    rankedMembers: int
    beatSpy: int
    meanExcessReturn: float | None
    minPurchases: int
    minWindowDays: int
    benchmark: Literal["SPY"]
    # Starting capital of every ranked book.
    initialValue: float
    refreshedAt: str | None
    minPartyMembers: int


class CongressionalLeaderboard(TypedDict):
    # Members whose copy bot beat SPY, best first.
    best: list[CongressionalLeaderboardRow]
    featured: CongressionalLeaderboardFeatured | None
    # Members whose copy bot trailed SPY, worst first.
    worst: list[CongressionalLeaderboardRow]
    byParty: list[CongressionalLeaderboardParty]
    basis: CongressionalLeaderboardBasis


class CongressionalTradeDetail(TypedDict):
    current: CongressionalTrade
    # Every version public as of the read, amendments included.
    history: list[CongressionalTrade]


class CongressionalTradeResponse(CongressionalEnvelope):
    data: CongressionalTradeDetail


class CongressionalFiling(TypedDict):
    chamber: CongressChamber
    docId: str
    filerKey: str | None
    filerName: str
    stateDistrict: str | None
    filingDate: str
    availableAt: str
    sourceUrl: str
    extractionStatus: Literal["ok", "failed", "unsupported"]
    extractedRows: int
    amendedReportDate: str | None
    reportDate: str | None
    trades: list[CongressionalTrade]


class CongressionalFilingResponse(CongressionalEnvelope):
    data: CongressionalFiling


class CongressionalCoverageResponse(CongressionalEnvelope):
    data: CongressionalCoverage


def congressional_query_params(
    *,
    q: str | None = None,
    filer_key: str | None = None,
    ticker: str | None = None,
    chamber: str | None = None,
    action: str | None = None,
    instrument: str | None = None,
    sort: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    as_of: str | None = None,
    cursor: str | None = None,
    limit: int | None = None,
) -> dict[str, str]:
    """Query pairs in one fixed order, so both SDKs put the same bytes on the wire."""
    pairs: dict[str, str | None] = {
        "q": q,
        "filerKey": filer_key,
        "ticker": ticker,
        "chamber": chamber,
        "action": action,
        "instrument": instrument,
        "sort": sort,
        "from": from_date,
        "to": to_date,
        "asOf": as_of,
        "cursor": cursor,
        "limit": None if limit is None else str(limit),
    }
    return {key: value for key, value in pairs.items() if value}
