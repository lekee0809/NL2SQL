-- 仅用于本地测试：会清空这五张项目表并重新生成数据。
TRUNCATE payments, order_items, orders, products, customers RESTART IDENTITY CASCADE;
SELECT setseed(0.42);

INSERT INTO customers (name, region)
SELECT '测试客户_' || n,
       (ARRAY['华东','华北','华南','西南','西北'])[1 + floor(random() * 5)::int]
FROM generate_series(1, 200) AS s(n);

INSERT INTO products (name, category)
SELECT '测试商品_' || n,
       (ARRAY['电子产品','办公用品','家居用品','食品'])[1 + floor(random() * 4)::int]
FROM generate_series(1, 30) AS s(n);

DO $$
DECLARE
  i int; item_no int; item_count int; customer_id int; order_id int;
  product_id int; quantity int; unit_price numeric(12,2); total numeric(12,2);
  order_status text; order_date date;
BEGIN
  FOR i IN 1..2000 LOOP
    customer_id := 1 + floor(random() * 200)::int;
    order_date := date '2024-01-01' + floor(random() * 731)::int;
    order_status := (ARRAY['PAID','PAID','PAID','CANCELLED','REFUNDED'])[1 + floor(random() * 5)::int];
    INSERT INTO orders(customer_id, created_at, status, total_amount)
    VALUES (customer_id, order_date, order_status, 0) RETURNING id INTO order_id;
    total := 0;
    item_count := 1 + floor(random() * 3)::int;
    FOR item_no IN 1..item_count LOOP
      product_id := 1 + floor(random() * 30)::int;
      quantity := 1 + floor(random() * 5)::int;
      unit_price := round((10 + random() * 990)::numeric, 2);
      INSERT INTO order_items(order_id, product_id, quantity, unit_price)
      VALUES (order_id, product_id, quantity, unit_price);
      total := total + quantity * unit_price;
    END LOOP;
    UPDATE orders SET total_amount = total WHERE id = order_id;
    IF order_status = 'PAID' THEN
      INSERT INTO payments(order_id, paid_at, method)
      VALUES (order_id, order_date, (ARRAY['card','alipay','wechat','bank'])[1 + floor(random() * 4)::int]);
    END IF;
  END LOOP;
END $$;
