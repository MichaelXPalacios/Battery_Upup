## backtest_home · ERCOT · dispatch league ($/kW-yr)
| Algorithm | 1 year (2025) | 3 years (2023–25) | 5 years (2021–25) | $ per MWh discharged (5 y) | Share of perfect foresight (5 y) |
|---|---:|---:|---:|---:|---:|
| A1 Clockwork | 13.1 | 41.6 | 34.3 | 63 | 37% |
| A2 Forecaster | 11.7 | 40.7 | 34.6 | 77 | 37% |
| A3 Weather-regime | 11.3 | 31.2 | 29.4 | 64 | 32% |
| A4 Spike hunter | 11.8 | 24.0 | 23.2 | 206 | 25% |
| A5 Adaptive learner | 13.8 | 44.1 | 38.1 | 95 | 41% |
| Perfect foresight (ceiling) | 37.2 | 86.4 | 92.8 | | 100% |

### ERCOT by region, 5 years ($/kW-yr)
| Region | A1 | A2 | A3 | A4 | A5 | Ceiling | Best |
|---|---:|---:|---:|---:|---:|---:|---:|
| DFW (LZ_NORTH) | 31.8 | 33.1 | 28.4 | 22.4 | 35.9 | 87.9 | A5 |
| Houston | 35.4 | 37.4 | 31.6 | 23.9 | 39.4 | 94.3 | A5 |
| South / Valley | 28.0 | 28.0 | 24.9 | 19.0 | 32.4 | 81.7 | A5 |
| West / Permian | 38.3 | 38.7 | 30.5 | 28.2 | 41.1 | 107.3 | A5 |
| Austin Energy | 35.6 | 36.4 | 31.3 | 23.8 | 41.1 | 95.3 | A5 |
| San Antonio (CPS) | 36.8 | 34.7 | 29.4 | 22.5 | 38.8 | 91.9 | A5 |
| LCRA co-ops | 37.3 | 35.2 | 30.7 | 23.1 | 39.4 | 95.7 | A5 |
| Rayburn co-ops | 31.2 | 33.3 | 28.4 | 22.7 | 36.7 | 88.1 | A5 |

### ERCOT adaptive learner: share of days following each expert
| Region | Clockwork | Forecaster | Weather-regime | Spike hunter q97 | Spike hunter q90 | Forecaster (low carry) |
|---|---:|---:|---:|---:|---:|---:|
| DFW (LZ_NORTH) | 28% | 6% | 19% | 27% | 11% | 9% |
| Houston | 27% | 18% | 20% | 32% | 3% | 1% |
| South / Valley | 23% | 20% | 20% | 29% | 3% | 4% |
| West / Permian | 22% | 25% | 16% | 27% | 8% | 2% |
| Austin Energy | 28% | 11% | 22% | 23% | 7% | 9% |
| San Antonio (CPS) | 29% | 16% | 24% | 24% | 5% | 3% |
| LCRA co-ops | 31% | 11% | 22% | 23% | 7% | 6% |
| Rayburn co-ops | 28% | 12% | 18% | 29% | 8% | 4% |

## backtest_home · NYISO · dispatch league ($/kW-yr)
| Algorithm | 1 year (2025) | 3 years (2023–25) | 5 years (2021–25) | $ per MWh discharged (5 y) | Share of perfect foresight (5 y) |
|---|---:|---:|---:|---:|---:|
| A1 Clockwork | 13.3 | 4.4 | 4.6 | 8 | 11% |
| A2 Forecaster | 20.0 | 13.0 | 15.9 | 39 | 39% |
| A3 Weather-regime | 17.7 | 12.5 | 16.1 | 35 | 39% |
| A4 Spike hunter | 11.2 | 9.0 | 11.6 | 131 | 28% |
| A5 Adaptive learner | 19.6 | 12.8 | 16.2 | 39 | 40% |
| Perfect foresight (ceiling) | 48.0 | 30.5 | 40.8 | | 100% |

### NYISO by region, 5 years ($/kW-yr)
| Region | A1 | A2 | A3 | A4 | A5 | Ceiling | Best |
|---|---:|---:|---:|---:|---:|---:|---:|
| NY West (A) | 1.3 | 12.6 | 12.7 | 10.6 | 12.8 | 31.6 | A5 |
| Genesee (B) | 1.4 | 11.6 | 11.3 | 9.7 | 11.9 | 30.7 | A5 |
| Central NY (C) | 1.9 | 12.1 | 11.7 | 9.8 | 12.8 | 31.8 | A5 |
| North Country (D) | -0.8 | 17.2 | 14.9 | 14.0 | 16.8 | 43.6 | A2 |
| Mohawk Valley (E) | 2.1 | 12.9 | 12.6 | 10.5 | 13.1 | 33.6 | A5 |
| Capital (F) | 4.8 | 18.8 | 18.0 | 11.6 | 18.8 | 49.2 | A2 |
| Hudson Valley (G) | 4.5 | 13.9 | 14.6 | 10.4 | 14.6 | 36.5 | A3 |
| Millwood (H) | 5.9 | 14.8 | 15.9 | 10.2 | 15.0 | 38.7 | A3 |
| Dunwoodie (I) | 5.6 | 15.0 | 16.3 | 10.5 | 15.3 | 39.4 | A3 |
| New York City (J) | 6.8 | 16.5 | 17.4 | 11.2 | 16.3 | 42.4 | A3 |
| Long Island (K) | 16.5 | 29.2 | 31.4 | 18.6 | 31.1 | 71.4 | A3 |

