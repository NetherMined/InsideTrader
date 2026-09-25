Spot Trading: 
Call GET /api/v1/exchangeInfo and look for the MIN_NOTIONAL or NOTIONAL filter type inside each symbol's array.

USDⓈ-M Futures: 
Call GET /fapi/v1/exchangeInfo to inspect the latest rules for perpetual and delivery contracts.

COIN-M Futures: 
Call GET /dapi/v1/exchangeInfo to check coin-margined contract constraints.