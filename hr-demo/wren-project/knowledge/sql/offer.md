---
nl: Offer拒绝原因分布？
sql: SELECT reject_reason AS 拒绝原因, count(*) AS 数量 FROM offers WHERE status = '已拒绝'
  GROUP BY reject_reason
source: user
---