### NYISO adaptive learner: share of days following each expert
| Region | Clockwork | Forecaster | Weather-regime | Spike hunter q97 | Spike hunter q90 | Forecaster (low carry) |
|---|---:|---:|---:|---:|---:|---:|
| NY West (A) | 3% | 26% | 42% | 9% | 7% | 13% |
| Genesee (B) | 3% | 16% | 50% | 17% | 6% | 9% |
| Central NY (C) | 2% | 15% | 48% | 16% | 8% | 11% |
| North Country (D) | 2% | 17% | 26% | 13% | 17% | 25% |
| Mohawk Valley (E) | 3% | 14% | 46% | 19% | 7% | 11% |
| Capital (F) | 4% | 28% | 55% | 3% | 7% | 3% |
| Hudson Valley (G) | 4% | 20% | 67% | 5% | 2% | 3% |
| Millwood (H) | 4% | 20% | 64% | 5% | 3% | 5% |
| Dunwoodie (I) | 2% | 21% | 67% | 4% | 1% | 4% |
| New York City (J) | 2% | 21% | 68% | 4% | 2% | 2% |
| Long Island (K) | 6% | 22% | 58% | 6% | 2% | 6% |

## backtest_home · siting portfolio · ERCOT ($/kW-yr)
| Algorithm | 1 year | 3 years | 5 years | Fleet after 5 years (MW) | Next 10 MW (using data through 2025) |
|---|---:|---:|---:|---|---|
| A1 Clockwork | 14.8 | 30.3 | 32.8 | West / Permian 20, Houston 10, South / Valley 10, San Antonio (CPS) 10 | LCRA co-ops 10 |
| A2 Forecaster | 13.1 | 29.6 | 32.9 | West / Permian 25, South / Valley 10, Austin Energy 10, Houston 5 | West / Permian 5, San Antonio (CPS) 5 |
| A3 Weather-regime | 11.4 | 22.4 | 26.2 | West / Permian 19, South / Valley 15, Houston 14, Austin Energy 2 | Houston 5, Austin Energy 3, LCRA co-ops 2 |
| A4 Spike hunter | 12.9 | 21.3 | 23.9 | West / Permian 28, South / Valley 8, Austin Energy 8, Houston 6 | West / Permian 6, Austin Energy 4 |
| A5 Adaptive learner | 17.5 | 31.6 | 35.8 | West / Permian 30, Houston 10, South / Valley 10 | West / Permian 10 |
| A5ts Adaptive learner, Thompson-sampling siting (first design) | 14.8 | 31.7 | 36.9 | West / Permian 14, Austin Energy 7, LCRA co-ops 7, South / Valley 6, Rayburn co-ops 6, San Antonio (CPS) 5, Houston 4, DFW (LZ_NORTH) 1 | Rayburn co-ops 3, West / Permian 2, Austin Energy 2, Houston 1, South / Valley 1, LCRA co-ops 1 |

## backtest_home · siting portfolio · NYISO ($/kW-yr)
| Algorithm | 1 year | 3 years | 5 years | Fleet after 5 years (MW) | Next 10 MW (using data through 2025) |
|---|---:|---:|---:|---|---|
| A1 Clockwork | 24.2 | 16.1 | 16.3 | Long Island (K) 50 | Long Island (K) 10 |
| A2 Forecaster | 25.4 | 19.7 | 20.3 | Long Island (K) 25, NY West (A) 10, Capital (F) 10, New York City (J) 5 | New York City (J) 5, Long Island (K) 5 |
| A3 Weather-regime | 24.3 | 18.4 | 19.7 | Long Island (K) 23, NY West (A) 14, New York City (J) 11, Capital (F) 2 | Long Island (K) 5, New York City (J) 3, Capital (F) 2 |
| A4 Spike hunter | 14.7 | 12.5 | 14.1 | Long Island (K) 24, North Country (D) 18, NY West (A) 8 | Long Island (K) 6, North Country (D) 4 |
| A5 Adaptive learner | 27.7 | 23.3 | 27.6 | Long Island (K) 50 | Long Island (K) 10 |
| A5ts Adaptive learner, Thompson-sampling siting (first design) | 22.6 | 18.9 | 20.4 | Long Island (K) 29, NY West (A) 9, Capital (F) 5, North Country (D) 3, New York City (J) 2, Hudson Valley (G) 1, Millwood (H) 1 | Long Island (K) 5, Dunwoodie (I) 2, Mohawk Valley (E) 1, Capital (F) 1, Hudson Valley (G) 1 |

## backtest_home · siting portfolio · BOTH ($/kW-yr)
| Algorithm | 1 year | 3 years | 5 years | Fleet after 5 years (MW) | Next 10 MW (using data through 2025) |
|---|---:|---:|---:|---|---|
| A1 Clockwork | 14.8 | 30.3 | 25.8 | West / Permian 20, Houston 10, South / Valley 10, Long Island (K) 10 | Long Island (K) 10 |
| A2 Forecaster | 13.1 | 23.7 | 30.3 | West / Permian 20, Austin Energy 10, Long Island (K) 10, Houston 5, South / Valley 5 | West / Permian 5, Long Island (K) 5 |
| A3 Weather-regime | 14.1 | 22.2 | 24.0 | South / Valley 15, West / Permian 15, Long Island (K) 9, Houston 8, NY West (A) 3 | Long Island (K) 5, Houston 3, Austin Energy 2 |
| A4 Spike hunter | 12.9 | 21.3 | 23.9 | West / Permian 28, South / Valley 8, Austin Energy 8, Houston 6 | West / Permian 6, Austin Energy 4 |
| A5 Adaptive learner | 17.5 | 31.6 | 35.8 | West / Permian 30, Houston 10, South / Valley 10 | West / Permian 10 |
| A5ts Adaptive learner, Thompson-sampling siting (first design) | 15.0 | 27.9 | 35.3 | West / Permian 11, San Antonio (CPS) 11, Houston 6, Long Island (K) 5, South / Valley 4, Austin Energy 4, LCRA co-ops 4, DFW (LZ_NORTH) 3, Rayburn co-ops 2 | Houston 4, Austin Energy 3, South / Valley 1, West / Permian 1, San Antonio (CPS) 1 |

