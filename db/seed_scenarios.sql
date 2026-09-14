-- V0.5 边界场景数据。可重复执行，只替换本脚本占用的高位 ID 数据。
BEGIN;

DELETE FROM payments WHERE id BETWEEN 10001 AND 10020;
DELETE FROM order_items WHERE id BETWEEN 20001 AND 20040;
DELETE FROM orders WHERE id BETWEEN 10001 AND 10020;
DELETE FROM products WHERE id BETWEEN 1001 AND 1010;
DELETE FROM customers WHERE id BETWEEN 1001 AND 1008;

INSERT INTO customers (id, name, region) VALUES
  (1001, '华东实验学校', '华东'),
  (1002, '华东地区第一实验学校', '华东'),
  (1003, '华南实验学校', '华南'),
  (1004, '西南职业学院', '西南'),
  (1005, '北辰科技公司', '华北'),
  (1006, '西北希望小学', '西北'),
  (1007, '华东实验学校附属中学', '华东'),
  (1008, '零订单客户', '华北');

INSERT INTO products (id, name, category) VALUES
  (1001, '极光键盘-标准版', '电子产品'),
  (1002, '极光键盘-专业版', '电子产品'),
  (1003, '极光无线鼠标', '电子产品'),
  (1004, '智慧教学白板A1', '办公用品'),
  (1005, '智慧教学白板A2', '办公用品'),
  (1006, '校园资料册', '办公用品'),
  (1007, '桂花糕礼盒', '食品'),
  (1008, '零销量展示柜', '家居用品'),
  (1009, '测试商品_115', '电子产品'),
  (1010, '星云护眼台灯', '家居用品');

INSERT INTO orders (id, customer_id, created_at, status, total_amount) VALUES
  (10001, 1001, DATE '2024-03-15', 'PAID', 0),
  (10002, 1001, DATE '2025-01-10', 'PAID', 0),
  (10003, 1001, DATE '2025-02-11', 'PAID', 0),
  (10004, 1002, DATE '2025-01-18', 'PAID', 0),
  (10005, 1002, DATE '2025-04-05', 'PAID', 0),
  (10006, 1003, DATE '2025-03-20', 'PAID', 0),
  (10007, 1003, DATE '2025-06-08', 'REFUNDED', 0),
  (10008, 1004, DATE '2025-07-12', 'PAID', 0),
  (10009, 1005, DATE '2025-09-01', 'CANCELLED', 0),
  (10010, 1005, DATE '2025-09-02', 'PAID', 0),
  (10011, 1006, DATE '2025-11-15', 'PAID', 0),
  (10012, 1007, DATE '2025-12-20', 'PAID', 0),
  (10013, 1001, DATE '2026-01-08', 'PAID', 0),
  (10014, 1002, DATE '2026-02-14', 'PAID', 0),
  (10015, 1003, DATE '2024-04-10', 'PAID', 0),
  (10016, 1004, DATE '2024-07-21', 'PAID', 0),
  (10017, 1005, DATE '2024-10-03', 'PAID', 0),
  (10018, 1006, DATE '2025-05-22', 'PAID', 0),
  (10019, 1007, DATE '2025-02-28', 'PAID', 0),
  (10020, 1001, DATE '2025-03-31', 'PAID', 0);

INSERT INTO order_items (id, order_id, product_id, quantity, unit_price) VALUES
  (20001, 10001, 1001, 2, 300), (20002, 10001, 1007, 5, 50),
  (20003, 10002, 1001, 3, 320), (20004, 10002, 1003, 2, 120),
  (20005, 10003, 1002, 1, 650), (20006, 10003, 1004, 2, 1200),
  (20007, 10004, 1001, 5, 310),
  (20008, 10005, 1002, 2, 680), (20009, 10005, 1005, 1, 1350),
  (20010, 10006, 1003, 4, 125), (20011, 10006, 1007, 10, 48),
  (20012, 10007, 1001, 1, 300),
  (20013, 10008, 1004, 1, 1250), (20014, 10008, 1006, 20, 15),
  (20015, 10009, 1002, 1, 700),
  (20016, 10010, 1010, 3, 180),
  (20017, 10011, 1006, 30, 14), (20018, 10011, 1007, 6, 55),
  (20019, 10012, 1005, 2, 1300),
  (20020, 10013, 1001, 2, 330),
  (20021, 10014, 1002, 1, 700), (20022, 10014, 1010, 2, 190),
  (20023, 10015, 1003, 3, 110),
  (20024, 10016, 1004, 2, 1180),
  (20025, 10017, 1007, 8, 45),
  (20026, 10018, 1005, 1, 1320), (20027, 10018, 1006, 10, 16),
  (20028, 10019, 1002, 2, 660),
  (20029, 10020, 1001, 1, 315), (20030, 10020, 1002, 1, 675),
  (20031, 10020, 1003, 1, 130);

UPDATE orders o
SET total_amount = totals.amount
FROM (
  SELECT order_id, SUM(quantity * unit_price) AS amount
  FROM order_items
  WHERE order_id BETWEEN 10001 AND 10020
  GROUP BY order_id
) totals
WHERE o.id = totals.order_id;

INSERT INTO payments (id, order_id, paid_at, method)
SELECT o.id, o.id, o.created_at,
       (ARRAY['alipay', 'wechat', 'card', 'bank'])[1 + ((o.id - 10001) % 4)]
FROM orders o
WHERE o.id BETWEEN 10001 AND 10020 AND o.status = 'PAID';

SELECT setval(pg_get_serial_sequence('customers', 'id'), GREATEST((SELECT MAX(id) FROM customers), 1));
SELECT setval(pg_get_serial_sequence('products', 'id'), GREATEST((SELECT MAX(id) FROM products), 1));
SELECT setval(pg_get_serial_sequence('orders', 'id'), GREATEST((SELECT MAX(id) FROM orders), 1));
SELECT setval(pg_get_serial_sequence('order_items', 'id'), GREATEST((SELECT MAX(id) FROM order_items), 1));
SELECT setval(pg_get_serial_sequence('payments', 'id'), GREATEST((SELECT MAX(id) FROM payments), 1));

COMMIT;
