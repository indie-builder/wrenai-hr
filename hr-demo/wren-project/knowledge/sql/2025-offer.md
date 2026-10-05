---
nl: 2025年Offer接受率（已接受/已回复）？
sql: 'SELECT count(*) FILTER (WHERE status = ''已接受'') AS 已接受, count(*) FILTER (WHERE
  status = ''已拒绝'') AS 已拒绝,

  round(count(*) FILTER (WHERE status = ''已接受'') * 100.0 / NULLIF(count(*) FILTER
  (WHERE status IN (''已接受'',''已拒绝'')), 0), 1) AS 接受率

  FROM offers WHERE offer_date BETWEEN DATE ''2025-01-01'' AND DATE ''2025-12-31'''
source: user
---