## backtest_grid · ERCOT · dispatch league ($/kW-yr)
| Algorithm | 1 year (2025) | 3 years (2023–25) | 5 years (2021–25) | $ per MWh discharged (5 y) | Share of perfect foresight (5 y) |
|---|---:|---:|---:|---:|---:|
| A1 Clockwork | 16.5 | 49.1 | 39.8 | 54 | 38% |
| A2 Forecaster | 15.6 | 50.1 | 43.0 | 80 | 41% |
| A3 Weather-regime | 15.7 | 40.3 | 37.5 | 71 | 36% |
| A4 Spike hunter | 14.4 | 31.2 | 29.6 | 224 | 29% |
| A5 Adaptive learner | 17.9 | 51.8 | 44.5 | 89 | 43% |
| Perfect foresight (ceiling) | 42.8 | 95.8 | 103.7 | | 100% |

### ERCOT by region, 5 years ($/kW-yr)
| Region | A1 | A2 | A3 | A4 | A5 | Ceiling | Best |
|---|---:|---:|---:|---:|---:|---:|---:|
| DFW (LZ_NORTH) | 38.1 | 41.1 | 35.6 | 28.9 | 41.5 | 98.0 | A5 |
| Houston | 40.4 | 46.0 | 39.9 | 31.2 | 46.8 | 104.9 | A5 |
| South / Valley | 32.6 | 35.0 | 31.4 | 24.0 | 37.2 | 91.3 | A5 |
| West / Permian | 44.1 | 47.9 | 41.1 | 34.8 | 49.6 | 120.1 | A5 |
| Austin Energy | 41.0 | 45.3 | 40.5 | 30.5 | 46.7 | 106.8 | A5 |
| San Antonio (CPS) | 42.1 | 43.3 | 37.7 | 28.8 | 45.5 | 102.5 | A5 |
| LCRA co-ops | 43.6 | 44.1 | 38.9 | 29.7 | 47.9 | 107.5 | A5 |
| Rayburn co-ops | 36.4 | 40.9 | 35.0 | 29.1 | 40.5 | 98.1 | A2 |

### ERCOT adaptive learner: share of days following each expert
| Region | Clockwork | Forecaster | Weather-regime | Spike hunter q97 | Spike hunter q90 | Forecaster (low carry) |
|---|---:|---:|---:|---:|---:|---:|
| DFW (LZ_NORTH) | 25% | 10% | 22% | 29% | 10% | 4% |
| Houston | 21% | 22% | 20% | 31% | 4% | 1% |
| South / Valley | 23% | 20% | 19% | 28% | 6% | 4% |
| West / Permian | 20% | 15% | 21% | 29% | 6% | 9% |
| Austin Energy | 25% | 16% | 24% | 18% | 15% | 3% |
| San Antonio (CPS) | 25% | 14% | 23% | 25% | 8% | 4% |
| LCRA co-ops | 30% | 15% | 21% | 22% | 9% | 3% |
| Rayburn co-ops | 24% | 13% | 28% | 25% | 5% | 4% |

## backtest_grid · NYISO · dispatch league ($/kW-yr)
| Algorithm | 1 year (2025) | 3 years (2023–25) | 5 years (2021–25) | $ per MWh discharged (5 y) | Share of perfect foresight (5 y) |
|---|---:|---:|---:|---:|---:|
| A1 Clockwork | 15.0 | 4.2 | 4.4 | 6 | 10% |
| A2 Forecaster | 23.4 | 14.9 | 18.2 | 41 | 42% |
| A3 Weather-regime | 21.1 | 14.5 | 18.9 | 37 | 44% |
| A4 Spike hunter | 12.7 | 10.1 | 13.1 | 131 | 30% |
| A5 Adaptive learner | 22.9 | 14.7 | 18.6 | 40 | 43% |
| Perfect foresight (ceiling) | 52.0 | 32.4 | 43.3 | | 100% |

### NYISO by region, 5 years ($/kW-yr)
| Region | A1 | A2 | A3 | A4 | A5 | Ceiling | Best |
|---|---:|---:|---:|---:|---:|---:|---:|
| NY West (A) | 0.4 | 14.3 | 14.7 | 12.0 | 14.5 | 33.5 | A3 |
| Genesee (B) | 0.6 | 13.5 | 13.4 | 10.9 | 13.9 | 32.7 | A5 |
| Central NY (C) | 1.4 | 14.1 | 14.0 | 11.0 | 14.9 | 33.9 | A5 |
| North Country (D) | -2.7 | 20.5 | 19.1 | 15.9 | 20.4 | 47.0 | A2 |
| Mohawk Valley (E) | 1.8 | 14.9 | 14.8 | 11.8 | 15.1 | 35.7 | A5 |
| Capital (F) | 4.3 | 21.1 | 20.6 | 13.1 | 21.0 | 51.4 | A2 |
| Hudson Valley (G) | 4.2 | 15.4 | 16.7 | 11.6 | 16.6 | 38.3 | A3 |
| Millwood (H) | 5.5 | 16.5 | 18.2 | 11.6 | 16.7 | 40.8 | A3 |
| Dunwoodie (I) | 5.7 | 16.9 | 18.7 | 11.8 | 17.1 | 41.5 | A3 |
| New York City (J) | 6.9 | 18.6 | 20.2 | 12.7 | 18.8 | 44.7 | A3 |
| Long Island (K) | 19.7 | 33.9 | 36.8 | 21.7 | 36.0 | 76.5 | A3 |

