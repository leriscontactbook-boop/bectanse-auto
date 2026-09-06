# Bectanse Coach deterministic scoring

The Coach reads only verified, closed trades. It generates no signal, forecast,
entry, exit or market recommendation. With fewer than 20 rolling trades it shows
`INSUFFICIENT` and suppresses behavioral conclusions.

The score contains five equally weighted components: discipline, risk,
consistency, execution and timing. Each begins at 100. Eligible detector evidence
deducts 22 points for HIGH severity, 12 for MEDIUM and 6 for LOW, multiplied by
the detector confidence. Each detector maps only to documented components; each
component is floored at zero. The final score is their arithmetic mean.

Sequence detectors require at least eight observations. Bucket comparisons
(session, weekday, symbol, holding time) require at least five trades per bucket
and at least two eligible buckets. Position-size consistency requires 15 trades.
Confidence grows from 0.55 with sample coverage and is capped at 0.99.

Daily, weekly and monthly reviews use templates over the same structured detector
records. An optional AI layer may rephrase titles and recommendations, but must
preserve the evidence, impact, confidence, sample size and time period verbatim.
If AI is unavailable, the templates remain the complete product behavior.
