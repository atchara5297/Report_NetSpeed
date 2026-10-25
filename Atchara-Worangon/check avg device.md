1. Same day — ค่าเฉลี่ยรวมทุกอุปกรณ์ในวันเดียวกัน
SELECT
COUNT(\*) AS total\_records,
ROUND(AVG(throughput\_mbps), 2) AS avg\_throughput
FROM ThroughputSpeed
WHERE DATE(recorded\_at) = '2026-10-11';

รายนาที ทุกอุปกรณ์
2. SELECT
COUNT(\*) AS total\_records,
ROUND(AVG(throughput\_mbps), 2) AS avg\_throughput
FROM ThroughputSpeed
WHERE recorded\_at >= '2026-10-11 17:54:00'
AND recorded\_at < '2026-10-11 17:55:00';



###### รายนาที รายอุปกรณ์

1. SELECT
device\_id,
COUNT(\*) AS total\_records,
ROUND(AVG(throughput\_mbps), 2) AS avg\_throughput
FROM ThroughputSpeed
WHERE device\_id = 3
AND recorded\_at >= '2026-10-11 17:54:00'
AND recorded\_at <  '2026-10-11 17:55:00'
GROUP BY device\_id;


2. Same day — ค่าเฉลี่ยรายอุปกรณ์ในวันเดียวกัน
SELECT
device\_id,
COUNT(\*) AS total\_records,
ROUND(AVG(throughput\_mbps), 2) AS avg\_throughput
FROM ThroughputSpeed
WHERE device\_id = 3
AND DATE(recorded\_at) = '2026-09-27'
GROUP BY device\_id;

3. Different day — ค่าเฉลี่ยรายอุปกรณ์ตามช่วงวันที่ที่เราเลือก
SELECT
device\_id,
COUNT(\*) AS total\_records,
ROUND(AVG(throughput\_mbps), 2) AS avg\_throughput
FROM ThroughputSpeed
WHERE device\_id = 3
AND DATE(recorded\_at) BETWEEN '2026-10-01' AND '2026-10-11'
GROUP BY device\_id;
4. Different day — ค่าเฉลี่ยรวมทุกอุปกรณ์ตามช่วงวันที่ที่เราเลือก
SELECT
COUNT(\*) AS total\_records,
ROUND(AVG(throughput\_mbps), 2) AS avg\_throughput
FROM ThroughputSpeed
WHERE DATE(recorded\_at) BETWEEN '2026-10-01' AND '2026-10-11';
5. One day — ค่าเฉลี่ยของอุปกรณ์หมายเลข 3 ในวันเดียว

คำสั่งนี้เหมือนกับข้อ 2 แต่ไม่แสดงจำนวนรายการ

SELECT
ROUND(AVG(throughput\_mbps), 2) AS avg\_throughput
FROM ThroughputSpeed
WHERE device\_id = 3
AND DATE(recorded\_at) = '2026-09-27';