### NYISO adaptive learner: share of days following each expert
| Region | Clockwork | Forecaster | Weather-regime | Spike hunter q97 | Spike hunter q90 | Forecaster (low carry) |
|---|---:|---:|---:|---:|---:|---:|
| NY West (A) | 2% | 19% | 54% | 9% | 6% | 9% |
| Genesee (B) | 3% | 15% | 58% | 11% | 7% | 6% |
| Central NY (C) | 2% | 16% | 59% | 14% | 4% | 5% |
| North Country (D) | 0% | 11% | 48% | 7% | 13% | 21% |
| Mohawk Valley (E) | 2% | 15% | 56% | 10% | 8% | 9% |
| Capital (F) | 4% | 23% | 63% | 6% | 4% | 0% |
| Hudson Valley (G) | 3% | 13% | 74% | 4% | 3% | 3% |
| Millwood (H) | 3% | 15% | 73% | 4% | 2% | 3% |
| Dunwoodie (I) | 2% | 14% | 75% | 3% | 3% | 3% |
| New York City (J) | 1% | 15% | 77% | 4% | 2% | 1% |
| Long Island (K) | 5% | 13% | 68% | 4% | 3% | 7% |

## backtest_grid · siting portfolio · ERCOT ($/kW-yr)
| Algorithm | 1 year | 3 years | 5 years | Fleet after 5 years (MW) | Next 10 MW (using data through 2025) |
|---|---:|---:|---:|---|---|
| A1 Clockwork | 18.8 | 35.2 | 38.2 | West / Permian 20, Houston 10, South / Valley 10, San Antonio (CPS) 10 | LCRA co-ops 10 |
| A2 Forecaster | 17.7 | 37.1 | 41.1 | West / Permian 25, South / Valley 10, Austin Energy 10, Houston 5 | West / Permian 5, LCRA co-ops 5 |
| A3 Weather-regime | 16.3 | 29.6 | 34.2 | West / Permian 19, South / Valley 15, Houston 14, Austin Energy 2 | Austin Energy 5, West / Permian 3, Houston 2 |
| A4 Spike hunter | 15.5 | 25.2 | 31.1 | West / Permian 24, Houston 14, Austin Energy 8, San Antonio (CPS) 4 | West / Permian 6, Austin Energy 4 |
| A5 Adaptive learner | 22.3 | 38.0 | 46.8 | West / Permian 40, Houston 10 | West / Permian 10 |
| A5ts Adaptive learner, Thompson-sampling siting (first design) | 19.0 | 36.7 | 43.2 | West / Permian 14, South / Valley 7, LCRA co-ops 7, Rayburn co-ops 6, Houston 5, Austin Energy 5, San Antonio (CPS) 5, DFW (LZ_NORTH) 1 | Austin Energy 3, Rayburn co-ops 3, West / Permian 2, Houston 1, LCRA co-ops 1 |

## backtest_grid · siting portfolio · NYISO ($/kW-yr)
| Algorithm | 1 year | 3 years | 5 years | Fleet after 5 years (MW) | Next 10 MW (using data through 2025) |
|---|---:|---:|---:|---|---|
| A1 Clockwork | 30.9 | 20.3 | 19.9 | Long Island (K) 50 | Long Island (K) 10 |
| A2 Forecaster | 29.0 | 22.3 | 23.3 | Long Island (K) 25, NY West (A) 10, Capital (F) 10, North Country (D) 5 | New York City (J) 5, Long Island (K) 5 |
| A3 Weather-regime | 29.5 | 21.5 | 24.4 | Long Island (K) 25, NY West (A) 12, North Country (D) 6, New York City (J) 5, Capital (F) 2 | Long Island (K) 5, New York City (J) 3, Capital (F) 2 |
| A4 Spike hunter | 17.2 | 15.1 | 16.6 | Long Island (K) 30, North Country (D) 12, NY West (A) 8 | Long Island (K) 6, North Country (D) 4 |
| A5 Adaptive learner | 32.6 | 28.3 | 32.6 | Long Island (K) 50 | Long Island (K) 10 |
| A5ts Adaptive learner, Thompson-sampling siting (first design) | 28.1 | 22.7 | 23.1 | Long Island (K) 26, NY West (A) 10, North Country (D) 5, Capital (F) 5, New York City (J) 2, Hudson Valley (G) 1, Millwood (H) 1 | Long Island (K) 6, Mohawk Valley (E) 2, Hudson Valley (G) 1, Dunwoodie (I) 1 |

## backtest_grid · siting portfolio · BOTH ($/kW-yr)
| Algorithm | 1 year | 3 years | 5 years | Fleet after 5 years (MW) | Next 10 MW (using data through 2025) |
|---|---:|---:|---:|---|---|
| A1 Clockwork | 18.8 | 35.2 | 30.2 | West / Permian 20, Houston 10, South / Valley 10, Long Island (K) 10 | Long Island (K) 10 |
| A2 Forecaster | 17.7 | 29.5 | 37.4 | West / Permian 20, Austin Energy 10, Long Island (K) 10, Houston 5, South / Valley 5 | West / Permian 5, Long Island (K) 5 |
| A3 Weather-regime | 16.3 | 29.6 | 33.9 | West / Permian 17, South / Valley 15, Houston 14, Austin Energy 2, Long Island (K) 2 | Long Island (K) 5, Austin Energy 3, West / Permian 2 |
| A4 Spike hunter | 15.5 | 25.2 | 31.1 | West / Permian 24, Houston 14, Austin Energy 8, San Antonio (CPS) 4 | West / Permian 6, Austin Energy 4 |
| A5 Adaptive learner | 22.3 | 38.0 | 46.8 | West / Permian 40, Houston 10 | West / Permian 10 |
| A5ts Adaptive learner, Thompson-sampling siting (first design) | 20.0 | 35.5 | 41.5 | San Antonio (CPS) 12, West / Permian 10, Houston 8, South / Valley 4, Austin Energy 4, LCRA co-ops 4, Long Island (K) 3, DFW (LZ_NORTH) 2, Rayburn co-ops 2, North Country (D) 1 | Houston 4, West / Permian 2, San Antonio (CPS) 2, South / Valley 1, Austin Energy 1 |

## forward · ERCOT · portfolio $/kW-yr
| Algorithm | 2027 (1 y): mean · P10–P90 · wins | 2027–29 (3 y) | 2027–31 (5 y) |
|---|---|---|---|
| A1 Clockwork | 90.1 · 66.1–102.0 · 42% | 90.6 · 71.3–104.6 · 15% | 86.5 · 70.6–97.1 · 10% |
| A2 Forecaster | 86.7 · 61.9–115.5 · 35% | 89.7 · 72.7–112.3 · 15% | 87.9 · 73.2–100.2 · 15% |
| A3 Weather-regime | 78.3 · 51.5–93.2 · 0% | 80.8 · 63.5–95.6 · 0% | 79.7 · 65.5–92.3 · 0% |
| A4 Spike hunter | 83.3 · 62.5–94.1 · 0% | 84.3 · 71.5–93.9 · 0% | 82.4 · 72.5–93.1 · 0% |
| A5 Adaptive learner | 91.3 · 65.6–102.0 · 22% | 93.8 · 77.2–107.5 · 70% | 90.3 · 77.7–104.1 · 75% |
| A5ts Adaptive learner, Thompson-sampling siting (first design) | 80.9 · 63.7–101.6 · not ranked | 83.8 · 65.6–96.5 · not ranked | 82.7 · 67.7–100.7 · not ranked |

### forward · ERCOT · fleets
| Algorithm | First 10 MW in 2027 | Average fleet in 2031 (MW of 50) |
|---|---|---|
| A1 Clockwork | Austin Energy + Austin Energy tolling 10 | LCRA co-ops + TX co-op peak 30.0, Austin Energy + Austin Energy tolling 11.0, Rayburn co-ops + TX co-op peak 9.0 |
| A2 Forecaster | LCRA co-ops + TX co-op peak 5, Austin Energy + Austin Energy tolling 5 | LCRA co-ops + TX co-op peak 24.2, Rayburn co-ops + TX co-op peak 15.5, Austin Energy + Austin Energy tolling 10.2 |
| A3 Weather-regime | Austin Energy + Austin Energy tolling 5, LCRA co-ops + TX co-op peak 3, Rayburn co-ops + TX co-op peak 2 | LCRA co-ops + TX co-op peak 20.4, Rayburn co-ops + TX co-op peak 12.7, Austin Energy + Austin Energy tolling 11.0, Houston + SB 415 feeder contract 1.8, Houston 1.5, DFW (LZ_NORTH) + Data-center flexibility 1.4 |
| A4 Spike hunter | Austin Energy + Austin Energy tolling 6, Rayburn co-ops + TX co-op peak 4 | Rayburn co-ops + TX co-op peak 19.2, LCRA co-ops + TX co-op peak 19.2, Austin Energy + Austin Energy tolling 11.0, DFW (LZ_NORTH) + Data-center flexibility 0.6 |
| A5 Adaptive learner | Austin Energy + Austin Energy tolling 10 | LCRA co-ops + TX co-op peak 33.5, Austin Energy + Austin Energy tolling 11.0, Rayburn co-ops + TX co-op peak 5.5 |

### forward · ERCOT · site ranking (A5 dispatch)
| Rank | Site (region + program) | Median 2027–29 $/kW-yr | Bad-case (P10) |
|---:|---|---:|---:|
| 1 | Austin Energy + Austin Energy tolling | 102.0 | 102.0 |
| 2 | DFW (LZ_NORTH) + Data-center flexibility | 82.0 | 54.2 |
| 3 | LCRA co-ops + TX co-op peak | 81.9 | 65.2 |
| 4 | Rayburn co-ops + TX co-op peak | 76.5 | 61.5 |
| 5 | Houston + SB 415 feeder contract | 60.7 | 43.7 |
| 6 | DFW (LZ_NORTH) + SB 415 feeder contract | 59.3 | 44.6 |
| 7 | Houston | 32.5 | 12.8 |
| 8 | LCRA co-ops | 29.3 | 15.9 |
| 9 | Rayburn co-ops | 29.1 | 12.2 |
| 10 | South / Valley | 28.3 | 12.3 |
| 11 | Austin Energy | 27.6 | 14.3 |
| 12 | DFW (LZ_NORTH) | 26.6 | 12.4 |

## forward · NYISO · portfolio $/kW-yr
| Algorithm | 2027 (1 y): mean · P10–P90 · wins | 2027–29 (3 y) | 2027–31 (5 y) |
|---|---|---|---|
| A1 Clockwork | 225.7 · 177.4–272.4 · 0% | 224.9 · 175.1–272.4 · 0% | 224.7 · 175.2–272.9 · 0% |
| A2 Forecaster | 193.0 · 144.8–240.5 · 0% | 192.7 · 141.9–239.5 · 0% | 192.8 · 140.9–243.1 · 0% |
| A3 Weather-regime | 189.3 · 148.8–231.2 · 0% | 188.8 · 143.6–231.8 · 0% | 188.3 · 143.4–233.9 · 0% |
| A4 Spike hunter | 199.2 · 150.0–250.2 · 0% | 198.9 · 148.1–246.7 · 0% | 198.9 · 147.7–248.6 · 0% |
| A5 Adaptive learner | 237.6 · 191.5–285.6 · 100% | 237.7 · 186.3–286.6 · 100% | 237.6 · 185.6–288.8 · 100% |
| A5ts Adaptive learner, Thompson-sampling siting (first design) | 237.6 · 191.5–285.6 · not ranked | 236.4 · 186.0–284.0 · not ranked | 236.6 · 185.6–285.2 · not ranked |

### forward · NYISO · fleets
| Algorithm | First 10 MW in 2027 | Average fleet in 2031 (MW of 50) |
|---|---|---|
| A1 Clockwork | New York City (J) + Con Ed load relief 10 | New York City (J) + Con Ed load relief 50.0 |
| A2 Forecaster | New York City (J) 5, New York City (J) + Con Ed load relief 5 | New York City (J) + Con Ed load relief 25.0, New York City (J) 21.0, Long Island (K) + Utility battery program 4.0 |
| A3 Weather-regime | New York City (J) + Con Ed load relief 5, New York City (J) 3, Long Island (K) + Utility battery program 2 | New York City (J) + Con Ed load relief 25.0, New York City (J) 14.7, Long Island (K) + Utility battery program 10.3 |
| A4 Spike hunter | New York City (J) + Con Ed load relief 6, New York City (J) 4 | New York City (J) + Con Ed load relief 30.0, New York City (J) 18.0, Long Island (K) + Utility battery program 2.0 |
| A5 Adaptive learner | New York City (J) + Con Ed load relief 10 | New York City (J) + Con Ed load relief 50.0 |

### forward · NYISO · site ranking (A5 dispatch)
| Rank | Site (region + program) | Median 2027–29 $/kW-yr | Bad-case (P10) |
|---:|---|---:|---:|
| 1 | New York City (J) + Con Ed load relief | 232.2 | 186.3 |
| 2 | New York City (J) | 144.6 | 97.5 |
| 3 | Long Island (K) + Utility battery program | 124.3 | 104.5 |
| 4 | Hudson Valley (G) + Utility battery program | 103.2 | 89.3 |
| 5 | NY West (A) + Utility battery program | 95.3 | 84.0 |
| 6 | Capital (F) + Utility battery program | 93.7 | 82.7 |
| 7 | North Country (D) + Utility battery program | 92.7 | 83.9 |
| 8 | Central NY (C) + Utility battery program | 92.5 | 83.6 |
| 9 | Mohawk Valley (E) + Utility battery program | 92.3 | 82.7 |
| 10 | Genesee (B) + Utility battery program | 92.1 | 82.3 |
| 11 | Dunwoodie (I) + Con Ed Westchester relief | 82.9 | 68.7 |
| 12 | Millwood (H) + Con Ed Westchester relief | 82.1 | 68.8 |

## forward · BOTH · portfolio $/kW-yr
| Algorithm | 2027 (1 y): mean · P10–P90 · wins | 2027–29 (3 y) | 2027–31 (5 y) |
|---|---|---|---|
| A1 Clockwork | 225.7 · 177.4–272.4 · 0% | 224.9 · 175.1–272.4 · 0% | 224.7 · 175.2–272.9 · 0% |
| A2 Forecaster | 193.0 · 144.8–240.5 · 0% | 192.9 · 142.8–239.5 · 0% | 192.7 · 140.8–243.1 · 0% |
| A3 Weather-regime | 189.3 · 148.8–231.2 · 0% | 188.8 · 143.6–231.8 · 0% | 188.3 · 143.4–233.9 · 0% |
| A4 Spike hunter | 199.2 · 150.0–250.2 · 0% | 198.9 · 148.1–246.7 · 0% | 198.8 · 147.7–248.6 · 0% |
| A5 Adaptive learner | 237.6 · 191.5–285.6 · 100% | 237.7 · 186.3–286.6 · 100% | 237.6 · 185.6–288.8 · 100% |
| A5ts Adaptive learner, Thompson-sampling siting (first design) | 237.6 · 191.5–285.6 · not ranked | 236.1 · 186.3–283.7 · not ranked | 236.5 · 185.6–286.7 · not ranked |

### forward · BOTH · fleets
| Algorithm | First 10 MW in 2027 | Average fleet in 2031 (MW of 50) |
|---|---|---|
| A1 Clockwork | New York City (J) + Con Ed load relief 10 | New York City (J) + Con Ed load relief 50.0 |
| A2 Forecaster | New York City (J) 5, New York City (J) + Con Ed load relief 5 | New York City (J) + Con Ed load relief 25.0, New York City (J) 20.8, Long Island (K) + Utility battery program 2.0, LCRA co-ops + TX co-op peak 1.5, Austin Energy + Austin Energy tolling 0.8 |
| A3 Weather-regime | New York City (J) + Con Ed load relief 5, New York City (J) 3, Long Island (K) + Utility battery program 2 | New York City (J) + Con Ed load relief 25.0, New York City (J) 14.4, Long Island (K) + Utility battery program 10.2, Austin Energy + Austin Energy tolling 0.4 |
| A4 Spike hunter | New York City (J) + Con Ed load relief 6, New York City (J) 4 | New York City (J) + Con Ed load relief 30.0, New York City (J) 18.0, Long Island (K) + Utility battery program 0.8, Austin Energy + Austin Energy tolling 0.6, Rayburn co-ops + TX co-op peak 0.6 |
| A5 Adaptive learner | New York City (J) + Con Ed load relief 10 | New York City (J) + Con Ed load relief 50.0 |

### forward · BOTH · site ranking (A5 dispatch)
| Rank | Site (region + program) | Median 2027–29 $/kW-yr | Bad-case (P10) |
|---:|---|---:|---:|
| 1 | New York City (J) + Con Ed load relief | 232.2 | 186.3 |
| 2 | New York City (J) | 144.6 | 97.5 |
| 3 | Long Island (K) + Utility battery program | 124.3 | 104.5 |
| 4 | Hudson Valley (G) + Utility battery program | 103.2 | 89.3 |
| 5 | Austin Energy + Austin Energy tolling | 102.0 | 102.0 |
| 6 | NY West (A) + Utility battery program | 95.3 | 84.0 |
| 7 | Capital (F) + Utility battery program | 93.7 | 82.7 |
| 8 | North Country (D) + Utility battery program | 92.7 | 83.9 |
| 9 | Central NY (C) + Utility battery program | 92.5 | 83.6 |
| 10 | Mohawk Valley (E) + Utility battery program | 92.3 | 82.7 |
| 11 | Genesee (B) + Utility battery program | 92.1 | 82.3 |
| 12 | Dunwoodie (I) + Con Ed Westchester relief | 82.9 | 68.7 |

## forward · ERCOT · energy trading only, by region ($/kW-yr, median scenario, 2027-31)
| Region | A1 | A2 | A3 | A4 | A5 |
|---|---:|---:|---:|---:|---:|
| DFW (LZ_NORTH) | 33.4 | 34.3 | 24.7 | 25.3 | 36.6 |
| Houston | 31.5 | 35.6 | 27.5 | 26.3 | 35.6 |
| South / Valley | 26.5 | 31.3 | 24.7 | 23.5 | 32.4 |
| West / Permian | 32.5 | 32.6 | 23.6 | 23.2 | 34.0 |
| Austin Energy | 34.2 | 35.7 | 29.0 | 26.4 | 38.7 |
| San Antonio (CPS) | 33.2 | 35.1 | 27.8 | 25.7 | 36.0 |
| LCRA co-ops | 34.5 | 36.6 | 27.6 | 25.3 | 38.5 |
| Rayburn co-ops | 31.5 | 33.5 | 24.0 | 24.9 | 35.4 |
| Backup hours at outage start | 6.2 | 21.0 | 22.0 | 25.6 | 16.4 |

## forward · NYISO · energy trading only, by region ($/kW-yr, median scenario, 2027-31)
| Region | A1 | A2 | A3 | A4 | A5 |
|---|---:|---:|---:|---:|---:|
| NY West (A) | 1.6 | 16.9 | 17.4 | 15.0 | 17.7 |
| Genesee (B) | 0.9 | 13.5 | 12.7 | 11.8 | 13.4 |
| Central NY (C) | 1.4 | 14.4 | 13.3 | 12.5 | 14.3 |
| North Country (D) | 1.2 | 14.5 | 12.8 | 12.5 | 14.3 |
| Mohawk Valley (E) | 1.6 | 15.2 | 13.7 | 13.1 | 15.2 |
| Capital (F) | 2.7 | 15.3 | 15.3 | 12.5 | 15.1 |
| Hudson Valley (G) | 3.9 | 16.7 | 16.2 | 14.1 | 16.8 |
| Millwood (H) | 4.3 | 17.2 | 17.2 | 14.4 | 17.5 |
| Dunwoodie (I) | 4.4 | 17.3 | 17.3 | 14.5 | 17.6 |
| New York City (J) | 4.4 | 16.2 | 17.5 | 14.4 | 17.2 |
| Long Island (K) | 11.3 | 28.8 | 29.2 | 22.7 | 29.7 |
| Backup hours at outage start | 6.2 | 19.9 | 23.2 | 24.7 | 19.7 |

## forward · infrastructure
| Transmission upgrade | Region | After − before, scenarios with it | Same split, scenarios without | Effect on a battery there ($/kW-yr) | Scenarios with / without |
|---|---|---:|---:|---:|---:|
| Permian 765-kV lines | West / Permian | -9.0 | 6.0 | −15.0 | 14 / 6 |
| DFW large loads land | DFW (LZ_NORTH) | 1.6 | 2.9 | −1.3 | 7 / 13 |
| Valley import upgrade 2029 | South / Valley | 9.8 | 1.0 | +8.8 | 6 / 14 |
| Propel NY 2030 | Long Island (K) | -11.9 | 5.7 | −17.6 | 12 / 8 |

Feeder hosting (repo SMART-DS feeder, an Oncor-suburb stand-in): 1007 homes, 689 Cores fit with no upgrade, 298 more after 239 transformer upgrades (30% of a concentrated build). Upgrade cost spread over a concentrated build: $121/kW, or $16.1/kW-yr over 12 years at 8%.

## forward · where the money comes from (A5, median, 2027-29)
| Site | Energy trading | Capacity | Program payment | Event energy | Transformer upgrades | Total ($/kW-yr) |
|---|---:|---:|---:|---:|---:|---:|
| New York City (J) + Con Ed load relief | 16.4 | 126.8 | 90.0 | 15.0 | -16.1 | 232.1 |
| New York City (J) | 17.5 | 126.8 | 0.0 | 0.0 | 0.0 | 144.3 |
| Long Island (K) + Utility battery program | 29.7 | 42.6 | 50.0 | 0.0 | 0.0 | 122.3 |
| Hudson Valley (G) + Utility battery program | 15.7 | 36.4 | 50.0 | 0.0 | 0.0 | 102.2 |
| Austin Energy + Austin Energy tolling | 0.0 | 0.0 | 102.0 | 0.0 | 0.0 | 102.0 |
| NY West (A) + Utility battery program | 16.0 | 27.3 | 50.0 | 0.0 | 0.0 | 93.3 |
| Capital (F) + Utility battery program | 14.9 | 27.3 | 50.0 | 0.0 | 0.0 | 92.2 |
| Mohawk Valley (E) + Utility battery program | 13.9 | 27.3 | 50.0 | 0.0 | 0.0 | 91.2 |
| North Country (D) + Utility battery program | 13.8 | 27.3 | 50.0 | 0.0 | 0.0 | 91.1 |
| Central NY (C) + Utility battery program | 13.5 | 27.3 | 50.0 | 0.0 | 0.0 | 90.8 |
| Genesee (B) + Utility battery program | 12.8 | 27.3 | 50.0 | 0.0 | 0.0 | 90.1 |
| LCRA co-ops + TX co-op peak | 33.3 | 0.0 | 50.0 | 0.0 | 0.0 | 83.3 |
| Dunwoodie (I) + Con Ed Westchester relief | 16.9 | 36.4 | 30.0 | 15.0 | -16.1 | 82.2 |
| Millwood (H) + Con Ed Westchester relief | 16.8 | 36.4 | 30.0 | 15.0 | -16.1 | 82.1 |

## equations · ERCOT
| Region | July spread ($/MWh) | +5 °F hotter | Spike chance → hotter | January spread | −10 °F colder | Spike chance → colder | +50% gas: price · spread | Spike chance after a spike day |
|---|---:|---:|---|---:|---:|---|---|---:|
| DFW (LZ_NORTH) | 40 | +128% | 6.1% → 12.3% | 28 | +56% | 5.2% → 8.3% | +15% · +12% | 9.6% |
| Houston | 42 | +146% | 7.8% → 16.1% | 30 | +38% | 5.3% → 6.7% | +17% · +15% | 11.3% |
| South / Valley | 41 | +148% | 7.3% → 14.6% | 33 | +60% | 5.6% → 9.0% | +14% · +9% | 12.2% |
| West / Permian | 52 | +105% | 6.1% → 12.7% | 50 | +41% | 6.6% → 9.9% | +10% · +6% | 8.8% |
| Austin Energy | 40 | +104% | 7.0% → 11.4% | 32 | +47% | 6.0% → 9.2% | +15% · +13% | 10.6% |
| San Antonio (CPS) | 38 | +137% | 6.6% → 14.5% | 33 | +47% | 6.0% → 8.2% | +14% · +12% | 9.7% |
| LCRA co-ops | 40 | +133% | 6.5% → 12.8% | 32 | +58% | 5.3% → 8.5% | +15% · +13% | 9.7% |
| Rayburn co-ops | 39 | +126% | 6.1% → 12.4% | 28 | +54% | 5.2% → 8.5% | +15% · +12% | 9.3% |

Spike-equation skill against each zone's historical spike rate (Brier skill, out of sample): 2019 +0.07, 2021 +0.15, 2023 +0.30, 2024 +0.10, 2025 +0.03.

## equations · NYISO
| Region | July spread ($/MWh) | +5 °F hotter | Spike chance → hotter | January spread | −10 °F colder | Spike chance → colder | +50% gas: price · spread | Spike chance after a spike day |
|---|---:|---:|---|---:|---:|---|---|---:|
| NY West (A) | 55 | +36% | 11.7% → 16.2% | 47 | +9% | 8.1% → 9.3% | +12% · +20% | 19.9% |
| Genesee (B) | 42 | +27% | 4.9% → 6.5% | 48 | +10% | 7.1% → 9.2% | +13% · +26% | 7.4% |
| Central NY (C) | 44 | +26% | 5.5% → 7.3% | 50 | +9% | 7.7% → 9.9% | +13% · +25% | 8.6% |
| North Country (D) | 50 | +26% | 5.4% → 7.4% | 51 | +14% | 7.3% → 10.2% | +17% · +24% | 8.8% |
| Mohawk Valley (E) | 47 | +33% | 5.7% → 8.4% | 51 | +12% | 8.4% → 11.5% | +12% · +25% | 8.5% |
| Capital (F) | 46 | +35% | 6.1% → 8.6% | 66 | +14% | 14.3% → 18.2% | +12% · +20% | 8.9% |
| Hudson Valley (G) | 52 | +41% | 7.9% → 12.6% | 58 | +7% | 11.9% → 14.6% | +13% · +23% | 10.5% |
| Millwood (H) | 57 | +38% | 10.0% → 14.1% | 59 | +5% | 12.6% → 15.0% | +13% · +24% | 13.5% |
| Dunwoodie (I) | 58 | +38% | 10.0% → 14.1% | 59 | +5% | 12.5% → 14.8% | +13% · +25% | 13.4% |
| New York City (J) | 60 | +57% | 9.4% → 15.6% | 64 | +6% | 14.2% → 17.1% | +15% · +26% | 13.1% |
| Long Island (K) | 93 | +44% | 22.9% → 31.0% | 86 | +6% | 23.0% → 26.4% | +15% · +18% | 32.7% |

Spike-equation skill against each zone's historical spike rate (Brier skill, out of sample): 2019 +0.20, 2021 +0.10, 2023 +0.15, 2024 +0.11, 2025 +0.18.
